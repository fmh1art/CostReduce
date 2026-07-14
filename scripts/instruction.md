# Cost-saving instructions

## General principles
- Batch multiple independent actions into one step rather than sequential calls.
- Prefer one tool call that achieves multiple actions over separate calls for each.
- Read multiple files or file sections in a single step; batch grep/sed reads together.

## Verification and testing
- Combine multiple verification/test commands into a single call (e.g., run all `go test` targets together, not one at a time).
- Avoid `npx` — it frequently times out; use direct `node`/`npm` commands or pre-installed tools instead.
- If a verification command times out once, retry with a longer timeout before giving up.
- If a verification command fails, stop after 2 failed attempts and re-evaluate the approach before retrying.

## When to give up / stop trying
- If the same approach fails twice, stop and re-read the original issue before retrying.
- If a dependency install fails twice, skip it and work with what is available.
- If you have been reading different ranges of the same file across 3+ steps, batch remaining reads into one call.

## When to exit early
- If the issue is clearly infeasible given the current environment, submit a best-effort fix without full validation.
