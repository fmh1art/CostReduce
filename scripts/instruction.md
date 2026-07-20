# Cost-saving instructions

## Batching
- Batch independent reads, searches, and edits into one step per phase; merge adjacent reads into one range.
- Read non-adjacent ranges from the same file in one call, not separate reads.
- You start in the repository root — skip directory discovery (cd, pwd, ls) and begin working immediately.
- Prefer structured tools over raw bash for smaller observations; never create temp scripts just to debug.
- Batch git diff with your submit or preceding edit — never diff in isolation.
- Run only relevant test subdirectories with a short explicit timeout rather than filtering the full suite.

## Give up / pivot
- After two failures of the same approach, re-read the issue and pivot.
- When a command returns 'not found', pivot immediately; abandon a failing test runner after 2 attempts.
- When tests time out, narrow scope; if failures may be pre-existing, stash and confirm on base commit.
- Never re-run an identical command without changes; analyze the output first.

## Early exit
- Trust edits: don't re-read, syntax-check, or git-log after editing; never re-read a range you've already seen.
- Once you have the definitive answer, write it and stop; stop exploring when the first query yields core information.

## Risky moves / skip validation
- Skip separate compile checks (e.g., go build) before tests — test runners compile automatically.
- After inline verification passes, skip redundant test runner re-runs; if no runner is viable, verify inline and submit.
- Do NOT build/install the project itself; install only missing third-party deps.
- If the fix is trivially correct or the environment is unreliable, submit without full tests.
