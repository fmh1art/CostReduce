#!/usr/bin/env python3
"""Supplement: fetch+verify+download a handful of landmark papers whose arxiv
id was missing from OpenAlex. Verify title vs expected substring to drop bad ids.
"""
import json, os, re, time, html
import requests

PROXY = {"http": "http://sys-proxy-rd-relay.byted.org:8118",
         "https": "http://sys-proxy-rd-relay.byted.org:8118"}
H = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
     "Accept": "text/html,*/*"}
BASE = "/data00/home/fanmeihao/projects/CostReduce/survey/cost_reduce_evolve"
PDF_DIR = os.path.join(BASE, "_staging", "pdf")
MD_DIR = os.path.join(BASE, "_staging", "md")
CC_DIR = os.path.join(BASE, "_staging", "concise")
MAN = os.path.join(BASE, "_staging", "manifest.json")
import fitz

# (arxiv_id, expect_substr_in_title, tier_guess, note)
CANDS = [
    ("2307.02291", "llmlingua", "goal", "LLMLingua: prompt compression for accelerated inference."),
    ("2310.06839", "llmlingua", "goal", "LongLLMLingua: prompt compression in long-context scenarios."),
    ("2308.10144", "expel", "core", "ExpeL: LLM agents are experiential learners (insight extraction)."),
    ("2211.01910", "prompt", "core", "APE: Large Language Models Are Human-Level Prompt Engineers."),
    ("2602.04837", "group-evolving", "core", "Group-Evolving Agents: open-ended self-improvement via experience sharing."),
    ("2305.01812", "creator", "core", "Creator: tool creation for disentangling abstract & concrete reasoning."),
    ("2310.10648", "batch", "goal", "Batch Prompting: efficient inference with LLM APIs."),
    ("2303.11366", "reflexion", "method", "Reflexion: language agents with verbal RL (self-reflection)."),
    ("2303.17651", "self-refine", "method", "Self-Refine: iterative refinement with self-feedback."),
    ("2302.04761", "toolformer", "method", "Toolformer: language models can teach themselves to use tools."),
    ("2305.17125", "ghost", "method", "Ghost in the Minecraft (GITM): decompose-plan-act w/ text memory."),
    ("2310.12823", "agent", "goal", "AgentTuning: enabling customized agent abilities via tuned RL."),
]

S = requests.Session(); S.proxies = PROXY; S.headers.update(H)
man = json.load(open(MAN))

def slug(aid): return aid.replace(".", "_")

def fetch_abs(aid):
    url = f"https://arxiv.org/abs/{aid}"
    r = S.get(url, timeout=20)
    if r.status_code != 200:
        return None, None
    t = r.text
    m = re.search(r'<meta name="citation_title" content="((?:[^"\'\\]|\\.)*)"', t)
    title = html.unescape(m.group(1)).strip() if m else ""
    ma = re.search(r'<meta name="citation_abstract" content="((?:[^"\'\\]|\\.)*)"', t)
    ab = html.unescape(ma.group(1)).strip() if ma else ""
    if not ab:
        bq = re.search(r'<blockquote[^>]*abstract[^>]*>(.*?)</blockquote>', t, re.DOTALL)
        if bq:
            ab = re.sub(r'<[^>]+>', ' ', bq.group(1))
            ab = re.sub(r'\s+', ' ', html.unescape(ab)).strip()
            ab = re.sub(r'^\s*Abstract:?\s*', '', ab)
    return title, ab

def download_extract(aid, title):
    s = slug(aid)
    pdf = os.path.join(PDF_DIR, s + ".pdf")
    if not (os.path.exists(pdf) and os.path.getsize(pdf) > 10240):
        r = S.get(f"https://arxiv.org/pdf/{aid}.pdf", timeout=60, stream=True)
        if r.status_code != 200:
            return None, f"http_{r.status_code}"
        with open(pdf, "wb") as f:
            for c in r.iter_content(65536):
                f.write(c)
    doc = fitz.open(pdf)
    pages = [doc[i].get_text("text") for i in range(min(18, len(doc)))]
    doc.close()
    full = re.sub(r'\n{3,}', '\n\n', "\n".join(pages))
    open(os.path.join(MD_DIR, s + ".md"), "w", encoding="utf-8").write(full)
    open(os.path.join(CC_DIR, s + ".md"), "w", encoding="utf-8").write(full[:4500])
    return os.path.getsize(pdf), "ok"

results = []
for aid, expect, tier, note in CANDS:
    s = slug(aid)
    if s in man and man[s].get("status") == "ok":
        print(f"[skip] {aid} already in manifest")
        results.append({"arxiv_id": aid, "slug": s, "title": man[s].get("title",""), "status": "already", "tier_guess": tier, "note": note})
        continue
    title, ab = fetch_abs(aid)
    if not title:
        print(f"[miss] {aid}: no title")
        results.append({"arxiv_id": aid, "slug": s, "title": "", "status": "no_title", "tier_guess": tier, "note": note})
        continue
    ok_id = expect.lower() in title.lower()
    sz, st = download_extract(aid, title) if ok_id else (None, "title_mismatch")
    print(f"[{'OK' if ok_id else 'BAD-ID'}] {aid} -> {title[:60]}  ({st})")
    results.append({"arxiv_id": aid, "slug": s, "title": title, "abstract": ab[:600], "url": f"https://arxiv.org/abs/{aid}",
                    "status": st, "tier_guess": tier, "note": note, "source": "supplement",
                    "year": aid[:2] and ("20"+aid[:2] if aid[:2]<'30' else '19'+aid[:2]) })
    time.sleep(0.5)

# merge into manifest
for r in results:
    man[r["slug"]] = {**man.get(r["slug"],{}), **r,
                      "title": r.get("title") or man.get(r["slug"],{}).get("title",""),
                      "pdf_path": os.path.join(PDF_DIR, r["slug"]+".pdf")}
json.dump(man, open(MAN, "w"), ensure_ascii=False, indent=1)
print("\nwritten. ok supplement:", sum(1 for r in results if r['status']=='ok' or r['status']=='already'))
