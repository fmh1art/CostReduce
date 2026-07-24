#!/usr/bin/env python3
"""Run the code-agent baseline matrix with fixed, paired case samples.

The matrix keeps each model backbone within its own concurrency ceiling.  A
single invocation runs jobs for one backbone sequentially; the shell wrapper
starts one invocation per backbone in parallel.
"""

from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tomllib
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from run_harbor_smoke import (
    BASELINES,
    BASELINES_ROOT,
    PROJECT_ROOT,
    _agent_env,
    _append_env,
    _load_llm,
    _mounts,
    _normalize_deveval_tasks,
)


SCRIPTS_ROOT = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_ROOT))
from select_evolve_cases import codebase_key, select_cases  # noqa: E402


OUTPUT_ROOT = PROJECT_ROOT / "results" / "baselines"
MATRIX_STATE_ROOT = OUTPUT_ROOT / "_matrix"
SAMPLE_ROOT = BASELINES_ROOT / "experiments" / "matrix64_v1"
RUNTIME_ROOT = BASELINES_ROOT / "runtime_tasks" / "matrix64_v1"

BENCHMARK_ROOTS = {
    "deep-swe": PROJECT_ROOT / "benchmark" / "deep-swe" / "tasks",
    "swe-bench": (
        PROJECT_ROOT / "tmp" / "harbor" / "datasets" / "swebench-verified"
    ),
    "dab": (
        PROJECT_ROOT
        / "benchmark"
        / "DBA-bench"
        / "harbor"
        / "datasets"
        / "dab"
    ),
    "terminal-bench-2.1": (
        PROJECT_ROOT
        / "tmp"
        / "harbor"
        / "datasets"
        / "terminal-bench-2-1"
    ),
    "deveval": PROJECT_ROOT / "tmp" / "harbor" / "datasets" / "deveval",
}
SELECTOR_BENCHMARKS = {
    "deep-swe": "deep-swe",
    "swe-bench": "swebench",
    "dab": "dab",
    "terminal-bench-2.1": "terminal-bench-2.1",
    "deveval": "deveval",
}
BENCHMARK_ALIASES = {
    "deepswe": "deep-swe",
    "deep-swe": "deep-swe",
    "swebench": "swe-bench",
    "swe-bench": "swe-bench",
    "dab": "dab",
    "terminalbench": "terminal-bench-2.1",
    "terminal-bench": "terminal-bench-2.1",
    "terminal-bench-2-1": "terminal-bench-2.1",
    "terminal-bench-2.1": "terminal-bench-2.1",
    "deveval": "deveval",
}
METHODS = ("agentdiet", "eet", "zipact")
DEFAULT_BENCHMARK_ORDER = (
    "swe-bench",
    "deep-swe",
    "dab",
    "terminal-bench-2.1",
    "deveval",
)
BENCHMARK_SAMPLE_LIMITS = {
    "deep-swe": 64,
    "swe-bench": 64,
    "dab": 64,
    "terminal-bench-2.1": 64,
    # DevEval contains 63 tasks in total.
    "deveval": 63,
}

EXPERIMENT_CONFIGS = {
    "agentdiet": (
        BASELINES_ROOT
        / "trajectory_reduction"
        / "harbor_experiment_config.yaml"
    ),
    "zipact": BASELINES_ROOT / "zipact" / "harbor_experiment_config.yaml",
    "eet": BASELINES_ROOT / "eet" / "harbor_experiment_config.yaml",
}

MODEL_MAX_CONCURRENCY = {
    "deepseekv4_flash": 8,
    "deepseekv4_pro": 8,
    "doubao_seed2_lite": 6,
    "gpt5_5": 4,
}
MATRIX_BACKBONES = {
    name: {
        "llm_config": PROJECT_ROOT / "_config" / f"{name}.yaml",
        "max_concurrency": limit,
    }
    for name, limit in MODEL_MAX_CONCURRENCY.items()
}


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _config_stem(path: Path) -> str:
    return path.stem.replace(" ", "_")


def _normalize_benchmarks(raw: str) -> list[str]:
    values: list[str] = []
    for item in raw.split(","):
        key = item.strip().lower()
        if not key:
            continue
        try:
            benchmark = BENCHMARK_ALIASES[key]
        except KeyError as exc:
            raise ValueError(f"Unsupported benchmark: {item}") from exc
        if benchmark in values:
            raise ValueError(f"Duplicate benchmark: {benchmark}")
        values.append(benchmark)
    if not values:
        raise ValueError("At least one benchmark is required")
    return values


def _normalize_methods(raw: str) -> list[str]:
    values = [item.strip().lower() for item in raw.split(",") if item.strip()]
    unknown = sorted(set(values) - set(METHODS))
    if unknown:
        raise ValueError(f"Unsupported baseline method(s): {', '.join(unknown)}")
    if len(values) != len(set(values)):
        raise ValueError("Baseline method list contains duplicates")
    if not values:
        raise ValueError("At least one baseline method is required")
    return values


def _selected_cases(benchmark: str, limit: int = 64) -> tuple[list[str], dict[str, str]]:
    source = BENCHMARK_ROOTS[benchmark]
    if not source.is_dir():
        raise FileNotFoundError(f"Benchmark task root is missing: {source}")
    selected, membership = select_cases(
        source,
        SELECTOR_BENCHMARKS[benchmark],
        limit,
        policy="diverse",
    )
    if len(selected) != limit:
        raise RuntimeError(
            f"{benchmark} requires {limit} cases, but only {len(selected)} "
            f"were selected from {source}"
        )
    return selected, membership


def _write_or_validate(path: Path, expected: str) -> None:
    if path.exists():
        actual = path.read_text(encoding="utf-8")
        if actual != expected:
            raise RuntimeError(
                f"Refusing to change an existing fixed sample artifact: {path}"
            )
        return
    _atomic_write(path, expected)


def ensure_case_manifests() -> dict[str, dict[str, Any]]:
    """Create or validate the shared 64/16-case sample artifacts."""

    SAMPLE_ROOT.mkdir(parents=True, exist_ok=True)
    lock_path = SAMPLE_ROOT / ".prepare.lock"
    records: dict[str, dict[str, Any]] = {}
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        for benchmark in BENCHMARK_ROOTS:
            sample_limit = BENCHMARK_SAMPLE_LIMITS[benchmark]
            selected, membership = _selected_cases(benchmark, sample_limit)
            selected16 = selected[:16]
            text_full = "".join(f"{case_id}\n" for case_id in selected)
            text16 = "".join(f"{case_id}\n" for case_id in selected16)
            cases_full_path = SAMPLE_ROOT / f"{benchmark}_{sample_limit}.txt"
            cases16_path = SAMPLE_ROOT / f"{benchmark}_16.txt"
            _write_or_validate(cases_full_path, text_full)
            _write_or_validate(cases16_path, text16)
            sample_key = "sample_64" if sample_limit == 64 else "sample_full"
            prefix_key = (
                "is_prefix_of_sample_64"
                if sample_limit == 64
                else "is_prefix_of_sample_full"
            )

            manifest = {
                "schema_version": "baseline.matrix-case-selection.v1",
                "policy": "codebase-round-robin",
                "selector_salt": "evolve-diverse-v1",
                "benchmark": benchmark,
                "task_root": str(BENCHMARK_ROOTS[benchmark].resolve()),
                sample_key: {
                    "path": str(cases_full_path.resolve()),
                    "sha256": _sha256_text(text_full),
                    "count": sample_limit,
                    "codebases": len({membership[item] for item in selected}),
                    "cases": [
                        {"case_id": item, "codebase": membership[item]}
                        for item in selected
                    ],
                },
                "zipact_16": {
                    "path": str(cases16_path.resolve()),
                    "sha256": _sha256_text(text16),
                    "count": 16,
                    prefix_key: selected16 == selected[:16],
                    "cases": selected16,
                },
            }
            manifest_path = SAMPLE_ROOT / f"{benchmark}_manifest.json"
            expected_manifest = (
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
            )
            _write_or_validate(manifest_path, expected_manifest)
            records[benchmark] = {
                "sample_limit": sample_limit,
                "cases_full": selected,
                "cases16": selected16,
                "cases_full_path": cases_full_path,
                "cases16_path": cases16_path,
                "manifest_path": manifest_path,
                "sample_full_sha256": _sha256_text(text_full),
                "sample16_sha256": _sha256_text(text16),
            }
    return records


def _enable_deepswe_agent_network(task_toml: Path) -> None:
    lines = task_toml.read_text(encoding="utf-8").splitlines()
    in_environment = False
    changed = False
    already_enabled = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_environment = stripped == "[environment]"
            continue
        if not in_environment:
            continue
        if stripped == "allow_internet = false":
            lines[index] = "allow_internet = true"
            changed = True
        elif stripped == "allow_internet = true":
            already_enabled = True
    if not changed and not already_enabled:
        raise RuntimeError(
            f"Expected [environment] allow_internet in {task_toml}"
        )
    if changed:
        task_toml.write_text("\n".join(lines) + "\n", encoding="utf-8")

    parsed = tomllib.loads(task_toml.read_text(encoding="utf-8"))
    if parsed.get("environment", {}).get("allow_internet") is not True:
        raise RuntimeError(f"DeepSWE agent network was not enabled: {task_toml}")
    verifier_network = (
        parsed.get("verifier", {}).get("environment", {}).get("allow_internet")
    )
    if verifier_network is not False:
        raise RuntimeError(
            f"DeepSWE verifier isolation unexpectedly changed: {task_toml}"
        )


def prepare_deepswe_runtime(case_ids: list[str]) -> Path:
    """Stage DeepSWE cases while preserving the source and verifier isolation."""

    source_root = BENCHMARK_ROOTS["deep-swe"]
    target_root = RUNTIME_ROOT / "deep-swe"
    RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
    lock_path = RUNTIME_ROOT / ".prepare.lock"
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        for case_id in case_ids:
            source = source_root / case_id
            target = target_root / case_id
            if not source.is_dir():
                raise FileNotFoundError(f"DeepSWE task is missing: {source}")
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(source, target)
            _enable_deepswe_agent_network(target / "task.toml")

        staged = sorted(
            child.name
            for child in target_root.iterdir()
            if child.is_dir() and (child / "task.toml").is_file()
        )
        missing = sorted(set(case_ids) - set(staged))
        if missing:
            raise RuntimeError(
                f"DeepSWE runtime staging is incomplete: {', '.join(missing)}"
            )
        manifest = {
            "schema_version": "baseline.deepswe-runtime.v1",
            "source": str(source_root.resolve()),
            "target": str(target_root.resolve()),
            "agent_network": "public",
            "verifier_network": "unchanged-private-separate-environment",
            "cases": case_ids,
        }
        _atomic_write(
            RUNTIME_ROOT / "deep-swe_manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        )
    return target_root


def audit_dab_cases(case_ids: list[str]) -> None:
    """Reject legacy DAB tasks that expose verifier answers to the agent."""

    root = BENCHMARK_ROOTS["dab"]
    leaked: list[str] = []
    for case_id in case_ids:
        query = root / case_id / "environment" / "dab" / "query"
        for name in ("ground_truth.csv", "validate.py"):
            if (query / name).exists():
                leaked.append(f"{case_id}:{name}")
    if leaked:
        raise RuntimeError(
            "Selected DAB tasks expose verifier-only files: "
            + ", ".join(leaked[:10])
        )


def _safe_llm_metadata(llm_config: Path) -> dict[str, Any]:
    data = yaml.safe_load(llm_config.read_text(encoding="utf-8")) or {}
    return {
        "config_path": str(llm_config.resolve()),
        "config_sha256": hashlib.sha256(llm_config.read_bytes()).hexdigest(),
        "config_name": _config_stem(llm_config),
        "llm_name": data.get("llm_name"),
        "api_type": data.get("api_type", "chat"),
        "temperature": data.get("temperature"),
        "thinking": data.get("thinking"),
        "price_yuan_per_million_token": data.get(
            "price_yuan_per_million_token"
        ),
    }


def materialize_experiment_config(
    baseline: str,
    llm_config: Path,
    destination: Path,
) -> Path:
    """Merge non-secret model controls into a baseline's formal config."""

    source = EXPERIMENT_CONFIGS[baseline]
    base = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    llm = yaml.safe_load(llm_config.read_text(encoding="utf-8")) or {}
    config = copy.deepcopy(base)
    model = config.setdefault("model", {})
    api_type = str(llm.get("api_type") or "chat").lower()
    model["model_class"] = (
        "litellm_response" if api_type == "responses" else "litellm"
    )
    kwargs = model.setdefault("model_kwargs", {})

    # The checked-in formal configs were created for Doubao.  Remove those
    # controls from the copy, then apply only the selected backbone's controls.
    kwargs.pop("temperature", None)
    extra_body = kwargs.get("extra_body")
    if isinstance(extra_body, dict):
        extra_body.pop("thinking", None)
        if not extra_body:
            kwargs.pop("extra_body", None)

    temperature = llm.get("temperature")
    if temperature not in (None, ""):
        kwargs["temperature"] = float(temperature)
    thinking = str(llm.get("thinking") or "").strip().lower()
    if thinking:
        if thinking not in {"enabled", "disabled", "auto"}:
            raise ValueError(
                f"Unsupported thinking={thinking!r} in {llm_config}"
            )
        kwargs.setdefault("extra_body", {})["thinking"] = {"type": thinking}

    text = yaml.safe_dump(
        config,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
    )
    if "key:" in text or "api_key" in text.lower():
        raise RuntimeError("Generated experiment config unexpectedly has a key")
    if destination.exists() and destination.read_text(encoding="utf-8") != text:
        raise RuntimeError(
            f"Refusing to change an existing runtime config: {destination}"
        )
    if not destination.exists():
        _atomic_write(destination, text)
    return destination


def _load_case_file(path: Path, expected: int) -> list[str]:
    cases = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if len(cases) != expected or len(cases) != len(set(cases)):
        raise ValueError(
            f"Expected {expected} unique cases in {path}, got {len(cases)}"
        )
    return cases


def _dataset_root(benchmark: str, samples: dict[str, dict[str, Any]]) -> Path:
    if benchmark == "deep-swe":
        return prepare_deepswe_runtime(samples["deep-swe"]["cases_full"])
    if benchmark == "deveval":
        _normalize_deveval_tasks(BENCHMARK_ROOTS["deveval"])
    return BENCHMARK_ROOTS[benchmark]


def build_batch_command(
    baseline: str,
    benchmark: str,
    *,
    run_id: str,
    n_concurrent: int,
    llm_config: Path,
    runtime_config: Path,
    output_root: Path,
    samples: dict[str, dict[str, Any]],
) -> tuple[list[str], dict[str, str], list[str], Path]:
    if baseline not in METHODS:
        raise ValueError(f"Unsupported baseline: {baseline}")
    if benchmark not in BENCHMARK_ROOTS:
        raise ValueError(f"Unsupported benchmark: {benchmark}")
    if n_concurrent < 1:
        raise ValueError("n_concurrent must be positive")
    if not llm_config.is_file():
        raise FileNotFoundError(f"LLM config is missing: {llm_config}")
    if not runtime_config.is_file():
        raise FileNotFoundError(
            f"Runtime experiment config is missing: {runtime_config}"
        )

    n_tasks = (
        16 if baseline == "zipact" else samples[benchmark]["sample_limit"]
    )
    cases_path = samples[benchmark][
        "cases16_path" if baseline == "zipact" else "cases_full_path"
    ]
    cases = _load_case_file(cases_path, n_tasks)
    if benchmark == "dab":
        audit_dab_cases(cases)
    dataset = _dataset_root(benchmark, samples)

    spec = BASELINES[baseline]
    harbor = spec["prefix"] / "bin" / "harbor"
    if not harbor.is_file():
        raise FileNotFoundError(
            f"Harbor is not installed in {spec['prefix']}; "
            "run baselines/setup_envs.sh"
        )

    method_output = output_root / baseline
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
        str(n_tasks),
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

    verifier_keys = {
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
        if key in verifier_keys
    }
    verifier_env["TEST_DIR"] = "/tests"
    _append_env(command, "--ve", verifier_env)

    host_env = os.environ.copy()
    host_env.update(llm_env)
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


def _inspect_job(job_dir: Path, expected: int) -> dict[str, Any] | None:
    result_path = job_dir / "result.json"
    if not result_path.is_file():
        return None
    result = json.loads(result_path.read_text(encoding="utf-8"))
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
        "n_cancelled_trials": stats.get("n_cancelled_trials"),
        "n_input_tokens": stats.get("n_input_tokens"),
        "n_cache_tokens": stats.get("n_cache_tokens"),
        "n_output_tokens": stats.get("n_output_tokens"),
        "cost_usd": stats.get("cost_usd"),
        "finished_at": result.get("finished_at"),
    }


def _driver_status_path(matrix_id: str, config_name: str) -> Path:
    return MATRIX_STATE_ROOT / matrix_id / "status" / f"{config_name}.json"


def _write_driver_status(
    path: Path,
    *,
    state: str,
    metadata: dict[str, Any],
    jobs: list[dict[str, Any]],
    current: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    benchmark_order = metadata.get("benchmark_order") or BENCHMARK_ROOTS
    method_order = metadata.get("method_order") or METHODS
    payload = {
        "schema_version": "baseline.matrix-driver-status.v1",
        "updated_at": datetime.now().astimezone().isoformat(),
        "state": state,
        **metadata,
        "jobs_total": len(benchmark_order) * len(method_order),
        "jobs_finished": len(jobs),
        "current": current,
        "jobs": jobs,
        "error": error,
    }
    _atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def prepare_matrix(matrix_id: str) -> dict[str, Any]:
    samples = ensure_case_manifests()
    prepare_deepswe_runtime(samples["deep-swe"]["cases_full"])
    audit_dab_cases(samples["dab"]["cases_full"])
    matrix_root = MATRIX_STATE_ROOT / matrix_id
    matrix_root.mkdir(parents=True, exist_ok=True)
    plan = {
        "schema_version": "baseline.matrix-plan.v1",
        "matrix_id": matrix_id,
        "created_or_validated_at": datetime.now().astimezone().isoformat(),
        "methods": {
            "agentdiet": {
                "cases_per_benchmark": BENCHMARK_SAMPLE_LIMITS
            },
            "eet": {"cases_per_benchmark": BENCHMARK_SAMPLE_LIMITS},
            "zipact": {"cases_per_benchmark": 16},
        },
        "benchmarks": {
            name: {
                "source": str(BENCHMARK_ROOTS[name].resolve()),
                "sample_manifest": str(record["manifest_path"].resolve()),
                "sample_full_count": record["sample_limit"],
                "sample_full_sha256": record["sample_full_sha256"],
                "sample16_sha256": record["sample16_sha256"],
            }
            for name, record in samples.items()
        },
        "backbones": {
            name: {
                **_safe_llm_metadata(spec["llm_config"]),
                "max_concurrency": spec["max_concurrency"],
            }
            for name, spec in MATRIX_BACKBONES.items()
        },
        "output_root": str(OUTPUT_ROOT.resolve()),
    }
    plan_path = matrix_root / "plan.json"
    if plan_path.exists():
        previous = json.loads(plan_path.read_text(encoding="utf-8"))
        stable_keys = (
            "schema_version",
            "matrix_id",
            "methods",
            "benchmarks",
            "backbones",
            "output_root",
        )
        if any(previous.get(key) != plan.get(key) for key in stable_keys):
            raise RuntimeError(
                f"Matrix ID already has a different plan: {plan_path}"
            )
    else:
        _atomic_write(
            plan_path, json.dumps(plan, ensure_ascii=False, indent=2) + "\n"
        )
    return {"samples": samples, "plan": plan, "matrix_root": matrix_root}


def run_backbone(args: argparse.Namespace) -> int:
    llm_config = args.llm_config.expanduser().resolve()
    config_name = _config_stem(llm_config)
    if config_name not in MODEL_MAX_CONCURRENCY:
        raise ValueError(
            f"No concurrency ceiling is registered for {config_name}"
        )
    maximum = MODEL_MAX_CONCURRENCY[config_name]
    if args.n_concurrent > maximum:
        raise ValueError(
            f"{config_name} concurrency {args.n_concurrent} exceeds {maximum}"
        )

    prepared = prepare_matrix(args.matrix_id)
    samples = prepared["samples"]
    matrix_root: Path = prepared["matrix_root"]
    benchmarks = _normalize_benchmarks(args.benchmark_order)
    methods = _normalize_methods(args.methods)

    runtime_config_root = matrix_root / "configs" / config_name
    runtime_configs = {
        baseline: materialize_experiment_config(
            baseline,
            llm_config,
            runtime_config_root / f"{baseline}.yaml",
        )
        for baseline in methods
    }
    metadata = {
        "matrix_id": args.matrix_id,
        "config": _safe_llm_metadata(llm_config),
        "n_concurrent": args.n_concurrent,
        "max_concurrency": maximum,
        "benchmark_order": benchmarks,
        "method_order": methods,
        "pid": os.getpid(),
    }
    status_path = _driver_status_path(args.matrix_id, config_name)
    finished_jobs: list[dict[str, Any]] = []
    _write_driver_status(
        status_path,
        state="dry_run" if args.dry_run else "running",
        metadata=metadata,
        jobs=finished_jobs,
    )

    for benchmark in benchmarks:
        for baseline in methods:
            n_tasks = (
                16
                if baseline == "zipact"
                else samples[benchmark]["sample_limit"]
            )
            run_id = (
                f"{args.matrix_id}-{config_name}-{baseline}-"
                f"{benchmark}-n{n_tasks}"
            )
            job_spec = {
                "baseline": baseline,
                "benchmark": benchmark,
                "n_tasks": n_tasks,
                "run_id": run_id,
                "job_dir": str((args.output_root / baseline / run_id).resolve()),
            }
            existing = _inspect_job(
                args.output_root / baseline / run_id, n_tasks
            )
            if existing and existing["complete"] and args.resume:
                finished_jobs.append(
                    {**job_spec, "state": "skipped_complete", **existing}
                )
                _write_driver_status(
                    status_path,
                    state="dry_run" if args.dry_run else "running",
                    metadata=metadata,
                    jobs=finished_jobs,
                )
                continue
            if (args.output_root / baseline / run_id).exists():
                raise FileExistsError(
                    "Existing job is not complete; use a new matrix ID: "
                    f"{args.output_root / baseline / run_id}"
                )

            command, host_env, cases, job_dir = build_batch_command(
                baseline,
                benchmark,
                run_id=run_id,
                n_concurrent=args.n_concurrent,
                llm_config=llm_config,
                runtime_config=runtime_configs[baseline],
                output_root=args.output_root,
                samples=samples,
            )
            safe_summary = {
                **job_spec,
                "llm_config": str(llm_config),
                "runtime_config": str(runtime_configs[baseline]),
                "n_concurrent": args.n_concurrent,
                "case_file": str(
                    samples[benchmark][
                        "cases16_path"
                        if baseline == "zipact"
                        else "cases_full_path"
                    ]
                ),
                "first_case": cases[0],
                "last_case": cases[-1],
                "dry_run": args.dry_run,
            }
            print(json.dumps(safe_summary, ensure_ascii=False), flush=True)
            _write_driver_status(
                status_path,
                state="dry_run" if args.dry_run else "running",
                metadata=metadata,
                jobs=finished_jobs,
                current=job_spec,
            )
            if args.dry_run:
                finished_jobs.append({**job_spec, "state": "validated"})
                continue

            returncode = subprocess.run(
                command, env=host_env, check=False
            ).returncode
            inspected = _inspect_job(job_dir, n_tasks)
            if returncode != 0 or not inspected or not inspected["complete"]:
                message = (
                    f"Harbor job did not complete: rc={returncode}, "
                    f"job_dir={job_dir}, inspection={inspected}"
                )
                _write_driver_status(
                    status_path,
                    state="failed",
                    metadata=metadata,
                    jobs=finished_jobs,
                    current=job_spec,
                    error=message,
                )
                print(message, file=sys.stderr, flush=True)
                return returncode or 1
            finished_jobs.append(
                {**job_spec, "state": "complete", **inspected}
            )
            _write_driver_status(
                status_path,
                state="running",
                metadata=metadata,
                jobs=finished_jobs,
            )

    _write_driver_status(
        status_path,
        state="dry_run_complete" if args.dry_run else "complete",
        metadata=metadata,
        jobs=finished_jobs,
    )
    return 0


def report_status(matrix_id: str) -> int:
    root = MATRIX_STATE_ROOT / matrix_id
    plan_path = root / "plan.json"
    if not plan_path.is_file():
        raise FileNotFoundError(f"Matrix plan is missing: {plan_path}")
    reports = []
    for name in MODEL_MAX_CONCURRENCY:
        status_path = _driver_status_path(matrix_id, name)
        if status_path.is_file():
            status = json.loads(status_path.read_text(encoding="utf-8"))
            current = status.get("current") or {}
            reports.append(
                {
                    "backbone": name,
                    "state": status.get("state"),
                    "jobs_finished": status.get("jobs_finished"),
                    "jobs_total": status.get("jobs_total"),
                    "current": (
                        f"{current.get('baseline')}/{current.get('benchmark')}"
                        if current
                        else None
                    ),
                    "current_job_dir": current.get("job_dir"),
                    "errored_trials_finished_jobs": sum(
                        int(job.get("n_errored_trials") or 0)
                        for job in status.get("jobs") or []
                    ),
                    "updated_at": status.get("updated_at"),
                    "error": status.get("error"),
                }
            )
        else:
            reports.append(
                {
                    "backbone": name,
                    "state": "not_started",
                    "jobs_finished": 0,
                    "jobs_total": len(BENCHMARK_ROOTS) * len(METHODS),
                }
            )
    print(
        json.dumps(
            {"matrix_id": matrix_id, "backbones": reports},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run or inspect the fixed code-agent baseline matrix."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser(
        "prepare", help="Create and validate case/runtime manifests."
    )
    prepare.add_argument("--matrix-id", required=True)

    run = subparsers.add_parser(
        "run", help="Run all baselines for one model backbone."
    )
    run.add_argument("--matrix-id", required=True)
    run.add_argument("--llm-config", type=Path, required=True)
    run.add_argument("--n-concurrent", type=int, required=True)
    run.add_argument(
        "--benchmark-order",
        default=",".join(DEFAULT_BENCHMARK_ORDER),
    )
    run.add_argument("--methods", default=",".join(METHODS))
    run.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    run.add_argument("--resume", action="store_true")
    run.add_argument("--dry-run", action="store_true")

    status = subparsers.add_parser("status", help="Report matrix progress.")
    status.add_argument("--matrix-id", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "prepare":
        prepared = prepare_matrix(args.matrix_id)
        print(
            json.dumps(
                {
                    "matrix_id": args.matrix_id,
                    "matrix_root": str(prepared["matrix_root"]),
                    "sample_root": str(SAMPLE_ROOT),
                    "deepswe_runtime": str(RUNTIME_ROOT / "deep-swe"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.command == "run":
        args.output_root = args.output_root.expanduser().resolve()
        return run_backbone(args)
    if args.command == "status":
        return report_status(args.matrix_id)
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
