# Cost-saving instructions

High-level guidance for reducing future agent cost: batching tool calls per step, keeping output lean, and avoiding anti-patterns observed in past trajectories.

Do NOT describe specific scripts here — each script's usage lives in its own ./<script_name>/intro.json.

## Behavior contracts

1. STOP-PROGRESSING-IF making more than 2 individual batch_edit replace calls on the same file — use a single multi-replace or replace-lines call to replace everything at once instead.

2. STOP-PROGRESSING-IF running find or grep in the same directory tree more than once — combine all name/type/pattern filters into a single batch_find or batch_grep call.

3. STOP-PROGRESSING-IF using sed -n to read file sections — use batch_read --lines=N-M instead. batch_read --lines handles comma-separated ranges (e.g. --lines=10-20,30,40-50) and can show line numbers with --number.

4. STOP-PROGRESSING-IF using grep | grep -v chains — use batch_grep with --include/--exclude filters instead.

5. STOP-PROGRESSING-IF re-reading the same file more than 3 times with different --lines ranges — use a single wider range or comma-separated ranges (e.g. --lines=10-20,30,40-50) instead.

6. STOP-PROGRESSING-IF using python3 heredocs to debug whitespace/indentation — use batch_read --repr or batch_edit show-indent instead.

7. STOP-PROGRESSING-IF sticking for 3+ steps on the same approach — re-read repo structure with batch_find/batch_read --dir and re-plan.

8. STOP-PROGRESSING-IF making separate batch_edit replace or sed calls for each file in a multi-file refactor — use batch_edit multi-file-replace (all occurrences) or multi-file-sed to apply the same edit across N files in one step.

9. STOP-PROGRESSING-IF using head -N to read file starts — use batch_read --head=N instead.

10. STOP-PROGRESSING-IF using batch_edit sed/replace on JSON files — use batch_json --delete-key/--set-key for structural JSON edits.

11. STOP-PROGRESSING-IF using grep -n to find code locations before editing — use batch_grep (supports multiple OR-patterns, --include, --exclude, -l, -c) to search and locate in one step.

12. STOP-PROGRESSING-IF using cd dir && command to work in a subdirectory — use batch_grep --dir=DIR, batch_read with absolute paths, or batch_find with directory argument instead.

13. STOP-PROGRESSING-IF piping git show output through grep/sed/head — use git_ops show --grep/--lines/--head instead.

14. Use batch_read --lines=N-M --number instead of sed -n 'N,Mp' for reading file sections.

15. Use batch_read with multiple files in one call instead of separate cat file1 file2 calls.

16. Use batch_read --dir instead of ls -la or find | sort for directory listing.

17. Use batch_edit script (stdin pipe for multi-line Python) instead of python3 << 'PYEOF' heredocs for complex file transformations. Use batch_python for other inline Python code.

18. Use batch_write with content arguments instead of cat > file <<'EOF' heredoc write patterns.

19. Use git_ops summary for combined git diff --stat + git status --short in one step.

20. Use batch_rm --recursive instead of bare rm -rf for directory removal.
