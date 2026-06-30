#!/usr/bin/env python3
"""Merge evolve + cost candidate pools, dedupe, and pre-filter on title+abstract
against the user's relevance criteria.

User project: reduce Agent cost (e.g. Code Agent) by EVOLVING tools/skills,
while maintaining performance.

Angles:
  A (method) = evolving/optimizing/discovering tools, skills, prompts, workflows for agents
  B (goal)   = reducing cost of LLM/code agents (token/context/call reduction, routing, etc.)
  C (core)   = both A and B

Output: _shortlist.json (candidates to download+read) + _rejected_pre.json.
"""
import json
import re
import os
import unicodedata

BASE = "/data00/home/fanmeihao/projects/CostReduce/survey/cost_reduce_evolve"
EVOLVE = os.path.join(BASE, "_candidates_evolve.json")
COST = os.path.join(BASE, "_candidates_cost.json")
OUT = os.path.join(BASE, "_shortlist.json")
OUT_REJ = os.path.join(BASE, "_rejected_pre.json")

# ---- keyword sets (lowercased substrings matched in title+abstract) ----
A_KW = [
    "self-evolv", "self-improv", "self-refin", "experience-driven",
    "skill discover", "skill library", "skill learn", "skill evolv", "skill self-evol",
    "skill hone", "skill refin", "skill creat", "skill accumul",
    "tool discover", "tool evolv", "tool creat", "tool learn", "tool mak",
    "tool use", "toolbuild", "tool build", "automatic tool", "autonomous tool",
    "procedural memory", "lifelong", "auto-curriculum", "auto curriculum",
    "prompt evolv", "prompt optim", "automatic prompt", "autoprompt",
    "workflow optim", "workflow generation", "workflow learn",
    "agent evolv", "evolving agent", "evolve agent",
    "meta-learning agent", "meta agent", "program of thought", "program synthesis agent",
    "voyager", "skillweaver", "autoSkill".lower(), "memskill".lower(),
    "tool library", "library of tool", "library of skill",
    "agentic skill", "skill self-evol", "test-time tool",
    "memory skill", "agent self-improv", "self-evolving llm",
    "agent skill", "agent tool", "learn to use tool", "learn tool",
]
B_KW = [
    "cost-efficient", "cost-efficient", "cost effective", "cost-effect",
    "cost-aware", "cost aware", "cost reduc", "cost sav", "lower cost", "cheaper",
    "frugal", "token-efficient", "token efficient", "token reduc", "token budget",
    "token compress", "token prun", "token saving", "token usage", "fewer token",
    "context compress", "context prun", "context filter", "context distill",
    "context summar", "long context", "compress prompt", "prompt compress",
    "trajectory compress", "trajectory prun", "trajectory summar",
    "interaction history", "history compress", "history summar",
    "efficient agent", "efficient llm", "efficient code agent", "efficient software",
    "efficient inference", "efficient multi-agent", "efficient reasoning",
    "model cascade", "llm cascade", "model routing", "routellm", "router",
    "speculative", "draft model", "small-to-large", "cheap model",
    "fewer call", "fewer step", "fewer interaction", "reduce call", "reduce step",
    "tool call", "api call", "call reduc", "step reduc",
    "test-time compute", "inference cost", "inference budget", "latency",
    "reasoning length", "reasoning budget", "concise reason", "concise think",
    "thinking budget", "reasoning token", "swe-bench", "code agent",
    "agent cost", "agent efficien", "agent latency", "agent throughput",
    "self-consistency", "interaction cost", "deploy cost",
    "model aggregat", "mixture of agent", "adaptive agent",
    "budget-aware", "length-aware", "efficiency",
]
# domains that, if dominant AND no agent/llm-cost signal, indicate off-topic
OFFTOPIC = [
    "clinical", "usmle", "medical", "healthcare", "patient", "disease",
    "drug", "molecule", "protein", "biomed", "radiology", "chemotherap",
    "finance", "stock", "bloomberg", "corporate governance",
    "education", "classroom", "student", "pedagog",
    "numpy", "decision tree", "grey wolf", "headache",
]
CONTEXT_KW = ["agent", "llm", "large language model", "language model", "gpt",
              "tool use", "tool-use", "reasoning model", "code agent", "codeagent",
              "software engineering", "autonomous agent", "agentic"]


def norm(s):
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()


def count_hits(text, kws):
    return sum(1 for k in kws if k in text)


def load_evolve():
    out = []
    d = json.load(open(EVOLVE, encoding="utf-8"))
    for w in d:
        title = w.get("title") or w.get("title_guess") or ""
        if not title:
            continue
        out.append({
            "title": title,
            "abstract": w.get("abstract") or "",
            "year": w.get("year") or "",
            "venue": w.get("venue") or "",
            "arxiv_id": w.get("arxiv_id") or "",
            "pdf_url": w.get("pdf_url") or "",
            "url": w.get("url") or "",
            "section": w.get("section") or "",
            "source": "evolve",
            "cited_by_count": 0,
        })
    return out


def load_cost():
    out = []
    d = json.load(open(COST, encoding="utf-8"))
    for w in d:
        title = w.get("title") or ""
        if not title:
            continue
        out.append({
            "title": title,
            "abstract": w.get("abstract") or "",
            "year": str(w.get("year") or ""),
            "venue": w.get("venue") or "",
            "arxiv_id": w.get("arxiv_id") or "",
            "pdf_url": w.get("pdf_url") or "",
            "url": w.get("landing_url") or w.get("doi") or "",
            "section": w.get("query") or "",
            "source": "cost",
            "cited_by_count": w.get("cited_by_count") or 0,
            "doi": w.get("doi") or "",
        })
    return out


def title_key(t):
    t = norm(t)
    t = re.sub(r'[^a-z0-9 ]', ' ', t)
    t = re.sub(r'\s+', ' ', t).strip()
    return t


def main():
    merged = load_evolve() + load_cost()
    # dedupe: by arxiv_id first, then by normalized title
    by_aid, by_title, dedup = {}, {}, []
    for w in merged:
        aid = w.get("arxiv_id")
        tk = title_key(w["title"])
        if aid and aid in by_aid:
            # merge: keep richer abstract, prefer evolve source tag
            base = by_aid[aid]
            if not base.get("abstract") and w.get("abstract"):
                base["abstract"] = w["abstract"]
            if w["source"] == "evolve" and base["source"] != "evolve":
                base["source"] = "evolve"
                base["section"] = w.get("section", base.get("section", ""))
            base["sources"] = sorted(set(base.get("sources", [base.get("source")]) + [w["source"]]))
            continue
        if tk in by_title:
            continue
        if aid:
            by_aid[aid] = w
        by_title[tk] = w
        w["sources"] = [w["source"]]
        dedup.append(w)
    print(f"[prefilter] merged {len(merged)} -> deduped {len(dedup)}", flush=True)

    shortlist, rejected = [], []
    for w in dedup:
        text = norm(w["title"] + " " + w.get("abstract", ""))
        a = count_hits(text, A_KW)
        b = count_hits(text, B_KW)
        ctx = count_hits(text, CONTEXT_KW)
        off = count_hits(text, OFFTOPIC)
        w["score_a"] = a
        w["score_b"] = b
        w["score_ctx"] = ctx
        w["score_off"] = off
        # tier guess
        if a >= 1 and b >= 1:
            tier = "core"
        elif a >= 1:
            tier = "method"
        elif b >= 1:
            tier = "goal"
        else:
            tier = ""
        w["tier_guess"] = tier
        # shortlist decision: needs a relevance signal + agent/llm context,
        # and not dominated by off-topic domain.
        keep = (a >= 1 or b >= 1) and ctx >= 1 and (off <= 2 or (a + b) >= 2)
        # boost: explicitly evolve-section or has arxiv id + decent score
        w["keep"] = keep
        (shortlist if keep else rejected).append(w)

    # rank shortlist: tier priority (core>method>goal), then score, then citations
    tier_rank = {"core": 0, "method": 1, "goal": 2}
    shortlist.sort(key=lambda w: (tier_rank.get(w["tier_guess"], 9),
                                  -(w["score_a"] + w["score_b"]),
                                  -(w["cited_by_count"] or 0),
                                  w["title"]))
    print(f"[prefilter] shortlist: {len(shortlist)}  rejected: {len(rejected)}", flush=True)
    from collections import Counter
    print("[prefilter] tier counts:", dict(Counter(w["tier_guess"] for w in shortlist)), flush=True)
    print("[prefilter] by source:", dict(Counter(w["source"] for w in shortlist)), flush=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(shortlist, f, ensure_ascii=False, indent=1)
    with open(OUT_REJ, "w", encoding="utf-8") as f:
        json.dump(rejected, f, ensure_ascii=False, indent=1)
    print(f"[prefilter] wrote {OUT}", flush=True)
    # preview top 30
    print("\n=== shortlist preview (top 30) ===")
    for w in shortlist[:30]:
        print(f"[{w['tier_guess']:6s}] a={w['score_a']} b={w['score_b']} cite={w['cited_by_count']:>5} "
              f"src={w['source']:6s} {w.get('year','')[:4]}  {w['title'][:62]}")


if __name__ == "__main__":
    main()
