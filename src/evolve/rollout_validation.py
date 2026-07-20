"""Validate Harbor/Pier rollout artifacts before COAT consumes them.

The runners can create a trial directory and even an ATIF trajectory when the
agent never started successfully.  Counting ``trajectory.json`` files alone is
therefore not a sufficient completion check.  This module deliberately checks
only infrastructure/agent-execution completeness; a verifier reward of zero is
still valid evolution evidence.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _case_id(config: Any) -> str | None:
    if not isinstance(config, dict):
        return None
    task = config.get("task")
    if not isinstance(task, dict) or not task.get("path"):
        return None
    return Path(str(task["path"])).name or None


def validate_rollout_artifacts(
    run_dir: str | Path,
    expected_case_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Return a JSON-serializable completeness report for one runner output."""

    root = Path(run_dir)
    expected = list(dict.fromkeys(str(case_id) for case_id in (expected_case_ids or [])))
    trials: list[dict[str, Any]] = []
    seen: dict[str, int] = {}

    if root.is_dir():
        for config_path in sorted(root.rglob("config.json")):
            config = _read_json(config_path)
            case_id = _case_id(config)
            if not case_id:
                # Harbor/Pier also writes a run-level config.json.
                continue
            trial_dir = config_path.parent
            errors: list[str] = []

            result_path = trial_dir / "result.json"
            result = _read_json(result_path)
            if not isinstance(result, dict):
                errors.append("missing_or_invalid_result")
            elif result.get("exception_info") is not None:
                exception = result.get("exception_info")
                exception_type = (
                    exception.get("exception_type")
                    if isinstance(exception, dict)
                    else type(exception).__name__
                )
                errors.append(f"trial_exception:{exception_type or 'unknown'}")

            trajectory_path = trial_dir / "agent" / "trajectory.json"
            trajectory = _read_json(trajectory_path)
            if not isinstance(trajectory, dict):
                errors.append("missing_or_invalid_trajectory")
                action_count = 0
            else:
                steps = trajectory.get("steps")
                if not isinstance(steps, list):
                    errors.append("invalid_trajectory_steps")
                    action_count = 0
                else:
                    # A prose-only assistant message is not evidence that the
                    # benchmark agent actually interacted with its environment.
                    action_count = sum(
                        1
                        for step in steps
                        if isinstance(step, dict)
                        and step.get("source") == "agent"
                        and isinstance(step.get("tool_calls"), list)
                        and bool(step["tool_calls"])
                    )
                    if action_count == 0:
                        errors.append("no_agent_tool_action")

            seen[case_id] = seen.get(case_id, 0) + 1
            trials.append({
                "case_id": case_id,
                "trial_dir": str(trial_dir),
                "agent_tool_action_count": action_count,
                "valid": not errors,
                "errors": errors,
            })

    actual = sorted(seen)
    expected_set = set(expected)
    actual_set = set(actual)
    missing = sorted(expected_set - actual_set) if expected else []
    unexpected = sorted(actual_set - expected_set) if expected else []
    duplicates = sorted(case_id for case_id, count in seen.items() if count != 1)
    invalid_trials = [trial["case_id"] for trial in trials if not trial["valid"]]
    valid = bool(trials) and not invalid_trials and not duplicates
    if expected:
        valid = valid and not missing and not unexpected and len(trials) == len(expected)

    return {
        "schema_version": "coat.rollout-validation.v1",
        "run_dir": str(root),
        "valid": valid,
        "expected_case_count": len(expected) if expected else None,
        "trial_count": len(trials),
        "valid_trial_count": sum(1 for trial in trials if trial["valid"]),
        "missing_case_ids": missing,
        "unexpected_case_ids": unexpected,
        "duplicate_case_ids": duplicates,
        "invalid_case_ids": sorted(invalid_trials),
        "trials": trials,
    }


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--expected-cases-file")
    parser.add_argument("--report")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    expected: list[str] = []
    if args.expected_cases_file:
        expected = [
            line.strip()
            for line in Path(args.expected_cases_file).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    report = validate_rollout_artifacts(args.run_dir, expected)
    encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.report:
        Path(args.report).write_text(encoded, encoding="utf-8")
    if not args.quiet or not report["valid"]:
        print(encoded, end="")
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(_main())
