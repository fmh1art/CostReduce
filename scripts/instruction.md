# Cost-saving instructions

## Batching
- Batch independent reads, searches, and edits into one step per phase; merge adjacent reads into one range.
- You start in the repository root — skip directory discovery (cd, pwd, ls) and begin working immediately.
- Prefer structured tools over raw bash for smaller observations; never create temp scripts just to debug.
- Batch git diff with your submit or preceding edit — never diff in isolation.
- Run only relevant test subdirectories with a short explicit timeout rather than filtering the full suite.

## Give up / pivot
- After two failures of the same approach, re-read the issue and pivot.
- Abandon a failing test runner after 2 attempts; verify with inline scripts instead.
- When tests time out, narrow the test scope (single package, single test) before retrying.
- When you suspect test failures may be pre-existing, stash your changes and re-run the test to confirm before debugging further.
- When test output is confusing or truncated, read the test config or source instead of re-running.
- When a ModuleNotFoundError names a well-known package, pip-install it immediately rather than building from source.
- Never re-run an identical command without changes; analyze the output first.

## Early exit
- Trust successful edits; do not re-read files or syntax-check after editing.
- After committing, submit immediately — skip git log/status, type-checkers, and post-commit validation.
- Once you have the definitive answer, write it and stop; stop exploring when the first query yields core information.
- Track what you have read; never re-read a file range already in context.

## Risky moves / skip validation
- After inline Python verification passes, skip redundant test runner re-runs.
- If no test runner is viable after 2 attempts, verify inline and submit.
- Do NOT build/install the project itself; install only missing third-party deps.
- If the fix is trivially correct or the environment is unreliable, submit without full tests.
