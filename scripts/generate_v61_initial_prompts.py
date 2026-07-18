#!/usr/bin/env python3
"""Reconstruct inspectable v6.1 cycle-1 prompts from real prep trajectories.

This utility is deliberately offline: it does not call an annotation/evolve LLM or
run a benchmark.  It snapshots the selected prep trajectories, maps the legacy
``step_meta.success`` field to v6.1's ``op_state``, builds focused DAG samples with
the production v6.1 builder, and renders prompts against the production cycle-1
seed harness.

The generated batch prompts freeze the initial harness for every batch so batch
sizes can be compared fairly.  In a live sequential evolve run, batch 1 has this
same harness while later batches see files changed by earlier evolve-agent calls.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import re
import shutil
import statistics
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evolve.evolve_v6_1_cycle import (  # noqa: E402
    DAGContrastiveSampleBuilderV61,
    EvolvePromptBuilderV61,
    PromptBudgetExceededV61,
    ScriptEvolverV61,
    _sample_has_forbidden_oracle_action,
)
from src.evolve.evolver import TrajectorySerializer  # noqa: E402
from src.evolve.native_tools_v6 import seed as seed_v6  # noqa: E402


LOGGER = logging.getLogger("v61-prompt-preview")
DEFAULT_BENCHMARKS = (
    "deep-swe",
    "swe-atlas-qa",
    "swe-atlas-tw",
    "swebench",
    "dab",
)
SOURCE_RE = re.compile(r"^Source: (.+)$", re.MULTILINE)
HISTORY_RE = re.compile(r"^# Executional History \d+$", re.MULTILINE)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _action_steps(trajectory: dict) -> list[dict]:
    return [
        step
        for step in trajectory.get("steps", [])
        if DAGContrastiveSampleBuilderV61._is_action_step(step)
    ]


def _snapshot_trajectory(source: Path, destination: Path) -> dict:
    """Copy one trajectory and add only the v6.1 compatibility metadata."""
    trajectory = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(trajectory.get("dependencies"), dict):
        raise ValueError(f"trajectory has no dependency annotation: {source}")

    mapped_states = 0
    inferred_states = 0
    inferred_types = 0
    actions = _action_steps(trajectory)
    for step in actions:
        existing = step.get("step_meta")
        if not isinstance(existing, dict):
            existing = {}
            step["step_meta"] = existing
        inferred = DAGContrastiveSampleBuilderV61._step_meta(step)
        if existing.get("op_type") not in {"read", "write", "verify", "explore"}:
            existing["op_type"] = inferred["op_type"]
            existing["op_type_source"] = "v61_preview_rule_fallback"
            inferred_types += 1
        if existing.get("op_state") not in {"success", "fail"}:
            if isinstance(existing.get("success"), bool):
                existing["op_state"] = "success" if existing["success"] else "fail"
                existing["op_state_source"] = "legacy_step_meta.success"
                mapped_states += 1
            else:
                existing["op_state"] = inferred["op_state"]
                existing["op_state_source"] = "v61_preview_rule_fallback"
                inferred_states += 1

    trajectory["v61_prompt_preview_provenance"] = {
        "original_trajectory": str(source.resolve()),
        "dependencies_and_op_type": "existing_prep_annotation",
        "op_state": "legacy_success_bool_mapping_or_rule_fallback",
        "llm_reannotated": False,
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(trajectory, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    states = Counter((step.get("step_meta") or {}).get("op_state") for step in actions)
    metrics = trajectory.get("final_metrics") or {}
    return {
        "case": source.parent.parent.name,
        "original_trajectory": str(source.resolve()),
        "snapshot_trajectory": str(destination.resolve()),
        "action_steps": len(actions),
        "mapped_op_states": mapped_states,
        "rule_inferred_op_states": inferred_states,
        "rule_inferred_op_types": inferred_types,
        "op_state_success": states.get("success", 0),
        "op_state_fail": states.get("fail", 0),
        "recorded_prompt_tokens": metrics.get("total_prompt_tokens"),
        "recorded_completion_tokens": metrics.get("total_completion_tokens"),
    }


def _token_counter(model: str) -> tuple[Callable[[str], int], str]:
    try:
        from litellm import token_counter

        # Probe once so an unsupported model is caught before prompt generation.
        token_counter(model=model, text="tokenizer probe")
        return (
            lambda text: int(token_counter(model=model, text=text)),
            f"litellm.token_counter(model={model})",
        )
    except Exception as litellm_error:  # noqa: BLE001
        LOGGER.warning("LiteLLM token counter unavailable: %s", litellm_error)
    try:
        import tiktoken

        encoding = tiktoken.get_encoding("cl100k_base")
        return (
            lambda text: len(encoding.encode(text)),
            "tiktoken(cl100k_base fallback)",
        )
    except Exception as tiktoken_error:  # noqa: BLE001
        LOGGER.warning("tiktoken unavailable: %s", tiktoken_error)
    return lambda text: (len(text) + 3) // 4, "ceil(chars/4) fallback"


def _batched(items: Sequence[Path], batch_size: int) -> Iterable[Sequence[Path]]:
    for start in range(0, len(items), batch_size):
        yield items[start : start + batch_size]


def _replace_staged_sources(prompt: str, source_paths: dict[Path, Path]) -> str:
    """Make Source lines match paths a live run would use beside prep trajectories."""
    for staged, live_equivalent in source_paths.items():
        prompt = prompt.replace(f"Source: {staged}", f"Source: {live_equivalent}")
    return prompt


def _render_batches(
    *,
    benchmark: str,
    samples: Sequence[Path],
    source_paths: dict[Path, Path],
    scripts_dir: Path,
    output_dir: Path,
    batch_size: int,
    max_prompt_chars: int,
    max_observation_chars: int,
    count_tokens: Callable[[str], int],
    token_method: str,
) -> tuple[dict, list[dict]]:
    samples = ScriptEvolverV61.order_samples_for_batches(samples)
    builder = EvolvePromptBuilderV61(
        serializer=TrajectorySerializer(
            max_observation_chars=max_observation_chars
        ),
        max_prompt_chars=max_prompt_chars,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    prompt_rows: list[dict] = []
    cursor = 0
    batch_id = 0
    while cursor < len(samples):
        batch_id += 1
        group = ScriptEvolverV61._sample_group_key(samples[cursor])
        requested_count = 0
        for sample in samples[cursor:cursor + batch_size]:
            if ScriptEvolverV61._sample_group_key(sample) != group:
                break
            requested_count += 1
        requested_count = max(1, requested_count)
        prompt = None
        batch: Sequence[Path] = ()
        for count in range(requested_count, 0, -1):
            candidate = samples[cursor:cursor + count]
            try:
                prompt = builder.build(
                    candidate,
                    cwd_name="scripts",
                    scripts_dir=scripts_dir,
                )
                batch = candidate
                break
            except PromptBudgetExceededV61:
                continue
        if prompt is None:
            raise RuntimeError(
                f"single sample cannot fit and was not skipped: {samples[cursor]}"
            )
        cursor += len(batch)
        prompt = _replace_staged_sources(prompt, source_paths)
        prompt_path = output_dir / f"evolve_batch_{batch_id}.traj.prompt.md"
        prompt_path.write_text(prompt, encoding="utf-8")
        included_sources = SOURCE_RE.findall(prompt)
        included_source_set = set(included_sources)
        oracle_filtered = []
        budget_skipped = []
        for sample in batch:
            data = json.loads(sample.read_text(encoding="utf-8"))
            live_source = str(source_paths[sample])
            if _sample_has_forbidden_oracle_action(data):
                oracle_filtered.append(live_source)
            elif live_source not in included_source_set:
                budget_skipped.append(live_source)
        prompt_rows.append(
            {
                "benchmark": benchmark,
                "batch_size": batch_size,
                "batch_id": batch_id,
                "prompt_path": str(prompt_path.resolve()),
                "requested_samples": len(batch),
                "rendered_samples": len(included_sources),
                "dropped_samples": len(batch) - len(included_sources),
                "oracle_filtered_samples": len(oracle_filtered),
                "budget_skipped_samples": len(budget_skipped),
                "budget_deferred_samples": requested_count - len(batch),
                "empty_evidence_prompt": not included_sources,
                "executional_history_blocks": len(HISTORY_RE.findall(prompt)),
                "chars": len(prompt),
                "bytes": len(prompt.encode("utf-8")),
                "tokens": count_tokens(prompt),
                "sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                "requested_sample_paths": [
                    str(source_paths[path]) for path in batch
                ],
                "rendered_source_paths": included_sources,
                "oracle_filtered_sample_paths": oracle_filtered,
                "budget_skipped_sample_paths": budget_skipped,
            }
        )
    _write_json(output_dir / "prompt_manifest.json", prompt_rows)

    tokens = [row["tokens"] for row in prompt_rows]
    chars = [row["chars"] for row in prompt_rows]
    rendered = sum(row["rendered_samples"] for row in prompt_rows)
    requested = sum(row["requested_samples"] for row in prompt_rows)
    oracle_filtered = sum(row["oracle_filtered_samples"] for row in prompt_rows)
    budget_skipped = sum(row["budget_skipped_samples"] for row in prompt_rows)
    summary = {
        "benchmark": benchmark,
        "batch_size": batch_size,
        "prompt_count": len(prompt_rows),
        "focused_sample_count": len(samples),
        "requested_sample_slots": requested,
        "rendered_sample_slots": rendered,
        "dropped_sample_slots": requested - rendered,
        "oracle_filtered_sample_slots": oracle_filtered,
        "budget_skipped_sample_slots": budget_skipped,
        "empty_evidence_prompt_count": sum(
            bool(row["empty_evidence_prompt"]) for row in prompt_rows
        ),
        "avg_tokens": round(statistics.fmean(tokens), 2) if tokens else 0,
        "min_tokens": min(tokens, default=0),
        "max_tokens": max(tokens, default=0),
        "avg_chars": round(statistics.fmean(chars), 2) if chars else 0,
        "min_chars": min(chars, default=0),
        "max_chars": max(chars, default=0),
        "token_count_method": token_method,
        "prompt_dir": str(output_dir.resolve()),
        "first_prompt": prompt_rows[0]["prompt_path"] if prompt_rows else None,
    }
    return summary, prompt_rows


def _source_run(benchmark: str, model_name: str, prep_root: Path) -> Path:
    handle = prep_root / "handles" / benchmark / model_name
    if not handle.exists():
        raise FileNotFoundError(f"prep handle does not exist: {handle}")
    run = handle.resolve()
    if not run.is_dir():
        raise NotADirectoryError(run)
    return run


def _markdown_report(
    *,
    output_root: Path,
    summaries: Sequence[dict],
    benchmark_manifests: Sequence[dict],
    token_method: str,
    model: str,
    max_prompt_chars: int,
    max_observation_chars: int,
) -> str:
    lines = [
        "# v6.1 真实 Cycle-1 初始 Prompt 预览",
        "",
        "这些文件由真实 prep trajectory、生产版 v6.1 focused-DAG builder、",
        "生产版 serializer/prompt builder 和真实 cycle-1 seed harness 离线生成。",
        "没有调用标注/evolve LLM，也没有运行 benchmark。",
        "",
        "为公平比较 batch size，所有 batch 都冻结在同一份初始 harness。真实顺序",
        "evolve 中 batch 1 与此相同；batch 2 以后会看到前一 batch 修改后的 harness，",
        "因此之后的 live prompt 只有真正执行 evolve agent 后才能完全确定。",
        "",
        "## 汇总",
        "",
        "| benchmark | batch size | prompt 条数 | focused samples | 实际渲染/请求 | oracle/budget 丢弃 | 空 evidence prompts | avg tokens | min–max tokens | avg chars |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in summaries:
        lines.append(
            f"| {row['benchmark']} | {row['batch_size']} | {row['prompt_count']} | "
            f"{row['focused_sample_count']} | {row['rendered_sample_slots']}/"
            f"{row['requested_sample_slots']} | {row['oracle_filtered_sample_slots']}/"
            f"{row['budget_skipped_sample_slots']} | {row['empty_evidence_prompt_count']} | "
            f"{row['avg_tokens']:.2f} | "
            f"{row['min_tokens']}–{row['max_tokens']} | {row['avg_chars']:.2f} |"
        )
    lines += [
        "",
        "## 口径",
        "",
        f"- tokenizer：`{token_method}`（目标 evolve 模型：`{model}`）。",
        "- tokens 只统计 `.traj.prompt.md` 正文；不包含 mini-swe-agent 自身 system prompt、",
        "  bash tool schema、读取 prompt 文件的额外对话开销和模型输出。",
        f"- prompt 字符预算：`{max_prompt_chars}`；每个 observation 序列化上限："
        f"`{max_observation_chars}` 字符。",
        "- `prompt 条数`严格按 production `ScriptEvolver._batched` 计算；batch size",
        "  指 focused contrastive sample 数，不是原始 trajectory 数。",
        "- `实际渲染/请求`可暴露 production prompt builder 因字符预算或 oracle",
        "  过滤而跳过的 sample。",
        "- prep 旧标注中的 `step_meta.success` 已在快照内映射为",
        "  `op_state=success/fail`；dependencies/op_type 沿用 prep 标注，未付费重标注。",
        "",
        "## 数据来源与查看入口",
        "",
    ]
    for manifest in benchmark_manifests:
        benchmark = manifest["benchmark"]
        lines += [
            f"### {benchmark}",
            "",
            f"- prep run：`{manifest['source_run']}`",
            f"- trajectories：{manifest['trajectory_count']}；focused samples："
            f"{manifest['focused_sample_count']}；类型："
            f"`{json.dumps(manifest['sample_type_counts'], ensure_ascii=False, sort_keys=True)}`",
        ]
        if manifest["zero_action_trajectory_count"]:
            lines.append(
                f"- **数据告警**：{manifest['zero_action_trajectory_count']}/"
                f"{manifest['trajectory_count']} 条 trajectory 没有 action step。"
            )
        for size in manifest["batch_sizes"]:
            first = next(
                row["first_prompt"]
                for row in summaries
                if row["benchmark"] == benchmark and row["batch_size"] == size
            )
            lines.append(f"- batch={size} 第一条完整 prompt：`{first}`")
        lines.append("")
    lines += [
        "## 文件说明",
        "",
        "- `summary.json` / `summary.csv`：benchmark × batch size 聚合统计。",
        "- `<benchmark>/batch_size_N/prompt_manifest.json`：逐 prompt token、字符数、",
        "  hash、请求/实际渲染 sample 列表。",
        "- `<benchmark>/snapshot/`：只供本次复现使用的 trajectory 副本及 focused samples。",
        "  Prompt 内的 `Source:` 保持正式运行时的等价 prep 路径；为避免污染 prep，",
        "  本次真正生成的 sample 文件只写在对应 `snapshot/` 中。",
        "- `harness_seed/scripts/`：生成所有初始 prompt 时使用的完整 seed harness。",
        "",
        f"生成目录：`{output_root.resolve()}`",
    ]
    return "\n".join(lines) + "\n"


def generate(args: argparse.Namespace) -> Path:
    output_root = args.output_dir.resolve()
    if output_root.exists():
        if not args.force:
            raise FileExistsError(
                f"output already exists (pass --force to replace): {output_root}"
            )
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True)

    scripts_dir = output_root / "harness_seed" / "scripts"
    seed_v6(scripts_dir)
    count_tokens, token_method = _token_counter(args.tokenizer_model)

    summaries: list[dict] = []
    all_prompt_rows: list[dict] = []
    benchmark_manifests: list[dict] = []
    for benchmark in args.benchmarks:
        source_run = _source_run(benchmark, args.prep_model, args.prep_root)
        source_trajectories = sorted(
            source_run.glob("**/agent/trajectory.json")
        )
        if args.limit_trajectories:
            source_trajectories = source_trajectories[: args.limit_trajectories]
        if not source_trajectories:
            raise FileNotFoundError(f"no trajectories under {source_run}")

        benchmark_dir = output_root / benchmark
        snapshot_root = benchmark_dir / "snapshot"
        trajectory_rows: list[dict] = []
        original_by_snapshot: dict[Path, Path] = {}
        for source in source_trajectories:
            relative = source.relative_to(source_run)
            destination = (snapshot_root / relative).resolve()
            trajectory_rows.append(_snapshot_trajectory(source, destination))
            original_by_snapshot[destination] = source.resolve()

        samples = sorted(DAGContrastiveSampleBuilderV61().build_dir(snapshot_root))
        source_paths: dict[Path, Path] = {}
        for sample in samples:
            sample = sample.resolve()
            snapshot_trajectory = sample.with_name("trajectory.json")
            original_trajectory = original_by_snapshot[snapshot_trajectory]
            source_paths[sample] = original_trajectory.with_name(sample.name)
        samples = sorted(source_paths)

        type_counts = Counter()
        for sample in samples:
            data = json.loads(sample.read_text(encoding="utf-8"))
            type_counts[data.get("type", "unknown")] += 1

        manifest = {
            "benchmark": benchmark,
            "source_run": str(source_run),
            "trajectory_count": len(source_trajectories),
            "focused_sample_count": len(samples),
            "zero_action_trajectory_count": sum(
                row["action_steps"] == 0 for row in trajectory_rows
            ),
            "sample_type_counts": dict(sorted(type_counts.items())),
            "batch_sizes": args.batch_sizes,
            "annotation_provenance": {
                "dependencies_and_op_type": "existing_prep_annotation",
                "op_state": "mapped from legacy step_meta.success; rule fallback only if absent",
                "llm_reannotated": False,
            },
            "trajectories": trajectory_rows,
        }
        _write_json(benchmark_dir / "source_manifest.json", manifest)
        benchmark_manifests.append(manifest)

        for batch_size in args.batch_sizes:
            summary, prompt_rows = _render_batches(
                benchmark=benchmark,
                samples=samples,
                source_paths=source_paths,
                scripts_dir=scripts_dir,
                output_dir=benchmark_dir / f"batch_size_{batch_size}",
                batch_size=batch_size,
                max_prompt_chars=args.max_prompt_chars,
                max_observation_chars=args.max_observation_chars,
                count_tokens=count_tokens,
                token_method=token_method,
            )
            summary["source_run"] = str(source_run)
            summary["trajectory_count"] = len(source_trajectories)
            summaries.append(summary)
            all_prompt_rows.extend(prompt_rows)
            LOGGER.info(
                "%s batch=%d: prompts=%d avg_tokens=%.2f rendered=%d/%d",
                benchmark,
                batch_size,
                summary["prompt_count"],
                summary["avg_tokens"],
                summary["rendered_sample_slots"],
                summary["requested_sample_slots"],
            )

    report = {
        "schema_version": "v6.1-initial-prompt-preview.1",
        "generated_at": datetime.now().astimezone().isoformat(),
        "llm_invoked": False,
        "benchmark_invoked": False,
        "production_builders": {
            "contrastive": "DAGContrastiveSampleBuilderV61",
            "prompt": "EvolvePromptBuilderV61",
            "serializer": "TrajectorySerializer",
            "seed": "native_tools_v6.seed",
        },
        "frozen_cycle_1_seed_harness": True,
        "batch_sizes": args.batch_sizes,
        "max_prompt_chars": args.max_prompt_chars,
        "max_observation_chars": args.max_observation_chars,
        "tokenizer_model": args.tokenizer_model,
        "token_count_method": token_method,
        "summaries": summaries,
        "benchmarks": benchmark_manifests,
    }
    _write_json(output_root / "summary.json", report)
    _write_json(output_root / "all_prompt_manifest.json", all_prompt_rows)

    csv_fields = [
        "benchmark",
        "batch_size",
        "trajectory_count",
        "focused_sample_count",
        "prompt_count",
        "requested_sample_slots",
        "rendered_sample_slots",
        "dropped_sample_slots",
        "oracle_filtered_sample_slots",
        "budget_skipped_sample_slots",
        "empty_evidence_prompt_count",
        "avg_tokens",
        "min_tokens",
        "max_tokens",
        "avg_chars",
        "min_chars",
        "max_chars",
        "source_run",
        "prompt_dir",
        "first_prompt",
        "token_count_method",
    ]
    with (output_root / "summary.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=csv_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(summaries)

    readme = _markdown_report(
        output_root=output_root,
        summaries=summaries,
        benchmark_manifests=benchmark_manifests,
        token_method=token_method,
        model=args.tokenizer_model,
        max_prompt_chars=args.max_prompt_chars,
        max_observation_chars=args.max_observation_chars,
    )
    (output_root / "README.md").write_text(readme, encoding="utf-8")
    return output_root


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--benchmarks",
        nargs="+",
        default=list(DEFAULT_BENCHMARKS),
        choices=DEFAULT_BENCHMARKS,
    )
    parser.add_argument("--batch-sizes", nargs="+", type=int, default=[1, 2, 4])
    parser.add_argument(
        "--prep-root",
        type=Path,
        default=ROOT / "results" / "prep",
    )
    parser.add_argument("--prep-model", default="deepseek-v4-flash")
    parser.add_argument("--tokenizer-model", default="openai/deepseek-v4-flash")
    parser.add_argument("--max-prompt-chars", type=int, default=50000)
    parser.add_argument("--max-observation-chars", type=int, default=1000)
    parser.add_argument("--limit-trajectories", type=int, default=0)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=(
            ROOT
            / "results"
            / "evolve"
            / "v61_prompt_preview"
            / datetime.now().strftime("%m%d-%H%M%S")
        ),
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    if any(size <= 0 for size in args.batch_sizes):
        parser.error("--batch-sizes values must be positive")
    args.batch_sizes = list(dict.fromkeys(args.batch_sizes))
    return args


def main() -> int:
    args = _parse_args()
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )
    output = generate(args)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
