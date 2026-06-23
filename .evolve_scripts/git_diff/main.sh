#!/bin/bash
# git_diff - Show git changes summary in one step
# Usage: git_diff [directory] [options]
#   directory : optional, repository directory (default: current dir)
# Options:
#   --stat-only : show only diff statistics
#   --name-only : show only file names
#   --cached    : show staged changes
#   --log=N     : show last N commits
#   --short     : compact status output
#   --oneline   : one-line commit format (for log)
#
# Combines git status, git diff, and git log into one view.
# Saves steps by replacing multiple separate git commands.
#
# Examples:
#   git_diff
#   git_diff /workspace/repo
#   git_diff --stat-only
#   git_diff --name-only
#   git_diff --cached
#   git_diff --log=5
#   git_diff --short
#   git_diff --oneline

DIR="."
STAT_ONLY=false
NAME_ONLY=false
CACHED=false
LOG_COUNT=0
SHORT_MODE=false
ONELINE_MODE=false

# Parse arguments
for arg in "$@"; do
    case "$arg" in
        --stat-only) STAT_ONLY=true ;;
        --name-only) NAME_ONLY=true ;;
        --cached) CACHED=true ;;
        --log=*) LOG_COUNT="${arg#--log=}" ;;
        --short) SHORT_MODE=true ;;
        --oneline) ONELINE_MODE=true ;;
        --help|-h)
            echo "Usage: git_diff [directory] [options]"
            echo ""
            echo "Options:"
            echo "  --stat-only   Show only diff statistics"
            echo "  --name-only   Show only file names"
            echo "  --cached      Show staged changes"
            echo "  --log=N       Show last N commits"
            echo "  --short       Compact status output"
            echo "  --oneline     One-line commit format"
            echo ""
            echo "Examples:"
            echo "  git_diff"
            echo "  git_diff /workspace/repo"
            echo "  git_diff --stat-only"
            echo "  git_diff --name-only"
            echo "  git_diff --cached"
            echo "  git_diff --log=5"
            echo "  git_diff --short"
            echo "  git_diff --oneline"
            exit 0
            ;;
        *)
            if [ -d "$arg" ]; then
                DIR="$arg"
            fi
            ;;
    esac
done

cd "$DIR" 2>/dev/null || { echo "ERROR: Directory not found: $DIR"; exit 1; }

if [ "$SHORT_MODE" = true ]; then
    echo "=== Git Status (compact) ==="
    git status --short 2>/dev/null
    exit 0
fi

echo "=== Git Status ==="
git status --short 2>/dev/null
echo ""

if [ "$NAME_ONLY" = true ]; then
    echo "--- Changed files ---"
    git diff --name-only 2>/dev/null
    exit 0
fi

if [ "$STAT_ONLY" = true ]; then
    echo "--- Diff statistics ---"
    git diff --stat 2>/dev/null
    exit 0
fi

if [ "$CACHED" = true ]; then
    echo "--- Staged changes ---"
    git diff --cached --stat 2>/dev/null
    exit 0
fi

echo "--- Unstaged diff summary ---"
git diff --stat 2>/dev/null
echo ""

echo "--- Full status ---"
git status 2>/dev/null
echo ""

if [ "$LOG_COUNT" -gt 0 ]; then
    echo "--- Last $LOG_COUNT commits ---"
    if [ "$ONELINE_MODE" = true ]; then
        git log --oneline -"$LOG_COUNT" 2>/dev/null
    else
        git log -"$LOG_COUNT" 2>/dev/null
    fi
fi
