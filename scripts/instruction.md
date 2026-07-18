# Cost-saving instructions

## Batching
- Batch independent reads, searches, and edits into one step per phase.
- Merge adjacent reads into one range read; batch multiple files per call.
- Chain edits sequentially without testing between them.
- Execute ad-hoc analysis inline rather than writing scripts to disk; consolidate all work into one comprehensive execution per phase without incremental refinement.

## Give up / pivot
- After two failures of the same approach, re-read the issue and pivot strategy.
- When targeted tests fail repeatedly, search test source for assertions instead of re-running.
- If pytest is missing after one install attempt, use inline Python verification.
- Try the simplest correct answer first before multi-step exploration.

## Early exit
- Trust successful edits and proceed without re-reading or syntax-checking.
- Once you have the definitive answer, write it and stop. No follow-up queries.
- **Stop exploring when the first query already yields core information. Do not re-query the same data through different interfaces or refine analysis incrementally—one comprehensive execution is enough.**

## Risky moves / skip validation
- After inline Python verification passes, skip re-running through different runners.
- Do NOT build or install packages to test changes; run targeted tests directly.
- If the fix is trivially correct or the test environment is unreliable, submit without full test suite validation.
