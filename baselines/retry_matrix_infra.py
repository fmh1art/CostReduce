#!/usr/bin/env python3
"""Retry only explicitly transient infrastructure failures in a matrix run.

The original Harbor jobs are never modified.  Each retry round gets its own
job directory, and a machine-readable manifest records which original cases
were retried and why.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from run_harbor_smoke import (
    BASELINES,
    BASELINES_ROOT,
    PROJECT_ROOT,
    _agent_env,
    _append_env,
    _load_llm,
    _mounts,
)


OUTPUT_ROOT = PROJECT_ROOT / "results" / "baselines"
MATRIX_ROOT = OUTPUT_ROOT / "_matrix"
MODEL_MAX_CONCURRENCY = {
    "deepseekv4_flash": 8,
    "deepseekv4_pro": 8,
    "doubao_seed2_lite": 6,
    "gpt5_5": 4,
}
DEFAULT_TRANSIENT_TYPES = ("ApiRateLimitError",)
DEFAULT_TRANSIENT_MESSAGES = ("Insufficient Balance",)


def _atomic_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _case_id(trial: dict[str, Any]) -> str:
    task_id = trial.get("task_id")
    if isinstance(task_id, dict) and task_id.get("path"):
        return Path(str(task_id["path"])).name
    if isinstance(task_id, str) and task_id:
        return Path(task_id).name
    task_name = str(trial.get("task_name") or "")
    if task_name:
        return task_name.rsplit("/", 1)[-1]
    raise ValueError(f"Cannot determine case ID for trial {trial.get('id')}")


def _trial_records(job_dir: Path) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for path in sorted(job_dir.glob("*/result.json")):
        trial = _load_json(path)
        case_id = _case_id(trial)
        if case_id in records:
            raise RuntimeError(f"Duplicate case {case_id} in {job_dir}")
        exception = trial.get("exception_info") or {}
        exception_message = str(exception.get("exception_message") or "")
        rewards = (trial.get("verifier_result") or {}).get("rewards") or {}
        agent_result = trial.get("agent_result") or {}
        records[case_id] = {
            "case_id": case_id,
            "trial_result": str(path.resolve()),
            "exception_type": exception.get("exception_type"),
            "exception_message": exception_message,
            "reward": rewards.get("reward"),
            "n_input_tokens": int(agent_result.get("n_input_tokens") or 0),
            "n_cache_tokens": int(agent_result.get("n_cache_tokens") or 0),
            "n_output_tokens": int(agent_result.get("n_output_tokens") or 0),
        }
    return records


def _inspect_job(job_dir: Path, expected: int) -> dict[str, Any] | None:
    result_path = job_dir / "result.json"
    if not result_path.is_file():
        return None
    result = _load_json(result_path)
    stats = result.get("stats") or {}
    complete = (
        int(result.get("n_total_trials") or 0) == expected
        and int(stats.get("n_completed_trials") or 0) == expected
        and int(stats.get("n_running_trials") or 0) == 0
        and int(stats.get("n_pending_trials") or 0) == 0
    )
    return {
        "complete": complete,
        "n_total_trials": result.get("n_total_trials"),
        "n_completed_trials": stats.get("n_completed_trials"),
        "n_errored_trials": stats.get("n_errored_trials"),
    }


def _dataset_path(
    plan: dict[str, Any], matrix_dir: Path, benchmark: str
) -> Path:
    if benchmark == "deep-swe":
        staged = BASELINES_ROOT / "runtime_tasks" / "matrix64_v1" / "deep-swe"
        if not staged.is_dir():
            raise FileNotFoundError(f"DeepSWE staged tasks are missing: {staged}")
        return staged
    source = Path(plan["benchmarks"][benchmark]["source"])
    if not source.is_dir():
        raise FileNotFoundError(f"Benchmark source is missing: {source}")
    return source


def _build_command(
    *,
    plan: dict[str, Any],
    matrix_dir: Path,
    backbone: str,
    baseline: str,
    benchmark: str,
    cases: list[str],
    run_id: str,
    n_concurrent: int,
) -> tuple[list[str], dict[str, str], Path]:
    spec = BASELINES[baseline]
    harbor = spec["prefix"] / "bin" / "harbor"
    if not harbor.is_file():
        raise FileNotFoundError(f"Harbor is missing: {harbor}")
    llm_config = PROJECT_ROOT / "_config" / f"{backbone}.yaml"
    runtime_config = matrix_dir / "configs" / backbone / f"{baseline}.yaml"
    if not llm_config.is_file() or not runtime_config.is_file():
        raise FileNotFoundError(
            f"Missing LLM/runtime config for {backbone}/{baseline}"
        )
    dataset = _dataset_path(plan, matrix_dir, benchmark)
    method_output = OUTPUT_ROOT / baseline
    method_output.mkdir(parents=True, exist_ok=True)
    job_dir = method_output / run_id
    model, llm_env = _load_llm(llm_config)

    command = [
        str(harbor),
        "run",
        "-p",
        str(dataset),
        "-a",
        "mini-swe-agent",
        "-m",
        model,
        "-e",
        "docker",
        "-k",
        "1",
        "-n",
        str(n_concurrent),
        "--n-tasks",
        str(len(cases)),
        "--agent-setup-timeout-multiplier",
        "4",
        "-o",
        str(method_output),
        "--job-name",
        run_id,
        "--yes",
        "--quiet",
        "--ak",
        "version=2.4.5",
        "--ak",
        f"config_file={runtime_config}",
        "--mounts",
        json.dumps(_mounts(spec)),
    ]
    for case_id in cases:
        command.extend(["--include-task-name", case_id])
    _append_env(command, "--ae", _agent_env(llm_env))

    proxy_keys = {
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "http_proxy",
        "https_proxy",
        "NO_PROXY",
        "no_proxy",
        "UV_CACHE_DIR",
    }
    verifier_env = {
        key: value for key, value in _agent_env({}).items() if key in proxy_keys
    }
    verifier_env["TEST_DIR"] = "/tests"
    _append_env(command, "--ve", verifier_env)

    host_env = os.environ.copy()
    host_env.update(llm_env)
    host_env["UV_CACHE_DIR"] = str(BASELINES_ROOT / ".cache" / "uv-host")
    proxy = "http://sys-proxy-rd-relay.byted.org:8118"
    host_env.update(
        {
            "HTTP_PROXY": proxy,
            "HTTPS_PROXY": proxy,
            "http_proxy": proxy,
            "https_proxy": proxy,
            "NO_PROXY": "localhost,127.0.0.1,::1",
            "no_proxy": "localhost,127.0.0.1,::1",
        }
    )
    return command, host_env, job_dir


def _original_groups(
    status: dict[str, Any],
    transient_types: set[str],
    transient_messages: tuple[str, ...],
) -> tuple[
    dict[tuple[str, str], list[str]], dict[tuple[str, str, str], dict[str, Any]]
]:
    groups: dict[tuple[str, str], list[str]] = {}
    originals: dict[tuple[str, str, str], dict[str, Any]] = {}
    for job in status.get("jobs") or []:
        baseline = str(job["baseline"])
        benchmark = str(job["benchmark"])
        job_dir = Path(job["job_dir"])
        for case_id, record in _trial_records(job_dir).items():
            originals[(baseline, benchmark, case_id)] = record
            if _is_transient(record, transient_types, transient_messages):
                groups.setdefault((baseline, benchmark), []).append(case_id)
    return groups, originals


def _is_transient(
    record: dict[str, Any],
    transient_types: set[str],
    transient_messages: tuple[str, ...],
) -> bool:
    if record["exception_type"] in transient_types:
        return True
    message = record["exception_message"].lower()
    return any(pattern.lower() in message for pattern in transient_messages)


def _ordered_cases(
    matrix_dir: Path, benchmark: str, wanted: list[str]
) -> list[str]:
    wanted_set = set(wanted)
    candidates = [
        BASELINES_ROOT / "experiments" / "matrix64_v1" / f"{benchmark}_64.txt",
        BASELINES_ROOT / "experiments" / "matrix64_v1" / f"{benchmark}_16.txt",
    ]
    ordered: list[str] = []
    for path in candidates:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            case_id = line.strip()
            if case_id in wanted_set and case_id not in ordered:
                ordered.append(case_id)
    missing = sorted(wanted_set - set(ordered))
    if missing:
        raise RuntimeError(
            f"Retry cases are absent from fixed manifests: {', '.join(missing)}"
        )
    return ordered


def retry(args: argparse.Namespace) -> int:
    maximum = MODEL_MAX_CONCURRENCY[args.backbone]
    if not 1 <= args.n_concurrent <= maximum:
        raise ValueError(
            f"{args.backbone} retry concurrency must be within 1..{maximum}"
        )
    matrix_dir = MATRIX_ROOT / args.matrix_id
    plan = _load_json(matrix_dir / "plan.json")
    status_path = matrix_dir / "status" / f"{args.backbone}.json"
    status = _load_json(status_path)
    if status.get("state") != "complete":
        raise RuntimeError(
            f"Main backbone driver is not complete: {status.get('state')}"
        )
    transient_types = set(args.transient_type)
    transient_messages = tuple(args.transient_message)
    pending, originals = _original_groups(
        status, transient_types, transient_messages
    )
    pending = {
        group: _ordered_cases(matrix_dir, group[1], cases)
        for group, cases in pending.items()
    }

    retry_dir = matrix_dir / "retries" / args.backbone
    manifest_path = retry_dir / "manifest.json"
    manifest: dict[str, Any] = {
        "schema_version": "baseline.matrix-infra-retry.v1",
        "matrix_id": args.matrix_id,
        "backbone": args.backbone,
        "created_at": datetime.now().astimezone().isoformat(),
        "n_concurrent": args.n_concurrent,
        "max_concurrency": maximum,
        "transient_types": sorted(transient_types),
        "transient_message_substrings": list(transient_messages),
        "original_transient_cases": {
            f"{baseline}/{benchmark}": cases
            for (baseline, benchmark), cases in sorted(pending.items())
        },
        "rounds": [],
        "unresolved": {},
    }
    if manifest_path.exists():
        previous = _load_json(manifest_path)
        stable = (
            "matrix_id",
            "backbone",
            "n_concurrent",
            "max_concurrency",
            "transient_types",
            "transient_message_substrings",
            "original_transient_cases",
        )
        if any(previous.get(key) != manifest.get(key) for key in stable):
            raise RuntimeError(f"Retry manifest has different settings: {manifest_path}")
        manifest = previous
    _atomic_write(manifest_path, manifest)

    for round_number in range(1, args.max_rounds + 1):
        if not pending:
            break
        round_record: dict[str, Any] = {
            "round": round_number,
            "started_at": datetime.now().astimezone().isoformat(),
            "jobs": [],
        }
        next_pending: dict[tuple[str, str], list[str]] = {}
        for (baseline, benchmark), cases in sorted(pending.items()):
            run_id = (
                f"{args.matrix_id}-{args.backbone}-{baseline}-{benchmark}-"
                f"infra-retry-r{round_number}-n{len(cases)}"
            )
            command, host_env, job_dir = _build_command(
                plan=plan,
                matrix_dir=matrix_dir,
                backbone=args.backbone,
                baseline=baseline,
                benchmark=benchmark,
                cases=cases,
                run_id=run_id,
                n_concurrent=args.n_concurrent,
            )
            inspected = _inspect_job(job_dir, len(cases))
            if inspected and inspected["complete"]:
                returncode = 0
                state = "skipped_complete"
            else:
                if job_dir.exists():
                    raise RuntimeError(f"Incomplete retry job already exists: {job_dir}")
                print(
                    json.dumps(
                        {
                            "run_id": run_id,
                            "baseline": baseline,
                            "benchmark": benchmark,
                            "n_cases": len(cases),
                            "n_concurrent": args.n_concurrent,
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                returncode = subprocess.run(
                    command, env=host_env, check=False
                ).returncode
                state = "complete"
                inspected = _inspect_job(job_dir, len(cases))
            if returncode != 0 or not inspected or not inspected["complete"]:
                raise RuntimeError(
                    f"Retry job failed: rc={returncode}, job={job_dir}, "
                    f"inspection={inspected}"
                )

            trial_records = _trial_records(job_dir)
            transient_again = [
                case_id
                for case_id in cases
                if _is_transient(
                    trial_records[case_id],
                    transient_types,
                    transient_messages,
                )
            ]
            if transient_again:
                next_pending[(baseline, benchmark)] = transient_again
            round_record["jobs"].append(
                {
                    "baseline": baseline,
                    "benchmark": benchmark,
                    "run_id": run_id,
                    "job_dir": str(job_dir.resolve()),
                    "cases": cases,
                    "state": state,
                    "inspection": inspected,
                    "transient_again": transient_again,
                }
            )
        round_record["finished_at"] = datetime.now().astimezone().isoformat()
        manifest["rounds"] = [
            item
            for item in manifest.get("rounds") or []
            if int(item.get("round") or 0) != round_number
        ]
        manifest["rounds"].append(round_record)
        pending = next_pending
        manifest["unresolved"] = {
            f"{baseline}/{benchmark}": cases
            for (baseline, benchmark), cases in sorted(pending.items())
        }
        manifest["updated_at"] = datetime.now().astimezone().isoformat()
        _atomic_write(manifest_path, manifest)

    manifest["state"] = "complete" if not pending else "exhausted"
    manifest["finished_at"] = datetime.now().astimezone().isoformat()
    _atomic_write(manifest_path, manifest)
    print(
        json.dumps(
            {
                "manifest": str(manifest_path.resolve()),
                "state": manifest["state"],
                "original_transient_count": sum(
                    len(cases)
                    for cases in manifest["original_transient_cases"].values()
                ),
                "unresolved_count": sum(
                    len(cases) for cases in manifest["unresolved"].values()
                ),
            },
            ensure_ascii=False,
        )
    )
    return 0 if not pending else 2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Retry only transient infrastructure failures."
    )
    parser.add_argument("--matrix-id", required=True)
    parser.add_argument(
        "--backbone", required=True, choices=tuple(MODEL_MAX_CONCURRENCY)
    )
    parser.add_argument("--n-concurrent", type=int, required=True)
    parser.add_argument("--max-rounds", type=int, default=3)
    parser.add_argument(
        "--transient-type",
        action="append",
        default=list(DEFAULT_TRANSIENT_TYPES),
    )
    parser.add_argument(
        "--transient-message",
        action="append",
        default=list(DEFAULT_TRANSIENT_MESSAGES),
        help="Case-insensitive exception-message substring to retry.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(retry(parse_args()))
