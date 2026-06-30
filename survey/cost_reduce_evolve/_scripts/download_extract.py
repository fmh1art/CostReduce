#!/usr/bin/env python3
"""Download shortlisted PDFs + extract text.

- arxiv_id  -> https://arxiv.org/pdf/{id}.pdf
- else resolve from url (openreview/neurips/mlr/aclanthology/acm)
- download via proxy (8 threads), extract full text + concise slice (pymupdf)
- outputs: _staging/pdf/<slug>.pdf, _staging/md/<slug>.md (full),
           _staging/concise/<slug>.md (abstract+intro slice), _staging/manifest.json
"""
import json
import os
import re
import time
import threading
import html as _html
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse

import requests

try:
    import fitz  # pymupdf
except Exception:
    fitz = None

BASE = "/data00/home/fanmeihao/projects/CostReduce/survey/cost_reduce_evolve"
SHORT = os.path.join(BASE, "_shortlist.json")
PDF_DIR = os.path.join(BASE, "_staging", "pdf")
MD_DIR = os.path.join(BASE, "_staging", "md")
CC_DIR = os.path.join(BASE, "_staging", "concise")
MANIFEST = os.path.join(BASE, "_staging", "manifest.json")
LOG = os.path.join(BASE, "_logs", "download_extract.log")

PROXY = {"http": "http://sys-proxy-rd-relay.byted.org:8118",
         "https": "http://sys-proxy-rd-relay.byted.org:8118"}
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
           "Accept": "application/pdf,application/octet-stream,*/*"}
for d in (PDF_DIR, MD_DIR, CC_DIR):
    os.makedirs(d, exist_ok=True)

_tls = threading.local()
def sess():
    if not hasattr(_tls, "s"):
        s = requests.Session(); s.proxies = PROXY; s.headers.update(HEADERS)
        _tls.s = s
    return _tls.s


def slug(w):
    aid = w.get("arxiv_id")
    if aid:
        return aid.replace(".", "_")
    t = re.sub(r"[^\w\s-]", "", w.get("title", "") or "").strip()
    t = re.sub(r"\s+", "_", t).lower()[:80]
    return t or "untitled"


def arxiv_pdf_url(url, aid):
    if aid:
        return f"https://arxiv.org/pdf/{aid}.pdf"
    m = re.search(r'arxiv\.org/(?:abs|pdf)/([0-9]{4}\.[0-9]{4,5})', url or "")
    if m:
        return f"https://arxiv.org/pdf/{m.group(1)}.pdf"
    return None


def resolve_pdf_url(url):
    """Resolve non-arxiv paper-page URL to a direct PDF (best-effort)."""
    if not url:
        return None
    if "openreview.net/forum" in url:
        return url.replace("/forum?", "/pdf?")
    if "openreview.net/pdf" in url:
        return url
    m = re.search(r'proceedings\.neurips\.cc/paper_files/paper/(\d+)/hash/([a-f0-9]+)-Abstract(-[^.]+)?\.html', url)
    if m:
        y, h, suf = m.group(1), m.group(2), m.group(3) or ""
        return f"https://proceedings.neurips.cc/paper_files/paper/{y}/file/{h}-Paper{suf}.pdf"
    if "proceedings.mlr.press" in url and url.endswith(".html"):
        return url[:-5] + ".pdf"
    if "aclanthology.org" in url:
        return url.rstrip("/") + ".pdf"
    if url.lower().endswith(".pdf"):
        return url
    return None


def try_download(pdf_url, fpath):
    s = sess()
    r = s.get(pdf_url, timeout=60, stream=True, allow_redirects=True)
    if r.status_code != 200:
        return False, f"http_{r.status_code}"
    ct = r.headers.get("Content-Type", "").lower()
    if "pdf" not in ct and "octet-stream" not in ct:
        return False, f"not_pdf:{ct[:30]}"
    with open(fpath, "wb") as f:
        for c in r.iter_content(65536):
            f.write(c)
    sz = os.path.getsize(fpath)
    if sz < 10240:
        os.remove(fpath)
        return False, f"too_small_{sz}"
    return True, sz


def extract(fpath, slug):
    """Extract full text + concise (abstract+intro) slice. Returns (full_ok, concise_text)."""
    if fitz is None:
        return False, ""
    try:
        doc = fitz.open(fpath)
    except Exception as e:
        return False, f"open_err:{e}"
    pages = []
    for i, pg in enumerate(doc):
        if i >= 18:
            break
        pages.append(pg.get_text("text"))
    doc.close()
    full = "\n".join(pages)
    full = re.sub(r'\n{3,}', '\n\n', full)
    with open(os.path.join(MD_DIR, slug + ".md"), "w", encoding="utf-8") as f:
        f.write(full)
    # concise: first ~4500 chars usually covers title/abstract/intro
    concise = full[:4500]
    with open(os.path.join(CC_DIR, slug + ".md"), "w", encoding="utf-8") as f:
        f.write(concise)
    return True, concise


def process(w):
    s = slug(w)
    pdf_path = os.path.join(PDF_DIR, s + ".pdf")
    rec = {"slug": s, "title": w["title"], "arxiv_id": w.get("arxiv_id", ""),
           "url": w.get("url", ""), "tier_guess": w.get("tier_guess", ""),
           "source": w.get("source", ""), "year": w.get("year", ""),
           "cited_by_count": w.get("cited_by_count", 0), "section": w.get("section", ""),
           "pdf_path": pdf_path, "status": "", "pdf_bytes": 0}
    if os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 10240:
        ok, info = True, os.path.getsize(pdf_path)
    else:
        pdf_url = arxiv_pdf_url(w.get("url", ""), w.get("arxiv_id")) or resolve_pdf_url(w.get("url", ""))
        if not pdf_url:
            rec["status"] = "no_pdf_url"
            return rec
        ok, info = False, ""
        for attempt in range(2):
            try:
                ok, info = try_download(pdf_url, pdf_path)
                if ok:
                    break
            except Exception as e:
                info = str(e)[:80]
            time.sleep(1.0)
        if not ok:
            rec["status"] = f"dl_fail:{info}"
            return rec
    rec["pdf_bytes"] = info if isinstance(info, int) else os.path.getsize(pdf_path)
    ok2, _ = extract(pdf_path, s)
    rec["status"] = "ok" if ok2 else "extract_fail"
    return rec


def main():
    short = json.load(open(SHORT, encoding="utf-8"))
    # only attempt those with arxiv_id or paper-host url
    def has_easy(w):
        if w.get("arxiv_id"):
            return True
        h = urlparse(w.get("url", "") or "").netloc.lower()
        return any(x in h for x in ("openreview.net", "proceedings.neurips.cc",
                                    "proceedings.mlr.press", "aclanthology.org", "dl.acm.org"))
    todo = [w for w in short if has_easy(w)]
    print(f"[dl] shortlist={len(short)} attempt={len(todo)}", flush=True)
    manifest = {}
    done = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(process, w): w for w in todo}
        for fut in as_completed(futs):
            done += 1
            try:
                rec = fut.result()
            except Exception as e:
                rec = {"slug": "?", "status": f"exc:{e}", "title": ""}
            manifest[rec.get("slug", "?")] = rec
            if done % 25 == 0 or done == len(todo):
                ok = sum(1 for v in manifest.values() if v.get("status") == "ok")
                print(f"[dl] {done}/{len(todo)} ok={ok} ({time.time()-t0:.0f}s)", flush=True)
    json.dump(manifest, open(MANIFEST, "w"), ensure_ascii=False, indent=1)
    ok = [v for v in manifest.values() if v.get("status") == "ok"]
    fail = [v for v in manifest.values() if v.get("status") != "ok"]
    with open(LOG, "w", encoding="utf-8") as f:
        f.write(f"ok={len(ok)} fail={len(fail)}\n\n== OK ==\n")
        for v in sorted(ok, key=lambda x: x.get("tier_guess", "")):
            f.write(f"[{v['tier_guess']:6s}] {v['slug']:20s} {v['title'][:70]}\n")
        f.write("\n== FAIL ==\n")
        for v in fail:
            f.write(f"[{v.get('tier_guess',''):6s}] {v['slug']:20s} {v.get('status',''):30s} {v.get('title','')[:60]}\n")
    print(f"[dl] DONE ok={len(ok)} fail={len(fail)} -> {MANIFEST}", flush=True)


if __name__ == "__main__":
    main()
