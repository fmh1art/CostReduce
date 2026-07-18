#!/usr/bin/env python3
"""Paired trajectory diagnostics for the preserved v9 experiments."""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RUNS = {
    "swebench": {
        "work": ROOT / "results/evolve/v9cycle/swebench/0715-235050",
        "evolved": ROOT / "results/eval/swebench-verified/evolve-v9cycle-swebench-0715-235050",
        "baseline": ROOT / "results/no_evolve/swebench-verified/noevolve-swebench-0703-031441",
    },
    "deep-swe": {
        "work": ROOT / "results/evolve/v9cycle/deep-swe/0716-023159",
        "evolved": ROOT / "results/eval/deep-swe/evolve-v9cycle-deep-swe-0716-023159",
        "baseline": ROOT / "results/no_evolve/deep-swe/noevolve-deep-swe-0702-205240",
    },
    "dab": {
        "work": ROOT / "results/evolve/v9cycle/dab/0716-063558",
        "evolved": ROOT / "results/eval/dab/evolve-v9cycle-dab-0716-063558",
        "baseline": ROOT / "results/no_evolve/dab/noevolve-dab-0713-174201",
    },
}
SHELL_NAMES = {"bash", "shell", "terminal", "exec", "exec_command"}


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def case_id(trial: Path) -> str:
    config = read_json(trial / "config.json", {}) or {}
    task = config.get("task") if isinstance(config.get("task"), dict) else {}
    path = task.get("path")
    if path:
        return Path(str(path)).name
    result = read_json(trial / "result.json", {}) or {}
    task_id = result.get("task_id") if isinstance(result.get("task_id"), dict) else {}
    if task_id.get("path"):
        return Path(str(task_id["path"])).name
    return trial.name.rsplit("__", 1)[0]


def exception_map(run: Path) -> dict[str, str]:
    root = read_json(run / "result.json", {}) or {}
    evals = ((root.get("stats") or {}).get("evals") or {})
    result: dict[str, str] = {}
    for details in evals.values():
        for kind, trial_names in (details.get("exception_stats") or {}).items():
            for trial_name in trial_names:
                trial = run / trial_name
                result[case_id(trial)] = kind
    return result


def observation_text(step: dict) -> str:
    observation = step.get("observation")
    if not isinstance(observation, dict):
        return str(observation or "")
    parts: list[str] = []
    for item in observation.get("results") or []:
        content = item.get("content", item) if isinstance(item, dict) else item
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except json.JSONDecodeError:
                pass
        if isinstance(content, dict):
            parts.append(str(content.get("output", content)))
        else:
            parts.append(str(content))
    return "\n".join(parts)


def result_returncodes(step: dict) -> list[int | None]:
    observation = step.get("observation")
    if not isinstance(observation, dict):
        return []
    rows = []
    for item in observation.get("results") or []:
        content = item.get("content", item) if isinstance(item, dict) else item
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except json.JSONDecodeError:
                content = {}
        rows.append(content.get("returncode") if isinstance(content, dict) else None)
    return rows


def success_and_score(trial: Path, benchmark: str) -> tuple[bool, float]:
    result = read_json(trial / "result.json", {}) or {}
    reward_file = read_json(trial / "verifier/reward.json", {}) or {}
    rewards = ((result.get("verifier_result") or {}).get("rewards") or reward_file)
    rewards = rewards if isinstance(rewards, dict) else {}
    if benchmark == "deep-swe":
        score = float(rewards.get("primary_score", rewards.get("partial", rewards.get("reward", 0))) or 0)
        f2p_total = int(rewards.get("f2p_total", 0) or 0)
        p2p_total = int(rewards.get("p2p_total", 0) or 0)
        passed = (f2p_total > 0 and int(rewards.get("f2p_passed", 0) or 0) == f2p_total
                  and int(rewards.get("p2p_passed", 0) or 0) == p2p_total)
        return passed or float(rewards.get("reward", 0) or 0) > 0, score
    raw = rewards.get("resolved", rewards.get("overall_pass", rewards.get("reward", 0)))
    try:
        score = float(raw or 0)
    except (TypeError, ValueError):
        score = float(bool(raw))
    return score > 0, score


def trial_metrics(trial: Path, benchmark: str, error: str | None) -> dict:
    trajectory = read_json(trial / "agent/trajectory.json")
    passed, score = success_and_score(trial, benchmark)
    if not isinstance(trajectory, dict):
        text = ""
        exception_file = trial / "exception.txt"
        if exception_file.is_file():
            text = exception_file.read_text(encoding="utf-8", errors="replace")[:500]
        return {
            "case": case_id(trial), "trial": trial.name, "trajectory": False,
            "success": passed, "score": score, "error": error or text or "missing trajectory",
        }

    result = read_json(trial / "result.json", {}) or {}
    agent_result = result.get("agent_result") or {}
    final = trajectory.get("final_metrics") or {}
    prompt = int(agent_result.get("n_input_tokens", final.get("total_prompt_tokens", 0)) or 0)
    cached = min(prompt, max(0, int(agent_result.get(
        "n_cache_tokens", final.get("total_cached_tokens", 0)
    ) or 0)))
    output = max(0, int(agent_result.get(
        "n_output_tokens", final.get("total_completion_tokens", 0)
    ) or 0))
    new = max(0, prompt - cached)
    cost_new = new / 1_000_000
    cost_cached = cached * 0.02 / 1_000_000
    cost_output = output * 2.0 / 1_000_000
    actions = [step for step in trajectory.get("steps", [])
               if isinstance(step, dict) and step.get("tool_calls")]
    tool_counts: Counter[str] = Counter()
    native_calls = native_failures = bash_calls = 0
    obs_chars = obs_steps = truncations = timeouts = 0
    call_signatures: list[str] = []
    for step in actions:
        text = observation_text(step)
        obs_chars += len(text)
        obs_steps += bool(text)
        lowered = text.lower()
        truncations += "truncated" in lowered
        timeouts += bool(re.search(r"timed?\s*out|timeout", lowered))
        returncodes = result_returncodes(step)
        for index, call in enumerate(step.get("tool_calls") or []):
            name = str(call.get("function_name") or call.get("name") or call.get("tool") or "unknown")
            tool_counts[name] += 1
            args = call.get("arguments") or {}
            call_signatures.append(json.dumps([name, args], sort_keys=True, default=str))
            if name.lower() in SHELL_NAMES:
                bash_calls += 1
            else:
                native_calls += 1
                if index >= len(returncodes) or returncodes[index] not in (None, 0):
                    native_failures += 1
    repeated = sum(a == b for a, b in zip(call_signatures, call_signatures[1:]))
    user_message = next((str(step.get("message", "")) for step in trajectory.get("steps", [])
                         if step.get("source") == "user"), "")
    first_action_prompt = 0
    if actions:
        first_action_prompt = int((actions[0].get("metrics") or {}).get("prompt_tokens", 0) or 0)
    return {
        "case": case_id(trial), "trial": trial.name, "trajectory": True,
        "success": passed, "score": score, "error": error,
        "prompt_tokens": prompt, "cached_tokens": cached, "new_tokens": new,
        "output_tokens": output, "cost": cost_new + cost_cached + cost_output,
        "cost_new": cost_new, "cost_cached": cost_cached, "cost_output": cost_output,
        "steps": len(actions), "obs_chars": obs_chars,
        "obs_tokens_est": math.ceil(obs_chars / 4),
        "avg_obs_tokens_est": (obs_chars / 4 / obs_steps if obs_steps else 0),
        "native_calls": native_calls, "native_failures": native_failures,
        "bash_calls": bash_calls, "repeated_calls": repeated,
        "truncated_steps": truncations, "timeout_steps": timeouts,
        "tool_counts": dict(tool_counts), "user_message_chars": len(user_message),
        "first_action_prompt_tokens": first_action_prompt,
    }


def index_run(run: Path, benchmark: str) -> dict[str, dict]:
    errors = exception_map(run)
    result = {}
    for trial in sorted(path for path in run.iterdir() if path.is_dir() and (path / "config.json").exists()):
        cid = case_id(trial)
        result[cid] = trial_metrics(trial, benchmark, errors.get(cid))
    return result


def reason(base: dict, evolved: dict) -> str:
    if not evolved or not evolved.get("trajectory"):
        suffix = f"；no-evolve 也有运行异常={base['error']}" if base.get("error") else ""
        return (f"无有效 trajectory（{(evolved or {}).get('error', 'missing trial')}），"
                f"不能把缺失成本当节省{suffix}")
    if not base.get("trajectory"):
        return "no-evolve trajectory 缺失，无法配对"
    delta = evolved["cost"] - base["cost"]
    step_delta = evolved["steps"] - base["steps"]
    obs_ratio = ((evolved["avg_obs_tokens_est"] / base["avg_obs_tokens_est"] - 1)
                 if base["avg_obs_tokens_est"] else 0)
    components = {
        "new-input": evolved["cost_new"] - base["cost_new"],
        "cached-context": evolved["cost_cached"] - base["cost_cached"],
        "model-output": evolved["cost_output"] - base["cost_output"],
    }
    driver = max(components, key=lambda key: components[key]) if delta > 0 else min(components, key=lambda key: components[key])
    parts = []
    if delta > 1e-9:
        parts.append(f"成本增加，最大正贡献={driver}")
        if step_delta > 0:
            parts.append(f"多{step_delta}步导致历史重复计费")
        elif step_delta <= 0 and components["cached-context"] > 0:
            parts.append("步数未增但每轮上下文/固定 harness 更重")
        if obs_ratio > 0.15:
            parts.append(f"平均 observation +{obs_ratio:.0%}")
        if evolved["native_calls"] == 0:
            parts.append("新工具零采用，差异来自固定 instruction、rollout 路径或运行噪声")
        elif step_delta >= 0:
            parts.append(f"用了{evolved['native_calls']}次新工具但未替代 LLM 轮次")
    else:
        parts.append(f"成本降低，最大负贡献={driver}")
        if step_delta < 0:
            parts.append(f"少{-step_delta}步")
        if obs_ratio < -0.15:
            parts.append(f"平均 observation {obs_ratio:.0%}")
        if base["success"] and not evolved["success"]:
            parts.append("但 performance 回退，属于无效节省")
        elif evolved["native_calls"] > 0 and step_delta < 0:
            parts.append(f"新工具{evolved['native_calls']}次与轮次下降同时出现，但单次 rollout 不能证明因果")
        elif evolved["native_calls"] == 0:
            parts.append("主要来自 rollout 随机性/路径变短，非工具采用")
    if evolved.get("native_failures"):
        parts.append(f"新工具失败{evolved['native_failures']}次，产生恢复/回退开销")
    if evolved.get("truncated_steps", 0) > base.get("truncated_steps", 0):
        parts.append(f"含截断 observation 的 step {base.get('truncated_steps', 0)}→{evolved['truncated_steps']}")
    if evolved.get("error"):
        parts.append(f"同时存在运行异常={evolved['error']}")
    if base.get("error"):
        parts.append(f"no-evolve 运行异常={base['error']}")
    if base["success"] != evolved["success"]:
        parts.append("成功→失败" if base["success"] else "失败→成功")
    return "；".join(parts)


def mean(rows: list[dict], key: str) -> float:
    values = [float(row[key]) for row in rows if row.get(key) is not None]
    # Python 3.7 (the repository's base environment) does not provide fmean.
    return (sum(values) / len(values)) if values else 0.0


def analyze(benchmark: str) -> dict:
    paths = RUNS[benchmark]
    baseline = index_run(paths["baseline"], benchmark)
    evolved = index_run(paths["evolved"], benchmark)
    expected = (paths["work"] / "final_eval_cases.txt").read_text(encoding="utf-8").split()
    pairs = []
    for cid in expected:
        base = baseline.get(cid, {"case": cid, "trajectory": False, "error": "missing baseline"})
        evo = evolved.get(cid, {"case": cid, "trajectory": False, "error": "missing evolved trial"})
        row = {"case": cid, "baseline": base, "evolved": evo}
        if base.get("trajectory") and evo.get("trajectory"):
            row.update({
                "cost_delta": evo["cost"] - base["cost"],
                "cost_delta_pct": ((evo["cost"] / base["cost"] - 1) if base["cost"] else None),
                "step_delta": evo["steps"] - base["steps"],
                "obs_avg_delta": evo["avg_obs_tokens_est"] - base["avg_obs_tokens_est"],
                "success_delta": int(evo["success"]) - int(base["success"]),
            })
        row["reason"] = reason(base, evo)
        pairs.append(row)
    valid = [row for row in pairs if row["baseline"].get("trajectory") and row["evolved"].get("trajectory")]
    base_valid = [row["baseline"] for row in valid]
    evo_valid = [row["evolved"] for row in valid]
    base_all = [row["baseline"] for row in pairs if row["baseline"].get("trajectory")]
    increases = [row for row in valid if row["cost_delta"] > 1e-9]
    decreases = [row for row in valid if row["cost_delta"] < -1e-9]
    summary = {
        "expected": len(expected), "valid_pairs": len(valid),
        "baseline_trajectories": len(base_all),
        "evolved_trajectories": sum(row["evolved"].get("trajectory", False) for row in pairs),
        "baseline_total_cost_64": sum(row["cost"] for row in base_all),
        "paired_baseline_cost": sum(row["cost"] for row in base_valid),
        "paired_evolved_cost": sum(row["cost"] for row in evo_valid),
        "paired_cost_delta": sum(row["cost_delta"] for row in valid),
        "cost_increase_cases": len(increases), "cost_decrease_cases": len(decreases),
        "baseline_successes_valid": sum(row["success"] for row in base_valid),
        "evolved_successes_valid": sum(row["success"] for row in evo_valid),
        "success_regressions": sum(row.get("success_delta") == -1 for row in valid),
        "success_improvements": sum(row.get("success_delta") == 1 for row in valid),
    }
    for prefix, rows in (("baseline", base_valid), ("evolved", evo_valid)):
        for key in ("cost", "steps", "avg_obs_tokens_est", "obs_tokens_est", "new_tokens",
                    "cached_tokens", "output_tokens", "native_calls", "bash_calls",
                    "repeated_calls", "first_action_prompt_tokens", "user_message_chars"):
            summary[f"{prefix}_mean_{key}"] = mean(rows, key)
        summary[f"{prefix}_native_adoption_cases"] = sum(row["native_calls"] > 0 for row in rows)
        summary[f"{prefix}_total_native_calls"] = sum(row["native_calls"] for row in rows)
        summary[f"{prefix}_total_native_failures"] = sum(row["native_failures"] for row in rows)
        summary[f"{prefix}_total_truncated_steps"] = sum(row["truncated_steps"] for row in rows)
        summary[f"{prefix}_total_timeout_steps"] = sum(row["timeout_steps"] for row in rows)
    return {"benchmark": benchmark, "paths": {key: str(value) for key, value in paths.items()},
            "summary": summary, "pairs": pairs}


def fmt(value: Any, digits: int = 4) -> str:
    if value is None:
        return "NA"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def markdown_cases(analysis: dict) -> str:
    lines = [
        "| Case | no-evolve: success/cost/steps/obs-step | v9: success/cost/steps/obs-step/native/fail | Δcost | 原因 |",
        "|---|---:|---:|---:|---|",
    ]
    for row in analysis["pairs"]:
        base, evo = row["baseline"], row["evolved"]
        b = (f"{int(base.get('success', False))}/{fmt(base.get('cost'))}/{base.get('steps','NA')}/"
             f"{fmt(base.get('avg_obs_tokens_est'), 1)}") if base.get("trajectory") else "无 trajectory"
        e = (f"{int(evo.get('success', False))}/{fmt(evo.get('cost'))}/{evo.get('steps','NA')}/"
             f"{fmt(evo.get('avg_obs_tokens_est'), 1)}/{evo.get('native_calls',0)}/"
             f"{evo.get('native_failures',0)}") if evo.get("trajectory") else "无 trajectory"
        reason_text = str(row["reason"]).replace("|", "\\|").replace("\n", " ")
        lines.append(f"| `{row['case']}` | {b} | {e} | {fmt(row.get('cost_delta'))} | {reason_text} |")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("benchmark", choices=RUNS)
    parser.add_argument("--format", choices=("json", "summary", "cases"), default="summary")
    args = parser.parse_args()
    data = analyze(args.benchmark)
    if args.format == "json":
        print(json.dumps(data, ensure_ascii=False, indent=2))
    elif args.format == "cases":
        print(markdown_cases(data))
    else:
        print(json.dumps(data["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
