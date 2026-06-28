# Behavior contracts

- After reading 5+ files without making an edit, STOP reading and make the needed changes or write new code now.
- After 2+ file-discovery searches for related patterns, merge them into one broader find-read call instead of repeating similar find/grep.
- After 2 consecutive edits to the same file or package, STOP and run its tests before making more edits.
- If you are 5+ steps into debugging without a fix attempt, STOP exploring and try a concrete fix instead.
- Before running tests that need infrastructure, start PostgreSQL and Redis in one call via start-services instead of checking them separately.
- If you keep repeating cd+export chains before every command, save the environment once with env-persist --save and reuse it with --exec.
- After writing a test file, do not re-examine source files you already read; run the test first to see if it passes.
- If you have written 3+ files without running tests on any of them, STOP and run tests now.
- Instead of making 2+ sequential curl calls to check HTTP headers/codes/redirects for different URLs, use batch-http --summary or --headers with multiple URLs in one call.
- Instead of 2+ sequential cat calls to read different files, pass them all to batch-read in one call with file1 file2 ... arguments.
