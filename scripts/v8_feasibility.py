#!/usr/bin/env python3
"""v8 feasibility pre-experiment on results/prep (no re-rollout).

Tests three v8 core assumptions against real baseline trajectories:

  A. Turn-collapse premise: are multi-step read/search ops spread across
     SEPARATE LLM turns (collapsible -> turn savings), or already batched
     into one compound bash turn (no turn savings, only obs savings)?
  B. Cost structure: with REAL prices (input=1, output=2, cached=0.02 yuan/M),
     where does per-task cost actually go (uncached-input / cached-input /
     completion), and how much is long-observation "context exposure"?
  C. Training-data supply: how many trajectories qualify as "successful"
     under strict vs relaxed definitions, per benchmark?

Reads only files already on disk under results/prep/runs/<bench>/.
"""
from __future__ import annotations

import json
import re
import glob
import os
from collections import Counter, defaultdict

# Real prices (yuan per 1e6 tokens) from _config/deepseekv4_flash.yaml
P_IN, P_OUT, P_CACHE = 1.0, 2.0, 0.02

BENCHES = {
    "deep-swe": "results/prep/runs/deep-swe/*/*",
    "swe-atlas-qa": "results/prep/runs/swe-atlas-qa/*/*",
    "swe-atlas-tw": "results/prep/runs/swe-atlas-tw/*/*",
    "swebench-verified": "results/prep/runs/swebench-verified/*/*",
}

# ---- op classification (deterministic, rejectable) -------------------------
OP_RULES = [
    ("VERIFY", re.compile(r"\b(pytest|py\.test|tox|go\s+test|cargo\s+test|npm\s+(?:run\s+)?test|jest|runtests\.py|unittest)\b")),
    ("VCS",    re.compile(r"\bgit\s+(diff|status|log|show|add|commit|stash|checkout|apply)\b")),
    ("WRITE",  re.compile(r"(>>?|\btee\b|\bsed\s+-i\b|\bapply_patch\b|\bpatch\b|<<\s*'?EOF|\bcat\s*>)")),
    ("SEARCH", re.compile(r"\b(grep|egrep|rg|ripgrep|ack|git\s+grep)\b")),
    ("FIND",   re.compile(r"\b(find|fd|ls|git\s+ls-files|tree)\b")),
    ("READ",   re.compile(r"\b(cat|sed\s+-n|head|tail|less|more|nl|wc)\b")),
]
CONNECTORS = re.compile(r"&&|\|\||\||;")


def classify_subcmd(sub: str) -> str:
    for op, rx in OP_RULES:
        if rx.search(sub):
            return op
    return "OTHER"


def parse_command_ops(command: str):
    """Split a bash command on connectors, classify each sub-command.
    `cd` and `head`/`tail` after a pipe are output-limit modifiers, not ops."""
    subs = [s.strip() for s in CONNECTORS.split(command) if s.strip()]
    ops = []
    for s in subs:
        if re.match(r"^cd\b", s):
            continue  # cwd change, not an op
        ops.append(classify_subcmd(s))
    return ops or ["OTHER"]


def obs_text(step) -> str:
    obs = step.get("observation")
    if isinstance(obs, dict):
        parts = []
        for r in obs.get("results", []) or []:
            if isinstance(r, dict):
                parts.append(str(r.get("content", "")))
            else:
                parts.append(str(r))
        return "\n".join(parts)
    if isinstance(obs, str):
        return obs
    return ""


def load_reward(case_dir) -> dict:
    """Return a dict of relaxed success signals for one case."""
    out = {"reward": None, "partial": None, "f2p": None}
    rj = os.path.join(case_dir, "verifier", "reward.json")
    if os.path.isfile(rj):
        try:
            d = json.load(open(rj))
            out["reward"] = d.get("reward")
            out["partial"] = d.get("partial")
            out["f2p"] = d.get("f2p")
            return out
        except Exception:
            pass
    resj = os.path.join(case_dir, "result.json")
    if os.path.isfile(resj):
        try:
            d = json.load(open(resj))
            vr = (d.get("verifier_result") or {}).get("rewards") or {}
            out["reward"] = vr.get("reward")
        except Exception:
            pass
    return out


def analyze_trajectory(path):
    d = json.load(open(path))
    steps = d.get("steps", [])
    agent_steps = [s for s in steps if s.get("source") == "agent" and s.get("tool_calls")]

    # ---- A: batching / turn-collapse ----
    n_turns = len(agent_steps)
    turn_op_profiles = []      # list of (set_of_ops, n_primitive_read_search_ops, is_readsearch_only)
    multi_op_turns = 0
    for s in agent_steps:
        all_ops = []
        for tc in s.get("tool_calls", []):
            fn = tc.get("function_name") or (tc.get("function") or {}).get("name")
            args = tc.get("arguments") or (tc.get("function") or {}).get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {}
            if fn == "bash":
                cmd = args.get("command", "") if isinstance(args, dict) else ""
                all_ops += parse_command_ops(cmd)
            else:
                all_ops.append((fn or "TOOL").upper())
        opset = set(all_ops)
        rs_ops = [o for o in all_ops if o in ("FIND", "SEARCH", "READ")]
        is_rs_only = bool(all_ops) and all(o in ("FIND", "SEARCH", "READ") for o in all_ops)
        if len(all_ops) >= 2:
            multi_op_turns += 1
        turn_op_profiles.append((opset, len(rs_ops), is_rs_only, all_ops))

    # maximal runs of consecutive read/search-only turns -> burst lengths
    bursts = []
    cur = 0
    for _, _, is_rs, _ in turn_op_profiles:
        if is_rs:
            cur += 1
        else:
            if cur:
                bursts.append(cur)
            cur = 0
    if cur:
        bursts.append(cur)
    collapsible_turns = sum(b - 1 for b in bursts if b >= 2)  # turns removable by collapsing bursts

    # ---- B: cost structure (real prices) ----
    cost_unc = cost_cache = cost_out = 0.0
    obs_tokens_by_op = Counter()
    per_turn = []  # (obs_tokens, dominant_op)
    for s in agent_steps:
        m = s.get("metrics") or {}
        prompt = int(m.get("prompt_tokens", 0) or 0)
        cached = min(prompt, int(m.get("cached_tokens", 0) or 0))
        completion = int(m.get("completion_tokens", 0) or 0)
        cost_unc += max(0, prompt - cached) * P_IN / 1e6
        cost_cache += cached * P_CACHE / 1e6
        cost_out += completion * P_OUT / 1e6
        otoks = len(obs_text(s)) / 4.0
        # attribute observation to dominant op of this turn
        _, _, _, all_ops = None, None, None, None
        # recompute op for this step cheaply
        dom = "OTHER"
        oplist = []
        for tc in s.get("tool_calls", []):
            fn = tc.get("function_name") or (tc.get("function") or {}).get("name")
            args = tc.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {}
            if fn == "bash":
                oplist += parse_command_ops(args.get("command", "") if isinstance(args, dict) else "")
            else:
                oplist.append((fn or "TOOL").upper())
        if oplist:
            dom = Counter(oplist).most_common(1)[0][0]
        obs_tokens_by_op[dom] += otoks
        per_turn.append((otoks, dom))

    total_cost = cost_unc + cost_cache + cost_out

    # observation exposure: an obs produced at turn i is re-carried in prompts of
    # turns i+1..N, almost entirely as CACHED tokens. Approx exposure cost:
    exposure_cost = 0.0
    N = len(per_turn)
    for i, (otoks, _dom) in enumerate(per_turn):
        remaining = N - 1 - i
        exposure_cost += otoks * (P_IN + remaining * P_CACHE) / 1e6

    return {
        "n_turns": n_turns,
        "multi_op_turns": multi_op_turns,
        "bursts": bursts,
        "n_bursts_ge2": sum(1 for b in bursts if b >= 2),
        "collapsible_turns": collapsible_turns,
        "cost_unc": cost_unc,
        "cost_cache": cost_cache,
        "cost_out": cost_out,
        "total_cost": total_cost,
        "exposure_cost": exposure_cost,
        "obs_tokens_by_op": dict(obs_tokens_by_op),
        "total_obs_tokens": sum(obs_tokens_by_op.values()),
    }


def main():
    report = {}
    for bench, pat in BENCHES.items():
        traj_paths = sorted(glob.glob(os.path.join(pat, "agent", "trajectory.json")))
        if not traj_paths:
            continue
        agg = defaultdict(float)
        obs_by_op = Counter()
        n = 0
        all_bursts = []
        multi_op_sum = turns_sum = collapsible_sum = 0
        # success accounting
        succ = {"reward1": 0, "reward_gt0": 0, "partial_ge05": 0, "f2p_gt0": 0, "total": 0}
        for tp in traj_paths:
            case_dir = os.path.dirname(os.path.dirname(tp))
            try:
                r = analyze_trajectory(tp)
            except Exception as e:
                print("  skip", tp, e)
                continue
            n += 1
            for k in ("cost_unc", "cost_cache", "cost_out", "total_cost",
                      "exposure_cost", "total_obs_tokens"):
                agg[k] += r[k]
            for op, t in r["obs_tokens_by_op"].items():
                obs_by_op[op] += t
            all_bursts += r["bursts"]
            multi_op_sum += r["multi_op_turns"]
            turns_sum += r["n_turns"]
            collapsible_sum += r["collapsible_turns"]
            # success
            rw = load_reward(case_dir)
            succ["total"] += 1
            if rw["reward"] == 1 or rw["reward"] == 1.0:
                succ["reward1"] += 1
            if (rw["reward"] or 0) > 0:
                succ["reward_gt0"] += 1
            if (rw["partial"] or 0) >= 0.5:
                succ["partial_ge05"] += 1
            if (rw["f2p"] or 0) > 0:
                succ["f2p_gt0"] += 1

        bursts_ge2 = [b for b in all_bursts if b >= 2]
        report[bench] = {
            "n_traj": n,
            "avg_turns": round(turns_sum / n, 1),
            "pct_multi_op_turns": round(100 * multi_op_sum / max(1, turns_sum), 1),
            "read_search_bursts": len(all_bursts),
            "bursts_ge2": len(bursts_ge2),
            "avg_burst_len_ge2": round(sum(bursts_ge2) / max(1, len(bursts_ge2)), 2),
            "collapsible_turns_total": collapsible_sum,
            "pct_turns_collapsible": round(100 * collapsible_sum / max(1, turns_sum), 1),
            "cost_split_pct": {
                "uncached_input": round(100 * agg["cost_unc"] / max(1e-9, agg["total_cost"]), 1),
                "cached_input": round(100 * agg["cost_cache"] / max(1e-9, agg["total_cost"]), 1),
                "completion": round(100 * agg["cost_out"] / max(1e-9, agg["total_cost"]), 1),
            },
            "exposure_over_total_cost_pct": round(100 * agg["exposure_cost"] / max(1e-9, agg["total_cost"]), 1),
            "obs_tokens_by_op_pct": {
                op: round(100 * t / max(1, agg["total_obs_tokens"]), 1)
                for op, t in obs_by_op.most_common()
            },
            "success": succ,
        }
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
