# Cost-saving instructions

- Use `write-file` instead of `cat > file << 'EOF'` to write files — heredocs embed the entire file content in the command, massively bloating the observation when the command appears in error output.
- Use `multi-edit --stdin-code` instead of `python3 << PYEOF` or complex `sed -i` — Python-heredoc and sed patterns often produce broken state that takes multiple cleanup steps.
- Use `build-check --mocha --tail=15 --cd=DIR` instead of `cd DIR && npx mocha tests/... 2>&1 | tail -15` — the pipe can timeout and the cd + npx pattern wastes steps.
- Use `build-check --go --cd=DIR --test-only --tail=10 --fail-only` instead of `cd /app && go test ... -v 2>&1 | grep -E "PASS|FAIL|---"` — grep filters out failure details that the agent later needs to re-read.
- Instead of running `cd + go build`/`cd + go vet`/`cd + gofmt | head` as separate chained steps, use `build-check --cd=DIR --build-only/--vet-only/--gofmt/--list-only` with `--timeout=N` and `--head=N` to collapse them into one step.
- When running pytest, use `build-check --trim-pytest --pytest` (or `--tail=1` as fallback) instead of raw `pytest | tail -15` — pytest output contains long duration dumps, snapshot reports, and docs links that are never useful; only the summary line matters.
- After 4 consecutive reads without attempting a fix, STOP and attempt a fix based on what you already know — repeatedly reading the same file with different commands (sed -n, nl -ba, head, grep) wastes steps.
- If you've made 5+ edits without running tests/build, stop editing and run the tests now.
- Before editing code to fix an anticipated issue or writing custom smoke tests, run the project's relevant tests first — they may already pass, saving unnecessary edit and intermediate-verification steps.
- After 5+ unsuccessful edit attempts on the same code section, reconsider your overall approach rather than continuing to iterate with minor changes.
