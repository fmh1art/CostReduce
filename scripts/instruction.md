# Cost-saving instructions

## Batching
- Batch independent reads, searches, and edits into one step per phase; read multiple ranges from the same file in one call.
- You start in the repository root — skip directory discovery (cd, pwd, ls) and begin working immediately.
- When the task description already describes schemas, tables, or data layout, query directly — skip schema discovery steps.
- Prefer registered tools over raw bash — never use `cat`/`grep`/`sed`/`find` when a structured tool exists; never shell out to a tool’s underlying script; never create temp scripts just to debug.
- Batch git diff with your submit or preceding edit — never diff in isolation.
- Run only relevant test subdirectories with a short explicit timeout rather than filtering the full suite.

## Give up / pivot
- After two failures of the same approach, re-read the issue and pivot.
- When a command returns 'not found', pivot immediately; abandon a failing test runner after 2 attempts.
- When tests time out, narrow scope; if failures may be pre-existing, stash and confirm on base commit.
- Never re-run an identical command without changes; analyze the output first.

## Early exit
- Trust edits: don't re-read, syntax-check, or git-log after editing; never re-read a range you've already seen.
- Once you have the definitive answer, submit it directly — do not write to a file and re-read; stop exploring once core information is found.

## Risky moves / skip validation
- Skip separate compile checks (e.g., go build) before tests — test runners compile automatically.
- After inline verification passes, skip redundant test runner re-runs; if no runner is viable, verify inline and submit.
- Do NOT build/install the project itself; install only missing third-party deps.
- If the fix is trivially correct or the environment is unreliable, submit without full tests.
