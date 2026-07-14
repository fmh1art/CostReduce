import json
from pathlib import Path

from src.evolve.evolve_v7 import (
    EvolvePromptBuilderV7,
    PriceModel,
    V7SampleBuilder,
)


def _step(tool, arguments, op_type, *, success=True, observation="ok", tokens=100):
    return {
        "tool_calls": [{"function_name": tool, "arguments": arguments}],
        "observation": {
            "results": [{
                "content": json.dumps({
                    "returncode": 0 if success else 1,
                    "output": observation,
                    "exception_info": "",
                })
            }]
        },
        "metrics": {
            "prompt_tokens": tokens,
            "cached_tokens": tokens // 2,
            "completion_tokens": 10,
        },
        "step_meta": {
            "op_type": op_type,
            "success": success,
            "files_touched": [],
        },
    }


def _write_case(root: Path, *, verifier=True, long_observation=False) -> Path:
    agent_dir = root / "case__one" / "agent"
    agent_dir.mkdir(parents=True)
    long_text = "x" * 20_000 if long_observation else "located function"
    trajectory = {
        "steps": [
            {"message": "Fix the implementation in src/example.py."},
            _step("read-lines", {"file": "src/example.py"}, "read", observation=long_text),
            _step("write-file", {"file_path": "src/example.py", "content": "fixed\n"}, "write"),
            _step("run-tests", {"path": "tests/test_example.py"}, "verify", observation="1 passed"),
            _step("bash", {"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"}, "explore"),
        ],
        "dependencies": {
            "0": [],
            "1": [0],
            "2": [0, 1],
            "3": [0, 2],
            "4": [0],
        },
    }
    trajectory_path = agent_dir / "trajectory.json"
    trajectory_path.write_text(json.dumps(trajectory), encoding="utf-8")
    if verifier:
        result_path = agent_dir.parent / "result.json"
        result_path.write_text(json.dumps({
            "verifier_result": {"rewards": {"reward": 1}}
        }), encoding="utf-8")
    return trajectory_path


def test_outcome_anchor_ignores_final_submit_when_verifier_exists(tmp_path):
    _write_case(tmp_path, verifier=True)
    paths = V7SampleBuilder(None, max_macro_candidates=0).build_dir(tmp_path)
    sample = json.loads(paths[0].read_text(encoding="utf-8"))

    assert sample["outcome"]["write_anchor_steps"] == [2]
    assert sample["outcome"]["evidence_terminal_steps"] == []
    assert sample["slice"]["selected_steps"] == [1, 2]
    assert 4 in sample["slice"]["pruned_steps"]
    assert sample["slice"]["validation_level"] == "structurally_valid"
    assert sample["slice"]["state_replay_valid"] is None


def test_internal_verification_is_coverage_terminal_without_external_verifier(tmp_path):
    _write_case(tmp_path, verifier=False)
    paths = V7SampleBuilder(None, max_macro_candidates=0).build_dir(tmp_path)
    sample = json.loads(paths[0].read_text(encoding="utf-8"))

    assert sample["outcome"]["verifier_pass"] is None
    assert sample["outcome"]["evidence_terminal_steps"] == [3]
    assert sample["slice"]["selected_steps"] == [1, 2, 3]
    assert 4 in sample["slice"]["pruned_steps"]


def test_compact_prompt_obeys_budget_and_does_not_render_full_observation(tmp_path):
    _write_case(tmp_path / "results", verifier=True, long_observation=True)
    paths = V7SampleBuilder(None, max_observation_chars=200, max_macro_candidates=0).build_dir(
        tmp_path / "results"
    )
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    prompt = EvolvePromptBuilderV7(
        max_prompt_chars=6_000,
        max_steps_per_sample=4,
        max_observation_chars=120,
        max_macro_candidates=0,
    ).build(paths, scripts_dir=scripts_dir)

    assert len(prompt) <= 6_000
    assert "x" * 1_000 not in prompt
    assert "Step 2" in prompt
    assert "Step 4" not in prompt


def test_prompt_budget_preserves_footer_with_large_registration_files(tmp_path):
    _write_case(tmp_path / "results", verifier=True)
    paths = V7SampleBuilder(None, max_macro_candidates=0).build_dir(tmp_path / "results")
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    for name in ("tools.json", "executor.py", "instruction.md"):
        (scripts_dir / name).write_text("x" * 10_000, encoding="utf-8")

    prompt = EvolvePromptBuilderV7(max_prompt_chars=4_000).build(
        paths, scripts_dir=scripts_dir
    )

    assert len(prompt) <= 4_000
    assert "Finish after saving the three target files." in prompt


def test_price_model_uses_uncached_cached_and_output_prices():
    model = PriceModel(
        unit="tokens",
        input_price=1,
        cached_price=0.1,
        output_price=2,
        per_million=False,
    )
    step = {"metrics": {
        "prompt_tokens": 100,
        "cached_tokens": 80,
        "completion_tokens": 10,
    }}
    assert model.turn_cost(step) == 48


def test_git_stat_head_selects_content_write_and_excludes_commit(tmp_path):
    trajectory_path = _write_case(tmp_path, verifier=True)
    trajectory = json.loads(trajectory_path.read_text(encoding="utf-8"))
    trajectory["steps"] = [
        trajectory["steps"][0],
        _step(
            "bash",
            {"command": "python - <<'PY'\nopen('src/example.py', 'w').write('fixed')\nPY"},
            "write",
        ),
        _step(
            "bash",
            {"command": "git commit -am fix"},
            "write",
            observation="[main abc123] fix",
        ),
        _step("bash", {"command": "git diff --cached --stat"}, "read"),
    ]
    trajectory["dependencies"] = {"0": [], "1": [0], "2": [1], "3": [2]}
    stat_content = json.loads(
        trajectory["steps"][3]["observation"]["results"][0]["content"]
    )
    stat_content.pop("output")
    stat_content["output_head"] = " src/example.py | 2 +-\n 1 file changed, 1 insertion(+), 1 deletion(-)"
    trajectory["steps"][3]["observation"]["results"][0]["content"] = json.dumps(stat_content)
    trajectory_path.write_text(json.dumps(trajectory), encoding="utf-8")

    paths = V7SampleBuilder(None, max_macro_candidates=0).build_dir(tmp_path)
    sample = json.loads(paths[0].read_text(encoding="utf-8"))

    assert sample["graph_summary"]["anchor_mode"] == "observed_git_change_summary"
    assert sample["outcome"]["write_anchor_steps"] == [1]
