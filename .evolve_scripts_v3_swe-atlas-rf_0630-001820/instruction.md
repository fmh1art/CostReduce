# Cost-saving instructions

High-level guidance for reducing future agent cost: batching tool calls per step, keeping output lean, and avoiding anti-patterns observed in past trajectories.

Do NOT describe specific scripts here — each script's usage lives in its own ./<script_name>/intro.json.

## Behavior contracts

1. Use batch_edit script/transform instead of the `python3 << 'PYEOF'` heredoc pattern for complex file edits — batch_edit script accepts multi-line Python code via stdin (heredoc or pipe) and provides `content`/`f`/`result` variables, directly replacing the costly heredoc pattern that wastes 1+ steps per edit.
2. Use batch_grep instead of grep -rn or git grep for cross-file pattern searches; use --include=*.ext to narrow by extension, --exclude-dir=node_modules --exclude-name=*test* to filter out noise, --context=N to see surrounding lines instead of multiple grep calls on the same file, and --count to count matches across many files in one call.
3. Use batch_read --lines to read only specific line ranges or comma-separated line numbers instead of reading the entire file with cat -n or using Python/sed -n to extract ranges; read multiple disjoint ranges in one call (e.g., --lines=10-20,30,40-50) instead of separate calls; use --number to show line numbers like nl -ba instead of `cat -n file`; use --count instead of `wc -l file` to count lines.
4. Use batch_edit multi-replace to combine multiple substitutions on the same file into one call instead of separate replace calls or Python scripts that chain replace() calls.
5. Use batch_edit replace-lines for replacing a range of lines instead of writing Python scripts to read lines, find indices, and write new content; pipe multi-line content via heredoc for long replacements.
6. Use batch_edit sed with multiple expressions (e.g., `batch_edit sed file "s/old1/new1/" "s/old2/new2/"`) instead of running multiple consecutive sed -i calls on the same file; use batch_edit insert-at-line N instead of sed -i 'Na...'.
7. Use batch_edit delete-pattern <file> <pattern> instead of the grep+sed two-step pattern, and use batch_edit delete-lines <file> <start> <end> instead of sed -i 'Nd' for deleting a specific line or range, saving 1+ steps per deletion.
8. Use batch_python instead of `python3 << 'PYEOF'` or `python3 -c "..."` patterns — batch_python accepts inline code, script files via -f, or heredoc/pipe via stdin, eliminating the python3 wrapper overhead and reducing tool_call length.
9. Use git_ops summary to view combined git diff --stat and git status --short in one step instead of running them separately (saves 1 step per change review).
10. Use batch_find with multiple --name and --exclude/--exclude-dir/--exclude-name flags instead of separate find commands or find | grep -v | sort pipelines.
11. Use batch_read --dir --include --type to list files instead of ls -la or find without sorting.
12. Use batch_json to extract values from JSON files rather than python3 inline parsing.
13. Use batch_write with content arguments instead of the `cat > file <<'EOF' ... EOF` heredoc write pattern.
14. Use batch_cp for file/directory copies (including file-to-file backup) with auto parent directory creation instead of mkdir -p && cp/rm two-step patterns.
15. Use git_ops diff-tail [N] (default 5) instead of `git diff --name-only --diff-filter=M | while read f; do echo "===$f==="; tail -5 "$f"; done` bash loop to preview the end of modified files in one step.
16. Use git_ops diff instead of doing multiple grep searches on changed files — git diff directly shows what changed; use git_ops show to view file history.
17. Use batch_go with --build --test --vet --check-syntax --fmt to combine multiple actions instead of separate go build/test/vet calls; use --dir=DIR to change directory before running; use --run=PATTERN --timeout=DURATION --count=1 to filter and speed up failing tests; use --grep=PATTERN --head=N instead of piping output through grep/head.
18. Use batch_edit insert-after/insert-before (pattern-based) or insert-at-line N (line-number-based) instead of sed -i for inserting lines in files.
19. Use batch_edit check-balance <file> instead of inline Python scripts that count braces/parens/brackets to check syntax balance in a file, saving 1+ steps per balance check.
20. Use git_ops cleanup [files...] to remove .bak backup files (or specific file paths) and show git diff --stat in one step, replacing the separate rm + git diff two-step pattern and saving 1 step per cleanup.
21. STOP-PROGRESSING-IF grepping the same file for the same pattern more than once — if the first grep returned no matches or found the match, do not re-grep with pattern words flipped; use batch_grep with --context to see surrounding lines instead.
22. STOP-PROGRESSING-IF stuck for 3+ steps: re-read repo structure with batch_find/batch_read --dir and re-plan instead of repeating the same failing approach.
23. STOP-PROGRESSING-IF running npx tsc --noEmit on individual files without project-level tsconfig — TypeScript type checking requires the full project context and will fail on isolated files; use the project's existing type check scripts instead.
