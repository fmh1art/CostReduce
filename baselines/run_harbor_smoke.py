#!/usr/bin/env python3
"""Run one Harbor case for one reproduced baseline.

All generated jobs and dependency caches remain under ``baselines/``. Dataset
directories and the project's LLM YAML are read-only inputs.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml


BASELINES_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = BASELINES_ROOT.parent
LLM_CONFIG = PROJECT_ROOT / "_config" / "doubao_seed2_lite.yaml"

BASELINES: dict[str, dict[str, Any]] = {
    "agentdiet": {
        "include_legacy_common": False,
        "prefix": BASELINES_ROOT / "envs" / "agentdiet",
        "folder": BASELINES_ROOT / "trajectory_reduction",
        "config": BASELINES_ROOT
        / "trajectory_reduction"
        / "harbor_config.yaml",
        "mounts": [
            (
                BASELINES_ROOT
                / "fixed_adapters"
                / "agentdiet_harbor",
                "/opt/baseline_lib/agentdiet_harbor",
            ),
            (
                BASELINES_ROOT
                / "fixed_adapters"
                / "harbor_common_fixed",
                "/opt/baseline_lib/harbor_common_fixed",
            ),
        ],
    },
    "zipact": {
        "include_legacy_common": False,
        "prefix": BASELINES_ROOT / "envs" / "zipact",
        "folder": BASELINES_ROOT / "zipact",
        "config": BASELINES_ROOT / "zipact" / "harbor_config.yaml",
        "mounts": [
            (
                BASELINES_ROOT / "fixed_adapters" / "zipact_harbor",
                "/opt/baseline_lib/zipact_harbor",
            ),
            (
                BASELINES_ROOT
                / "fixed_adapters"
                / "harbor_common_fixed",
                "/opt/baseline_lib/harbor_common_fixed",
            ),
            (
                BASELINES_ROOT / "zipact" / "zipact",
                "/opt/baseline_lib/zipact",
            ),
        ],
    },
    "eet": {
        "prefix": BASELINES_ROOT / "envs" / "eet",
        "folder": BASELINES_ROOT / "eet",
        "config": BASELINES_ROOT / "eet" / "harbor_config.yaml",
        "mounts": [
            (
                BASELINES_ROOT / "eet" / "harbor_agent" / "eet_harbor",
                "/opt/baseline_lib/eet_harbor",
            ),
            (
                BASELINES_ROOT
                / "eet"
                / "mini-swe-agent"
                / "src"
                / "minisweagent"
                / "experience",
                "/opt/eet_experience",
            ),
        ],
    },
}

DEEPSWE_SOURCE_TASK = (
    PROJECT_ROOT
    / "benchmark"
    / "deep-swe"
    / "tasks"
    / "anko-default-function-arguments"
)

BENCHMARKS = {
    "swe-bench": PROJECT_ROOT
    / "tmp"
    / "harbor"
    / "datasets"
    / "swebench-verified"
    / "django__django-15382",
    "dab": PROJECT_ROOT
    / "benchmark"
    / "DBA-bench"
    / "harbor"
    / "datasets"
    / "dab"
    / "dab__github_repos__query3",
    "terminal-bench-2.1": PROJECT_ROOT
    / "tmp"
    / "harbor"
    / "datasets"
    / "terminal-bench-2-1"
    / "fix-code-vulnerability",
    "deveval": PROJECT_ROOT
    / "tmp"
    / "harbor"
    / "datasets"
    / "deveval"
    / "python-readtime-unit-testing",
}
BENCHMARK_CHOICES = (
    "dab",
    "deepswe",
    "swe-bench",
    "terminal-bench-2.1",
    "deveval",
)


def _load_llm(
    llm_config: Path = LLM_CONFIG,
) -> tuple[str, dict[str, str]]:
    data = yaml.safe_load(llm_config.read_text(encoding="utf-8"))
    api_type = str(data.get("api_type") or "chat").lower()
    key = str(data["key"])
    endpoint = str(
        data.get("azure_endpoint") or data.get("openai_base_url") or ""
    )
    if api_type in {"azure_chat", "responses"}:
        model = f"azure/{data['llm_name']}"
        variables = {
            "OPENAI_API_KEY": key,
            "MSWEA_API_KEY": key,
            "OPENAI_BASE_URL": endpoint,
            "OPENAI_API_BASE": endpoint,
            "AZURE_API_KEY": key,
            "AZURE_API_BASE": endpoint,
            "AZURE_API_VERSION": str(
                data.get("api_version") or "2024-03-01-preview"
            ),
        }
    elif api_type == "chat":
        model = f"openai/{data['llm_name']}"
        variables = {
            "OPENAI_API_KEY": key,
            "MSWEA_API_KEY": key,
            "OPENAI_BASE_URL": endpoint,
            "OPENAI_API_BASE": endpoint,
        }
    else:
        raise ValueError(f"Unsupported api_type in {llm_config}: {api_type}")
    return model, variables


def _bind(source: Path, target: str, *, read_only: bool = True) -> dict[str, Any]:
    mount: dict[str, Any] = {
        "type": "bind",
        "source": str(source.resolve()),
        "target": target,
    }
    if read_only:
        mount["read_only"] = True
    return mount


def _mounts(spec: dict[str, Any]) -> list[dict[str, Any]]:
    cache = BASELINES_ROOT / ".cache" / "uv-container"
    cache.mkdir(parents=True, exist_ok=True)
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("uv is required to install mini-swe-agent in Harbor")

    mounts = [
        # The benchmark tasks intentionally disable internet access.  Mount the
        # independently pinned baseline environment at its original absolute
        # path and expose its CLI, so Harbor's installer detects the requested
        # mini-swe-agent version without contacting PyPI.
        _bind(spec["prefix"], str(spec["prefix"])),
        _bind(
            spec["prefix"] / "bin" / "mini-swe-agent",
            "/root/.local/bin/mini-swe-agent",
        ),
        _bind(Path(uv), "/opt/baseline_toolchain/uv"),
        _bind(
            cache,
            "/opt/baseline_toolchain/uv-cache",
            read_only=False,
        ),
    ]
    if spec.get("include_legacy_common", True):
        mounts.append(
            _bind(
                BASELINES_ROOT / "harbor_common",
                "/opt/baseline_lib/harbor_common",
            )
        )
    mounts.extend(_bind(source, target) for source, target in spec["mounts"])
    return mounts


def _prepare_deepswe_task() -> Path:
    """Stage the fixed DeepSWE case with agent-only model connectivity.

    The source task disables networking for the whole environment, which also
    prevents any code agent from reaching its LLM endpoint.  Its verifier
    already runs in a separate environment, so the staged copy only changes
    the main/agent environment to public networking; verifier isolation stays
    exactly as authored.
    """

    if not DEEPSWE_SOURCE_TASK.is_dir():
        raise FileNotFoundError(
            f"DeepSWE source task is missing: {DEEPSWE_SOURCE_TASK}"
        )
    target = (
        BASELINES_ROOT
        / "runtime_tasks"
        / "deepswe"
        / DEEPSWE_SOURCE_TASK.name
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(DEEPSWE_SOURCE_TASK, target, dirs_exist_ok=True)

    task_toml = target / "task.toml"
    lines = task_toml.read_text(encoding="utf-8").splitlines()
    in_environment = False
    changed = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_environment = stripped == "[environment]"
        elif in_environment and stripped == "allow_internet = false":
            lines[index] = "allow_internet = true"
            changed = True
    if not changed:
        raise RuntimeError(
            "Expected [environment] allow_internet = false in "
            f"{DEEPSWE_SOURCE_TASK / 'task.toml'}"
        )
    task_toml.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def _benchmark_path(benchmark: str) -> Path:
    if benchmark == "deepswe":
        return _prepare_deepswe_task()
    if benchmark == "deveval":
        task = BENCHMARKS[benchmark]
        _normalize_deveval_tasks(task.parent)
    return BENCHMARKS[benchmark]


def _normalize_deveval_tasks(task_root: Path) -> None:
    subprocess.run(
        [
            "bash",
            str(PROJECT_ROOT / "scripts" / "normalize_deveval_tasks.sh"),
            str(task_root),
        ],
        check=True,
    )


def _agent_env(llm_env: dict[str, str]) -> dict[str, str]:
    proxy = "http://sys-proxy-rd-relay.byted.org:8118"
    no_proxy = (
        ".bytedance.net,bytedance.net,postgres,mongo,"
        "localhost,127.0.0.1,::1"
    )
    return {
        **llm_env,
        "MSWEA_COST_TRACKING": "ignore_errors",
        "MSWEA_MODEL_RETRY_STOP_AFTER_ATTEMPT": "3",
        "HARBOR_SHARED_UV_BIN": "/opt/baseline_toolchain/uv",
        "UV_CACHE_DIR": "/opt/baseline_toolchain/uv-cache",
        "PYTHONPATH": "/opt/baseline_lib",
        "HTTP_PROXY": proxy,
        "HTTPS_PROXY": proxy,
        "http_proxy": proxy,
        "https_proxy": proxy,
        "NO_PROXY": no_proxy,
        "no_proxy": no_proxy,
    }


def _append_env(command: list[str], flag: str, variables: dict[str, str]) -> None:
    for key, value in variables.items():
        command.extend([flag, f"{key}={value}"])


def build_command(
    baseline: str,
    benchmark: str,
    *,
    run_id: str,
    llm_config: Path = LLM_CONFIG,
) -> tuple[list[str], dict[str, str]]:
    spec = BASELINES[baseline]
    dataset = _benchmark_path(benchmark)
    if not dataset.is_dir():
        raise FileNotFoundError(f"Harbor dataset is missing: {dataset}")
    if not spec["config"].is_file():
        raise FileNotFoundError(f"Baseline config is missing: {spec['config']}")

    harbor = spec["prefix"] / "bin" / "harbor"
    if not harbor.is_file():
        raise FileNotFoundError(
            f"Harbor is not installed in {spec['prefix']}; run setup_envs.sh"
        )

    output = spec["folder"] / "results" / benchmark
    output.mkdir(parents=True, exist_ok=True)
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
        "1",
        "--n-tasks",
        "1",
        "--agent-setup-timeout-multiplier",
        "4",
        "-o",
        str(output),
        "--job-name",
        run_id,
        "--yes",
        "--ak",
        "version=2.4.5",
        "--ak",
        f"config_file={spec['config']}",
        "--mounts",
        json.dumps(_mounts(spec)),
    ]
    _append_env(command, "--ae", _agent_env(llm_env))

    verifier_proxy = {
        key: value
        for key, value in _agent_env({}).items()
        if key
        in {
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "http_proxy",
            "https_proxy",
            "NO_PROXY",
            "no_proxy",
            "UV_CACHE_DIR",
        }
    }
    # DevEval's exported verifier resolves its helper scripts through
    # $TEST_DIR but does not declare the variable in task.toml.
    verifier_proxy["TEST_DIR"] = "/tests"
    _append_env(command, "--ve", verifier_proxy)

    host_env = os.environ.copy()
    host_env["UV_CACHE_DIR"] = str(BASELINES_ROOT / ".cache" / "uv-host")
    host_env["HTTP_PROXY"] = "http://sys-proxy-rd-relay.byted.org:8118"
    host_env["HTTPS_PROXY"] = host_env["HTTP_PROXY"]
    host_env["http_proxy"] = host_env["HTTP_PROXY"]
    host_env["https_proxy"] = host_env["HTTP_PROXY"]
    host_env["NO_PROXY"] = "localhost,127.0.0.1,::1"
    host_env["no_proxy"] = host_env["NO_PROXY"]
    return command, host_env


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("baseline", choices=sorted(BASELINES))
    parser.add_argument("benchmark", choices=BENCHMARK_CHOICES)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--llm-config", type=Path, default=LLM_CONFIG)
    args = parser.parse_args()

    timestamp = datetime.now().strftime("%m%d-%H%M%S")
    run_id = args.run_id or f"{args.baseline}-{args.benchmark}-{timestamp}"
    llm_config = args.llm_config.expanduser().resolve()
    if not llm_config.is_file():
        parser.error(f"LLM config is missing: {llm_config}")
    command, host_env = build_command(
        args.baseline,
        args.benchmark,
        run_id=run_id,
        llm_config=llm_config,
    )
    print(
        json.dumps(
            {
                "baseline": args.baseline,
                "benchmark": args.benchmark,
                "run_id": run_id,
                "llm_config": str(llm_config),
                "n_tasks": 1,
            },
            indent=2,
        ),
        flush=True,
    )
    return subprocess.run(command, env=host_env, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
