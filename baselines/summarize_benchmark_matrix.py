#!/usr/bin/env python3
"""Create a case-level performance and API-cost summary for a matrix run."""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from retry_matrix_infra import (
    DEFAULT_TRANSIENT_MESSAGES,
    DEFAULT_TRANSIENT_TYPES,
    MATRIX_ROOT,
    MODEL_MAX_CONCURRENCY,
    OUTPUT_ROOT,
    PROJECT_ROOT,
    _is_transient,
    _load_json,
    _trial_records,
)


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def _prices(backbone: str) -> dict[str, float]:
    config = yaml.safe_load(
        (PROJECT_ROOT / "_config" / f"{backbone}.yaml").read_text(
            encoding="utf-8"
        )
    )
    raw = config["price_yuan_per_million_token"]
    return {
        "input": float(raw["input_token"]),
        "cache": float(raw["cached_token"]),
        "output": float(raw["output_token"]),
    }


def _record_cost(record: dict[str, Any], prices: dict[str, float]) -> float:
    input_tokens = int(record.get("n_input_tokens") or 0)
    cache_tokens = min(
        input_tokens, int(record.get("n_cache_tokens") or 0)
    )
    output_tokens = int(record.get("n_output_tokens") or 0)
    return (
        (input_tokens - cache_tokens) * prices["input"]
        + cache_tokens * prices["cache"]
        + output_tokens * prices["output"]
    ) / 1_000_000


def _reward(record: dict[str, Any]) -> float:
    value = record.get("reward")
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _exception_label(record: dict[str, Any]) -> str | None:
    exception_type = record.get("exception_type")
    if not exception_type:
        return None
    message = str(record.get("exception_message") or "").lower()
    if "insufficient balance" in message:
        return "InsufficientBalance"
    return str(exception_type)


def _retry_data(
    matrix_dir: Path, backbone: str
) -> tuple[dict[tuple[str, str], list[Path]], dict[str, Any] | None]:
    path = matrix_dir / "retries" / backbone / "manifest.json"
    if not path.is_file():
        return {}, None
    manifest = _load_json(path)
    groups: dict[tuple[str, str], list[Path]] = defaultdict(list)
    for round_record in manifest.get("rounds") or []:
        for job in round_record.get("jobs") or []:
            groups[(job["baseline"], job["benchmark"])].append(
                Path(job["job_dir"])
            )
    return dict(groups), manifest


def _job_row(
    *,
    backbone: str,
    job: dict[str, Any],
    retry_job_dirs: list[Path],
    transient_types: set[str],
    transient_messages: tuple[str, ...],
) -> dict[str, Any]:
    baseline = str(job["baseline"])
    benchmark = str(job["benchmark"])
    job_dir = Path(job["job_dir"])
    original = _trial_records(job_dir)
    expected = int(job["n_tasks"])
    prices = _prices(backbone)

    selected = dict(original)
    original_transient = {
        case_id
        for case_id, record in original.items()
        if _is_transient(record, transient_types, transient_messages)
    }
    retry_records: list[dict[str, Any]] = []
    retry_attempts = 0
    for retry_dir in retry_job_dirs:
        current = _trial_records(retry_dir)
        retry_attempts += len(current)
        retry_records.extend(current.values())
        for case_id, record in current.items():
            if case_id in original_transient:
                selected[case_id] = record

    formal_cost = sum(_record_cost(record, prices) for record in original.values())
    retry_cost = sum(_record_cost(record, prices) for record in retry_records)
    canonical_rewards = [_reward(record) for record in selected.values()]
    formal_rewards = [_reward(record) for record in original.values()]
    raw_exceptions: Counter[str] = Counter()
    for record in original.values():
        label = _exception_label(record)
        if label:
            raw_exceptions[label] += 1
    canonical_exceptions: Counter[str] = Counter()
    for record in selected.values():
        label = _exception_label(record)
        if label:
            canonical_exceptions[label] += 1
    unresolved = sorted(
        case_id
        for case_id in original_transient
        if case_id not in selected
        or _is_transient(
            selected[case_id], transient_types, transient_messages
        )
    )
    return {
        "backbone": backbone,
        "baseline": baseline,
        "benchmark": benchmark,
        "job_dir": str(job_dir.resolve()),
        "expected_cases": expected,
        "observed_cases": len(original),
        "complete": len(original) == expected,
        "formal": {
            "mean_reward": (
                sum(formal_rewards) / expected if expected else 0.0
            ),
            "solved": sum(value >= 1.0 for value in formal_rewards),
            "errors": sum(raw_exceptions.values()),
            "exception_counts": dict(sorted(raw_exceptions.items())),
            "cost_yuan": formal_cost,
            "avg_api_cost_yuan": formal_cost / expected if expected else 0.0,
        },
        "canonical_after_infra_retries": {
            "mean_reward": (
                sum(canonical_rewards) / expected if expected else 0.0
            ),
            "solved": sum(value >= 1.0 for value in canonical_rewards),
            "errors": sum(canonical_exceptions.values()),
            "exception_counts": dict(sorted(canonical_exceptions.items())),
            "infra_unresolved": unresolved,
            "actual_cost_yuan": formal_cost + retry_cost,
            "avg_actual_api_cost_yuan": (
                (formal_cost + retry_cost) / expected if expected else 0.0
            ),
        },
        "retry": {
            "original_transient_cases": len(original_transient),
            "retry_attempts": retry_attempts,
            "retry_cost_yuan": retry_cost,
            "job_dirs": [str(path.resolve()) for path in retry_job_dirs],
        },
    }


def _aggregate(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(row["backbone"], row["baseline"])].append(row)
    output: list[dict[str, Any]] = []
    for (backbone, baseline), items in sorted(groups.items()):
        cases = sum(item["expected_cases"] for item in items)
        formal = [item["formal"] for item in items]
        canonical = [item["canonical_after_infra_retries"] for item in items]
        formal_cost = sum(item["cost_yuan"] for item in formal)
        actual_cost = sum(item["actual_cost_yuan"] for item in canonical)
        output.append(
            {
                "backbone": backbone,
                "baseline": baseline,
                "benchmarks": len(items),
                "cases": cases,
                "formal_mean_reward": (
                    sum(
                        item["mean_reward"] * row["expected_cases"]
                        for item, row in zip(formal, items)
                    )
                    / cases
                    if cases
                    else 0.0
                ),
                "formal_solved": sum(item["solved"] for item in formal),
                "canonical_mean_reward": (
                    sum(
                        item["mean_reward"] * row["expected_cases"]
                        for item, row in zip(canonical, items)
                    )
                    / cases
                    if cases
                    else 0.0
                ),
                "canonical_solved": sum(item["solved"] for item in canonical),
                "canonical_errors": sum(item["errors"] for item in canonical),
                "infra_unresolved": sum(
                    len(item["infra_unresolved"]) for item in canonical
                ),
                "formal_cost_yuan": formal_cost,
                "retry_cost_yuan": actual_cost - formal_cost,
                "actual_cost_yuan": actual_cost,
                "avg_actual_api_cost_yuan": (
                    actual_cost / cases if cases else 0.0
                ),
            }
        )
    return output


def _markdown(summary: dict[str, Any]) -> str:
    lines = [
        f"# Baseline matrix summary: {summary['matrix_id']}",
        "",
        f"- State: `{summary['state']}`",
        f"- Generated: `{summary['generated_at']}`",
        f"- Main jobs: {summary['completed_main_jobs']}/{summary['expected_main_jobs']}",
        f"- Formal trials expected: {summary['expected_formal_trials']}",
        "",
        "## Per backbone and baseline",
        "",
        "| Backbone | Baseline | Cases | Canonical reward | Solved | Errors | Infra unresolved | Avg actual API cost (RMB) | Retry cost (RMB) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summary["aggregate"]:
        lines.append(
            "| {backbone} | {baseline} | {cases} | {canonical_mean_reward:.4f} "
            "| {canonical_solved} | {canonical_errors} | {infra_unresolved} "
            "| {avg_actual_api_cost_yuan:.4f} | {retry_cost_yuan:.4f} |".format(
                **row
            )
        )
    lines.extend(
        [
            "",
            "## Per benchmark",
            "",
            "| Backbone | Baseline | Benchmark | Cases | Formal reward | Canonical reward | Solved | Errors | Avg actual API cost (RMB) |",
            "|---|---|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in summary["jobs"]:
        formal = row["formal"]
        canonical = row["canonical_after_infra_retries"]
        lines.append(
            f"| {row['backbone']} | {row['baseline']} | {row['benchmark']} "
            f"| {row['expected_cases']} | {formal['mean_reward']:.4f} "
            f"| {canonical['mean_reward']:.4f} | {canonical['solved']} "
            f"| {canonical['errors']} "
            f"| {canonical['avg_actual_api_cost_yuan']:.4f} |"
        )
    lines.extend(
        [
            "",
            "Cost formula: `(non-cached input × input price + cached input × "
            "cache price + output × output price) / 1e6`. Actual cost includes "
            "all preserved formal attempts and infrastructure-retry attempts.",
            "",
        ]
    )
    return "\n".join(lines)


def summarize(args: argparse.Namespace) -> int:
    matrix_dir = MATRIX_ROOT / args.matrix_id
    plan = _load_json(matrix_dir / "plan.json")
    transient_types = set(DEFAULT_TRANSIENT_TYPES)
    transient_messages = tuple(DEFAULT_TRANSIENT_MESSAGES)
    rows: list[dict[str, Any]] = []
    status_states: dict[str, str] = {}
    completed_main_jobs = 0
    for backbone in MODEL_MAX_CONCURRENCY:
        status_path = matrix_dir / "status" / f"{backbone}.json"
        if not status_path.is_file():
            status_states[backbone] = "not_started"
            continue
        status = _load_json(status_path)
        status_states[backbone] = str(status.get("state"))
        retry_groups, _ = _retry_data(matrix_dir, backbone)
        for job in status.get("jobs") or []:
            completed_main_jobs += 1
            group = (str(job["baseline"]), str(job["benchmark"]))
            rows.append(
                _job_row(
                    backbone=backbone,
                    job=job,
                    retry_job_dirs=retry_groups.get(group, []),
                    transient_types=transient_types,
                    transient_messages=transient_messages,
                )
            )

    expected_main_jobs = len(plan["backbones"]) * 3 * len(plan["benchmarks"])
    benchmark_full_counts = []
    for value in plan["benchmarks"].values():
        if value.get("sample_full_count") is not None:
            benchmark_full_counts.append(int(value["sample_full_count"]))
            continue
        sample_manifest = _load_json(Path(value["sample_manifest"]))
        sample = sample_manifest.get("sample_64") or sample_manifest.get(
            "sample_full"
        )
        if not sample or sample.get("count") is None:
            raise RuntimeError(
                f"Cannot determine full sample count from {value['sample_manifest']}"
            )
        benchmark_full_counts.append(int(sample["count"]))
    expected_formal_trials = len(plan["backbones"]) * (
        2 * sum(benchmark_full_counts) + 16 * len(plan["benchmarks"])
    )
    unresolved = sum(
        len(row["canonical_after_infra_retries"]["infra_unresolved"])
        for row in rows
    )
    all_drivers_complete = all(
        status_states.get(backbone) == "complete"
        for backbone in plan["backbones"]
    )
    state = (
        "complete"
        if all_drivers_complete
        and completed_main_jobs == expected_main_jobs
        and unresolved == 0
        else "partial"
    )
    summary = {
        "schema_version": "baseline.matrix-summary.v1",
        "matrix_id": args.matrix_id,
        "generated_at": datetime.now().astimezone().isoformat(),
        "state": state,
        "status_states": status_states,
        "expected_main_jobs": expected_main_jobs,
        "completed_main_jobs": completed_main_jobs,
        "expected_formal_trials": expected_formal_trials,
        "infra_unresolved_in_finished_jobs": unresolved,
        "cost_currency": "CNY",
        "cost_policy": (
            "(input-cache)*input_rate + cache*cache_rate + "
            "output*output_rate, divided by 1e6"
        ),
        "jobs": sorted(
            rows,
            key=lambda row: (
                row["backbone"],
                row["baseline"],
                row["benchmark"],
            ),
        ),
    }
    summary["aggregate"] = _aggregate(summary["jobs"])
    stem = "summary" if state == "complete" else "summary.partial"
    json_path = matrix_dir / f"{stem}.json"
    md_path = matrix_dir / f"{stem}.md"
    _atomic_write_text(
        json_path, json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    )
    _atomic_write_text(md_path, _markdown(summary))
    print(
        json.dumps(
            {
                "state": state,
                "json": str(json_path.resolve()),
                "markdown": str(md_path.resolve()),
                "completed_main_jobs": completed_main_jobs,
                "expected_main_jobs": expected_main_jobs,
                "infra_unresolved": unresolved,
            },
            ensure_ascii=False,
        )
    )
    return 0 if state == "complete" else 2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Summarize baseline performance and API cost."
    )
    parser.add_argument("--matrix-id", required=True)
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(summarize(parse_args()))
