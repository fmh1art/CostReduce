from collections import Counter
import json
import os
import re
import subprocess
import threading
from pathlib import Path

import pytest

from src.evolve.coat import (
    DAGContrastiveSampleBuilderV61,
    EvolveV61Cycle,
    EvolvePromptBuilderV61,
    LLMJudgeGateV61,
    MiniSweAgentRunnerV61,
    PromptBudgetExceededV61,
    RolloutAgentV61,
    ScriptEvolverV61,
    TrajectoryAnnotatorV61,
    _max_completion_tokens,
    _run_has_forbidden_oracle_action,
)


def _step(
    name,
    *,
    dependencies=None,
    op_type="explore",
    op_state="success",
    output="ok",
):
    return {
        "message": "",
        "tool_calls": [{
            "tool_call_id": f"call-{name}",
            "function_name": "bash",
            "arguments": {"command": f"echo {name}"},
        }],
        "observation": {"results": [{"content": json.dumps({
            "returncode": 0 if op_state == "success" else 1,
            "output": output,
            "exception_info": "" if op_state == "success" else "failed",
        })}]},
        "step_meta": {"op_type": op_type, "op_state": op_state},
        "_test_dependencies": dependencies,
    }


def test_coat_reads_nested_default_max_completion_tokens(monkeypatch):
    monkeypatch.delenv("MSWEA_MAXTOK_CONFIG", raising=False)
    assert _max_completion_tokens() == 16384


def _trajectory(steps):
    deps = {"0": []}
    clean_steps = [{"source": "user", "message": "Solve the focused DAG test task."}]
    for index, step in enumerate(steps, start=1):
        step = dict(step)
        dependencies = step.pop("_test_dependencies")
        deps[str(index)] = dependencies if dependencies is not None else [0]
        clean_steps.append(step)
    return {"steps": clean_steps, "dependencies": deps}


def _write_trajectory(tmp_path: Path, trajectory: dict) -> Path:
    agent_dir = tmp_path / "case__one" / "agent"
    agent_dir.mkdir(parents=True)
    path = agent_dir / "trajectory.json"
    path.write_text(json.dumps(trajectory), encoding="utf-8")
    return path


class _RecordingLLM:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = []

    def query(self, system_prompt, history, user_prompt):
        self.calls.append((system_prompt, history, user_prompt))
        return next(self.replies)


class _DeterministicAnnotationLLM:
    """Thread-safe fake whose response depends only on the canonical prompt."""

    def __init__(self, fail_once_for=()):
        self.fail_once_for = set(fail_once_for)
        self.failed = set()
        self.calls = []
        self.lock = threading.Lock()

    def query(self, system_prompt, history, user_prompt):
        step_match = re.search(r"Current step (\d+):", user_prompt)
        name_match = re.search(r"echo ([A-Za-z0-9_-]+)", user_prompt)
        assert step_match and name_match
        index = int(step_match.group(1))
        name = name_match.group(1)
        with self.lock:
            self.calls.append((system_prompt, history, user_prompt))
            if name in self.fail_once_for and name not in self.failed:
                self.failed.add(name)
                raise RuntimeError(f"one-shot failure for {name}")
        dependencies = [0] if index == 1 else [0, index - 1]
        return json.dumps({
            "dependencies": dependencies,
            "op_type": "verify" if "verify" in name else "read",
            "op_state": "fail" if "fail" in name else "success",
        })


def _unannotated_trajectory(names):
    trajectory = _trajectory([_step(name) for name in names])
    trajectory.pop("dependencies", None)
    for step in trajectory["steps"]:
        step.pop("step_meta", None)
    return trajectory


def test_v61_annotation_is_prefix_only_and_writes_op_state(tmp_path):
    path = _write_trajectory(tmp_path, _trajectory([
        _step("unique-one"),
        _step("unique-two"),
        _step("unique-three"),
    ]))
    llm = _RecordingLLM([
        '{"dependencies":[0],"op_type":"explore","op_state":"success"}',
        '{"dependencies":[0,1],"op_type":"explore","op_state":"success"}',
        '{"dependencies":[0,2],"op_type":"explore","op_state":"fail"}',
    ])

    TrajectoryAnnotatorV61("unused.yaml").annotate_file(path, llm=llm)

    assert "unique-one" not in llm.calls[0][1]
    assert "unique-one" in llm.calls[1][1]
    assert "unique-two" not in llm.calls[1][1]
    assert "unique-one" in llm.calls[2][1]
    assert "unique-two" in llm.calls[2][1]
    assert "unique-three" not in llm.calls[2][1]
    data = json.loads(path.read_text(encoding="utf-8"))
    action_steps = TrajectoryAnnotatorV61._extract_action_steps(data)
    assert [step["step_meta"]["op_state"] for step in action_steps] == [
        "success", "success", "fail"
    ]
    assert TrajectoryAnnotatorV61.is_annotated(path)


def test_v61_exact_global_is_prompt_and_output_equivalent_to_serial(tmp_path):
    names = ["read-one", "read-two", "verify-three", "fail-four"]
    serial_path = _write_trajectory(
        tmp_path / "serial", _unannotated_trajectory(names)
    )
    global_path = _write_trajectory(
        tmp_path / "global", _unannotated_trajectory(names)
    )
    serial_llm = _DeterministicAnnotationLLM()
    global_llm = _DeterministicAnnotationLLM()

    TrajectoryAnnotatorV61("unused.yaml").annotate_file(
        serial_path, llm=serial_llm
    )
    annotator = TrajectoryAnnotatorV61(
        "unused.yaml",
        workers=3,
        execution_mode="exact-global",
        checkpoint=True,
        llm_factory=lambda: global_llm,
    )
    annotator.annotate_dir(tmp_path / "global")

    assert json.loads(global_path.read_text()) == json.loads(serial_path.read_text())
    assert Counter(global_llm.calls) == Counter(serial_llm.calls)
    assert annotator.last_run_metrics["llm_calls"] == len(names)
    assert annotator.last_run_metrics["unique_action_steps"] == len(names)
    assert not global_path.with_name("annotation_v61.checkpoint.jsonl").exists()


def test_v61_exact_global_checkpoint_resumes_only_missing_step(tmp_path):
    names = ["read-one", "read-two", "fail-once", "verify-four"]
    path = _write_trajectory(tmp_path, _unannotated_trajectory(names))
    llm = _DeterministicAnnotationLLM(fail_once_for={"fail-once"})
    first = TrajectoryAnnotatorV61(
        "unused.yaml",
        workers=4,
        retry_failed=0,
        execution_mode="exact-global",
        checkpoint=True,
        llm_factory=lambda: llm,
    )

    with pytest.raises(RuntimeError, match="failed to annotate 1 trajectory"):
        first.annotate_dir(tmp_path)

    checkpoint = path.with_name("annotation_v61.checkpoint.jsonl")
    assert checkpoint.exists()
    assert len(checkpoint.read_text().splitlines()) == len(names) - 1
    calls_after_failure = len(llm.calls)

    resumed = TrajectoryAnnotatorV61(
        "unused.yaml",
        workers=4,
        retry_failed=0,
        execution_mode="exact-global",
        checkpoint=True,
        llm_factory=lambda: llm,
    )
    resumed.annotate_dir(tmp_path)

    assert len(llm.calls) == calls_after_failure + 1
    assert resumed.last_run_metrics["checkpoint_hits"] == len(names) - 1
    assert resumed.last_run_metrics["llm_calls"] == 1
    assert TrajectoryAnnotatorV61.is_annotated(path)
    assert not checkpoint.exists()


def test_v61_prefers_diverse_focused_dag_signals(tmp_path):
    trajectory = _trajectory([
        _step("read-a", dependencies=[0], op_type="read"),
        _step("read-b", dependencies=[0], op_type="read"),
        _step("failed-a", dependencies=[0], op_state="fail"),
        _step("failed-b", dependencies=[0], op_state="fail"),
        _step("pivot", dependencies=[0]),
        _step("waste-a", dependencies=[0]),
        _step("waste-b", dependencies=[0]),
        _step("final", dependencies=[0, 1, 2, 5], op_type="read"),
    ])
    path = _write_trajectory(tmp_path, trajectory)

    outputs = DAGContrastiveSampleBuilderV61().build_file(path)
    samples = [json.loads(output.read_text(encoding="utf-8")) for output in outputs]

    assert len(samples) == 3
    assert {sample["type"] for sample in samples} == {
        "v61_mergeable", "v61_failure_pivot", "v61_skippable"
    }
    assert {sample["optimization_target"] for sample in samples} >= {
        "tools", "instruction"
    }
    merge = next(sample for sample in samples if sample["type"] == "v61_mergeable")
    merged_steps = merge["positive_sample"]["steps"]
    merged = next(step for step in merged_steps if step.get("merged_from_step_indices"))
    assert merged["merged_from_step_indices"] == [1, 2]
    assert len(merged["tool_calls"]) == 2
    assert all(len(sample["negative_sample"]["steps"]) <= 8 for sample in samples)
    # Focused construction must not mutate the source trajectory.
    source = json.loads(path.read_text(encoding="utf-8"))
    assert "merged_from_step_indices" not in source["steps"][1]


def test_v61_uses_bounded_phase_fallback_when_no_signal_exists(tmp_path):
    steps = []
    for index in range(1, 26):
        steps.append(_step(
            f"chain-{index}",
            dependencies=[0] if index == 1 else [0, index - 1],
            op_type="read" if index <= 13 else "verify",
        ))
    path = _write_trajectory(tmp_path, _trajectory(steps))

    outputs = DAGContrastiveSampleBuilderV61().build_file(path)
    samples = [json.loads(output.read_text(encoding="utf-8")) for output in outputs]

    assert 1 <= len(samples) <= 3
    assert {sample["type"] for sample in samples} == {"v61_phase_fallback"}
    assert all(
        len(sample["signal"]["target_step_indices"])
        <= DAGContrastiveSampleBuilderV61.MAX_PHASE_STEPS
        for sample in samples
    )


def test_v61_prompt_keeps_markdown_structure_and_expands_goal(tmp_path):
    path = _write_trajectory(tmp_path / "results", _trajectory([
        _step("read-a", dependencies=[0], op_type="read"),
        _step("read-b", dependencies=[0], op_type="read"),
        _step("final", dependencies=[0, 1, 2], op_type="read"),
    ]))
    samples = DAGContrastiveSampleBuilderV61().build_file(path)
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "tools.json").write_text("[]\n", encoding="utf-8")
    (scripts_dir / "executor.py").write_text(
        "def run_tool(action, cwd=None, timeout=120):\n    return {}\n",
        encoding="utf-8",
    )
    (scripts_dir / "instruction.md").write_text("# Rules\n", encoding="utf-8")

    prompt = EvolvePromptBuilderV61(max_prompt_chars=50000).build(
        samples[:1], scripts_dir=scripts_dir
    )

    assert "# Evolve task (v6.1" in prompt
    assert "# Executional History 1" in prompt
    assert "## Original Trajectory" in prompt
    assert "## Minimal Trajectory" in prompt
    assert "COMPLETE DOWNSTREAM HARNESS" in prompt
    assert "improving instruction.md" in prompt
    assert "Optimization target: tools" in prompt
    assert len(prompt) <= 50000


def test_v61_prompt_renders_positive_as_delta_without_repeating_kept_steps(tmp_path):
    path = _write_trajectory(tmp_path / "results", _trajectory([
        _step("read-a", dependencies=[0], op_type="read"),
        _step("read-b", dependencies=[0], op_type="read"),
        _step("final", dependencies=[0, 1, 2], op_type="read"),
    ]))
    sample = DAGContrastiveSampleBuilderV61().build_file(path)[0]
    prompt = EvolvePromptBuilderV61(max_prompt_chars=50000).build([sample])

    assert "## Minimal Trajectory Delta" in prompt
    assert "Kept unchanged:" in prompt
    assert "merges original steps" in prompt
    # Each action appears in Original Trajectory once; unchanged/merged copies
    # are represented by indices rather than serialized a second time.
    assert prompt.count("echo read-a") == 1
    assert prompt.count("echo read-b") == 1
    assert prompt.count("echo final") == 1


def test_v61_direct_runner_passes_exact_prompt_as_user_task(tmp_path, monkeypatch):
    runner = MiniSweAgentRunnerV61(
        mini_swe_agent_dir=tmp_path,
        llm_config=tmp_path / "unused.yaml",
    )
    monkeypatch.setattr(
        runner,
        "_load_llm_env",
        lambda: ({}, "test-model", 0.0, "test-model-class"),
    )
    monkeypatch.setattr(runner, "_load_thinking", lambda: None)
    captured = {}
    monkeypatch.setattr(
        runner,
        "_run_mini_swe",
        lambda cmd, cwd, env: captured.update(cmd=cmd, cwd=cwd, env=env),
    )
    prompt = "# exact evolve instruction\nDo the gated v6.1 edit."
    prompt_path = tmp_path / "logs" / "batch.prompt.md"

    runner.run(prompt, prompt_path, tmp_path / "logs" / "batch.json", tmp_path)

    cmd = captured["cmd"]
    assert cmd[cmd.index("-t") + 1] == prompt
    assert prompt_path.read_text(encoding="utf-8") == prompt
    assert "Read the full evolution instruction" not in cmd[cmd.index("-t") + 1]


class _NoChangeRunner:
    def __init__(self):
        self.prompts = []

    def run(self, prompt, prompt_path, output_path, cwd):
        self.prompts.append(prompt)


class _InstructionEditRunner:
    def __init__(self, line):
        self.line = line

    def run(self, prompt, prompt_path, output_path, cwd):
        path = Path(cwd) / "instruction.md"
        path.write_text(path.read_text(encoding="utf-8") + self.line + "\n", encoding="utf-8")


def _focused_sample(case_dir: Path, suffix: str, command: str = "echo focused") -> Path:
    agent = case_dir / "agent"
    agent.mkdir(parents=True, exist_ok=True)
    step = _step(command.replace(" ", "-"), dependencies=[0], op_type="read")
    step["tool_calls"][0]["arguments"]["command"] = command
    step["_display_index"] = 1
    sample = {
        "type": "v61_skippable",
        "optimization_target": "instruction",
        "evidence_status": "dependency_validated",
        "task_description": "A reusable focused task.",
        "signal": {"target_step_indices": [1], "operator_family": "read"},
        "negative_sample": {"steps": [step]},
        "positive_sample": {"steps": []},
    }
    path = agent / f"contrastive_v61_{suffix}_skippable.json"
    path.write_text(json.dumps(sample), encoding="utf-8")
    return path


def test_v61_budget_split_defers_every_sample_instead_of_skipping(tmp_path):
    result_dir = tmp_path / "result"
    first = _focused_sample(result_dir / "case-a", "01", "echo " + "a" * 1600)
    second = _focused_sample(result_dir / "case-b", "01", "echo " + "b" * 1600)
    scripts = tmp_path / "scripts"
    logs = tmp_path / "logs"
    from src.evolve.native_tools_v6 import seed
    seed(scripts)

    sizing = EvolvePromptBuilderV61(max_prompt_chars=100000)
    one_chars = len(sizing.build([first], scripts_dir=scripts))
    two_chars = len(sizing.build([first, second], scripts_dir=scripts))
    limit = max(10000, one_chars + 100)
    assert two_chars > limit
    with pytest.raises(PromptBudgetExceededV61):
        EvolvePromptBuilderV61(max_prompt_chars=limit).build(
            [first, second], scripts_dir=scripts
        )

    runner = _NoChangeRunner()
    evolver = ScriptEvolverV61(
        scripts_dir=scripts,
        runner=runner,
        prompt_builder=EvolvePromptBuilderV61(max_prompt_chars=limit),
        gate=LLMJudgeGateV61("unused.yaml"),
        batch_size=2,
        output_dir=logs,
    )
    evolver.run(result_dir)

    manifest = json.loads((logs / "evolve_batch_manifest.json").read_text())
    assert manifest["complete"] is True
    assert manifest["processed_samples"] == [str(first), str(second)]
    assert manifest["batch_count"] == 2
    assert len(runner.prompts) == 2


def test_v61_sample_order_round_robins_across_source_cases(tmp_path):
    result_dir = tmp_path / "result"
    paths = [
        _focused_sample(result_dir / "case-a", "01"),
        _focused_sample(result_dir / "case-a", "02"),
        _focused_sample(result_dir / "case-b", "01"),
        _focused_sample(result_dir / "case-b", "02"),
    ]
    evolver = ScriptEvolverV61(
        scripts_dir=tmp_path / "scripts",
        runner=_NoChangeRunner(),
        prompt_builder=EvolvePromptBuilderV61(),
        gate=LLMJudgeGateV61("unused.yaml"),
    )

    ordered = evolver.find_samples(result_dir)

    assert [path.parent.parent.name for path in ordered] == [
        "case-a", "case-b", "case-a", "case-b"
    ]
    assert set(ordered) == set(paths)


def _judge_reply(decision="accept", blocking=None):
    return json.dumps({
        "decision": decision,
        "scores": {
            "generality": 4,
            "cost_reduction": 4,
            "correctness": 4,
            "consistency": 4,
            "evidence_alignment_and_minimality": 4,
        },
        "blocking_issues": blocking or [],
        "non_blocking_notes": [],
        "summary": "candidate is reusable" if decision == "accept" else "case-specific rule",
    })


def test_v61_gate_promotes_only_an_accepted_diff(tmp_path):
    result_dir = tmp_path / "result"
    _focused_sample(result_dir / "case-a", "01")
    scripts = tmp_path / "scripts"
    logs = tmp_path / "logs"
    judge = _RecordingLLM([_judge_reply()])
    evolver = ScriptEvolverV61(
        scripts_dir=scripts,
        runner=_InstructionEditRunner("- Batch independent repository reads together."),
        prompt_builder=EvolvePromptBuilderV61(),
        gate=LLMJudgeGateV61("unused.yaml", llm_factory=lambda: judge),
        output_dir=logs,
    )

    evolver.run(result_dir)

    assert "Batch independent repository reads" in (scripts / "instruction.md").read_text()
    decision = json.loads((logs / "evolve_batch_1.gate" / "decision.json").read_text())
    assert decision["decision"] == "accept"
    assert decision["passed"] is True
    judge_prompt = (logs / "evolve_batch_1.gate" / "judge.prompt.md").read_text()
    assert "generality" in judge_prompt
    assert "net future API cost" in judge_prompt
    assert "Candidate git diff" in judge_prompt


def test_v61_gate_rejects_and_restores_the_exact_previous_harness(tmp_path):
    result_dir = tmp_path / "result"
    _focused_sample(result_dir / "case-a", "01")
    scripts = tmp_path / "scripts"
    logs = tmp_path / "logs"
    # Seed once so the byte-for-byte before state is observable.
    from src.evolve.native_tools_v6 import seed
    seed(scripts)
    before = {name: (scripts / name).read_bytes() for name in (
        "tools.json", "executor.py", "instruction.md"
    )}
    blocking = [{
        "criterion": "generality",
        "evidence": "hard-coded to case-a",
        "required_fix": "require independent reusable evidence",
    }]
    judge = _RecordingLLM([_judge_reply("reject", blocking)])
    evolver = ScriptEvolverV61(
        scripts_dir=scripts,
        runner=_InstructionEditRunner("- Always inspect case-a's private path."),
        prompt_builder=EvolvePromptBuilderV61(),
        gate=LLMJudgeGateV61("unused.yaml", llm_factory=lambda: judge),
        output_dir=logs,
    )

    evolver.run(result_dir)

    assert all((scripts / name).read_bytes() == content for name, content in before.items())
    decision = json.loads((logs / "evolve_batch_1.gate" / "decision.json").read_text())
    assert decision["decision"] == "reject"
    assert decision["passed"] is False
    assert "case-specific rule" in decision["summary"]


def test_v61_discards_oracle_contaminated_focused_sample(tmp_path):
    step = _step("oracle", dependencies=[0], op_type="read")
    step["tool_calls"][0]["arguments"]["command"] = (
        "cat /app/dab/query/ground_truth.csv"
    )
    sample = {
        "type": "v61_hotspot",
        "optimization_target": "tools",
        "evidence_status": "diagnostic",
        "signal": {},
        "negative_sample": {"steps": [step]},
        "positive_sample": {"steps": [step]},
    }
    sample_path = tmp_path / "contrastive_v61_01_hotspot.json"
    sample_path.write_text(json.dumps(sample), encoding="utf-8")

    prompt = EvolvePromptBuilderV61(max_prompt_chars=50000).build([sample_path])

    assert "# Executional History 1" not in prompt
    assert "No focused sample fit" in prompt
    assert "FORBIDDEN oracle evidence" in prompt


def test_v61_detects_oracle_contaminated_dab_run(tmp_path):
    path = _write_trajectory(tmp_path, _trajectory([
        _step("oracle", dependencies=[0], op_type="read"),
    ]))
    data = json.loads(path.read_text(encoding="utf-8"))
    data["steps"][1]["tool_calls"][0]["arguments"]["command"] = (
        "python /app/dab/query/validate.py candidate"
    )
    path.write_text(json.dumps(data), encoding="utf-8")

    assert _run_has_forbidden_oracle_action(tmp_path)


def test_v61_failed_empty_oracle_probe_does_not_contaminate_run(tmp_path):
    step = _step(
        "failed-oracle-probe",
        dependencies=[0],
        op_type="read",
        op_state="fail",
        output="",
    )
    step["tool_calls"][0]["arguments"]["command"] = (
        'grep -r "answer\\|ground_truth\\|expected" query/ query_dataset/'
    )
    path = _write_trajectory(tmp_path, _trajectory([step]))

    assert not _run_has_forbidden_oracle_action(tmp_path)

    data = json.loads(path.read_text(encoding="utf-8"))
    sample = {
        "type": "v61_hotspot",
        "optimization_target": "instruction",
        "evidence_status": "diagnostic",
        "signal": {},
        "negative_sample": {"steps": [data["steps"][1]]},
        "positive_sample": {"steps": [data["steps"][1]]},
    }
    sample_path = tmp_path / "contrastive_v61_failed_oracle_probe.json"
    sample_path.write_text(json.dumps(sample), encoding="utf-8")

    prompt = EvolvePromptBuilderV61(max_prompt_chars=50000).build([sample_path])
    assert "# Executional History 1" not in prompt
    assert "No focused sample fit" in prompt


def test_v61_does_not_reject_normal_repository_tests(tmp_path):
    path = _write_trajectory(tmp_path, _trajectory([
        _step("test", dependencies=[0], op_type="execute"),
    ]))
    data = json.loads(path.read_text(encoding="utf-8"))
    data["steps"][1]["tool_calls"][0]["arguments"]["command"] = (
        "python /repo/tests/validate.py"
    )
    path.write_text(json.dumps(data), encoding="utf-8")

    assert not _run_has_forbidden_oracle_action(tmp_path)


def test_v61_dab_rollout_uses_original_600_second_limit(tmp_path, monkeypatch):
    monkeypatch.delenv("EVOLVE_TOOLS_V6_TIMEOUT_SECONDS", raising=False)
    agent = RolloutAgentV61("dab", "unused.yaml", taskdir_root=tmp_path / "taskdirs")
    monkeypatch.setattr(agent, "_build_temp_task_dir", lambda *_args: None)

    env = agent._build_env(tmp_path / "scripts", [], "test-run")

    assert env["EVOLVE_TOOLS_V6_TIMEOUT_SECONDS"] == "600"


def test_v61_cycle_materializes_results_layout_without_mutating_prep(
    tmp_path, monkeypatch
):
    baseline = tmp_path / "results" / "prep" / "runs" / "swebench-verified" / "prep-run"
    source_path = _write_trajectory(
        baseline,
        _trajectory([
            _step("read-one", dependencies=[0], op_type="read"),
            _step("verify-two", dependencies=[0, 1], op_type="verify"),
        ]),
    )
    source_before = source_path.read_bytes()
    cases_file = tmp_path / "cases.txt"
    cases_file.write_text("case__one\n", encoding="utf-8")
    work_dir = tmp_path / "results" / "evolve" / "coat" / "swebench" / "run"
    scripts_dir = work_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "tools.json").write_text("[]\n", encoding="utf-8")
    (scripts_dir / "executor.py").write_text(
        "def run_tool(action, cwd=None, timeout=120):\n    return {}\n",
        encoding="utf-8",
    )
    (scripts_dir / "instruction.md").write_text("# Rules\n", encoding="utf-8")

    cycle = EvolveV61Cycle(
        "swebench",
        "unused.yaml",
        scripts_dir,
        eval_cases_file=str(cases_file),
        baseline_dir=str(baseline),
        work_dir=str(work_dir),
        n_cycles=1,
        dry_run=True,
    )

    def fake_annotate(run_dir, _task, metrics_path):
        copied = next(Path(run_dir).glob("**/agent/trajectory.json"))
        data = json.loads(copied.read_text(encoding="utf-8"))
        data["copy_was_annotated"] = True
        copied.write_text(json.dumps(data), encoding="utf-8")
        Path(metrics_path).write_text('{"all_trajectories_complete": true}\n')

    def fake_contrastive(run_dir):
        trajectory = next(Path(run_dir).glob("**/agent/trajectory.json"))
        output = trajectory.with_name("contrastive_v61_01_phase_fallback.json")
        output.write_text("{}\n", encoding="utf-8")
        return [output]

    def fake_evolve(_run_dir, _task, output_dir):
        Path(output_dir).mkdir(parents=True)
        (Path(output_dir) / "evolve_batch_1.traj.done").write_text("{}\n")
        return Path(output_dir)

    monkeypatch.setattr(cycle.evolve_agent, "annotate", fake_annotate)
    monkeypatch.setattr(cycle.evolve_agent, "contrastive", fake_contrastive)
    monkeypatch.setattr(cycle.evolve_agent, "evolve", fake_evolve)

    cycle.run()

    cycle_dir = work_dir / "cycle-1"
    assert source_path.read_bytes() == source_before
    assert (cycle_dir / "rollout").is_dir()
    assert (cycle_dir / "annotation_v61_metrics.json").is_file()
    assert (cycle_dir / "evolve_logs").is_dir()
    assert (cycle_dir / "harness_after" / "tools.json").is_file()
    assert (cycle_dir / "cycle_state.json").is_file()
    assert json.loads((work_dir / "output_layout.json").read_text())["valid"] is True


def test_shell_entry_maps_to_coat_module():
    root = Path(__file__).resolve().parents[1]
    script = (root / "scripts" / "run_evolve_experiment.sh").read_text(encoding="utf-8")
    assert 'EVOLVE_MOD="coat"' in script
    assert 'VERSION_TAG="coat"' in script
    assert 'python -m "src.evolve.${EVOLVE_MOD}" run' in script
    assert 'EVOLVE_FRAMEWORK" == "coat"' in script
    assert '--judge-config "$LLM_CONFIG"' in script
    assert 'EVAL_CASE_TAG="all"' in script
    assert "evolve${EVOLVE_CASE_COUNT}_eval${EVAL_CASE_TAG}" in script
    assert '-name "evolve-*-${BENCHMARK}-*"' not in script
    assert 'root.glob(f"evolve-coat-{benchmark}-*")' in script
    assert '"final_eval_exit_code": int(final_eval_rc)' in script
    assert '"complete": (' in script
    assert 'final eval 失败（rc=$FINAL_EVAL_RC；manifest 已写入）' in script
    assert 'evolve final eval 不完整' in script


def test_run_exp_supports_paired_no_evolve_after_main_run():
    root = Path(__file__).resolve().parents[1]
    entry = (root / "scripts" / "run_exp.sh").read_text(encoding="utf-8")
    waiter = (root / "scripts" / "wait_then_run_no_evolve.sh").read_text(
        encoding="utf-8"
    )
    assert 'RUN_NO_EVOLVE_AFTER="${RUN_NO_EVOLVE_AFTER:-0}"' in entry
    assert 'PHASE=no_evolve RUN_NO_EVOLVE_AFTER=0' in entry
    assert 'exec env PHASE=no_evolve RUN_NO_EVOLVE_AFTER=0' in waiter


def test_run_exp_layout_uses_config_filename_and_both_case_counts(tmp_path):
    root = Path(__file__).resolve().parents[1]
    config = root / "_config" / "deepseekv4_flash.yaml"
    env = os.environ.copy()
    env.update({
        "BENCHMARKS": " ",  # verify entrypoint setup without launching a benchmark
        "LLM_CONFIG": str(config),
        "EVOLVE_CASE_COUNT": "3",
        "EVAL_N_TASKS": "5",
        "RESULTS_ROOT": str(tmp_path),
    })
    completed = subprocess.run(
        ["bash", str(root / "scripts" / "run_exp.sh")],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    expected = tmp_path / "deepseekv4_flash" / "evolve3_eval5"
    assert f"EXPERIMENT_RESULTS_ROOT={expected}" in completed.stdout
    assert f"{expected}/evolve/coat/<bench>/<TS>/" in completed.stdout


def test_run_exp_eval_all_includes_evolve_cases(tmp_path):
    root = Path(__file__).resolve().parents[1]
    work_dir = tmp_path / "work"
    results_root = tmp_path / "results"
    env = os.environ.copy()
    env.update({
        "BENCHMARKS": "deep-swe",
        "LLM_CONFIG": str(root / "_config" / "deepseekv4_flash.yaml"),
        "EVOLVE_CASE_COUNT": "2",
        # EVAL_N_TASKS is deliberately non-numeric: eval-all must derive it
        # from the benchmark pool rather than use the fixed-count setting.
        "EVAL_N_TASKS": "ignored-in-eval-all-mode",
        "EVAL_ALL_CASES": "1",
        "COAT_N_CYCLES": "1",
        "N_CONCURRENT": "1",
        "EVOLVE_WORKERS": "1",
        "DRY_RUN": "1",
        "CONDA_ENV": "",
        "WORK_DIR": str(work_dir),
        "RESULTS_ROOT": str(results_root),
    })
    completed = subprocess.run(
        ["bash", str(root / "scripts" / "run_exp.sh")],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    namespace = results_root / "deepseekv4_flash" / "evolve2_evalall"
    assert f"EXPERIMENT_RESULTS_ROOT={namespace}" in completed.stdout

    evolve_ids = set((work_dir / "eval_cases.txt").read_text().splitlines())
    final_ids = set((work_dir / "final_eval_cases.txt").read_text().splitlines())
    assert len(evolve_ids) == 2
    assert len(final_ids) == 113
    assert evolve_ids <= final_ids
    split = json.loads((work_dir / "experiment_split_manifest.json").read_text())
    assert split["evaluation_scope"] == "all_including_evolve"
    assert split["evolve_is_subset_of_evaluation"] is True
    assert split["overlap_count"] == 2


def test_run_exp_canonicalizes_relative_swebench_task_path(tmp_path):
    root = Path(__file__).resolve().parents[1]
    work_dir = tmp_path / "work"
    env = os.environ.copy()
    env.update({
        "BENCHMARKS": "swebench",
        "LLM_CONFIG": str(root / "_config" / "deepseekv4_flash.yaml"),
        "SWEBENCH_TASK_PATH": "tmp/harbor/datasets/swebench-verified",
        "EVOLVE_CASE_COUNT": "1",
        "EVAL_N_TASKS": "1",
        "COAT_N_CYCLES": "1",
        "N_CONCURRENT": "1",
        "EVOLVE_WORKERS": "1",
        "DRY_RUN": "1",
        "CONDA_ENV": "",
        "WORK_DIR": str(work_dir),
        "RESULTS_ROOT": str(tmp_path / "results"),
    })
    completed = subprocess.run(
        ["bash", str(root / "scripts" / "run_exp.sh")],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    links = list((work_dir / "prep_taskdir").iterdir())
    assert len(links) == 1
    assert links[0].is_symlink()
    assert links[0].resolve().is_dir()
    assert (links[0].resolve() / "task.toml").is_file()


def test_run_exp_honors_custom_datamind_task_path(tmp_path):
    root = Path(__file__).resolve().parents[1]
    flat = tmp_path / "custom-longds-flat"
    for case_id in ("case-one", "case-two"):
        case = flat / case_id
        case.mkdir(parents=True)
        (case / "task.toml").write_text("[task]\n", encoding="utf-8")
    work_dir = tmp_path / "work"
    env = os.environ.copy()
    env.update({
        "BENCHMARKS": "datamind",
        "LLM_CONFIG": str(root / "_config" / "deepseekv4_flash.yaml"),
        "DATAMIND_TASK_PATH": str(flat),
        # Existing flat tasks must not require the original LongDS source tree.
        "DATAMIND_TASK_ROOT": str(tmp_path / "intentionally-missing-source"),
        "EVOLVE_CASE_COUNT": "1",
        "EVAL_N_TASKS": "1",
        "COAT_N_CYCLES": "1",
        "N_CONCURRENT": "1",
        "EVOLVE_WORKERS": "1",
        "DRY_RUN": "1",
        "CONDA_ENV": "",
        "WORK_DIR": str(work_dir),
        "RESULTS_ROOT": str(tmp_path / "results"),
    })
    completed = subprocess.run(
        ["bash", str(root / "scripts" / "run_exp.sh")],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    links = list((work_dir / "prep_taskdir").iterdir())
    assert len(links) == 1
    assert links[0].resolve().parent == flat.resolve()
    final_links = list((work_dir / "final_eval_taskdir").iterdir())
    assert len(final_links) == 1
    assert final_links[0].resolve().parent == flat.resolve()
    entry = (root / "scripts" / "run_evolve_experiment.sh").read_text(
        encoding="utf-8"
    )
    assert 'export DATAMIND_TASK_PATH="$SOURCE_TASK_DIR"' in entry


def test_single_llm_config_controls_all_llm_stages_except_atlas_evaluate():
    root = Path(__file__).resolve().parents[1]
    entry = (root / "scripts" / "run_evolve_experiment.sh").read_text(encoding="utf-8")
    common = (root / "scripts" / "_bench_common.sh").read_text(encoding="utf-8")
    datamind = (root / "scripts" / "run_datamind_harbor.sh").read_text(encoding="utf-8")
    atlas = (root / "scripts" / "run_swe_atlas.sh").read_text(encoding="utf-8")

    assert 'V61_JUDGE_CONFIG' not in entry
    assert '--config "$LLM_CONFIG"' in entry
    assert '--judge-config "$LLM_CONFIG"' in entry
    assert 'LLM_CONFIG="$LLM_CONFIG"' in entry
    assert "'JUDGE_MODEL': data['llm_name']" in common
    assert 'JUDGE_MODEL=${JUDGE_MODEL}' in datamind
    assert 'VERIFIER_MODEL' not in datamind
    assert 'python - "$ATLAS_EVAL_CONFIG"' in atlas
