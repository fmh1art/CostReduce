import json
from pathlib import Path

import pytest
import src.evolve.evolve_v8 as evolve_v8

from src.evolve.evolve_v8 import (
    CandidateSelector,
    CostLedger,
    EvolveV8Experiment,
    ExecutionGraphBuilder,
    InstructionCandidateBuilder,
    InstructionCardCompiler,
    InstructionSampleBuilder,
    InstructionValidationGate,
    MotifMiner,
    NormalizedCall,
    PriceModel,
    RegistryPilotGate,
    ShellNormalizer,
    ToolCardCompiler,
    ValidationGate,
    _stable_hash,
    validate_registry,
)


def test_shell_normalizer_accepts_repo_context_and_rejects_writes():
    normalizer = ShellNormalizer()
    calls = normalizer.normalize_shell("cd /app && rg Foo src | head -20")
    assert [call.op for call in calls] == ["SEARCH", "READ"]
    assert all(call.accepted for call in calls)
    rejected = normalizer.normalize_shell("cd /app && cat <<EOF > x.py")
    assert not rejected[0].accepted


def test_cost_ledger_attributes_future_cached_exposure():
    steps = [
        {"metrics": {"prompt_tokens": 100, "cached_tokens": 80, "completion_tokens": 10},
         "observation": "x" * 400},
        {"metrics": {"prompt_tokens": 200, "cached_tokens": 100, "completion_tokens": 5},
         "observation": ""},
    ]
    costs = CostLedger(PriceModel(1, 0.1, 2, 4)).annotate(steps)
    assert costs[0]["observation_tokens"] == 100
    assert costs[0]["exposure_cost"] == pytest.approx(100)  # first exposure is outside the cached prefix
    assert costs[1]["exposure_cost"] == 0


def test_graph_uses_explicit_dependencies_not_temporal_adjacency(tmp_path: Path):
    case = tmp_path / "case"
    agent = case / "agent"
    agent.mkdir(parents=True)
    trajectory = {
        "dependencies": {"1": [0], "2": [0], "3": [1], "4": [3]},
        "steps": [
            {"tool_calls": [{"function_name": "bash", "arguments": {"command": "rg Foo src"}}],
             "observation": "src/a.py:1:Foo"},
            {"tool_calls": [{"function_name": "bash", "arguments": {"command": "pwd"}}],
             "observation": "/repo"},
            {"step_meta": {"op_type": "write"},
             "tool_calls": [{"function_name": "bash", "arguments": {"command": "touch src/a.py"}}],
             "observation": ""},
            {"tool_calls": [{"function_name": "bash", "arguments": {"command": "pytest -q"}}],
             "observation": "1 passed"},
        ],
    }
    path = agent / "trajectory.json"
    path.write_text(json.dumps(trajectory))
    graph = ExecutionGraphBuilder().build(path)
    assert {(x["source"], x["target"]) for x in graph["edges"]} == {(1, 3), (3, 4)}
    assert 2 not in graph["outcome"]["anchor_closure"]
    assert graph["outcome"]["anchors"] == [3]


def test_motif_support_is_unique_tasks_and_hash_is_stable():
    nodes = [
        {"id": 1, "labels": ["SEARCH|<src>|symbol"], "read_only": True,
         "cost": {"direct_cost": 10, "exposure_cost": 5, "observation_tokens": 20}},
        {"id": 2, "labels": ["READ|<src>|path"], "read_only": True,
         "cost": {"direct_cost": 10, "exposure_cost": 5, "observation_tokens": 20}},
    ]
    def graph(task):
        return {"eligible": True, "task_id": task, "trajectory_path": task, "nodes": nodes,
                "edges": [{"source": 1, "target": 2}],
                "outcome": {"anchor_closure": [1, 2]}}
    motifs = MotifMiner(min_support=2).mine([graph("a"), graph("a"), graph("b")])
    assert len(motifs) == 1
    assert motifs[0]["support"] == 2
    again = MotifMiner(min_support=2).mine([graph("b"), graph("a")])
    assert motifs[0]["motif_hash"] == again[0]["motif_hash"]


def _candidate():
    return {
        "candidate_id": "cand-1234567890ab",
        "selected": True,
        "support_tasks": ["train-a", "train-b"],
        "occurrences": [{"task_id": "train-a", "node_ids": [1, 2]}],
        "output_token_cap": 10,
        "signature": {"node_labels": [["SEARCH|<src>|symbol"], ["READ|<src>|path"]]},
        "saving_mean": 20,
        "saving_lcb": 10,
        "support": 2,
    }


def test_validation_is_fail_closed_and_cards_require_matching_fingerprint():
    candidate = _candidate()
    gate = ValidationGate(non_inferiority_margin=0.05)
    missing = gate.validate(candidate, {})
    assert missing["status"] == "pending"
    assert not ToolCardCompiler().cards([candidate], {candidate["candidate_id"]: missing})

    evidence = {
        "scenario_replay": {"occurrences": [{
            "task_id": "train-a", "node_ids": [1, 2],
            "original_locations": ["src/a.py:1"], "replay_locations": ["src/a.py:1"],
            "output_chars": 20, "returncode": 0,
        }]},
        "downstream_replay": {"occurrences": [{
            "task_id": "train-a", "original_diff_hash": "abc", "replay_diff_hash": "abc",
            "target_tests_passed": True, "state_effects": [],
        }]},
        "heldout": {
            "baseline": [{"task_id": "held", "success": True, "cost": 10}],
            "treatment": [{"task_id": "held", "success": True, "cost": 8}],
        },
    }
    passed = gate.validate(candidate, evidence)
    assert passed["passed"]
    cards = ToolCardCompiler().cards([candidate], {candidate["candidate_id"]: passed})
    assert len(cards) == 1


def test_compile_prompt_separates_baseline_governance_from_instruction_cards(tmp_path: Path):
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    evolve_v8.seed_v8(scripts_dir)

    instruction_card = {
        "policy_id": "policy-1", "status": "validated", "policy_type": "early_exit",
        "trigger": "repeated_no_new_evidence",
        "rule": "Stop only after repeated attempts yield no new evidence.",
    }
    prompt = ToolCardCompiler.prompt(
        [], scripts_dir, instruction_cards=[instruction_card]
    )

    assert "preserve tools.json and executor.py byte-for-byte" in prompt
    assert "Baseline governance guardrails" in prompt
    assert "policy-1" in prompt
    assert "Stop repeating an approach when it produces no new evidence" in prompt
    assert "cheapest meaningful substitute" in prompt
    assert "scoped and reversible risks" in prompt
    assert "unconditional attempt count" in prompt


def _instruction_sample(task, role, policy_type="early_exit", trigger="stuck"):
    return {
        "sample_id": f"{task}-{role}", "task_id": task, "policy_type": policy_type,
        "trigger": trigger, "role": role,
    }


def test_instruction_candidates_require_repeated_hypotheses_and_negative_control():
    builder = InstructionCandidateBuilder(min_support=3)
    insufficient = builder.build([
        _instruction_sample("a", "hypothesis"),
        _instruction_sample("b", "hypothesis"),
        _instruction_sample("n", "negative"),
    ])
    assert not insufficient[0]["selected"]

    selected = builder.build([
        _instruction_sample("a", "hypothesis"),
        _instruction_sample("b", "hypothesis"),
        _instruction_sample("c", "hypothesis"),
        _instruction_sample("n", "negative"),
    ])[0]
    assert selected["selected"]
    assert selected["requires_paired_intervention"]


def test_instruction_candidates_accept_graph_derived_policy_types_and_bound_budget():
    samples = []
    for index in range(20):
        metadata = {
            "recommended_rule": f"Evidence-derived workflow rule {index}.",
            "adoption_signal": f"transition:READ>OP{index}",
            "adoption_direction": "present",
            "risk_level": "low",
        }
        for task, role in (("a", "hypothesis"), ("b", "hypothesis"), ("n", "negative")):
            samples.append({
                "sample_id": f"{index}-{task}", "task_id": task,
                "policy_type": f"workflow_open_{index}", "trigger": f"signal-{index}",
                "role": role, "metadata": metadata,
            })

    candidates = InstructionCandidateBuilder(min_support=2, max_candidates=15).build(samples)

    assert len(candidates) == 15
    assert all(candidate["selected"] for candidate in candidates)
    assert all(candidate["policy_type"].startswith("workflow_open_") for candidate in candidates)
    assert all(candidate["adoption_direction"] == "present" for candidate in candidates)


def test_graph_derived_instruction_signal_is_observable_in_paired_gate():
    policy = {
        "policy_type": "workflow_transition",
        "adoption_signal": "transition:SEARCH>READ",
        "adoption_direction": "present",
    }
    baseline = {"cases": {
        task: {"success": True, "cost": 2.0, "turns": 5, "native_calls": 0,
               "max_native_output_chars": 0, "instruction_signals": {}}
        for task in ("discovery", "heldout")
    }}
    treatment = {"cases": {
        task: {"success": True, "cost": 1.0, "turns": 4, "native_calls": 0,
               "max_native_output_chars": 0,
               "instruction_signals": {"transition:SEARCH>READ": 1}}
        for task in ("discovery", "heldout")
    }}

    result = evolve_v8.RegistryPilotGate().evaluate(
        baseline, treatment,
        {"discovery": ["discovery"], "heldout": ["heldout"]},
        instruction_policy=policy,
    )

    assert result["passed"]
    assert result["policy_adopted_tasks"] == ["discovery", "heldout"]


def test_missing_verification_is_only_an_instruction_hypothesis():
    graph = {
        "task_id": "a", "trajectory_path": "a/trajectory.json",
        "outcome": {"verifier_pass": True, "anchors": [1]},
        "nodes": [{
            "id": 1, "operations": ["WRITE"], "labels": ["WRITE|<src>|"] ,
            "call_examples": [], "observation_hash": "x", "observation_excerpt": "",
        }],
    }
    samples = InstructionSampleBuilder().build([graph])
    verification = [x for x in samples if x["policy_type"] == "verification_skip"]
    assert len(verification) == 1
    assert verification[0]["role"] == "hypothesis"
    assert "intervention" in verification[0]["rationale"]


def test_instruction_gate_requires_candidate_specific_paired_evidence():
    candidate = InstructionCandidateBuilder(min_support=3).build([
        _instruction_sample("a", "hypothesis"),
        _instruction_sample("b", "hypothesis"),
        _instruction_sample("c", "hypothesis"),
        _instruction_sample("n", "negative"),
    ])[0]
    gate = InstructionValidationGate()
    assert not gate.validate(candidate, {})["passed"]

    def arm(task, base_cost, treatment_cost):
        return {
            "baseline": [{"task_id": task, "success": True, "cost": base_cost}],
            "treatment": [{"task_id": task, "success": True, "cost": treatment_cost,
                           "policy_triggered": True}],
        }

    evidence = {
        "candidate_fingerprint": _stable_hash(candidate),
        "discovery": arm("a", 2, 1),
        "heldout": arm("heldout", 2, 1),
    }
    result = gate.validate(candidate, evidence)
    assert result["passed"]
    cards = InstructionCardCompiler().cards(
        [candidate], {candidate["candidate_id"]: result}
    )
    assert cards[0]["status"] == "validated"


def test_verification_skip_gate_requires_external_verifier():
    candidate = {
        "candidate_id": "policy-skip", "selected": True,
        "policy_type": "verification_skip", "support_tasks": ["a", "b", "c"],
    }
    arm = {
        "baseline": [{"task_id": "heldout", "success": True, "cost": 2}],
        "treatment": [{"task_id": "heldout", "success": True, "cost": 1,
                       "policy_triggered": True, "external_verifier_passed": False}],
    }
    evidence = {
        "candidate_fingerprint": _stable_hash(candidate),
        "discovery": arm,
        "heldout": {
            "baseline": [{"task_id": "other", "success": True, "cost": 2}],
            "treatment": [{"task_id": "other", "success": True, "cost": 1,
                           "policy_triggered": False}],
        },
    }
    result = InstructionValidationGate().validate(candidate, evidence)
    assert not result["passed"]
    assert any("external passing verifier" in reason for reason in result["reasons"])


def test_prepare_emits_separate_instruction_artifacts(tmp_path: Path):
    run_dir = tmp_path / "run"
    for task in ("a", "b"):
        agent = run_dir / task / "agent"
        agent.mkdir(parents=True)
        (agent / "trajectory.json").write_text(json.dumps({
            "task_id": task,
            "dependencies": {"1": []},
            "steps": [{
                "step_meta": {"op_type": "write"},
                "tool_calls": [{"function_name": "bash", "arguments": {
                    "command": f"touch src/{task}.py"}}],
                "observation": "",
            }],
        }))
        (run_dir / task / "result.json").write_text(json.dumps({
            "verifier_result": {"rewards": {"reward": 1}}
        }))

    work = tmp_path / "work"
    report = evolve_v8.V8Pipeline(work, min_support=2).prepare(
        run_dir, heldout_fraction=0.5
    )

    assert report["instruction_samples"] >= 1
    assert (work / "instruction_samples.json").exists()
    assert (work / "instruction_candidates.json").exists()
    samples = json.loads((work / "instruction_samples.json").read_text())["samples"]
    assert all(sample["role"] in {"hypothesis", "negative"} for sample in samples)


def test_policy_only_arm_restores_tool_files(tmp_path: Path):
    work = tmp_path / "cycle-2"
    work.mkdir()
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    evolve_v8.seed_v8(scripts)
    original_tools = (scripts / "tools.json").read_text()
    original_executor = (scripts / "executor.py").read_text()
    candidate = InstructionCandidateBuilder(min_support=3).build([
        _instruction_sample("a", "hypothesis"),
        _instruction_sample("b", "hypothesis"),
        _instruction_sample("c", "hypothesis"),
        _instruction_sample("n", "negative"),
    ])[0]
    (work / "candidates.json").write_text(json.dumps({"candidates": []}))
    (work / "instruction_candidates.json").write_text(json.dumps({
        "candidates": [candidate]
    }))
    pipeline = evolve_v8.V8Pipeline(work, min_support=3)

    prompt = pipeline.prototype_prompt(scripts)
    assert "preserve tools.json and executor.py byte-for-byte" in prompt
    candidate_pool = json.loads((work / "instruction_candidate_cards.json").read_text())["cards"]
    assert [card["policy_id"] for card in candidate_pool] == [candidate["candidate_id"]]
    (scripts / "tools.json").write_text("[] # compiler drift")
    (scripts / "executor.py").write_text("# compiler drift")

    assert pipeline.restore_policy_arm_tools(scripts)
    assert (scripts / "tools.json").read_text() == original_tools
    assert (scripts / "executor.py").read_text() == original_executor


def test_policy_only_validation_accepts_preserved_tool_candidate_ids(tmp_path: Path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "tools.json").write_text(json.dumps([{
        "name": "existing_context_tool",
        "description": "Previously promoted tool. [VCGC:cand-prior123]",
        "parameters": {"type": "object", "properties": {}, "required": []},
    }]))
    (scripts / "executor.py").write_text(
        "def run_tool(action, cwd=None, timeout=120):\n"
        "    if action.get('tool') == 'existing_context_tool':\n"
        "        return {'output': '', 'returncode': 0, 'exception_info': ''}\n"
        "    return {'output': '', 'returncode': 1, 'exception_info': 'unknown'}\n"
    )
    (scripts / "instruction.md").write_text("# policy canary\n")

    warnings = evolve_v8.validate_registry(scripts, cards=None)

    assert not any("unsupported candidate ids" in warning for warning in warnings)


def test_selector_bootstrap_and_overlap_selection_are_reproducible():
    motif = {
        "motif_hash": "1234567890abcdef1234",
        "signature": {"node_labels": [["READ|<src>|path"]]},
        "support": 2,
        "support_tasks": ["a", "b"],
        "occurrences": [
            {"task_id": task, "node_ids": [1, 2], "covered_steps": [f"{task}:1", f"{task}:2"],
             "direct_cost": 1000, "exposure_cost": 500, "observation_tokens": 2000,
             "trajectory_turns": 10, "boundary": {}}
            for task in ("a", "b")
        ],
    }
    first = CandidateSelector(bootstrap_samples=100, schema_tokens=10).score([motif])
    second = CandidateSelector(bootstrap_samples=100, schema_tokens=10).score([motif])
    assert first == second
    assert first[0]["selected"]
    assert first[0]["saving_lcb"] > 0


def test_registry_pilot_gate_requires_adoption_cost_drop_and_no_regression():
    split = {"discovery": ["a"], "heldout": ["b"]}
    baseline = {"cases": {
        "a": {"success": True, "cost": 2, "native_calls": 0, "max_native_output_chars": 0},
        "b": {"success": False, "cost": 2, "native_calls": 0, "max_native_output_chars": 0},
    }}
    treatment = {"cases": {
        "a": {"success": True, "cost": 1, "native_calls": 1, "max_native_output_chars": 100},
        "b": {"success": True, "cost": 1, "native_calls": 1, "max_native_output_chars": 100},
    }}
    assert RegistryPilotGate().evaluate(baseline, treatment, split)["passed"]
    treatment["cases"]["a"]["success"] = False
    result = RegistryPilotGate().evaluate(baseline, treatment, split)
    assert not result["passed"]
    assert result["regressions"] == ["a"]


def test_registry_pilot_gate_tolerates_bounded_llm_variance():
    task_ids = [str(i) for i in range(12)]
    split = {"discovery": task_ids[:9] + task_ids[10:11], "heldout": ["9", "11"]}
    baseline = {"cases": {
        task: {"success": index < 10, "cost": 1.0, "native_calls": 0,
               "max_native_output_chars": 0}
        for index, task in enumerate(task_ids)
    }}
    treatment = {"cases": {
        task: {"success": (index < 10 and index not in (0, 1)) or index == 10,
               "cost": 1.02, "native_calls": 1, "max_native_output_chars": 100}
        for index, task in enumerate(task_ids)
    }}
    result = RegistryPilotGate().evaluate(baseline, treatment, split)
    assert result["passed"]
    assert result["regression_rate"] == 0.2
    assert result["success_drop_rate"] == 0.1
    assert result["tolerated_variations"]

    for case in treatment["cases"].values():
        case["cost"] = 1.05
    result = RegistryPilotGate().evaluate(baseline, treatment, split)
    assert not result["passed"]
    assert any("cost increase rate" in reason for reason in result["reasons"])


def test_failed_cycle_summary_captures_problems_and_tool_attribution():
    baseline = {"run_dir": "base", "cases": {
        "a": {"success": True, "cost": 1, "turns": 10},
        "b": {"success": False, "cost": 1, "turns": 10},
    }}
    treatment = {"run_dir": "treatment", "cases": {
        "a": {"success": False, "cost": 2, "turns": 20,
              "native_tool_counts": {"search_context": 3}},
        "b": {"success": True, "cost": 0.5, "turns": 8,
              "native_tool_counts": {"search_context": 1}},
    }}
    gate = {"reasons": ["regression rate too high"], "tolerated_variations": [],
            "cost_increase_rate": 0.25, "cap_violations": []}
    summary = EvolveV8Experiment._cycle_summary(
        1, "rollback-and-continue", baseline, treatment, gate
    )
    assert summary["staging_abandoned"]
    assert summary["regressions"] == ["a"]
    assert summary["improvements"] == ["b"]
    assert summary["regression_tool_counts"] == {"search_context": 3}
    assert summary["problems"] == ["regression rate too high"]


def test_failed_pilot_is_abandoned_and_all_cycles_continue(tmp_path: Path, monkeypatch):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    work = tmp_path / "work"
    for cycle in range(1, 4):
        cycle_dir = work / f"cycle-{cycle}"
        (cycle_dir / "staging").mkdir(parents=True)
        (cycle_dir / "compile_trajectory.json").write_text("{}")
        (cycle_dir / "prototype_cards.json").write_text(json.dumps({"cards": []}))
        (cycle_dir / "split.json").write_text(json.dumps({
            "discovery": ["a"], "heldout": ["b"]
        }))

    class FakePipeline:
        def __init__(self, *args, **kwargs):
            pass

        def prepare(self, run_dir, heldout_fraction=0.25):
            return {"selected": 1}

    class FakeManager:
        def __init__(self, cycle_dir, scripts_dir):
            self.staging_dir = Path(cycle_dir) / "staging"

    class FakeRollout:
        calls = 0

        def __init__(self, *args, **kwargs):
            pass

        def rollout(self, *args, **kwargs):
            self.__class__.calls += 1
            return type("Result", (), {"run_dir": Path(f"treatment-{self.calls}")})()

    baseline = {"run_dir": "baseline", "cases": {
        "a": {"success": True, "cost": 1, "turns": 10, "native_calls": 0,
              "max_native_output_chars": 0},
        "b": {"success": True, "cost": 1, "turns": 10, "native_calls": 0,
              "max_native_output_chars": 0},
    }}
    treatment = {"run_dir": "treatment", "cases": {
        "a": {"success": False, "cost": 2, "turns": 20, "native_calls": 1,
              "native_tool_counts": {"search_context": 1}, "max_native_output_chars": 100},
        "b": {"success": False, "cost": 2, "turns": 20, "native_calls": 1,
              "native_tool_counts": {"search_context": 1}, "max_native_output_chars": 100},
    }}

    monkeypatch.setattr(evolve_v8, "seed_v8", lambda *args, **kwargs: None)
    monkeypatch.setattr(evolve_v8, "deploy_v6", lambda *args, **kwargs: None)
    monkeypatch.setattr(evolve_v8, "RolloutAgent", FakeRollout)
    monkeypatch.setattr(evolve_v8, "V8Pipeline", FakePipeline)
    monkeypatch.setattr(evolve_v8, "RegistryManager", FakeManager)
    monkeypatch.setattr(evolve_v8, "validate_registry", lambda *args, **kwargs: [])
    monkeypatch.setattr(evolve_v8, "analyze_run",
                        lambda path, prices: baseline if Path(path).name == "baseline" else treatment)
    monkeypatch.setattr(EvolveV8Experiment, "_ensure_annotated", lambda *args: None)
    monkeypatch.setattr(EvolveV8Experiment, "_require_complete_run", lambda *args: None)

    experiment = EvolveV8Experiment(
        benchmark="swebench", config="_config/deepseekv4_flash.yaml",
        scripts_dir=scripts, work_dir=work, case_ids=["a", "b"],
        baseline_dir=Path("baseline"), n_cycles=3,
    )
    report = experiment.run()
    assert len(report["cycles"]) == 3
    assert [item["decision"] for item in report["cycles"]] == [
        "rollback-and-continue", "rollback-and-continue", "rollback-and-continue"
    ]
    assert not any(item["promoted"] for item in report["cycles"])
    history = json.loads((work / "evolution_history.json").read_text())
    assert len(history["cycles"]) == 3
    assert all(item["staging_abandoned"] for item in history["cycles"])


def test_registry_validator_rejects_bash_wrapper(tmp_path: Path):
    (tmp_path / "tools.json").write_text(json.dumps([{
        "name": "bash", "description": "wrapper", "parameters": {
            "type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]},
    }]))
    (tmp_path / "executor.py").write_text(
        "import subprocess\nMAX_OUTPUT_CHARS=4000\n"
        "def run_tool(action,cwd=None,timeout=120):\n"
        " r=subprocess.run(action.get('arguments',{}).get('command',''),shell=True)\n"
        " return {'output':'','returncode':r.returncode,'exception_info':''}\n"
    )
    warnings = validate_registry(tmp_path)
    assert any("reserved" in x for x in warnings)
    assert any("shell=True" in x for x in warnings)
    assert any("arbitrary execution" in x for x in warnings)


def test_registry_validator_rejects_materialized_recursive_discovery(tmp_path: Path):
    (tmp_path / "tools.json").write_text(json.dumps([{
        "name": "search_context", "description": "search", "parameters": {
            "type": "object", "properties": {
                "query": {"type": "string"}, "path": {"type": "string"}},
            "required": ["query", "path"]},
    }]))
    (tmp_path / "executor.py").write_text(
        "MAX_OUTPUT_CHARS=4000\n"
        "def _sorted_files_recursive(path):\n yield path\n"
        "def run_tool(action,cwd=None,timeout=120):\n"
        " if action.get('tool') == 'search_context':\n"
        "  paths=list(_sorted_files_recursive(action.get('path')))\n"
        "  return {'output':str(paths),'returncode':0,'exception_info':''}\n"
        " return {'output':'missing','returncode':1,'exception_info':'missing'}\n"
    )
    warnings = validate_registry(tmp_path)
    assert any("materialize recursive file discovery" in warning for warning in warnings)


def test_prototype_cards_preserve_contraction_boundary():
    base = {
        "selected": True, "output_token_cap": 1000, "saving_lcb": 5,
        "support_tasks": ["a", "b", "c"], "occurrences": [],
    }
    read = {**base, "candidate_id": "cand-read", "signature": {
        "node_labels": [["READ|<src>|path"], ["READ|<src>|path"]]}}
    search = {**base, "candidate_id": "cand-search", "signature": {
        "node_labels": [["SEARCH|<src>|symbol"], ["READ|<src>|path"]]}}
    cards = ToolCardCompiler().prototype_cards([read, search])
    assert len(cards) == 2
    assert {tuple(x["candidate_ids"]) for x in cards} == {
        ("cand-read",), ("cand-search",),
    }
    assert all("tool_name" not in card for card in cards)
    assert all(card["output_char_cap"] == 4000 for card in cards)
    assert all("Infer a descriptive tool name" in card["design_contract"] for card in cards)


def test_prototype_cards_do_not_collapse_candidates_into_fixed_tool_families():
    base = {
        "selected": True, "output_token_cap": 1000, "saving_lcb": 5,
        "saving_mean": 6, "support_tasks": ["a", "b", "c"], "occurrences": [],
    }
    candidates = [
        {**base, "candidate_id": f"cand-{index}", "signature": {
            "node_labels": [["SEARCH|<src>|symbol"], ["READ|<src>|path"]],
            "edges": [], "size": 2,
        }}
        for index in range(3)
    ]
    cards = ToolCardCompiler().prototype_cards(candidates)
    assert len(cards) == 3
    assert [card["candidate_ids"] for card in cards] == [
        ["cand-0"], ["cand-1"], ["cand-2"],
    ]


def test_registry_validator_rejects_nonadvancing_cursor(tmp_path: Path):
    (tmp_path / "tools.json").write_text(json.dumps([{
        "name": "batch_read", "description": "bad cursor", "parameters": {
            "type": "object", "properties": {
                "files": {"type": "array", "items": {"type": "object"}, "minItems": 2},
                "offset": {"type": "integer"}}, "required": ["files"]},
    }]))
    (tmp_path / "executor.py").write_text(
        "MAX_OUTPUT_CHARS=4000\n"
        "def run_tool(action,cwd=None,timeout=120):\n"
        " if action.get('tool') == 'batch_read' and action.get('files'):\n"
        "  return {'output':'X'*4000,'returncode':0,'exception_info':'','next_offset':4000}\n"
        " return {'output':'missing','returncode':1,'exception_info':'missing'}\n"
    )
    warnings = validate_registry(tmp_path)
    assert any("did not advance" in warning for warning in warnings)


def test_registry_validator_reports_missing_name_without_crashing(tmp_path: Path):
    (tmp_path / "tools.json").write_text(json.dumps([{
        "tool_name": "batch_read", "parameters": {"files": {"type": "array"}}
    }]))
    (tmp_path / "executor.py").write_text(
        "MAX_OUTPUT_CHARS=4000\n"
        "def run_tool(action,cwd=None,timeout=120):\n"
        " return {'output':'','returncode':1,'exception_info':'bad'}\n"
    )
    warnings = validate_registry(tmp_path, cards=[{"candidate_ids": ["cand-read"]}])
    assert any("non-empty" in warning for warning in warnings)
    assert any("candidate cards missing" in warning for warning in warnings)


def test_registry_validator_allows_free_tool_names_with_exact_candidate_attribution(tmp_path: Path):
    (tmp_path / "tools.json").write_text(json.dumps([{
        "name": "inspect_related_definitions",
        "description": "Inspect related definitions [VCGC:cand-read]",
        "parameters": {
            "type": "object",
            "properties": {"file": {"type": "string"}},
            "required": ["file"],
        },
    }]))
    (tmp_path / "executor.py").write_text(
        "MAX_OUTPUT_CHARS=4000\n"
        "def run_tool(action,cwd=None,timeout=120):\n"
        " if action.get('tool') == 'inspect_related_definitions':\n"
        "  if not action.get('file'):\n"
        "   return {'output':'missing','returncode':1,'exception_info':'missing'}\n"
        "  return {'output':'ok','returncode':0,'exception_info':''}\n"
        " return {'output':'unknown','returncode':1,'exception_info':'unknown'}\n"
    )
    warnings = validate_registry(tmp_path, cards=[{"candidate_ids": ["cand-read"]}])
    assert not any("tool names must exactly match" in warning for warning in warnings)
    assert not any("candidate cards" in warning for warning in warnings)
