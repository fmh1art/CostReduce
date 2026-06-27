# Cost-saving instructions

High-level guidance for reducing future agent cost based on stuck patterns observed in past trajectories.
Each available helper script lives under ./<script_name>/ with its own intro.json describing its call signature — read those to learn how to invoke them. The contracts below describe WHEN to apply a cost-saving behavior, not which tool to call.

- When exploring a new file for a function or type, grep for its definition/declaration directly instead of doing iterative sed+grep exploration across multiple files.
- After 3 consecutive read-only steps without attempting a fix, STOP exploring and make a targeted edit attempt.
- If you've made 3 edits without running tests or syntax checks, run tests now before making more changes.
- When reading code sections, read a wider range (40-60 lines) with absolute line numbers in one step, combining multiple files or ranges into a single call, instead of reading overlapping narrow ranges across multiple steps.
- When searching for multiple related patterns in the same file or directory, combine them into a single grep call with -e flags instead of separate steps.
- When making the same edit across multiple files, batch it into one step instead of one sed/heredoc per file. Check whether a preinstalled helper exists for multi-file or regex edits before writing a custom loop.
- After making code changes, build or compile immediately to catch errors before running more tests. When a build emits long output, cap to the first N lines of errors in the same step rather than running build + tail/head as separate steps.
- After verifying a fix, clean up temp test files and scratch directories in the same step to avoid polluting git status.
- When you need to run tests on a clean working tree, combine the stash → run → pop sequence into a single step rather than three separate git commands.
- When committing, stage and commit in a single step with identity auto-configured, rather than separate add + config + commit steps.
