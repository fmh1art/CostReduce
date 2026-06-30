# Cost-saving instructions

High-level guidance for reducing future agent cost: batching tool calls per step, keeping output lean, and avoiding anti-patterns observed in past trajectories.

Do NOT describe specific scripts here — each script's usage lives in its own ./<script_name>/intro.json.

## Behavior contracts

1. Use batch_edit for file edits: multi-replace for multiple substitutions in one call, replace-lines for range replacement, sed with multiple expressions, delete-pattern/delete-lines for pattern/range deletion, insert-after/insert-before/insert-at-line for insertions, check-balance for brace/paren/bracket balance, and script/transform for complex Python edits instead of multi-step heredoc+python3 patterns that waste 3+ steps per edit.

2. Use batch_grep for multi-pattern searches with --include/--exclude-dir/--exclude-name/--exclude-pattern filters and --context/--count instead of separate grep -rn | grep -v | head pipelines; use batch_find for file discovery with --name/--exclude/--sort instead of find | grep -v | sort chains.

3. Use batch_read --lines START-END (e.g. --lines=670-900) instead of `sed -n '670,900p'` to read line ranges from files; use --lines with comma-separated ranges (e.g. --lines=10-20,30,40-50) instead of multiple sed -n calls; use --dir for directory listing instead of `ls -la` or `find | sort`; use --head/--tail for first/last N lines; use --number instead of `cat -n`; use --count instead of `wc -l`.

4. Use batch_python instead of `python3 -c "..."` or heredoc patterns for running Python code, accepting inline code, script files via -f, or stdin pipe.

5. Use git_ops summary for combined git diff --stat and git status --short in one step; use diff-tail [N] to view the end of modified files instead of bash loops; use diff/show for change review; use show REF:file --grep=PATTERN to filter git show output in one step instead of `git show | grep`; use show REF:file --lines=N-M instead of `git show | sed -n`; use show REF:file --head=N instead of `git show | head`; use cleanup to remove .bak files and show git diff --stat in one step instead of separate rm + git diff.

6. Use batch_json to extract values from JSON files by dot-notation key path instead of python3 inline parsing; use batch_write with content arguments instead of `cat > file <<'EOF' ... EOF` heredoc write patterns.

7. Use batch_cp for file/directory copies with auto parent directory creation and --backup instead of mkdir -p + cp two-step patterns.

8. Use batch_go with --build --test --vet --check-syntax --fmt --doc to combine multiple Go actions instead of separate go build/test/vet/doc/gofmt calls; use --run/--timeout/--count for test filtering; use --grep/--head for output filtering instead of piping through grep/head.

9. Use batch_rm with --recursive for directory removal instead of bare rm -rf commands.

10. Use find_repo_root to locate the git repository root instead of `find / -name .git` or cd-based detection loops.

11. Use batch_read --show-nonprintable|-A instead of `sed -n 'Np' | od -c` or `cat -A` for inspecting whitespace and non-printable characters in a file.

12. Use batch_go --doc for `go doc` on packages or identifiers instead of separate go doc calls; combine with other actions like --build --test to get all Go information in one step.

13. STOP-PROGRESSING-IF re-reading the same file more than 3 times with different `--lines` ranges — use a single wider range or comma-separated ranges (e.g. --lines=10-20,30,40-50) instead, and if you need to explore, read a broad range once.

14. STOP-PROGRESSING-IF grepping the same file for the same pattern more than once — if the first grep returned no matches or found the match, do not re-grep with words flipped; use batch_grep with --context to see surrounding lines instead.

15. STOP-PROGRESSING-IF stuck for 3+ steps: re-read repo structure with batch_find/batch_read --dir and re-plan instead of repeating the same failing approach.

16. STOP-PROGRESSING-IF running npx tsc --noEmit on individual files without project-level tsconfig — TypeScript type checking requires the full project context and will fail on isolated files.

17. STOP-PROGRESSING-IF piping `git show` output through grep/sed/head — use git_ops show --grep=PATTERN --lines=N-M --head=N instead to combine all filtering into one step.

18. STOP-PROGRESSING-IF running more than one `find` or `grep -rn` on the same directory with slightly different patterns — combine all patterns into a single batch_grep or batch_find call using multiple --include/--exclude/pattern arguments.
