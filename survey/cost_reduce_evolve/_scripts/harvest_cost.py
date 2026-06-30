#!/usr/bin/env python3
"""Harvest cost/efficiency-angle papers from OpenAlex (direct, no proxy).

OpenAlex is open & unthrottled. We run targeted search queries, reconstruct
abstracts from the inverted index, and pull title/year/venue/doi/arxiv_id/pdf_url/
cited_by_count. Output: _candidates_cost.json
"""
import json
import os
import time
import urllib.parse
import requests

# OpenAlex works DIRECT (no proxy). Do not set proxies.
BASE = "https://api.openalex.org/works"
MAILTO = "research@costreduce.local"
OUT = "/data00/home/fanmeihao/projects/CostReduce/survey/cost_reduce_evolve/_candidates_cost.json"

FIELDS = "id,title,abstract_inverted_index,publication_year,primary_location,cited_by_count,doi,type,open_access"
SELECT = FIELDS  # /works collection uses select=

# OpenAlex works DIRECT (no proxy). Force no proxy even if env sets one.
S = requests.Session()
S.trust_env = False
S.headers.update({"User-Agent": "costreduce-survey/1.0 (mailto:research@costreduce.local)"})

QUERIES = [
    "cost-efficient LLM agent",
    "cost-aware LLM agent",
    "token reduction large language model inference",
    "context compression LLM agent",
    "LLM cascade model routing cost",
    "frugal language model query",
    "efficient software engineering agent code",
    "LLM agent skill library skill discovery",
    "self-evolving LLM agent",
    "prompt optimization efficient agent",
    "multi-agent LLM cost efficient",
    "tool use LLM agent efficient",
    "efficient inference large language model survey",
    "trajectory compression agent memory",
    "speculative decoding agent",
    "LLM agent token budget reasoning",
]


def reconstruct_abstract(inv):
    if not inv:
        return ""
    pos = []
    for word, idxs in inv.items():
        for i in idxs:
            pos.append((i, word))
    pos.sort()
    return " ".join(w for _, w in pos)


def arxiv_id_from(doi, pdf_url, landing):
    for s in (doi or "", pdf_url or "", landing or ""):
        if not s:
            continue
        import re
        m = re.search(r'(?:arxiv\.org/(?:abs|pdf)/|arxiv\.org/abs/|10\.48550/arXiv\.?)([0-9]{4}\.[0-9]{4,5})', str(s), re.IGNORECASE)
        if m:
            return m.group(1)
        m = re.search(r'\b([0-9]{4}\.[0-9]{4,5})\b', str(s))
        # only trust the doi/pdf form above; avoid random 4.4 matches
    return None


def fetch_query(q, per_page=40):
    # date filter: LLM-agent era (post ChatGPT). Avoids classic high-cited noise.
    url = (f"{BASE}?search={urllib.parse.quote(q)}"
           f"&filter=from_publication_date:2022-08-01"
           f"&per_page={per_page}&mailto={MAILTO}&select={SELECT}")
    r = S.get(url, timeout=30)
    r.raise_for_status()
    return r.json().get("results", [])


def main():
    by_key = {}
    for i, q in enumerate(QUERIES):
        try:
            results = fetch_query(q)
        except Exception as e:
            print(f"[{i+1}/{len(QUERIES)}] '{q}' -> ERR {str(e)[:80]}", flush=True)
            time.sleep(2)
            continue
        added = 0
        for w in results:
            title = (w.get("title") or "").strip()
            if not title:
                continue
            loc = w.get("primary_location") or {}
            pdf_url = (loc.get("pdf") or {}).get("url") if loc else None
            landing = (loc.get("landing_page") or {}).get("url") if loc else None
            source = (loc.get("source") or {}).get("display_name") if loc else None
            doi = w.get("doi") or ""
            arxiv_id = arxiv_id_from(doi, pdf_url, landing)
            key = arxiv_id or (doi or "") or title.lower()
            rec = {
                "title": title,
                "abstract": reconstruct_abstract(w.get("abstract_inverted_index")),
                "year": w.get("publication_year"),
                "venue": source or "",
                "doi": doi,
                "arxiv_id": arxiv_id,
                "pdf_url": pdf_url or "",
                "landing_url": landing or "",
                "cited_by_count": w.get("cited_by_count", 0),
                "type": w.get("type", ""),
                "query": q,
            }
            if key not in by_key:
                by_key[key] = rec
                added += 1
            else:
                # merge queries
                bq = by_key[key].get("query", "")
                if q not in bq:
                    by_key[key]["query"] = bq + " | " + q
        print(f"[{i+1}/{len(QUERIES)}] '{q}' -> {len(results)} hits, +{added} new (total {len(by_key)})", flush=True)
        time.sleep(0.3)
    out = sorted(by_key.values(), key=lambda x: (-(x.get("cited_by_count") or 0), x.get("title", "")))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n[cost] total unique: {len(out)}")
    print(f"[cost] with arxiv_id: {sum(1 for x in out if x.get('arxiv_id'))}")
    print(f"[cost] with pdf_url: {sum(1 for x in out if x.get('pdf_url'))}")
    print(f"[cost] with abstract: {sum(1 for x in out if x.get('abstract'))}")
    print(f"[cost] wrote {OUT}")


if __name__ == "__main__":
    main()
