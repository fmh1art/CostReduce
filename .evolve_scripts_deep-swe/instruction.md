# Cost-saving instructions

- If you've read 3+ overlapping or adjacent ranges of the same file, read one wider range instead and stop exploring.
- After 3+ consecutive file reads without attempting a fix, make a targeted edit based on what you've already read.
- If you've made 3+ edits without running tests or build, verify compilation before making more edits.
- When a build or test fails, fix all related issues visible in the output in one pass instead of fixing one error at a time.
- If 2+ consecutive builds or test runs fail with the same error type, revert the last change and try a fundamentally different approach instead of iterating on the same approach.
- When editing the same file in multiple locations, combine all changes into one call instead of one edit per location.
- When reverting changes that broke the build, revert all related changes at once instead of piecemeal.
- When running commands in a subdirectory, combine the directory change with the command instead of a separate cd step.
- After reverting to a clean state, verify the code compiles before re-applying a modified fix.
- If you've run git status/diff on a file that has no changes (empty diff), stop exploring that file and read or edit the actual modified files instead.
