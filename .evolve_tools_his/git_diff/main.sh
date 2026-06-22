#!/bin/bash
# git_diff - Show git status, diff, and recent log in a single concise call
# Usage: main.sh [directory] [--stat-only] [--name-only] [--cached]
#   directory: git repo directory (default: .)
#   --stat-only: only show diffstat (git diff --stat) - use before committing to see changed files
#   --name-only: only show file names that changed (git diff --name-only)
#   --cached: show staged changes (diff --cached)
#   --log=N: show last N commit messages (default: 0, off)
# Combines git status, diff, and optionally log in one call to save steps.
#
# Examples:
#   main.sh                          # status + diff summary
#   main.sh /workspace               # status + diff summary for /workspace
#   main.sh . --stat-only            # quick diff stat
#   main.sh . --name-only            # changed file names only
#   main.sh . --cached               # show staged changes
#   main.sh . --log=5                # status + diff + last 5 commits

DIR="."
STAT_ONLY=false
NAME_ONLY=false
CACHED=false
LOG_COUNT=0

# Parse arguments
for arg in "$@"; do
    case "$arg" in
        --stat-only)
            STAT_ONLY=true
            ;;
        --name-only)
            NAME_ONLY=true
            ;;
        --cached)
            CACHED=true
            ;;
        --log=*)
            LOG_COUNT="${arg#*=}"
            ;;
        *)
            DIR="$arg"
            ;;
    esac
done

cd "$DIR" 2>/dev/null || { echo "ERROR: Cannot cd to $DIR"; exit 1; }

if ! git rev-parse --git-dir > /dev/null 2>&1; then
    echo "ERROR: Not a git repository: $(pwd)"
    exit 1
fi

echo "=== Git Status for $(pwd) ==="
echo ""

# Always show status
echo "--- Status ---"
git status --short 2>&1
echo ""

if [ "$STAT_ONLY" = true ]; then
    echo "--- Diff stat ---"
    git diff --stat 2>&1
    exit 0
fi

if [ "$NAME_ONLY" = true ]; then
    echo "--- Changed files ---"
    git diff --name-only 2>&1
    exit 0
fi

if [ "$CACHED" = true ]; then
    echo "--- Staged changes (diff --cached) ---"
    git diff --cached --stat 2>&1
    echo ""
    git diff --cached 2>&1 | head -100
    exit 0
fi

# Default: show diff summary (uncommitted changes)
echo "--- Uncommitted changes (diff) ---"
git diff --stat 2>&1
echo ""
# Show actual diff but limit to reasonable output
git diff 2>&1 | head -150
DIFF_LINES=$(git diff 2>&1 | wc -l)
if [ "$DIFF_LINES" -gt 150 ]; then
    echo "... (diff truncated, $DIFF_LINES lines total)"
fi

# Show staged but not committed changes
STAGED=$(git diff --cached --stat 2>&1)
if [ -n "$STAGED" ]; then
    echo ""
    echo "--- Staged changes ---"
    echo "$STAGED"
fi

# Optional log
if [ "$LOG_COUNT" -gt 0 ]; then
    echo ""
    echo "--- Recent commits (last $LOG_COUNT) ---"
    git log --oneline -"$LOG_COUNT" 2>&1
fi

exit 0
