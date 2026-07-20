import json
from pathlib import Path

from src.evolve.rollout_validation import validate_rollout_artifacts


def _trial(root: Path, case_id: str, *, exception=None, tool_action=True) -> None:
    trial = root / f"{case_id}__trial"
    (trial / "agent").mkdir(parents=True)
    (trial / "config.json").write_text(json.dumps({
        "task": {"path": f"/tasks/{case_id}"},
    }), encoding="utf-8")
    (trial / "result.json").write_text(json.dumps({
        "exception_info": exception,
        "verifier_result": {"reward": 0},
    }), encoding="utf-8")
    steps = [{"source": "user", "message": "solve"}]
    if tool_action:
        steps.append({
            "source": "agent",
            "message": "inspect",
            "tool_calls": [{"function_name": "bash", "arguments": {"command": "pwd"}}],
        })
    else:
        steps.append({"source": "agent", "message": "I cannot start."})
    (trial / "agent" / "trajectory.json").write_text(
        json.dumps({"steps": steps}), encoding="utf-8",
    )


def test_rollout_validation_accepts_zero_reward_after_real_action(tmp_path):
    _trial(tmp_path, "case-a")
    report = validate_rollout_artifacts(tmp_path, ["case-a"])
    assert report["valid"] is True
    assert report["valid_trial_count"] == 1


def test_rollout_validation_rejects_exception_and_prose_only_agent(tmp_path):
    _trial(
        tmp_path,
        "case-a",
        exception={"exception_type": "ApiRateLimitError"},
        tool_action=False,
    )
    report = validate_rollout_artifacts(tmp_path, ["case-a", "case-b"])
    assert report["valid"] is False
    assert report["missing_case_ids"] == ["case-b"]
    assert report["invalid_case_ids"] == ["case-a"]
    assert report["trials"][0]["errors"] == [
        "trial_exception:ApiRateLimitError",
        "no_agent_tool_action",
    ]
