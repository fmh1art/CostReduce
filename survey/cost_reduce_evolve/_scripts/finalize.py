#!/usr/bin/env python3
"""Finalize: copy kept PDFs to pdfs/, write index.md + _candidates.json.

- reads _staging/manifest.json + _scripts/_decisions.py
- copies each 'keep' PDF to survey/cost_reduce_evolve/pdfs/<readable>.pdf
- index.md grouped by tier (core / method / goal) + abstract-only section
- _candidates.json: every judged paper w/ decision, tier, note, metadata
"""
import json, os, re, sys, shutil, html
sys.path.insert(0, os.path.dirname(__file__))
from _decisions import DECISIONS

BASE = "/data00/home/fanmeihao/projects/CostReduce/survey/cost_reduce_evolve"
MAN = os.path.join(BASE, "_staging", "manifest.json")
SHORT = os.path.join(BASE, "_shortlist.json")
PDF_SRC = os.path.join(BASE, "_staging", "pdf")
PDF_DST = os.path.join(BASE, "pdfs")
INDEX = os.path.join(BASE, "index.md")
CAND = os.path.join(BASE, "_candidates.json")
os.makedirs(PDF_DST, exist_ok=True)

man = json.load(open(MAN))


def safe(t, n=70):
    t = re.sub(r"[^\w\s.-]", "", t or "").strip()
    t = re.sub(r"\s+", "_", t)
    return t[:n].rstrip("_") or "untitled"


def pdf_for(slug):
    return os.path.join(PDF_SRC, slug + ".pdf")


# ---- copy keeps ----
kept, rejected = [], []
for slug, (dec, tier, note) in DECISIONS.items():
    m = man.get(slug, {})
    rec = {
        "slug": slug, "title": m.get("title", "") or slug, "arxiv_id": m.get("arxiv_id", ""),
        "url": m.get("url", ""), "year": str(m.get("year", "") or ""),
        "venue": m.get("venue", ""), "tier": tier, "decision": dec, "note": note,
        "source": m.get("source", ""), "cited_by_count": m.get("cited_by_count", 0),
    }
    if dec == "keep":
        src = pdf_for(slug)
        if os.path.exists(src) and os.path.getsize(src) > 10240:
            aid = m.get("arxiv_id", "")
            yr = rec["year"] or ("20"+aid[:2] if aid and aid[0] in "23" else "")
            name = f"{yr}_{safe(m.get('title','') or slug, 60)}.pdf"
            if aid and not name.startswith(aid.replace('.', '_')):
                pass
            dst = os.path.join(PDF_DST, name)
            # uniqueness
            base, ext = os.path.splitext(name); i = 2
            while os.path.exists(dst) and os.path.getsize(dst) != os.path.getsize(src):
                dst = os.path.join(PDF_DST, f"{base}_{i}{ext}"); i += 1
            if not os.path.exists(dst):
                shutil.copy2(src, dst)
            rec["pdf_file"] = os.path.basename(dst)
        else:
            rec["pdf_file"] = ""  # missing pdf
        kept.append(rec)
    else:
        rejected.append(rec)

# ---- abstract-only relevant (no local PDF) from shortlist ----
# curated title-substring -> (tier, note)
ABSTRACT_ONLY = [
    ("Optima: Optimizing effectiveness and efficiency", "core", "Optimizes effectiveness AND efficiency of LLM multi-agent systems (efficiency-driven)."),
    ("MasRouter", "goal", "Learns to route LLMs for multi-agent systems (cost)."),
    ("Batch Prompting", "goal", "Efficient inference with LLM APIs via batching (cost)."),
    ("Token-Budget-Aware LLM Reasoning", "goal", "Reasons under a token budget for cost control."),
    ("Reasoning in Token Economies", "goal", "Budget-aware evaluation of LLM reasoning (cost)."),
    ("Compressing Context to Enhance Inference Efficiency", "goal", "Selective context compression for inference efficiency."),
    ("data-augmented model routing framework", "goal", "Data-augmented model routing for efficient LLM deployment."),
    ("TensorOpera Router", "goal", "Multi-model router for efficient LLM inference."),
    ("Select-then-Route", "goal", "Taxonomy-guided routing for LLMs (cost)."),
    ("Frugal Prompting for Dialog", "goal", "Frugal prompting for dialog models (cost)."),
    ("Accelerating PayPal's Commerce Agent with Speculative Decoding", "goal", "Speculative decoding applied to a production commerce agent (cost)."),
    ("SAGE: Self-evolving Agents with Reflective", "method", "Self-evolving agents with reflective + memory-augmented abilities."),
    ("Sirius: Self-improving multi-agent", "method", "Self-improving multi-agent systems via bootstrapping."),
    ("Agentic skill discovery", "core", "Agentic skill discovery (title-only; verify venue)."),
    ("LLM-Based Agents for Tool Learning: A Survey", "method", "Survey of LLM agents for tool learning."),
    ("Survey on the Memory Mechanism of Large Language Model-based Agents", "method", "Survey of agent memory mechanisms."),
    ("Small LLMs Are Weak Tool Learners: A Multi-LLM Agent", "method", "Multi-LLM agent where small models collaborate on tool use."),
    ("CodeAgent: Enhancing Code Generation with Tool-Integrated Agent", "method", "Tool-integrated code-generation agent."),
]
short = json.load(open(SHORT))
ao_rows = []
for sub, tier, note in ABSTRACT_ONLY:
    hit = next((w for w in short if sub.lower() in (w.get("title", "") or "").lower()), None)
    if not hit:
        continue
    ao_rows.append({
        "title": hit["title"], "year": str(hit.get("year", "") or ""),
        "arxiv_id": hit.get("arxiv_id", ""), "url": hit.get("landing_url") or hit.get("url", ""),
        "doi": hit.get("doi", ""), "tier": tier, "note": note,
        "abstract": (hit.get("abstract", "") or "")[:500],
    })

# ---- write _candidates.json ----
json.dump({"kept": kept, "rejected": rejected, "abstract_only": ao_rows},
          open(CAND, "w"), ensure_ascii=False, indent=1)

# ---- write index.md ----
tier_order = {"core": 0, "method": 1, "goal": 2}
kept.sort(key=lambda r: (tier_order.get(r["tier"], 9), -len(r["note"]), r["title"]))

def stars(note):
    return " ★" if "***" in note or "★" in note or "landmark" in note.lower() else ""

lines = []
lines.append("# Survey: Cost Reduction for Agents via Evolving Tools/Skills\n")
lines.append("Curated paper set for the project: **reduce Agent cost (e.g. Code Agent) by evolving "
             "tools/skills, while maintaining performance.**\n")
lines.append(f"- Papers with local PDF: **{len(kept)}** "
             f"(core={sum(1 for r in kept if r['tier']=='core')}, "
             f"method={sum(1 for r in kept if r['tier']=='method')}, "
             f"goal={sum(1 for r in kept if r['tier']=='goal')})")
lines.append(f"- Rejected (off-topic / pure training infra): {len(rejected)}")
lines.append(f"- Relevant but no local PDF (abstract only): {len(ao_rows)}")
lines.append(f"- Total judged: {len(kept)+len(rejected)}\n")
lines.append("## Tier meaning\n")
lines.append("- **core** — evolves tools / skills / prompts / workflows / memory *for agents* "
             "(the project's METHOD). ★ = closest match to the project.\n")
lines.append("- **method** — broader self-evolving-agent / experience-memory / skill-library / "
             "tool-use background.\n")
lines.append("- **goal** — reduces agent / LLM cost (routing, cascade, prompt/context compression, "
             "token budget, efficient serving).\n")
lines.append("- PDFs are in `pdfs/`; extracted text in `_staging/md/`.\n")

cur_tier = None
for r in kept:
    if r["tier"] != cur_tier:
        cur_tier = r["tier"]
        lines.append(f"\n## {cur_tier.upper()}\n")
        lines.append("| Year | Title | Note | PDF | Source |")
        lines.append("|---|---|---|---|---|")
    yr = r["year"] or ""
    src = r["arxiv_id"] or r["url"][:40]
    pdf = r.get("pdf_file", "")
    lines.append(f"| {yr} | {r['title']}{stars(r['note'])} | {r['note']} | {pdf} | {src} |")

lines.append("\n## Relevant — no local PDF (abstract only)\n")
lines.append("These were judged relevant from title+abstract but have no easily-resolvable PDF "
             "(OpenAlex DOI without arXiv). Fetch on demand via the DOI/URL.\n")
lines.append("| Year | Title | Tier | Note | URL |")
lines.append("|---|---|---|---|---|")
for r in ao_rows:
    lines.append(f"| {r['year']} | {r['title']} | {r['tier']} | {r['note']} | {r['url'][:60]} |")

lines.append(f"\n---\n*Generated from {len(kept)+len(rejected)} judged papers; "
             f"see `_candidates.json` for full records incl. rejects.*\n")
open(INDEX, "w", encoding="utf-8").write("\n".join(lines))

print(f"kept={len(kept)} rejected={len(rejected)} abstract_only={len(ao_rows)}")
print(f"pdfs copied to {PDF_DST}: {len(os.listdir(PDF_DST))} files")
print(f"wrote {INDEX} and {CAND}")
