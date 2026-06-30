# Cost-saving instructions

1. Use batch_read --grep=PATTERN [--number] [--ignore-case] [--context=N|-C=N] [--lines=RANGES] file[:START-END] to combine grep+sed+head in one step; REPEATABLE --grep searches multiple patterns at once. Use batch_read --func=NAME to read a function body from declaration to next function. Use batch_read --from-marker=REGEX --to-marker=REGEX to read content between two regex markers. Stop-progressing if you run grep -n then sed -n to find+read -- use batch_read --grep --number instead. Stop-progressing if you run back-to-back grep calls on the same file -- use repeatable --grep. Stop-progressing if you use grep -A/-B/-C to read function bodies -- use batch_read --grep=PATTERN --context=N (or -A=N/-B=N) file to show context lines around matches. Stop-progressing if you use `sed -n '/^func X/,/^func Y/p'` to read a function body -- use batch_read --func=X instead.

2. Use batch_read --lines=START-END[,START2-END2...] [--number] [--head=N|--tail=N] [--start-from=N] file to read multiple line ranges in one step instead of sequential sed -n calls; --start-from=N shows from line N to end (equivalent to --lines=N-). Stop-progressing if you read the same file with more than 2 sed -n calls -- merge into one --lines call. Stop-progressing if you read adjacent/overlapping ranges separately -- merge into a single range. Stop-progressing if you use `nl -ba file | tail -n +N | head -M` -- use --start-from=N [--head=M] --number instead.

3. Use multi_search [--dir=DIR] target pattern1 pattern2... [--include=GLOB] [-l] [--head=N] [--ignore-case] to search multiple patterns in one pass. Stop-progressing if you run back-to-back grep -rn calls on the same directory -- combine into one multi_search.

4. Use file_patch replace "old" "new" for single replacements, multi-replace for multiple pairs, replace --stdin (heredoc with --- delimiter) for multi-line replacements, batch-replace --stdin (with --- and === delimiters) for MULTIPLE old/new pairs on the SAME file in one read-write cycle, insert-before/insert-after for insertions, delete-matching for pattern deletions, delete-lines START END for line-number deletions, and replace-block for function body replacements. Stop-progressing if you use python3 << 'PYEOF' with open()+read()+replace()+write() to modify a file -- use file_patch replace --stdin or batch-replace --stdin instead. Stop-progressing if you make N sequential edits to the same file with separate file_patch replace calls -- use batch-replace --stdin to batch them into 1 step. Stop-progressing if you use python3 -c with open()+read() to read a function body -- use batch_read --grep --context=N instead. Stop-progressing if you use `sed -i 'START,ENDd' file` to delete lines -- use file_patch delete-lines START END instead.

5. Use run_cmd --dir=DIR command [args...] to combine cd+command in one step. Use run_cmd --write=FILEPATH command [args...] to write stdin to a file then run a command, replacing cat-heredoc+command combos. Stop-progressing if you use a separate cd step when --dir could combine it.

6. Use find_files [--dir=DIR] -n PATTERN1 [-n PATTERN2...] [-t f|d] [-p PATH] [-i] [--sort] [--max-depth=N] [--limit=N] for finding files with glob/path/case-insensitive matching instead of raw find commands.

7. Use code_structure [--dir=DIR] [--summary|-s] [--grep=PATTERN] file1 file2... to list functions/structs/classes in multiple files in one step instead of per-file grep for signatures.

8. Use git --commit "msg" to stage all and commit in one step (auto-configures git identity). Use git --short for quick status+log. Use git --show ref:path --grep=PATTERN [-A=N|-B=N|-C=N] to read committed files with context lines. Use git --checkout ref to restore files. Use git --diff FILE --head=N to truncate diff output. Stop-progressing if you pipe git show output through grep -A/-B/-C -- use git --show --grep=PATTERN with -A/-B/-C instead. Stop-progressing if you run git config before git commit -- use git --commit which auto-configures identity.

9. Use py_exec [--dir=DIR] [--stdin] "code" for running Python code, py_exec -f script.py for scripts, py_exec --check-import MODULE for import verification, py_exec --find-package PACKAGE for package discovery. Stop-progressing if you create a temp .py file with cat > file then run it -- use py_exec --stdin or py_exec "code" directly instead.

10. Use build_check --rust TARGET [--compile-only|--build-only|--test-only|--test-lib|--check-tests|--run|--vet-only] [--head=N|--tail=N] for Rust/Cargo checks (filters out Checking boilerplate). Use build_check --python file1.py file2.py... to syntax-check multiple Python files. Use build_check --bandit file1.py [--bandit-args="--incremental --cache-dir=DIR"] for security linting. Use build_check --clean-testcache [--test-only] ./pkg1/... ./pkg2/... to clean Go test cache and retest in one step (saves cd+go clean -testcache+go test). Use build_check --build-and-run <main_pkg> <arg1> [arg2...] to build a Go binary and run it with args in one step. Stop-progressing if you run raw `cargo check --lib -p PACKAGE`, `cargo run`, `cargo test --lib`, or `cargo check --tests` -- use build_check --rust with the appropriate mode flag instead. Stop-progressing if you run `go clean -testcache` then `go test` separately -- use build_check --clean-testcache instead.

11. Use quick_map [dir] [depth] [--filter=GLOBS] for a compact project tree view with extension stats instead of separate ls/find/tree calls.

12. Use write_file [--dir=DIR] filepath - (stdin heredoc) to write files with auto-created parent directories. Stop-progressing if you use cat << 'EOF' > file to create files -- use write_file instead.

13. Use run_tests [--dir=DIR] [--pytest|--go|--vitest|--jest|--testtools] [--timeout=SECONDS] [--grep=PATTERN] [--head=N] [--tail=N] [--brief] [--stash] [--exitfirst|-x] test_path1 [test_path2...] instead of raw cd+timeout+pytest commands. --brief strips Captured stdout/stderr sections and log lines. --stash stashes changes before tests and pops after. Stop-progressing if you use cd + git stash + python -m pytest + tail -- use run_tests --stash --tail=N instead.

14. Stop-progressing if you pipe to head/tail after git diff, git show, batch_read, run_tests, multi_search, or build_check when the tool already supports --head/--tail -- pass the flag directly to the tool instead. Stop-progressing if you pipe `git show ref:path` through `grep -A/B/C` -- use `git --show ref:path --grep=PATTERN -A=N|-B=N|-C=N` instead.

15. Stop-progressing if you run `cd /app && python -m testtools.run tests.X 2>&1 | tail -5` for each test module -- use run_tests --testtools --brief tests.X tests.Y instead to batch multiple modules.

16. Stop-progressing if you repeat similar failed experiments without understanding the error first -- each attempt costs a full agent step; analyze the error message and fix the root cause before trying a new approach.

17. Stop-progressing if you use batch_read with --lines/--head/--tail to read a range much larger than needed (e.g., 300+ lines when you need the last 10) -- use --tail=N or --grep with specific patterns to minimize observation size.

18. Stop-progressing if you run `grep -n "pattern" file` then `sed -n 'start,endp' file` separately to find+read content -- use batch_read --grep="pattern" --number file:START-END or batch_read --grep="pattern" --lines=START-END --number file to combine both in one step.

19. Stop-progressing if you use `python3 << 'PYEOF'` with ast.parse+ast.walk to analyze code structure -- use code_structure [--summary|-s] [--grep=PATTERN] files instead.

20. Stop-progressing if you run `bandit file.py 2>&1 | grep -E "B[0-9]+"` to filter bandit output -- use build_check --bandit-format=custom for concise output or build_check --bandit --bandit-args="..." to pass extra flags. Stop-progressing if you use `cd /app && go build -o /tmp/binary ./main.go && /tmp/binary /tmp/test.file` -- use build_check --build-and-run main.go /tmp/test.file instead to combine build+execute in one step.
