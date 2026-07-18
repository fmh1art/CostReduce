#!/usr/bin/env python3
"""Reproducible paired log analysis for the preserved v6/DAB experiment."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from analyze_v9_results import (  # noqa: E402
    case_id,
    exception_map,
    observation_text,
    read_json,
    result_returncodes,
    trial_metrics,
)


WORK = ROOT / "results/evolve/v6cycle/dab/0716-103752"
EVOLVED = ROOT / "results/eval/dab/evolve-v6cycle-dab-0716-103753"
BASELINE = ROOT / "results/no_evolve/dab/noevolve-dab-0713-174201"
SHELL_NAMES = {"bash", "shell", "terminal", "exec", "exec_command"}


REGRESSION_DIAGNOSIS = {
    "dab__civic_unstructured__query5":
        "将应为 3,722,235.29 的跨文档状态变化/资金映射算成 4,296,375；44 vs 72 steps，"
        "开局 run-command timeout，未运行 validator。no-evolve 读取 ground truth 且验证 3 次；"
        "属于早停和不完整实体映射，harness 有明显贡献，但 baseline 受 oracle 污染。",
    "dab__crmarenapro__query2":
        "agent 在产生 trajectory 前 NonZeroAgentExitCodeError，/app/answer.txt 缺失。"
        "这是运行失败，不是可归因于工具语义的答案错误。",
    "dab__crmarenapro__query7":
        "选错 knowledge article ID（Enwv 而非 EoD3）；32 vs 59 steps，run-script 失败一次且未做最终验证。"
        "双方均未读 ground truth，较短探索与单次 rollout 随机性共同作用。",
    "dab__cve__query10":
        "计数 11 而非 5；56 vs 144 steps，v6 完全没有调用 native tool，也未验证。"
        "因此不是工具实现直接导致，主要是 early-exit instruction/随机路径；no-evolve 读取 ground truth 并验证 5 次。",
    "dab__cve__query2":
        "回答 microsoft 而非 fortinet；v6 反而 94 vs 87 steps，19 次 native call，初始 run-command 失败。"
        "工具未压缩推理且增加分支；no-evolve 读取 ground truth 并验证 2 次，存在 oracle 混杂。",
    "dab__deps_dev_v1__query1":
        "只返回直观的五个顶层包，漏掉 verifier 要求的嵌套包名；33 vs 74 steps，未验证。"
        "是典型不完整枚举/过早提交；no-evolve 读取 ground truth 并验证 3 次。",
    "dab__github_repos__query3":
        "得到 1079 而非 1077；31 vs 33 steps，9 次 native call 中 run-command 失败 3 次。"
        "双方均无 oracle，属于边界过滤/计数错误，工具接口失败和缺少最终交叉检查有贡献。",
    "dab__imdb__query6":
        "复杂跨库 join 选成 Abby Mallard/Cusack/Chicken Little，而非 Lola/Andrews/Hoodwinked；"
        "115 steps、19 次 native call、native/bash 多次失败，工具没有简化任务。no-evolve 直接读取 ground truth。",
    "dab__imdb__query7":
        "排序首项选成 The Lady In Red 而非 !!!, Toy；81 vs 85 steps，11 次 native call、22 个 bash 非零结果。"
        "双方均无 oracle，主要是清洗/排序规则错误，harness 没有提供 DAB 专用的确定性 join 工具。",
    "dab__krama__query4":
        "回答 127832 而非 319655；55 vs 56 steps，虽调用 validator 仍提交错误结果。"
        "双方均无 ground truth，说明 early-exit/忽略失败验证是真实 harness 行为退化。",
    "dab__krama__query5":
        "平均值 19.45 而非 20.02；36 vs 98 steps，少做了日期/温标/边界数据核验。"
        "双方均无 oracle，是以大幅缩短路径换取错误聚合的直接例子。",
    "dab__krama__query7":
        "8 steps 后 agent 非零退出且没有 answer.txt；这是运行错误。no-evolve 还读取了 ground truth，"
        "因此既有基础设施/agent failure，也有 baseline oracle 混杂。",
    "dab__music_brainz_20k__query1":
        "收入合计 601.44 而非 1059.46；8 vs 10 steps，未覆盖全部匹配曲目/来源。"
        "双方均无 oracle，是 early exit 导致不完整聚合的高置信度例子。",
    "dab__music_brainz_20k__query3":
        "选成 Systemisch bled 而非 Zo gaat het leven aan je voor；13 vs 23 steps，"
        "3 次 run-command 全失败仍继续提交。no-evolve 读取 ground truth，工具失败与 oracle 差异同时存在。",
    "dab__patents__query1":
        "只输出 A61K，而正确答案包含 75 个 CPC code；28 vs 99 steps，run-command/run-script 均失败且未验证。"
        "这是最明显的 incomplete enumeration/过早退出；no-evolve 读取 ground truth 4 次并验证 6 次。",
}

IMPROVEMENT_DIAGNOSIS = {
    "dab__googlelocal__query2":
        "补全了 J B Oriental Inc 及评分，失败→成功；但成本和 steps 均上升，属于准确率改善而非降本。",
    "dab__krama__query3":
        "修正为 12964.8727，失败→成功；成本下降但 steps 上升，较短 observation 抵消了额外轮次。",
    "dab__yelp__query4":
        "修正类别与评分为 Restaurant/3.63，失败→成功；成本和 steps 均上升。",
}


def trial_index(run: Path) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for trial in sorted(run.iterdir()):
        if trial.is_dir() and (trial / "config.json").exists():
            result[case_id(trial)] = trial
    return result


def verifier_details(trial: Path) -> dict[str, str]:
    data = read_json(trial / "verifier/dab_result.json", {}) or {}
    return {
        "valid": str(bool(data.get("is_valid"))),
        "reason": " ".join(str(data.get("reason", "")).split()),
        "answer": " ".join(str(data.get("llm_answer", "")).split()),
    }


def call_features(trial: Path) -> dict[str, Any]:
    trajectory = read_json(trial / "agent/trajectory.json")
    result: dict[str, Any] = {
        "tool_counts": Counter(), "tool_failures": Counter(), "tool_obs_chars": Counter(),
        "ground_truth_calls": 0, "validate_calls": 0, "answer_calls": 0,
        "query_db_calls": 0, "schema_calls": 0,
    }
    if not isinstance(trajectory, dict):
        return result
    for step in trajectory.get("steps") or []:
        if not isinstance(step, dict) or not step.get("tool_calls"):
            continue
        returncodes = result_returncodes(step)
        observation = step.get("observation") or {}
        observations = observation.get("results") or [] if isinstance(observation, dict) else []
        for index, call in enumerate(step.get("tool_calls") or []):
            name = str(call.get("function_name") or call.get("name") or "unknown")
            result["tool_counts"][name] += 1
            arg_text = json.dumps(call.get("arguments") or {}, ensure_ascii=False).lower()
            result["ground_truth_calls"] += "ground_truth" in arg_text
            result["validate_calls"] += "validate" in arg_text
            result["answer_calls"] += "answer.txt" in arg_text
            result["query_db_calls"] += "query_db" in arg_text
            result["schema_calls"] += any(token in arg_text for token in (
                " dbs", " tables", "schema", "pragma table_info", "information_schema"
            ))
            if index < len(observations):
                content = observations[index].get("content", observations[index]) \
                    if isinstance(observations[index], dict) else observations[index]
                if isinstance(content, str):
                    try:
                        content = json.loads(content)
                    except json.JSONDecodeError:
                        pass
                if isinstance(content, dict):
                    output = str(content.get("output", content))
                else:
                    output = str(content)
                result["tool_obs_chars"][name] += len(output)
            if index >= len(returncodes) or returncodes[index] not in (None, 0):
                result["tool_failures"][name] += 1
    result["tool_counts"] = dict(result["tool_counts"])
    result["tool_failures"] = dict(result["tool_failures"])
    result["tool_obs_chars"] = dict(result["tool_obs_chars"])
    return result


def enrich(trial: Path, errors: dict[str, str]) -> dict[str, Any]:
    cid = case_id(trial)
    metrics = trial_metrics(trial, "dab", errors.get(cid))
    metrics.update(call_features(trial))
    metrics["verifier"] = verifier_details(trial)
    return metrics


def clip(text: str, limit: int = 110) -> str:
    text = " ".join(str(text).split()).replace("|", "\\|")
    return text if len(text) <= limit else text[:limit - 3].rstrip() + "..."


def outcome(base: dict[str, Any], evolved: dict[str, Any]) -> str:
    if base.get("success") and not evolved.get("success"):
        return "成功→失败"
    if not base.get("success") and evolved.get("success"):
        return "失败→成功"
    return "保持成功" if base.get("success") else "保持失败"


def generic_diagnosis(base: dict[str, Any], evolved: dict[str, Any]) -> str:
    status = outcome(base, evolved)
    if not evolved.get("trajectory"):
        return f"{status}；v6 无 trajectory（{evolved.get('error') or 'unknown error'}），不能把零/缺失成本当节省。"
    delta = evolved["cost"] - base["cost"]
    steps = evolved["steps"] - base["steps"]
    native = evolved["native_calls"]
    failure = evolved["native_failures"]
    fragments = [status, f"成本{'下降' if delta < 0 else '上升'} {abs(delta):.4f}",
                 f"steps {'减少' if steps < 0 else '增加'} {abs(steps)}", f"native {native} 次"]
    if failure:
        fragments.append(f"native failure {failure} 次")
    if base.get("ground_truth_calls") or evolved.get("ground_truth_calls"):
        fragments.append(
            f"ground-truth access {base.get('ground_truth_calls', 0)}→{evolved.get('ground_truth_calls', 0)}"
        )
    if base.get("validate_calls") or evolved.get("validate_calls"):
        fragments.append(f"validator calls {base.get('validate_calls', 0)}→{evolved.get('validate_calls', 0)}")
    if status == "保持成功":
        fragments.append("该单次日志只证明此次路径保持正确，不能证明工具因果")
    elif status == "保持失败":
        fragments.append("两次 rollout 都未解决，不能用成本下降主张有效优化")
    return "；".join(fragments) + "。"


def analyze() -> dict[str, Any]:
    base_dirs, evolved_dirs = trial_index(BASELINE), trial_index(EVOLVED)
    base_errors, evolved_errors = exception_map(BASELINE), exception_map(EVOLVED)
    expected = [line.strip() for line in (WORK / "final_eval_cases.txt").read_text().splitlines() if line.strip()]
    rows = []
    for cid in expected:
        base = enrich(base_dirs[cid], base_errors)
        evolved = enrich(evolved_dirs[cid], evolved_errors)
        status = outcome(base, evolved)
        diagnosis = REGRESSION_DIAGNOSIS.get(cid) or IMPROVEMENT_DIAGNOSIS.get(cid) \
            or generic_diagnosis(base, evolved)
        rows.append({"case": cid, "outcome": status, "baseline": base,
                     "evolved": evolved, "diagnosis": diagnosis})

    valid = [row for row in rows if row["baseline"].get("trajectory") and row["evolved"].get("trajectory")]
    neither_gt = [row for row in valid if not row["baseline"]["ground_truth_calls"]
                  and not row["evolved"]["ground_truth_calls"]]
    summary = {
        "expected": len(rows),
        "valid_pairs": len(valid),
        "strict_success": {
            "baseline": sum(row["baseline"]["success"] for row in rows),
            "v6": sum(row["evolved"]["success"] for row in rows),
        },
        "outcomes": dict(Counter(row["outcome"] for row in rows)),
        "paired_cost": {
            "baseline": sum(row["baseline"]["cost"] for row in valid),
            "v6": sum(row["evolved"]["cost"] for row in valid),
        },
        "mean_steps": {
            "baseline": sum(row["baseline"]["steps"] for row in valid) / len(valid),
            "v6": sum(row["evolved"]["steps"] for row in valid) / len(valid),
        },
        "mean_observation_tokens_per_step_est": {
            "baseline": sum(row["baseline"]["avg_obs_tokens_est"] for row in valid) / len(valid),
            "v6": sum(row["evolved"]["avg_obs_tokens_est"] for row in valid) / len(valid),
        },
        "native": {
            "adoption_cases": sum(row["evolved"].get("native_calls", 0) > 0 for row in valid),
            "calls": sum(row["evolved"].get("native_calls", 0) for row in valid),
            "failures": sum(row["evolved"].get("native_failures", 0) for row in valid),
        },
        "oracle": {
            "baseline_ground_truth_cases": sum(row["baseline"]["ground_truth_calls"] > 0 for row in rows),
            "v6_ground_truth_cases": sum(row["evolved"]["ground_truth_calls"] > 0 for row in rows),
            "neither_ground_truth_n": len(neither_gt),
            "neither_ground_truth_success_baseline": sum(row["baseline"]["success"] for row in neither_gt),
            "neither_ground_truth_success_v6": sum(row["evolved"]["success"] for row in neither_gt),
        },
    }
    summary["paired_cost"]["delta"] = summary["paired_cost"]["v6"] - summary["paired_cost"]["baseline"]
    summary["paired_cost"]["rate"] = summary["paired_cost"]["v6"] / summary["paired_cost"]["baseline"] - 1

    tool_stats: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "calls": 0, "cases": set(), "failures": 0, "observation_chars": 0,
    })
    for row in valid:
        cid, evolved = row["case"], row["evolved"]
        for name, count in evolved["tool_counts"].items():
            tool_stats[name]["calls"] += count
            tool_stats[name]["cases"].add(cid)
            tool_stats[name]["failures"] += evolved["tool_failures"].get(name, 0)
            tool_stats[name]["observation_chars"] += evolved["tool_obs_chars"].get(name, 0)
    summary["tool_stats"] = {
        name: {
            "calls": data["calls"], "cases": len(data["cases"]), "failures": data["failures"],
            "avg_observation_tokens_est": data["observation_chars"] / 4 / data["calls"] if data["calls"] else 0,
        }
        for name, data in sorted(tool_stats.items(), key=lambda item: -item[1]["calls"])
    }
    return {"summary": summary, "rows": rows}


def markdown_cases(data: dict[str, Any]) -> str:
    lines = [
        "| Case | 结果 | no-evolve cost/steps/GT/val | v6 cost/steps/native/fail/GT/val | Verifier（v6） | 逐 log 诊断 |",
        "|---|---|---:|---:|---|---|",
    ]
    for row in data["rows"]:
        b, e = row["baseline"], row["evolved"]
        bcell = (f"{b.get('cost', 0):.4f}/{b.get('steps', 'NA')}/"
                 f"{b.get('ground_truth_calls', 0)}/{b.get('validate_calls', 0)}")
        if e.get("trajectory"):
            ecell = (f"{e.get('cost', 0):.4f}/{e.get('steps', 'NA')}/{e.get('native_calls', 0)}/"
                     f"{e.get('native_failures', 0)}/{e.get('ground_truth_calls', 0)}/{e.get('validate_calls', 0)}")
        else:
            ecell = f"无 trajectory（{e.get('error')}）"
        verifier = clip(e.get("verifier", {}).get("reason", ""))
        diagnosis = clip(row["diagnosis"], 300)
        lines.append(f"| `{row['case']}` | {row['outcome']} | {bcell} | {ecell} | {verifier} | {diagnosis} |")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--format", choices=("summary", "json", "cases"), default="summary")
    args = parser.parse_args()
    data = analyze()
    if args.format == "cases":
        print(markdown_cases(data))
    elif args.format == "json":
        print(json.dumps(data, ensure_ascii=False, indent=2, default=list))
    else:
        print(json.dumps(data["summary"], ensure_ascii=False, indent=2, default=list))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
