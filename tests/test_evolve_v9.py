from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.evolve.evolve_v9 import (
    ActionNormalizerV9,
    BenchmarkMetricAdapter,
    ContrastiveEvidenceBuilderV9,
    EvolveV9Experiment,
    InstructionCandidateBuilderV9,
    PatternMinerV9,
    PriceModel,
    RelaxedPromotionGateV9,
    TaskMetrics,
    V9PromptBuilder,
    _message_usage_totals,
    batch_candidate_cards,
    prepare_evidence_prompt,
    seed_v9,
    validate_registry_v9,
)


def _step(command: str, *, returncode: int = 0, output: str = "ok", calls: int = 1) -> dict:
    tool_calls = [
        {
            "tool_call_id": f"call-{index}",
            "function_name": "bash",
            "arguments": {"command": command},
        }
        for index in range(calls)
    ]
    results = [
        {"content": json.dumps({"returncode": returncode, "output": output})}
        for _ in range(calls)
    ]
    return {
        "tool_calls": tool_calls,
        "observation": {"results": results},
        "metrics": {"prompt_tokens": 100, "cached_tokens": 50, "completion_tokens": 10},
    }


def _sample(tmp_path: Path, task: str, *, minimal=(0, 3, 4), reward=1,
            first_calls: int = 1) -> Path:
    trial = tmp_path / f"{task}__trial"
    agent = trial / "agent"
    agent.mkdir(parents=True)
    steps = [
        _step("rg needle src", calls=first_calls),
        _step("sed -n '1,80p' src/a.py"),
        _step("apply_patch < patch.diff"),
        _step("python -m pytest tests/test_a.py"),
    ]
    trajectory = {
        "steps": steps,
        "dependencies": {"0": [], "1": [0], "2": [1], "3": [1, 2], "4": [3]},
    }
    sample = {
        "negative_sample": trajectory,
        "positive_sample": {**trajectory, "minimal_step_indices": list(minimal),
                            "steps": [steps[index - 1] for index in minimal if index]},
    }
    path = agent / "contrastive_sample.json"
    path.write_text(json.dumps(sample), encoding="utf-8")
    (trial / "result.json").write_text(json.dumps({
        "verifier_result": {"rewards": {"reward": reward}}
    }), encoding="utf-8")
    return path


def _metrics(task: str, *, success: bool, cost: float, error: str | None = None) -> TaskMetrics:
    return TaskMetrics(
        task_id=task, success=success, primary_score=float(success), api_cost=cost,
        new_input_tokens=1, cached_input_tokens=0, output_tokens=0, llm_calls=1,
        native_tool_calls=0, native_tool_failures=0, error=error,
    )


def test_benchmark_cost_ignores_interface_cost_usd(tmp_path):
    trial = tmp_path / "case-one__trial"
    (trial / "agent").mkdir(parents=True)
    (trial / "agent" / "trajectory.json").write_text(
        json.dumps({"steps": [_step("rg needle src")]}), encoding="utf-8"
    )
    (trial / "result.json").write_text(json.dumps({
        "agent_result": {
            "n_input_tokens": 1000,
            "n_cache_tokens": 250,
            "n_output_tokens": 100,
            "cost_usd": 0,
        },
        "verifier_result": {"rewards": {"resolved": 1}},
    }), encoding="utf-8")
    prices = PriceModel(uncached_input=2.0, cached_input=0.5, completion=3.0)
    metrics = BenchmarkMetricAdapter("swebench", prices).extract_case(
        trial / "agent" / "trajectory.json", "case-one"
    )
    assert metrics.api_cost == pytest.approx((750 * 2 + 250 * 0.5 + 100 * 3) / 1_000_000)
    assert metrics.cost_source == "token_usage_x_configured_price"


def test_compiler_message_cost_usage_is_aggregated_without_instance_cost():
    trajectory = {
        "info": {"model_stats": {"instance_cost": 0, "api_calls": 99}},
        "messages": [
            {"role": "assistant", "extra": {"response": {"usage": {
                "prompt_tokens": 100, "completion_tokens": 20,
                "prompt_tokens_details": {"cached_tokens": 40},
            }}}},
            {"role": "assistant", "extra": {"response": {"usage": {
                "prompt_tokens": 200, "completion_tokens": 30,
                "prompt_cache_hit_tokens": 150,
            }}}},
        ],
    }
    assert _message_usage_totals(trajectory) == {
        "prompt_tokens": 300,
        "cached_tokens": 190,
        "completion_tokens": 50,
        "api_calls": 2,
    }


def test_open_ended_normalizer_retains_unknown_commands_and_ignores_redirection():
    normalizer = ActionNormalizerV9()
    value = normalizer.normalize_call({
        "function_name": "bash",
        "arguments": {"command": "cd /repo && novel-analyzer --fast 2>&1 | head -20"},
    })
    assert value["operations"] == ["command:novel-analyzer", "read"]
    assert "command:1" not in value["operations"]
    test_call = normalizer.normalize_call({
        "function_name": "bash", "arguments": {"command": "python -m pytest tests/test_x.py"}
    })
    assert test_call["operations"] == ["verify"]
    heredoc = normalizer.normalize_call({
        "function_name": "bash",
        "arguments": {"command": "python - <<'PY'\nconst x = 1; print(x)\nPY"},
    })
    assert heredoc["operations"] == ["interpreter"]


def test_tool_call_level_future_depth_is_not_double_counted(tmp_path):
    path = _sample(tmp_path, "parallel", first_calls=2)
    evidence = ContrastiveEvidenceBuilderV9().build_from_samples([path])
    first = [node for node in evidence["nodes"] if node["step_id"] == 1]
    assert len(first) == 2
    assert {node["future_llm_calls"] for node in first} == {3}
    assert {node["dependency_state"] for node in first} == {"low_criticality"}


def test_degenerate_minimal_sample_is_retained_for_cost_mining(tmp_path):
    path = _sample(tmp_path, "degenerate", minimal=(0, 4))
    evidence = ContrastiveEvidenceBuilderV9().build_from_samples([path])
    row = evidence["tasks"][0]
    assert row["usable_for_tool_patterns"]
    assert not row["rejection_reasons"]
    assert any("degenerate minimal path" in reason for reason in row["diagnostic_issues"])
    assert {node["dependency_state"] for node in evidence["nodes"]} == {
        "critical", "low_criticality",
    }


def test_failed_task_is_retained_for_cost_mining(tmp_path):
    path = _sample(tmp_path, "failed-but-observable", reward=0)
    evidence = ContrastiveEvidenceBuilderV9().build_from_samples([path])
    row = evidence["tasks"][0]
    assert not row["task_success"]
    assert row["usable_for_tool_patterns"]
    assert row["quality_filter_mode"] == "cost_only_no_outcome_gate"
    assert not row["rejection_reasons"]
    assert any("external task did not pass" in reason for reason in row["diagnostic_issues"])
    assert all(node["dependency_state"] != "uncertain" for node in evidence["nodes"])


def test_failure_only_pattern_is_labeled_as_waste_signal(tmp_path):
    paths = [_sample(tmp_path, "failed-one", reward=0),
             _sample(tmp_path, "failed-two", reward=0)]
    evidence = ContrastiveEvidenceBuilderV9().build_from_samples(paths)
    _, cards = PatternMinerV9(min_support=2).mine(evidence)
    assert cards
    assert all(card["evidence_role"] == "failure_only_waste_signal" for card in cards)
    assert all(card["outcome_support"] == {"passed": 0, "failed": 2} for card in cards)


def test_support_counts_unique_tasks_and_produces_attributed_cards(tmp_path):
    paths = [_sample(tmp_path, "one"), _sample(tmp_path, "two")]
    evidence = ContrastiveEvidenceBuilderV9().build_from_samples(paths)
    pool, cards = PatternMinerV9(min_support=2, max_cards=15).mine(evidence)
    assert pool
    assert cards
    assert all(card["support"] == 2 for card in cards)
    assert all(len(card["support_tasks"]) == len(set(card["support_tasks"])) for card in cards)
    covered = [node for card in cards for occurrence in card["occurrences"] for node in occurrence["node_ids"]]
    # Selected-card benefit uses a node union. The first selected card consumes
    # an overlap so no later selected card may claim exactly the same nodes.
    assert len(covered) == len(set(covered))


def test_instruction_cards_are_cross_task_hypotheses(tmp_path):
    paths = [_sample(tmp_path, "one"), _sample(tmp_path, "two")]
    evidence = ContrastiveEvidenceBuilderV9().build_from_samples(paths)
    cards = InstructionCandidateBuilderV9(min_support=2).build(evidence)
    assert cards
    assert all(card["support"] >= 2 for card in cards)
    assert all(card["status"] == "hypothesis_requires_paired_canary" for card in cards)


def test_prompt_has_no_fixed_tool_category_contract(tmp_path):
    scripts = tmp_path / "scripts"
    seed_v9(scripts)
    prompt = V9PromptBuilder().build(scripts, [], [], cycle=1)
    assert "NO fixed list of allowed tool categories or tool names" in prompt
    assert "change_manifest.json" in prompt
    assert 'rejected_cards only as a list of objects shaped {"candidate_ids": ["p-..."]' in prompt
    assert "manifest must retain attribution accumulated by earlier batches" in prompt
    assert "narrowing the path, query, range, or operation" in prompt


def test_candidate_cards_are_paired_in_bounded_serial_batches():
    patterns = [{"candidate_id": f"p-{index}"} for index in range(5)]
    instructions = [{"candidate_id": f"i-{index}"} for index in range(3)]
    batches = batch_candidate_cards(patterns, instructions, 2)
    assert len(batches) == 3
    assert [len(batch["pattern_cards"]) for batch in batches] == [2, 2, 1]
    assert [len(batch["instruction_cards"]) for batch in batches] == [2, 1, 0]
    assert all(len(batch["pattern_cards"]) <= 2 for batch in batches)
    assert all(len(batch["instruction_cards"]) <= 2 for batch in batches)


def test_offline_prepare_writes_complete_review_artifacts(tmp_path):
    paths = [_sample(tmp_path / "samples", "one"), _sample(tmp_path / "samples", "two")]
    scripts = tmp_path / "scripts"
    seed_v9(scripts)
    work = tmp_path / "review"
    manifest = prepare_evidence_prompt(
        sample_paths=paths, current_scripts=scripts, work_dir=work,
        prices=PriceModel(), min_support=2,
    )
    assert not manifest["llm_invoked"]
    for name in (
        "combined_compile_prompt.md", "repair_prompt_template.md", "prompt_manifest.json",
        "quality_report.json", "rejected_sample_report.json", "tool_call_evidence.json",
        "cost_attribution.json", "pattern_occurrences.json", "pattern_candidate_pool.json",
        "pattern_cards.json", "instruction_candidate_cards.json",
    ):
        assert (work / name).exists(), name
    batch_manifest = json.loads((work / "batch_manifest.json").read_text())
    assert batch_manifest["evolve_batch_size"] == 2
    assert batch_manifest["batch_count"] >= 1
    assert all(Path(row["prompt_path"]).exists() for row in batch_manifest["batches"])


def test_relaxed_gate_allows_declared_stochastic_regression_with_cost_saving():
    tasks = [f"t{i}" for i in range(16)]
    parent = {task: _metrics(task, success=i < 10, cost=1.0) for i, task in enumerate(tasks)}
    candidate = {task: _metrics(task, success=(i < 10 and i != 0) or i == 10, cost=0.9)
                 for i, task in enumerate(tasks)}
    gate = RelaxedPromotionGateV9().evaluate(parent, candidate, tasks)
    assert gate["promote"]
    assert gate["success_regressions"] == ["t0"]
    assert gate["cost_saving_ratio"] == pytest.approx(0.1)


def test_gate_counts_missing_candidate_as_error_and_failure():
    tasks = [f"t{i}" for i in range(16)]
    parent = {task: _metrics(task, success=True, cost=1.0) for task in tasks}
    candidate = {task: _metrics(task, success=True, cost=0.8) for task in tasks[2:]}
    gate = RelaxedPromotionGateV9(max_candidate_error_rate=0.10).evaluate(parent, candidate, tasks)
    assert not gate["promote"]
    assert gate["candidate_errors"] == ["t0", "t1"]
    assert gate["candidate_error_rate"] == pytest.approx(2 / 16)


def test_registry_validator_requires_manifest(tmp_path):
    scripts = tmp_path / "scripts"
    seed_v9(scripts)
    warnings = validate_registry_v9(scripts, require_manifest=True)
    assert "change_manifest.json is required and must be an object" in warnings


def test_registry_validator_requires_cumulative_batch_attribution(tmp_path):
    scripts = tmp_path / "scripts"
    seed_v9(scripts)
    (scripts / "change_manifest.json").write_text(json.dumps({
        "rejected_cards": [{"candidate_ids": ["p-one"], "reason": "unsafe"}],
    }), encoding="utf-8")
    warnings = validate_registry_v9(
        scripts, pattern_cards=[{"candidate_id": "p-one"}, {"candidate_id": "p-two"}],
        require_manifest=True,
    )
    assert "change manifest does not account for candidate ids: ['p-two']" in warnings


def test_registry_validator_accepts_legacy_bare_rejected_card_ids(tmp_path):
    scripts = tmp_path / "scripts"
    seed_v9(scripts)
    (scripts / "change_manifest.json").write_text(json.dumps({
        "rejected_cards": ["p-one", "i-one"],
    }), encoding="utf-8")
    warnings = validate_registry_v9(
        scripts,
        pattern_cards=[{"candidate_id": "p-one"}],
        instruction_cards=[{"candidate_id": "i-one"}],
        require_manifest=True,
    )
    assert warnings == []


def test_registry_validator_runs_isolated_bounded_smoke(tmp_path):
    scripts = tmp_path / "scripts"
    seed_v9(scripts)
    (scripts / "tools.json").write_text(json.dumps([{
        "name": "inspect-file",
        "description": "Inspect a bounded file prefix.",
        "parameters": {"type": "object", "properties": {"file": {"type": "string"}},
                       "required": ["file"]},
    }]), encoding="utf-8")
    (scripts / "executor.py").write_text(
        "MAX_OUTPUT_CHARS = 4000\n"
        "def run_tool(action, cwd=None, timeout=120):\n"
        "    if action.get('tool') == 'inspect-file':\n"
        "        file = action.get('file')\n"
        "        if not file:\n"
        "            return {'output':'missing file','returncode':1,'exception_info':'missing file'}\n"
        "        with open(file, encoding='utf-8') as stream:\n"
        "            output = stream.read(MAX_OUTPUT_CHARS)\n"
        "        return {'output':output,'returncode':0,'exception_info':''}\n"
        "    return {'output':'unknown','returncode':1,'exception_info':'unknown'}\n",
        encoding="utf-8",
    )
    (scripts / "change_manifest.json").write_text("{}\n", encoding="utf-8")
    assert validate_registry_v9(scripts, require_manifest=True) == []


def test_experiment_rejects_evolve_eval_overlap(tmp_path):
    evolve = [f"e{i}" for i in range(16)]
    final = ["e0", *[f"f{i}" for i in range(63)]]
    with pytest.raises(ValueError, match="must be disjoint"):
        EvolveV9Experiment(
            benchmark="swebench", config="_config/deepseekv4_flash.yaml",
            active_dir=tmp_path / "active", work_dir=tmp_path / "work",
            evolve_case_ids=evolve, final_eval_case_ids=final,
        )


def test_annotation_copy_uses_exact_locked_evolve_set(tmp_path):
    evolve = [f"e{i}" for i in range(16)]
    experiment = EvolveV9Experiment(
        benchmark="swebench", config="_config/deepseekv4_flash.yaml",
        active_dir=tmp_path / "active", work_dir=tmp_path / "work",
        evolve_case_ids=evolve, final_eval_case_ids=[f"f{i}" for i in range(64)],
    )
    source = tmp_path / "source"
    for task_id in [*evolve, "unrelated-case"]:
        agent = source / f"{task_id}__trial" / "agent"
        agent.mkdir(parents=True)
        (agent / "trajectory.json").write_text(
            json.dumps({"steps": [_step("pwd")]}), encoding="utf-8"
        )
    destination, copied_ids = experiment._annotation_copy(
        source, tmp_path / "evidence", evolve
    )
    assert copied_ids == sorted(evolve)
    assert len(list(destination.glob("*/agent/trajectory.json"))) == 16
    assert not (destination / "unrelated-case__trial").exists()
