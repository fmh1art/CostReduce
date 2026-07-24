#!/usr/bin/env python3
"""Run the fixed 16-case SWE-Bench baseline experiment with Harbor."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tomllib
from datetime import datetime
from pathlib import Path

from run_harbor_smoke import (
    BASELINES,
    BASELINES_ROOT,
    PROJECT_ROOT,
    _agent_env,
    _append_env,
    _load_llm,
    _mounts,
)


DATASET_ROOT = (
    PROJECT_ROOT / "tmp" / "harbor" / "datasets" / "swebench-verified"
)
DEFAULT_CASES_FILE = (
    BASELINES_ROOT / "experiments" / "swebench16_cases.txt"
)
DEFAULT_LLM_CONFIG = (
    PROJECT_ROOT / "_config" / "doubao_seed2_lite.yaml"
)
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "results" / "baselines"
EXPERIMENT_CONFIGS = {
    "agentdiet": (
        BASELINES_ROOT
        / "trajectory_reduction"
        / "harbor_experiment_config.yaml"
    ),
    "zipact": BASELINES_ROOT / "zipact" / "harbor_experiment_config.yaml",
    "eet": BASELINES_ROOT / "eet" / "harbor_experiment_config.yaml",
}


def load_cases(cases_file: Path, n_tasks: int) -> list[tuple[str, str]]:
    """Return validated ``(case directory, Harbor task name)`` pairs."""

    if not cases_file.is_file():
        raise FileNotFoundError(f"Case list is missing: {cases_file}")
    case_ids = [
        line.strip()
        for line in cases_file.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError(f"Case list contains duplicates: {cases_file}")
    if not 1 <= n_tasks <= len(case_ids):
        raise ValueError(
            f"n_tasks must be between 1 and {len(case_ids)}, got {n_tasks}"
        )

    cases: list[tuple[str, str]] = []
    for case_id in case_ids[:n_tasks]:
        task_toml = DATASET_ROOT / case_id / "task.toml"
        if not task_toml.is_file():
            raise FileNotFoundError(f"Harbor task is missing: {task_toml}")
        task = tomllib.loads(task_toml.read_text(encoding="utf-8"))
        task_name = str(task.get("task", {}).get("name") or "")
        if not task_name:
            raise ValueError(f"Missing [task].name in {task_toml}")
        cases.append((case_id, task_name))
    return cases


def build_batch_command(
    baseline: str,
    *,
    run_id: str,
    n_tasks: int,
    n_concurrent: int,
    cases_file: Path,
    llm_config: Path,
    output_root: Path,
) -> tuple[list[str], dict[str, str], list[tuple[str, str]], Path]:
    """Build a Harbor command without emitting the LLM credential."""

    if n_concurrent < 1:
        raise ValueError(f"n_concurrent must be positive, got {n_concurrent}")
    if not DATASET_ROOT.is_dir():
        raise FileNotFoundError(f"Harbor dataset is missing: {DATASET_ROOT}")
    if not llm_config.is_file():
        raise FileNotFoundError(f"LLM config is missing: {llm_config}")

    spec = BASELINES[baseline]
    experiment_config = EXPERIMENT_CONFIGS[baseline]
    if not experiment_config.is_file():
        raise FileNotFoundError(
            f"Experiment config is missing: {experiment_config}"
        )
    harbor = spec["prefix"] / "bin" / "harbor"
    if not harbor.is_file():
        raise FileNotFoundError(
            f"Harbor is not installed in {spec['prefix']}; "
            "run baselines/setup_envs.sh"
        )

    cases = load_cases(cases_file, n_tasks)
    output = output_root / baseline
    output.mkdir(parents=True, exist_ok=True)
    job_dir = output / run_id
    if job_dir.exists():
        raise FileExistsError(
            f"Refusing to overwrite an existing Harbor job: {job_dir}"
        )

    model, llm_env = _load_llm(llm_config)
    command = [
        str(harbor),
        "run",
        "-p",
        str(DATASET_ROOT),
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
        str(n_tasks),
        "--agent-setup-timeout-multiplier",
        "4",
        "-o",
        str(output),
        "--job-name",
        run_id,
        "--yes",
        "--quiet",
        "--ak",
        "version=2.4.5",
        "--ak",
        f"config_file={experiment_config}",
        "--mounts",
        json.dumps(_mounts(spec)),
    ]
    # A local Harbor dataset filters on directory IDs, not on the registered
    # [task].name stored inside task.toml.
    for case_id, _ in cases:
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
        key: value
        for key, value in _agent_env({}).items()
        if key in proxy_keys
    }
    _append_env(command, "--ve", verifier_env)

    host_env = os.environ.copy()
    host_env["UV_CACHE_DIR"] = str(
        BASELINES_ROOT / ".cache" / "uv-host"
    )
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
    return command, host_env, cases, job_dir


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the fixed SWE-Bench Verified baseline subset. "
            "Defaults: 16 tasks and concurrency 8."
        )
    )
    parser.add_argument("baseline", choices=sorted(BASELINES))
    parser.add_argument("--n-tasks", type=int, default=16)
    parser.add_argument("--n-concurrent", type=int, default=8)
    parser.add_argument(
        "--cases-file",
        type=Path,
        default=DEFAULT_CASES_FILE,
    )
    parser.add_argument(
        "--llm-config",
        type=Path,
        default=DEFAULT_LLM_CONFIG,
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
    )
    parser.add_argument("--run-id", default="")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and print a credential-free run summary.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_id = args.run_id or (
        f"swebench16-doubao-seed2-lite-{args.baseline}-{timestamp}"
    )
    command, host_env, cases, job_dir = build_batch_command(
        args.baseline,
        run_id=run_id,
        n_tasks=args.n_tasks,
        n_concurrent=args.n_concurrent,
        cases_file=args.cases_file.expanduser().resolve(),
        llm_config=args.llm_config.expanduser().resolve(),
        output_root=args.output_root.expanduser().resolve(),
    )
    summary = {
        "baseline": args.baseline,
        "run_id": run_id,
        "llm_config": str(args.llm_config.expanduser().resolve()),
        "experiment_config": str(EXPERIMENT_CONFIGS[args.baseline]),
        "n_tasks": len(cases),
        "n_concurrent": args.n_concurrent,
        "cases": [case_id for case_id, _ in cases],
        "job_dir": str(job_dir),
        "dry_run": args.dry_run,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    if args.dry_run:
        return 0
    return subprocess.run(command, env=host_env, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
