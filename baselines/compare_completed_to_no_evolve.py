#!/usr/bin/env python3
"""Compare terminal baseline trials with paired no-evolve trials.

The comparison is a point-in-time snapshot.  Trial paths are captured before
any result is parsed, so cases finishing while this script runs do not leak
into the report.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import yaml


PROJECT_ROOT = Path(__file__).resolve().parent.parent
RESULTS_ROOT = PROJECT_ROOT / "results"
BASELINE_ROOT = RESULTS_ROOT / "baselines"
MATRIX_ROOT = BASELINE_ROOT / "_matrix"
MODELS = (
    "deepseekv4_flash",
    "deepseekv4_pro",
    "doubao_seed2_lite",
    "gpt5_5",
)
METHODS = ("agentdiet", "eet", "zipact")
BENCHMARKS = ("swe-bench", "deep-swe", "dab")
NO_EVOLVE_BENCHMARK = {
    "swe-bench": "swebench-verified",
    "deep-swe": "deep-swe",
    "dab": "dab",
}


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


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


def _infra_reason(exception_type: str | None, message: str) -> str | None:
    """Return only high-confidence pre-method infrastructure failures."""

    lower = message.lower()
    if exception_type == "ApiRateLimitError":
        return "api_rate_limit"
    if "insufficient balance" in lower:
        return "insufficient_balance"
    if exception_type == "AgentSetupTimeoutError":
        return "agent_setup_timeout"
    setup_command = (
        "astral.sh/uv" in lower
        or "uv tool install mini-swe-agent" in lower
    )
    setup_http_failure = (
        "requested url returned error" in lower
        or "curl: (22)" in lower
        or "error 504" in lower
    )
    if setup_command and setup_http_failure:
        return "agent_setup_http_failure"
    if any(
        marker in lower
        for marker in (
            "authentication error",
            "authenticationerror",
            "error code: 401",
            "status code: 401",
            "error code: 403",
            "status code: 403",
        )
    ):
        return "api_authentication"
    return None


def _prices(backbone: str) -> dict[str, float]:
    config_path = PROJECT_ROOT / "_config" / f"{backbone}.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    raw = config["price_yuan_per_million_token"]
    return {
        "input": float(raw["input_token"]),
        "cache": float(raw["cached_token"]),
        "output": float(raw["output_token"]),
    }


def _cost(
    input_tokens: int,
    cache_tokens: int,
    output_tokens: int,
    prices: dict[str, float],
) -> float:
    cached = min(input_tokens, cache_tokens)
    return (
        (input_tokens - cached) * prices["input"]
        + cached * prices["cache"]
        + output_tokens * prices["output"]
    ) / 1_000_000


def _message_usage(message: dict[str, Any]) -> tuple[int, int, int]:
    """Normalize chat-completions and Responses API usage shapes."""

    extra = message.get("extra") or {}
    response = extra.get("response") or {}
    usage = response.get("usage") or {}
    if not usage and message.get("object") == "response":
        usage = message.get("usage") or {}
    details = usage.get("prompt_tokens_details") or usage.get(
        "input_tokens_details"
    ) or {}
    input_tokens = int(
        usage.get("prompt_tokens") or usage.get("input_tokens") or 0
    )
    output_tokens = int(
        usage.get("completion_tokens") or usage.get("output_tokens") or 0
    )
    cache_tokens = int(
        details.get("cached_tokens")
        or usage.get("prompt_cache_hit_tokens")
        or usage.get("cache_read_input_tokens")
        or 0
    )
    return input_tokens, cache_tokens, output_tokens


def _trajectory_usage(
    trial_result_path: Path,
    prices: dict[str, float],
) -> dict[str, Any] | None:
    trajectory_path = (
        trial_result_path.parent
        / "agent"
        / "mini-swe-agent.trajectory.json"
    )
    if not trajectory_path.is_file():
        return None
    trajectory = _load_json(trajectory_path)
    phases: dict[str, dict[str, int | float]] = defaultdict(
        lambda: {
            "api_calls_with_usage": 0,
            "n_input_tokens": 0,
            "n_cache_tokens": 0,
            "n_output_tokens": 0,
            "cost_yuan": 0.0,
        }
    )
    total_input = 0
    total_cache = 0
    total_output = 0
    for message in trajectory.get("messages") or []:
        input_tokens, cache_tokens, output_tokens = _message_usage(message)
        if input_tokens == 0 and output_tokens == 0:
            continue
        extra = message.get("extra") or {}
        phase = str(extra.get("baseline_phase") or "coding")
        # Calls removed from AgentDiet's active context are still ordinary
        # coding calls.  The archive marker exists precisely to retain usage.
        if extra.get("baseline_archived_erased_call"):
            phase = "coding"
        bucket = phases[phase]
        bucket["api_calls_with_usage"] += 1
        bucket["n_input_tokens"] += input_tokens
        bucket["n_cache_tokens"] += cache_tokens
        bucket["n_output_tokens"] += output_tokens
        total_input += input_tokens
        total_cache += cache_tokens
        total_output += output_tokens

    for bucket in phases.values():
        bucket["cost_yuan"] = _cost(
            int(bucket["n_input_tokens"]),
            int(bucket["n_cache_tokens"]),
            int(bucket["n_output_tokens"]),
            prices,
        )
    return {
        "trajectory_path": str(trajectory_path.resolve()),
        "n_input_tokens": total_input,
        "n_cache_tokens": total_cache,
        "n_output_tokens": total_output,
        "cost_yuan": _cost(
            total_input,
            total_cache,
            total_output,
            prices,
        ),
        "phases": {key: dict(value) for key, value in sorted(phases.items())},
    }


def _trial_record(path: Path, prices: dict[str, float]) -> dict[str, Any]:
    trial = _load_json(path)
    exception = trial.get("exception_info") or {}
    agent = trial.get("agent_result") or {}
    rewards = (trial.get("verifier_result") or {}).get("rewards") or {}
    try:
        reward = float(rewards.get("reward") or 0.0)
    except (TypeError, ValueError):
        reward = 0.0
    agent_input_tokens = int(agent.get("n_input_tokens") or 0)
    agent_cache_tokens = int(agent.get("n_cache_tokens") or 0)
    agent_output_tokens = int(agent.get("n_output_tokens") or 0)
    trajectory_usage = _trajectory_usage(path, prices)
    if trajectory_usage is not None:
        input_tokens = int(trajectory_usage["n_input_tokens"])
        cache_tokens = int(trajectory_usage["n_cache_tokens"])
        output_tokens = int(trajectory_usage["n_output_tokens"])
        usage_source = "final_trajectory"
        phases = trajectory_usage["phases"]
        trajectory_path = trajectory_usage["trajectory_path"]
    else:
        input_tokens = agent_input_tokens
        cache_tokens = agent_cache_tokens
        output_tokens = agent_output_tokens
        usage_source = "agent_result_fallback"
        trajectory_path = None
        fallback_cost = _cost(
            input_tokens, cache_tokens, output_tokens, prices
        )
        phases = {
            "unattributed_agent_result": {
                "api_calls_with_usage": None,
                "n_input_tokens": input_tokens,
                "n_cache_tokens": cache_tokens,
                "n_output_tokens": output_tokens,
                "cost_yuan": fallback_cost,
            }
        }
    exception_type = exception.get("exception_type")
    exception_message = str(exception.get("exception_message") or "")
    return {
        "case_id": _case_id(trial),
        "trial_result": str(path.resolve()),
        "reward": reward,
        "solved": reward >= 1.0,
        "n_input_tokens": input_tokens,
        "n_cache_tokens": cache_tokens,
        "n_output_tokens": output_tokens,
        "cost_yuan": _cost(
            input_tokens,
            cache_tokens,
            output_tokens,
            prices,
        ),
        "usage_source": usage_source,
        "trajectory_path": trajectory_path,
        "phase_usage": phases,
        "agent_result_n_input_tokens": agent_input_tokens,
        "agent_result_n_cache_tokens": agent_cache_tokens,
        "agent_result_n_output_tokens": agent_output_tokens,
        "agent_result_cost_yuan": _cost(
            agent_input_tokens,
            agent_cache_tokens,
            agent_output_tokens,
            prices,
        ),
        "trajectory_minus_agent_result_cost_yuan": _cost(
            input_tokens,
            cache_tokens,
            output_tokens,
            prices,
        )
        - _cost(
            agent_input_tokens,
            agent_cache_tokens,
            agent_output_tokens,
            prices,
        ),
        "exception_type": exception_type,
        "infra_reason": _infra_reason(exception_type, exception_message),
    }


def _job_complete(job_dir: Path, expected: int) -> bool:
    path = job_dir / "result.json"
    if not path.is_file():
        return False
    result = _load_json(path)
    stats = result.get("stats") or {}
    return (
        int(result.get("n_total_trials") or 0) == expected
        and int(stats.get("n_completed_trials") or 0) == expected
        and int(stats.get("n_running_trials") or 0) == 0
        and int(stats.get("n_pending_trials") or 0) == 0
    )


def _choose_no_evolve_job(
    backbone: str, benchmark: str
) -> tuple[Path, list[dict[str, Any]]]:
    root = (
        RESULTS_ROOT
        / backbone
        / "evolve16_evalall"
        / "no_evolve"
        / NO_EVOLVE_BENCHMARK[benchmark]
    )
    candidates: list[tuple[str, Path, dict[str, Any]]] = []
    for job_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        result_path = job_dir / "result.json"
        if not result_path.is_file():
            continue
        result = _load_json(result_path)
        stats = result.get("stats") or {}
        total = int(result.get("n_total_trials") or 0)
        complete = (
            total > 0
            and int(stats.get("n_completed_trials") or 0) == total
            and int(stats.get("n_running_trials") or 0) == 0
            and int(stats.get("n_pending_trials") or 0) == 0
        )
        if complete:
            candidates.append(
                (str(result.get("finished_at") or ""), job_dir, result)
            )
    if not candidates:
        raise FileNotFoundError(
            f"No complete no-evolve job for {backbone}/{benchmark}: {root}"
        )
    candidates.sort(key=lambda item: (item[0], item[1].name))
    chosen = candidates[-1][1]
    audit = []
    for finished_at, job_dir, result in candidates:
        stats = result.get("stats") or {}
        audit.append(
            {
                "job_dir": str(job_dir.resolve()),
                "finished_at": finished_at,
                "n_total_trials": result.get("n_total_trials"),
                "n_errored_trials": stats.get("n_errored_trials"),
                "selected": job_dir == chosen,
            }
        )
    return chosen, audit


def _metrics(pairs: list[dict[str, Any]], *, clean: bool) -> dict[str, Any]:
    selected = [
        pair
        for pair in pairs
        if not clean
        or (
            pair["baseline_infra_reason"] is None
            and pair["no_evolve_infra_reason"] is None
        )
    ]
    n = len(selected)
    if not n:
        return {
            "n": 0,
            "baseline_accuracy": None,
            "no_evolve_accuracy": None,
            "accuracy_delta_pp": None,
            "baseline_solved": 0,
            "no_evolve_solved": 0,
            "baseline_avg_cost_yuan": None,
            "no_evolve_avg_cost_yuan": None,
            "baseline_total_cost_yuan": 0.0,
            "no_evolve_total_cost_yuan": 0.0,
            "cost_delta_yuan": 0.0,
            "cost_change_pct": None,
            "cost_saving_pct": None,
            "baseline_total_input_tokens": 0,
            "baseline_total_cache_tokens": 0,
            "baseline_total_output_tokens": 0,
            "no_evolve_total_input_tokens": 0,
            "no_evolve_total_cache_tokens": 0,
            "no_evolve_total_output_tokens": 0,
            "baseline_max_case_cost_yuan": None,
            "no_evolve_max_case_cost_yuan": None,
            "outcomes": {
                "both_solved": 0,
                "baseline_only": 0,
                "no_evolve_only": 0,
                "neither": 0,
            },
        }
    baseline_accuracy = sum(pair["baseline_reward"] for pair in selected) / n
    no_evolve_accuracy = (
        sum(pair["no_evolve_reward"] for pair in selected) / n
    )
    baseline_avg_cost = (
        sum(pair["baseline_cost_yuan"] for pair in selected) / n
    )
    no_evolve_avg_cost = (
        sum(pair["no_evolve_cost_yuan"] for pair in selected) / n
    )
    baseline_total_cost = sum(
        pair["baseline_cost_yuan"] for pair in selected
    )
    no_evolve_total_cost = sum(
        pair["no_evolve_cost_yuan"] for pair in selected
    )
    outcomes = Counter()
    for pair in selected:
        baseline_solved = pair["baseline_solved"]
        no_evolve_solved = pair["no_evolve_solved"]
        if baseline_solved and no_evolve_solved:
            outcomes["both_solved"] += 1
        elif baseline_solved:
            outcomes["baseline_only"] += 1
        elif no_evolve_solved:
            outcomes["no_evolve_only"] += 1
        else:
            outcomes["neither"] += 1
    return {
        "n": n,
        "baseline_accuracy": baseline_accuracy,
        "no_evolve_accuracy": no_evolve_accuracy,
        "accuracy_delta_pp": 100 * (
            baseline_accuracy - no_evolve_accuracy
        ),
        "baseline_solved": sum(
            pair["baseline_solved"] for pair in selected
        ),
        "no_evolve_solved": sum(
            pair["no_evolve_solved"] for pair in selected
        ),
        "baseline_avg_cost_yuan": baseline_avg_cost,
        "no_evolve_avg_cost_yuan": no_evolve_avg_cost,
        "baseline_total_cost_yuan": baseline_total_cost,
        "no_evolve_total_cost_yuan": no_evolve_total_cost,
        "cost_delta_yuan": baseline_total_cost - no_evolve_total_cost,
        "cost_change_pct": (
            100 * (baseline_total_cost / no_evolve_total_cost - 1)
            if no_evolve_total_cost > 0
            else None
        ),
        "cost_saving_pct": (
            100 * (1 - baseline_avg_cost / no_evolve_avg_cost)
            if no_evolve_avg_cost > 0
            else None
        ),
        "baseline_total_input_tokens": sum(
            pair["baseline_n_input_tokens"] for pair in selected
        ),
        "baseline_total_cache_tokens": sum(
            pair["baseline_n_cache_tokens"] for pair in selected
        ),
        "baseline_total_output_tokens": sum(
            pair["baseline_n_output_tokens"] for pair in selected
        ),
        "no_evolve_total_input_tokens": sum(
            pair["no_evolve_n_input_tokens"] for pair in selected
        ),
        "no_evolve_total_cache_tokens": sum(
            pair["no_evolve_n_cache_tokens"] for pair in selected
        ),
        "no_evolve_total_output_tokens": sum(
            pair["no_evolve_n_output_tokens"] for pair in selected
        ),
        "baseline_max_case_cost_yuan": max(
            pair["baseline_cost_yuan"] for pair in selected
        ),
        "no_evolve_max_case_cost_yuan": max(
            pair["no_evolve_cost_yuan"] for pair in selected
        ),
        "outcomes": {
            key: int(outcomes.get(key, 0))
            for key in (
                "both_solved",
                "baseline_only",
                "no_evolve_only",
                "neither",
            )
        },
    }


def _summarize_group(
    label: dict[str, Any], pairs: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        **label,
        "raw": _metrics(pairs, clean=False),
        "infra_clean": _metrics(pairs, clean=True),
    }


def _aggregate(
    pairs: list[dict[str, Any]],
    keys: tuple[str, ...],
    *,
    complete_jobs_only: bool = False,
) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for pair in pairs:
        if complete_jobs_only and not pair["baseline_job_complete"]:
            continue
        groups[tuple(pair[key] for key in keys)].append(pair)
    rows = []
    for values, selected in sorted(groups.items()):
        label = dict(zip(keys, values))
        rows.append(_summarize_group(label, selected))
    return rows


def _aggregate_phase_usage(
    pairs: list[dict[str, Any]],
    field: str,
) -> dict[str, dict[str, int | float | None]]:
    phases: dict[str, dict[str, int | float | None]] = defaultdict(
        lambda: {
            "api_calls_with_usage": 0,
            "n_input_tokens": 0,
            "n_cache_tokens": 0,
            "n_output_tokens": 0,
            "cost_yuan": 0.0,
        }
    )
    for pair in pairs:
        for phase, usage in (pair.get(field) or {}).items():
            bucket = phases[phase]
            calls = usage.get("api_calls_with_usage")
            if calls is None:
                bucket["api_calls_with_usage"] = None
            elif bucket["api_calls_with_usage"] is not None:
                bucket["api_calls_with_usage"] = int(
                    bucket["api_calls_with_usage"]
                ) + int(calls)
            for key in (
                "n_input_tokens",
                "n_cache_tokens",
                "n_output_tokens",
            ):
                bucket[key] = int(bucket[key] or 0) + int(
                    usage.get(key) or 0
                )
            bucket["cost_yuan"] = float(bucket["cost_yuan"] or 0.0) + float(
                usage.get("cost_yuan") or 0.0
            )
    return {key: dict(value) for key, value in sorted(phases.items())}


def _fmt_pct(value: float | None) -> str:
    return "—" if value is None else f"{100 * value:.2f}%"


def _fmt_pp(value: float | None) -> str:
    return "—" if value is None else f"{value:+.2f}"


def _fmt_cost(value: float | None) -> str:
    return "—" if value is None else f"{value:.4f}"


def _fmt_saving(value: float | None) -> str:
    return "—" if value is None else f"{value:+.2f}%"


def _fmt_total_cost(value: float | None) -> str:
    return "—" if value is None else f"{value:,.2f}"


def _fmt_token_triplet(metrics: dict[str, Any], prefix: str) -> str:
    return "/".join(
        f"{float(metrics[f'{prefix}_total_{kind}_tokens']) / 1_000_000:.3f}"
        for kind in ("input", "cache", "output")
    )


def _phase_value(
    row: dict[str, Any], phase: str, field: str
) -> int | float | None:
    usage = (row.get("baseline_phase_usage") or {}).get(phase) or {}
    return usage.get(field)


def _fmt_calls(value: int | float | None) -> str:
    return "—" if value is None else f"{int(value):,}"


def _fmt_exception(value: Any) -> str:
    return str(value or "—").replace("|", "\\|")


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# 已终态 Baseline 与 No-evolve：分 Benchmark / Backbone 全流程成本报告",
        "",
        f"- 快照时间：`{report['snapshot_at']}`",
        f"- Matrix：`{report['matrix_id']}`",
        f"- 已终态 baseline trials：{report['coverage']['terminal_baseline_trials']}",
        f"- 成功匹配 no-evolve：{report['coverage']['matched_pairs']}",
        f"- 严格 infra-clean pairs：{report['coverage']['infra_clean_pairs']}",
        f"- 完整 baseline jobs：{report['coverage']['complete_jobs']}/{report['coverage']['observed_jobs']}",
        "",
        "**主口径是 Raw 全量口径：所有已终态 case 均计入准确率和成本，包括异常高成本、失败、超时、限流和余额不足；没有删除、截尾、Winsorize 或离群值过滤。**",
        "",
        "Accuracy 是相同 backbone、benchmark、case ID 配对后的主 `reward` 均值。Baseline 成本取最终 trajectory 内全部带 usage 的 LLM 调用（coding 与 baseline 内部过程）；仅当 trajectory 不存在时回退到 `agent_result`。金额单位为人民币。",
        "",
    ]
    benchmark_labels = {
        "swe-bench": "SWE-bench Verified",
        "deep-swe": "DeepSWE",
        "dab": "DAB",
    }
    for benchmark in BENCHMARKS:
        lines.extend(
            [
                f"## {benchmark_labels[benchmark]}",
                "",
                "| Backbone | Baseline | 状态 | Raw n/计划 | Raw acc B/N | Δ pp | Clean acc B/N (n) | 全流程总成本 B/N (¥) | 成本 Δ (¥) | Δ % | 平均成本 B/N (¥/case) | Baseline 最大单 case (¥) | Baseline tokens I/C/O (M) |",
                "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for row in report["by_job"]:
            if row["benchmark"] != benchmark:
                continue
            raw = row["raw"]
            clean = row["infra_clean"]
            state = "complete" if row["job_complete"] else "partial"
            lines.append(
                f"| {row['backbone']} | {row['method']} | {state} "
                f"| {raw['n']}/{row['expected_cases']} "
                f"| {_fmt_pct(raw['baseline_accuracy'])}/"
                f"{_fmt_pct(raw['no_evolve_accuracy'])} "
                f"| {_fmt_pp(raw['accuracy_delta_pp'])} "
                f"| {_fmt_pct(clean['baseline_accuracy'])}/"
                f"{_fmt_pct(clean['no_evolve_accuracy'])} ({clean['n']}) "
                f"| {_fmt_total_cost(raw['baseline_total_cost_yuan'])}/"
                f"{_fmt_total_cost(raw['no_evolve_total_cost_yuan'])} "
                f"| {_fmt_total_cost(raw['cost_delta_yuan'])} "
                f"| {_fmt_saving(raw['cost_change_pct'])} "
                f"| {_fmt_cost(raw['baseline_avg_cost_yuan'])}/"
                f"{_fmt_cost(raw['no_evolve_avg_cost_yuan'])} "
                f"| {_fmt_cost(raw['baseline_max_case_cost_yuan'])} "
                f"| {_fmt_token_triplet(raw, 'baseline')} |"
            )
        lines.append("")

    lines.extend(
        [
            "## 按 Baseline 汇总（当前所有已终态 case，Raw）",
            "",
            "| Baseline | Trials | Acc B/N | Δ pp | 全流程总成本 B/N (¥) | 成本 Δ (¥) | Δ % | 平均成本 B/N (¥/case) |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in report["all_terminal_by_method"]:
        raw = row["raw"]
        lines.append(
            f"| {row['method']} | {raw['n']} "
            f"| {_fmt_pct(raw['baseline_accuracy'])}/"
            f"{_fmt_pct(raw['no_evolve_accuracy'])} "
            f"| {_fmt_pp(raw['accuracy_delta_pp'])} "
            f"| {_fmt_total_cost(raw['baseline_total_cost_yuan'])}/"
            f"{_fmt_total_cost(raw['no_evolve_total_cost_yuan'])} "
            f"| {_fmt_total_cost(raw['cost_delta_yuan'])} "
            f"| {_fmt_saving(raw['cost_change_pct'])} "
            f"| {_fmt_cost(raw['baseline_avg_cost_yuan'])}/"
            f"{_fmt_cost(raw['no_evolve_avg_cost_yuan'])} |"
        )
    lines.append("")

    lines.extend(
        [
            "",
            "## Baseline 全流程成本拆分",
            "",
            "下表仍是 Raw 全量；括号内是该阶段有 usage 的 API call 数。`coding` 包括 AgentDiet 为缩短上下文而归档、但真实发生过的 coding 调用。",
            "",
            "| Benchmark | Backbone | Baseline | 全流程总成本 (¥) | Coding ¥ (calls) | AgentDiet compression ¥ (calls) | ZipAct init ¥ (calls) | ZipAct update ¥ (calls) | Fallback ¥ |",
            "|---|---|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in report["by_job"]:
        raw = row["raw"]

        def phase_cell(phase: str) -> str:
            cost = _phase_value(row, phase, "cost_yuan")
            calls = _phase_value(row, phase, "api_calls_with_usage")
            return (
                f"{_fmt_total_cost(float(cost or 0.0))} "
                f"({_fmt_calls(calls or 0)})"
            )

        lines.append(
            f"| {row['benchmark']} | {row['backbone']} | {row['method']} "
            f"| {_fmt_total_cost(raw['baseline_total_cost_yuan'])} "
            f"| {phase_cell('coding')} "
            f"| {phase_cell('agentdiet_compression')} "
            f"| {phase_cell('zipact_initializer')} "
            f"| {phase_cell('zipact_state_updater')} "
            f"| {_fmt_total_cost(float(_phase_value(row, 'unattributed_agent_result', 'cost_yuan') or 0.0))} |"
        )
    lines.extend(
        [
            "",
            "## Usage 完整性审计",
            "",
            "| Side | Trials | Final trajectory | `agent_result` fallback | 经 trajectory 补正的 trials | 相对 `agent_result` 增加成本 (¥) | 最终全流程成本 (¥) |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for side, audit in report["usage_accounting_audit"].items():
        sources = audit["usage_source_counts"]
        lines.append(
            f"| {side} | {audit['trials']} "
            f"| {sources.get('final_trajectory', 0)} "
            f"| {sources.get('agent_result_fallback', 0)} "
            f"| {audit['trials_with_trajectory_correction']} "
            f"| {_fmt_total_cost(audit['trajectory_correction_cost_yuan'])} "
            f"| {_fmt_total_cost(audit['all_process_cost_yuan'])} |"
        )
    lines.extend(
        [
            "",
            "## 最高成本 case 审计（未剔除）",
            "",
            "| Rank | Benchmark | Backbone | Baseline | Case | 成本 (¥) | Reward | Exception | Infra tag |",
            "|---:|---|---|---|---|---:|---:|---|---|",
        ]
    )
    for rank, case in enumerate(report["highest_cost_cases"][:20], start=1):
        lines.append(
            f"| {rank} | {case['benchmark']} | {case['backbone']} "
            f"| {case['method']} | {_fmt_exception(case['case_id'])} "
            f"| {_fmt_cost(case['cost_yuan'])} | {case['reward']:.3f} "
            f"| {_fmt_exception(case['exception_type'])} "
            f"| {_fmt_exception(case['infra_reason'])} |"
        )
    lines.extend(
        [
            "",
            "## 基础设施剔除审计",
            "",
            "这里只展示 clean 准确率敏感性分析会标记的 case；这些 case 在 Raw 准确率和所有成本汇总中仍然保留。",
            "",
            "| Side | Reason | Cases |",
            "|---|---|---:|",
        ]
    )
    for row in report["infra_exclusions"]:
        lines.append(f"| {row['side']} | {row['reason']} | {row['count']} |")
    if not report["infra_exclusions"]:
        lines.append("| — | — | 0 |")
    lines.extend(
        [
            "",
            "## 口径与限制",
            "",
            "- Raw 是主结论：所有终态错误按实际 reward（通常为 0）计入，所有实际 API 用量计入成本；异常高成本 case 不删除。",
            "- Infra-clean 仅是准确率敏感性分析，标记 API 限流、余额不足、鉴权失败、agent setup 超时或 setup HTTP 失败；绝不用于扣减成本。",
            "- AgentTimeout、VerifierTimeout、RewardFileNotFound、ZipAct context/state explosion 均保留为方法/端到端结果。",
            "- 没有使用离群值删除、成本上限、截尾均值、Winsorization 或事后筛选。",
            "- 最终 trajectory 是全流程 usage 的权威来源，覆盖 coding、AgentDiet compression、ZipAct initializer/state updater；缺失 trajectory 时才使用 `agent_result` fallback。",
            "- 进行中作业只观察到更早结束的 case，存在 completion/censoring bias。因此 complete 行可以直接作为完整作业结论；partial 行仅表示当前快照。",
            "- GPT-5.5/DAB 的 no-evolve 有两个完整作业：报告按 `finished_at` 选择后完成且 0 异常的 `0721-150842`，舍弃早期 104/104 异常作业。",
            "- 成本公式：`((input-cache)×input_price + cache×cache_price + output×output_price)/1e6`；输入、缓存、输出价格取对应 `_config/<backbone>.yaml`。",
            "- 本报告是快照，不包含尚在运行的 case，也不包含未来基础设施补跑的额外开销或补跑后 reward。",
            "",
        ]
    )
    return "\n".join(lines)


def compare(args: argparse.Namespace) -> int:
    matrix_dir = MATRIX_ROOT / args.matrix_id
    if not (matrix_dir / "plan.json").is_file():
        raise FileNotFoundError(f"Matrix plan is missing: {matrix_dir}")

    snapshot_at = datetime.now().astimezone()
    stamp = snapshot_at.strftime("%Y%m%d-%H%M%S")
    output_dir = (
        matrix_dir / "comparisons" / f"completed-vs-no-evolve-{stamp}"
    )
    if output_dir.exists():
        raise FileExistsError(output_dir)

    baseline_jobs: list[dict[str, Any]] = []
    snapshot_paths: dict[str, list[Path]] = {}
    for backbone in MODELS:
        for method in METHODS:
            expected = 16 if method == "zipact" else 64
            for benchmark in BENCHMARKS:
                job_name = (
                    f"{args.matrix_id}-{backbone}-{method}-"
                    f"{benchmark}-n{expected}"
                )
                job_dir = BASELINE_ROOT / method / job_name
                if not job_dir.is_dir():
                    continue
                paths = sorted(job_dir.glob("*/result.json"))
                if not paths:
                    continue
                snapshot_paths[job_name] = paths
                baseline_jobs.append(
                    {
                        "backbone": backbone,
                        "method": method,
                        "benchmark": benchmark,
                        "expected_cases": expected,
                        "job_dir": job_dir,
                        "job_name": job_name,
                        "job_complete": _job_complete(job_dir, expected),
                    }
                )

    no_evolve_cache: dict[tuple[str, str], dict[str, Path]] = {}
    no_evolve_record_cache: dict[Path, dict[str, Any]] = {}
    no_evolve_selection: dict[str, Any] = {}
    for backbone in MODELS:
        for benchmark in BENCHMARKS:
            job_dir, audit = _choose_no_evolve_job(backbone, benchmark)
            records: dict[str, Path] = {}
            for path in sorted(job_dir.glob("*/result.json")):
                case_id = _case_id(_load_json(path))
                if case_id in records:
                    raise RuntimeError(
                        f"Duplicate no-evolve case {case_id} in {job_dir}"
                    )
                records[case_id] = path
            no_evolve_cache[(backbone, benchmark)] = records
            no_evolve_selection[f"{backbone}/{benchmark}"] = {
                "selected_job_dir": str(job_dir.resolve()),
                "selected_case_count": len(records),
                "candidates": audit,
            }

    pairs: list[dict[str, Any]] = []
    missing_pairs: list[dict[str, str]] = []
    job_rows: list[dict[str, Any]] = []
    manifest_jobs: list[dict[str, Any]] = []
    for job in baseline_jobs:
        prices = _prices(job["backbone"])
        job_pairs: list[dict[str, Any]] = []
        for path in snapshot_paths[job["job_name"]]:
            baseline = _trial_record(path, prices)
            no_evolve_path = no_evolve_cache[
                (job["backbone"], job["benchmark"])
            ].get(baseline["case_id"])
            if no_evolve_path is None:
                missing_pairs.append(
                    {
                        "backbone": job["backbone"],
                        "method": job["method"],
                        "benchmark": job["benchmark"],
                        "case_id": baseline["case_id"],
                    }
                )
                continue
            no_evolve = no_evolve_record_cache.get(no_evolve_path)
            if no_evolve is None:
                no_evolve = _trial_record(no_evolve_path, prices)
                no_evolve_record_cache[no_evolve_path] = no_evolve
            pair = {
                "backbone": job["backbone"],
                "method": job["method"],
                "benchmark": job["benchmark"],
                "case_id": baseline["case_id"],
                "baseline_job_complete": job["job_complete"],
                "baseline_reward": baseline["reward"],
                "baseline_solved": baseline["solved"],
                "baseline_cost_yuan": baseline["cost_yuan"],
                "baseline_n_input_tokens": baseline["n_input_tokens"],
                "baseline_n_cache_tokens": baseline["n_cache_tokens"],
                "baseline_n_output_tokens": baseline["n_output_tokens"],
                "baseline_usage_source": baseline["usage_source"],
                "baseline_phase_usage": baseline["phase_usage"],
                "baseline_agent_result_cost_yuan": baseline[
                    "agent_result_cost_yuan"
                ],
                "baseline_trajectory_minus_agent_result_cost_yuan": baseline[
                    "trajectory_minus_agent_result_cost_yuan"
                ],
                "baseline_exception_type": baseline["exception_type"],
                "baseline_infra_reason": baseline["infra_reason"],
                "baseline_trial_result": baseline["trial_result"],
                "no_evolve_reward": no_evolve["reward"],
                "no_evolve_solved": no_evolve["solved"],
                "no_evolve_cost_yuan": no_evolve["cost_yuan"],
                "no_evolve_n_input_tokens": no_evolve["n_input_tokens"],
                "no_evolve_n_cache_tokens": no_evolve["n_cache_tokens"],
                "no_evolve_n_output_tokens": no_evolve["n_output_tokens"],
                "no_evolve_usage_source": no_evolve["usage_source"],
                "no_evolve_phase_usage": no_evolve["phase_usage"],
                "no_evolve_agent_result_cost_yuan": no_evolve[
                    "agent_result_cost_yuan"
                ],
                "no_evolve_trajectory_minus_agent_result_cost_yuan": no_evolve[
                    "trajectory_minus_agent_result_cost_yuan"
                ],
                "no_evolve_exception_type": no_evolve["exception_type"],
                "no_evolve_infra_reason": no_evolve["infra_reason"],
                "no_evolve_trial_result": no_evolve["trial_result"],
            }
            pairs.append(pair)
            job_pairs.append(pair)
        row = _summarize_group(
            {
                "backbone": job["backbone"],
                "method": job["method"],
                "benchmark": job["benchmark"],
                "job_complete": job["job_complete"],
                "expected_cases": job["expected_cases"],
            },
            job_pairs,
        )
        row["baseline_phase_usage"] = _aggregate_phase_usage(
            job_pairs, "baseline_phase_usage"
        )
        row["highest_cost_cases"] = [
            {
                "case_id": pair["case_id"],
                "cost_yuan": pair["baseline_cost_yuan"],
                "reward": pair["baseline_reward"],
                "exception_type": pair["baseline_exception_type"],
                "infra_reason": pair["baseline_infra_reason"],
            }
            for pair in sorted(
                job_pairs,
                key=lambda pair: pair["baseline_cost_yuan"],
                reverse=True,
            )[:5]
        ]
        job_rows.append(row)
        manifest_jobs.append(
            {
                **{
                    key: value
                    for key, value in job.items()
                    if key not in {"job_dir"}
                },
                "job_dir": str(job["job_dir"].resolve()),
                "snapshot_trial_count": len(
                    snapshot_paths[job["job_name"]]
                ),
                "snapshot_trial_results": [
                    str(path.resolve())
                    for path in snapshot_paths[job["job_name"]]
                ],
            }
        )

    infra_counts: Counter[tuple[str, str]] = Counter()
    for pair in pairs:
        if pair["baseline_infra_reason"]:
            infra_counts[
                ("baseline", pair["baseline_infra_reason"])
            ] += 1
        if pair["no_evolve_infra_reason"]:
            infra_counts[
                ("no-evolve", pair["no_evolve_infra_reason"])
            ] += 1
    infra_exclusions = [
        {"side": side, "reason": reason, "count": count}
        for (side, reason), count in sorted(infra_counts.items())
    ]

    complete_jobs_by_method = _aggregate(
        pairs, ("method",), complete_jobs_only=True
    )
    all_terminal_by_method = _aggregate(pairs, ("method",))
    by_backbone_method = _aggregate(
        pairs, ("backbone", "method"), complete_jobs_only=True
    )
    phase_usage_by_method = []
    for method in METHODS:
        method_pairs = [pair for pair in pairs if pair["method"] == method]
        if method_pairs:
            phase_usage_by_method.append(
                {
                    "method": method,
                    "terminal_trials": len(method_pairs),
                    "phases": _aggregate_phase_usage(
                        method_pairs, "baseline_phase_usage"
                    ),
                }
            )

    highest_cost_cases = [
        {
            "backbone": pair["backbone"],
            "method": pair["method"],
            "benchmark": pair["benchmark"],
            "case_id": pair["case_id"],
            "cost_yuan": pair["baseline_cost_yuan"],
            "reward": pair["baseline_reward"],
            "exception_type": pair["baseline_exception_type"],
            "infra_reason": pair["baseline_infra_reason"],
            "trial_result": pair["baseline_trial_result"],
        }
        for pair in sorted(
            pairs,
            key=lambda pair: pair["baseline_cost_yuan"],
            reverse=True,
        )[:30]
    ]

    baseline_audit_rows = pairs
    no_evolve_unique = {}
    for pair in pairs:
        no_evolve_unique[pair["no_evolve_trial_result"]] = pair

    def usage_audit(
        rows: Iterable[dict[str, Any]], prefix: str
    ) -> dict[str, Any]:
        selected = list(rows)
        correction_key = (
            f"{prefix}_trajectory_minus_agent_result_cost_yuan"
        )
        source_key = f"{prefix}_usage_source"
        cost_key = f"{prefix}_cost_yuan"
        agent_cost_key = f"{prefix}_agent_result_cost_yuan"
        corrections = [
            float(row[correction_key] or 0.0) for row in selected
        ]
        return {
            "trials": len(selected),
            "usage_source_counts": dict(
                sorted(Counter(row[source_key] for row in selected).items())
            ),
            "trials_with_trajectory_correction": sum(
                abs(value) > 1e-12 for value in corrections
            ),
            "trajectory_correction_cost_yuan": sum(corrections),
            "all_process_cost_yuan": sum(
                float(row[cost_key]) for row in selected
            ),
            "agent_result_snapshot_cost_yuan": sum(
                float(row[agent_cost_key]) for row in selected
            ),
        }

    usage_accounting_audit = {
        "baseline": usage_audit(baseline_audit_rows, "baseline"),
        "no_evolve_unique_trials": usage_audit(
            no_evolve_unique.values(), "no_evolve"
        ),
    }
    clean_pairs = sum(
        pair["baseline_infra_reason"] is None
        and pair["no_evolve_infra_reason"] is None
        for pair in pairs
    )
    report = {
        "schema_version": "baseline.completed-vs-no-evolve.v1",
        "matrix_id": args.matrix_id,
        "snapshot_at": snapshot_at.isoformat(),
        "comparison_policy": {
            "pairing": "same backbone + benchmark + case_id",
            "no_evolve_root": (
                "results/<backbone>/evolve16_evalall/no_evolve/"
                "<benchmark>"
            ),
            "no_evolve_job_selection": (
                "latest finished complete job"
            ),
            "accuracy": "mean primary reward",
            "api_cost_scope": (
                "all usage-bearing calls in the final trajectory for every "
                "terminal case, including failures and high-cost cases; "
                "agent_result is used only when the trajectory is absent"
            ),
            "infra_clean_excludes": [
                "api_rate_limit",
                "insufficient_balance",
                "api_authentication",
                "agent_setup_timeout",
                "agent_setup_http_failure",
            ],
            "cost_currency": "CNY",
            "cost_formula": (
                "((input-cache)*input_price + cache*cache_price + "
                "output*output_price)/1e6"
            ),
        },
        "coverage": {
            "observed_jobs": len(baseline_jobs),
            "complete_jobs": sum(job["job_complete"] for job in baseline_jobs),
            "terminal_baseline_trials": sum(
                len(paths) for paths in snapshot_paths.values()
            ),
            "matched_pairs": len(pairs),
            "missing_pairs": len(missing_pairs),
            "infra_clean_pairs": clean_pairs,
        },
        "complete_jobs_by_method": complete_jobs_by_method,
        "all_terminal_by_method": all_terminal_by_method,
        "complete_jobs_by_backbone_method": by_backbone_method,
        "phase_usage_by_method": phase_usage_by_method,
        "by_job": sorted(
            job_rows,
            key=lambda row: (
                row["backbone"],
                row["method"],
                row["benchmark"],
            ),
        ),
        "infra_exclusions": infra_exclusions,
        "highest_cost_cases": highest_cost_cases,
        "usage_accounting_audit": usage_accounting_audit,
        "no_evolve_selection": no_evolve_selection,
        "missing_pairs": missing_pairs,
    }

    output_dir.mkdir(parents=True)
    report_path = output_dir / "report.md"
    json_path = output_dir / "report.json"
    csv_path = output_dir / "paired_cases.csv"
    manifest_path = output_dir / "snapshot_manifest.json"
    _atomic_write(report_path, _markdown(report))
    _atomic_write(
        json_path, json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )
    _atomic_write(
        manifest_path,
        json.dumps(
            {
                "schema_version": "baseline.comparison-snapshot.v1",
                "matrix_id": args.matrix_id,
                "snapshot_at": snapshot_at.isoformat(),
                "baseline_jobs": manifest_jobs,
                "no_evolve_selection": no_evolve_selection,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )
    fieldnames = [
        "backbone",
        "method",
        "benchmark",
        "case_id",
        "baseline_job_complete",
        "baseline_reward",
        "no_evolve_reward",
        "baseline_n_input_tokens",
        "baseline_n_cache_tokens",
        "baseline_n_output_tokens",
        "no_evolve_n_input_tokens",
        "no_evolve_n_cache_tokens",
        "no_evolve_n_output_tokens",
        "baseline_cost_yuan",
        "no_evolve_cost_yuan",
        "baseline_usage_source",
        "no_evolve_usage_source",
        "baseline_agent_result_cost_yuan",
        "no_evolve_agent_result_cost_yuan",
        "baseline_trajectory_minus_agent_result_cost_yuan",
        "no_evolve_trajectory_minus_agent_result_cost_yuan",
        "baseline_coding_cost_yuan",
        "baseline_agentdiet_compression_cost_yuan",
        "baseline_zipact_initializer_cost_yuan",
        "baseline_zipact_state_updater_cost_yuan",
        "baseline_unattributed_cost_yuan",
        "baseline_exception_type",
        "no_evolve_exception_type",
        "baseline_infra_reason",
        "no_evolve_infra_reason",
        "baseline_trial_result",
        "no_evolve_trial_result",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for pair in sorted(
            pairs,
            key=lambda row: (
                row["backbone"],
                row["method"],
                row["benchmark"],
                row["case_id"],
            ),
        ):
            phases = pair.get("baseline_phase_usage") or {}
            csv_row = {key: pair.get(key) for key in fieldnames}
            csv_row.update(
                {
                    "baseline_coding_cost_yuan": (
                        phases.get("coding") or {}
                    ).get("cost_yuan", 0.0),
                    "baseline_agentdiet_compression_cost_yuan": (
                        phases.get("agentdiet_compression") or {}
                    ).get("cost_yuan", 0.0),
                    "baseline_zipact_initializer_cost_yuan": (
                        phases.get("zipact_initializer") or {}
                    ).get("cost_yuan", 0.0),
                    "baseline_zipact_state_updater_cost_yuan": (
                        phases.get("zipact_state_updater") or {}
                    ).get("cost_yuan", 0.0),
                    "baseline_unattributed_cost_yuan": (
                        phases.get("unattributed_agent_result") or {}
                    ).get("cost_yuan", 0.0),
                }
            )
            writer.writerow(csv_row)

    print(
        json.dumps(
            {
                "output_dir": str(output_dir.resolve()),
                "report": str(report_path.resolve()),
                "json": str(json_path.resolve()),
                "csv": str(csv_path.resolve()),
                "terminal_baseline_trials": report["coverage"][
                    "terminal_baseline_trials"
                ],
                "matched_pairs": len(pairs),
                "infra_clean_pairs": clean_pairs,
                "complete_jobs": report["coverage"]["complete_jobs"],
                "observed_jobs": report["coverage"]["observed_jobs"],
            },
            ensure_ascii=False,
        )
    )
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare terminal baseline trials with no-evolve."
    )
    parser.add_argument("--matrix-id", required=True)
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(compare(parse_args()))
