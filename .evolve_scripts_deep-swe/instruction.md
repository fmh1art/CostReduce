# Cost-saving instructions

- After 4 consecutive reads without attempting a fix, STOP exploring and attempt a fix based on what you already know.
- If you've made 5+ edits without running tests/build, stop editing and run the tests now.
- When a targeted text replacement fails after verifying the old text exists, read current file content around the target to find the exact text before retrying.
- After 5+ unsuccessful edit attempts on the same code section, reconsider your approach rather than continuing to iterate.
- If you've inspected the same file/line 3+ times with different commands, stop and directly run the build/test command you're investigating for.
- When test output is very long (>50 lines), rerun with --head/--tail/--trim-ansi to reduce observation size before the next action.
- After 3+ failed compilation attempts on the same code block, stop editing and re-read the file to understand its full context before making further changes.
- If you've written a temp file and run it twice without progress, use build-check --go-run/--node-run to skip the write-file step entirely.
- When stuck on a failing test, use batch-read --structure or code_structure to understand broader context before further edits.
- If you git checkout --file to undo edits 3+ times on the same file, stop and understand the full logic before attempting another edit.
