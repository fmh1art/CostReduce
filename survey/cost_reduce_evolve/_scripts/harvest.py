#!/usr/bin/env python3
"""Harvest paper metadata from the Awesome-Self-Evolving-Agents README.

Strategy: extract every [[Paper]](url) entry with its section + nearby title text.
Then fetch each URL and parse <meta name="citation_*"> tags (works for arxiv,
openreview, neurips, mlr, aclanthology, acm). For arxiv URLs also derive arxiv_id.
Output: _candidates_evolve.json
"""
import html
import json
import os
import re
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse

import requests

PROXY = {
    "http": "http://sys-proxy-rd-relay.byted.org:8118",
    "https": "http://sys-proxy-rd-relay.byted.org:8118",
}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
README = "/data00/home/fanmeihao/projects/CostReduce/survey/Awesome-Self-Evolving-Agents/README.md"
OUT = "/data00/home/fanmeihao/projects/CostReduce/survey/cost_reduce_evolve/_candidates_evolve.json"
LOG = "/data00/home/fanmeihao/projects/CostReduce/survey/cost_reduce_evolve/_logs/harvest_evolve.log"

_tls = threading.local()

def sess():
    if not hasattr(_tls, "s"):
        s = requests.Session()
        s.proxies = PROXY
        s.headers.update(HEADERS)
        _tls.s = s
    return _tls.s


def section_of(content, idx):
    """Find the nearest preceding ## heading for a position."""
    prefix = content[:idx]
    heads = list(re.finditer(r'^##\s+(.+)$', prefix, re.MULTILINE))
    if not heads:
        # top-level #
        heads = list(re.finditer(r'^#\s+(.+)$', prefix, re.MULTILINE))
    return heads[-1].group(1).strip() if heads else ""


def parse_readme():
    with open(README, encoding="utf-8") as f:
        content = f.read()
    # Only the research/survey/application sections (skip News/Trends/ToC/Citation)
    start = content.find("# 📚 Related Survey Papers")
    end = content.find("# 🍀 Citation")
    if start < 0:
        start = content.find("# 📜 Research Papers")
    if end < 0:
        end = len(content)
    body = content[start:end]

    entries = []
    seen = set()
    # match [[Paper]](url) or [\[Paper\]](url) etc.
    pat = re.compile(r'\[\[?\\?Paper\]?\]\(([^)]+)\)')
    for m in pat.finditer(body):
        url = m.group(1).strip()
        if not url.startswith("http"):
            if url.startswith("//"):
                url = "https:" + url
            elif url.startswith(":/"):
                url = "https" + url
            else:
                url = "https://" + url
        # skip github/web-only non-paper links
        host = urlparse(url).netloc.lower()
        if not host:
            continue
        # Look at the surrounding ~200 chars for a title guess
        a, b = max(0, m.start() - 200), min(len(body), m.end() + 50)
        ctx = body[a:b]
        # table cell title: first bolded/text token before [[Paper]]
        seg = body[a:m.start()]
        # take last meaningful line segment
        title_guess = ""
        for line in re.split(r'[\n|]', seg):
            line = re.sub(r'\*+', '', line).strip()
            line = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', line)  # link text
            if len(line) > 3 and not line.startswith('---'):
                title_guess = line
        key = url
        if key in seen:
            continue
        seen.add(key)
        entries.append({
            "url": url,
            "section": section_of(body, m.start()),
            "title_guess": title_guess[:160],
        })
    return entries


def arxiv_id_of(url):
    m = re.search(r'arxiv\.org/(?:abs|pdf)/([0-9]{4}\.[0-9]{4,5})(?:v\d+)?', url)
    return m.group(1) if m else None


PAPER_HOSTS = (
    "arxiv.org", "openreview.net", "proceedings.neurips.cc", "proceedings.mlr.press",
    "aclanthology.org", "dl.acm.org", "biorxiv.org", "pmc.ncbi.nlm.nih.gov",
    "nature.com", "sciencedirect.com", "ieeexplore.ieee.org", "link.springer.com",
    "openaccess.thecvf.com", "ojs.aaai.org", "proceedings.mlr.press",
)


def fetch_meta(entry):
    url = entry["url"]
    out = {**entry, "arxiv_id": arxiv_id_of(url), "title": "", "abstract": "",
           "year": "", "pdf_url": "", "venue": "", "status": ""}
    host = urlparse(url).netloc.lower()
    if not any(h in host for h in PAPER_HOSTS):
        out["status"] = "skip_nonpaper"
        return out
    try:
        s = sess()
        r = s.get(url, timeout=15, allow_redirects=True)
        if r.status_code != 200:
            out["status"] = f"http_{r.status_code}"
            return out
        text = r.text

        def meta(name):
            m = re.search(
                r'<meta\s+name=["\']' + re.escape(name) + r'["\']\s+content=["\']((?:[^"\'\\]|\\.)*)["\']',
                text, re.IGNORECASE)
            return html.unescape(m.group(1)).strip() if m else ""

        out["title"] = meta("citation_title")
        if not out["title"]:
            tm = re.search(r'<title[^>]*>(.*?)</title>', text, re.IGNORECASE | re.DOTALL)
            if tm:
                out["title"] = html.unescape(re.sub(r'\s+', ' ', tm.group(1))).strip()
        out["abstract"] = meta("citation_abstract") or meta("description")
        if not out["abstract"]:
            bq = re.search(r'<blockquote[^>]*class=["\'][^"\']*abstract[^"\']*["\'][^>]*>(.*?)</blockquote>',
                           text, re.IGNORECASE | re.DOTALL)
            if bq:
                ab = re.sub(r'<[^>]+>', ' ', bq.group(1))
                ab = html.unescape(re.sub(r'\s+', ' ', ab)).strip()
                ab = re.sub(r'^\s*Abstract:?\s*', '', ab)
                out["abstract"] = ab
        out["pdf_url"] = meta("citation_pdf_url") or ""
        d = meta("citation_publication_date") or meta("citation_online_date") or meta("citation_date")
        out["year"] = (d[:4] if d else "")
        out["venue"] = meta("citation_journal_title") or meta("citation_conference_title") or ""
        out["status"] = "ok" if out["title"] else "no_title"
        if out["arxiv_id"] and not out["pdf_url"]:
            out["pdf_url"] = f"https://arxiv.org/pdf/{out['arxiv_id']}.pdf"
    except Exception as e:
        out["status"] = f"err:{str(e)[:80]}"
    return out


def main():
    entries = parse_readme()
    print(f"[harvest] {len(entries)} paper-link entries found in README", flush=True)
    results = []
    done = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(fetch_meta, e): e for e in entries}
        for fut in as_completed(futs):
            done += 1
            try:
                res = fut.result()
            except Exception as e:
                res = {"url": "", "status": f"exc:{str(e)[:80]}", "title": "", "abstract": ""}
            results.append(res)
            if done % 25 == 0 or done == len(entries):
                print(f"[harvest] {done}/{len(entries)} ({time.time()-t0:.0f}s)", flush=True)
    # stats
    ok = [r for r in results if r.get("title")]
    with_arxiv = [r for r in ok if r.get("arxiv_id")]
    print(f"[harvest] got title for {len(ok)}/{len(results)}; arxiv:{len(with_arxiv)}", flush=True)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    with open(LOG, "w", encoding="utf-8") as f:
        for r in sorted(results, key=lambda x: (x.get("section",""), x.get("title",""))):
            f.write(f"[{r.get('status','?')}] {r.get('section','')[:30]:30s} | {r.get('title','')[:80]}\n")
            f.write(f"    url: {r.get('url','')}\n")
    print(f"[harvest] wrote {OUT}", flush=True)
    # print failed/empty for visibility
    fails = [r for r in results if not r.get("title")]
    print(f"[harvest] {len(fails)} entries with no title (likely non-paper/web-only):", flush=True)
    for r in fails[:15]:
        print(f"    - {r.get('url','')[:90]}", flush=True)


if __name__ == "__main__":
    main()
