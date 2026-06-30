# Cost-Reduce-by-Evolving-Tools — Paper Survey

Goal: collect papers relevant to **reducing Agent cost (e.g. Code Agent) by evolving
tools/skills, while maintaining performance**.

Start at **[`index.md`](index.md)** — the curated table, grouped by tier.

## Result

- **106 papers** with local PDF (`pdfs/`, 345 MB), judged by reading each PDF's
  abstract+intro (extracted text in `_staging/md/`).
  - `core` (40) — evolves tools / skills / prompts / workflows / memory *for agents*
    (the project's method). ★ marks the closest matches (e.g. **EvoRoute**, **AgentEvolver**,
    **LATM**, **SkillWeaver**, **AutoSkill**, **GEPA**, **ADAS**, **DSPy**, **Voyager**).
  - `method` (38) — broader self-evolving-agent / experience-memory / skill-library background.
  - `goal` (28) — reduces agent/LLM cost (routing: FrugalGPT, RouteLLM, Hybrid-LLM;
    compression: LLMLingua, LongLLMLingua, ReadAgent; budget: Token-Economy; serving: AIOS).
- **57 rejected** (pure RL-policy-training, benchmarks, model tech-reports, off-topic) —
  see `_candidates.json` (`rejected` array) for reasons.
- **18 relevant but no local PDF** (OpenAlex DOI without arXiv) — listed at the bottom of
  `index.md` ("abstract only"). Fetch on demand via the DOI/URL.

## Layout

```
index.md                 curated table (start here)
pdfs/                    the 106 kept PDFs (readable names: YYYY_Title.pdf)
_candidates.json         all judged papers {kept, rejected, abstract_only}
_candidates_evolve.json  raw pool: Awesome-Self-Evolving-Agents README (287 entries)
_candidates_cost.json    raw pool: OpenAlex cost-angle search (417 entries)
_shortlist.json          merged + keyword pre-filtered (222)
_rejected_pre.json       dropped at pre-filter stage
_staging/{pdf,md,concise}  downloaded PDFs + extracted text (full + abstract slice)
_staging/manifest.json   per-paper download metadata
_logs/                   run logs
_scripts/                harvest.py, harvest_cost.py, prefilter.py,
                         download_extract.py, supplement.py, finalize.py, _decisions.py
```

## Pipeline (reproducible)

1. `_scripts/harvest.py` — parse the sibling `Awesome-Self-Evolving-Agents/README.md`
   (287 paper links), fetch each via proxy, parse `citation_*` meta tags → title/abstract/pdf_url.
2. `_scripts/harvest_cost.py` — OpenAlex search (direct, no proxy; ≥2022-08 filter) for the
   cost-reduction angle (16 queries → 417 unique).
3. `_scripts/prefilter.py` — merge + dedupe + keyword-score into angles A (evolve
   tools/skills) / B (reduce cost) / context → 222 shortlist.
4. `_scripts/download_extract.py` — download shortlist PDFs (proxy, 8 threads, arXiv +
   OpenReview/NeurIPS/MLR/ACL resolvers), extract text via PyMuPDF.
5. `_scripts/supplement.py` — fetch+verify a handful of landmark arXiv IDs missed by OpenAlex
   (LLMLingua, ExpeL, APE, Reflexion, Self-Refine, Toolformer, …) — title-substring verified.
6. `_scripts/_decisions.py` — hand-curated keep/reject + tier + note per paper (read the
   `_staging/concise/*.md` excerpts).
7. `_scripts/finalize.py` — copy keeps to `pdfs/`, write `index.md` + `_candidates.json`.

## Notes

- Proxy `sys-proxy-rd-relay.byted.org:8118` for arXiv/GitHub/Scholar; OpenAlex works direct.
- `export.arxiv.org` API and Semantic Scholar were blocked (429/timeout); used `arxiv.org/abs`
  page scraping + OpenAlex instead.
- The sibling repo `survey/Awesome-Self-Evolving-Agents/` is the source of the "evolve" angle.
