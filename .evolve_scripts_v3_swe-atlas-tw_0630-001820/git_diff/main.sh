#!/usr/bin/env bash
set -euo pipefail

# git_diff - Show git changes summary (status, diff, log) in one view, or checkout files
# Usage: git_diff [directory] [--stat-only|--name-only|--cached|--short]
#        git_diff [--log=N] [--oneline]
#        git_diff [--checkout=FILE] [--checkout-backup]

DIR="${1:-.}"
[[ $# -gt 0 && "$1" != -* ]] && DIR="$1" && shift

STAT_ONLY=false
NAME_ONLY=false
CACHED=false
SHORT_MODE=false
LOG_COUNT=""
ONELINE=false
CHECKOUT_FILE=""
CHECKOUT_BACKUP=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --stat-only) STAT_ONLY=true; shift ;;
        --name-only) NAME_ONLY=true; shift ;;
        --cached) CACHED=true; shift ;;
        --short) SHORT_MODE=true; shift ;;
        --log=*) LOG_COUNT="${1#*=}"; shift ;;
        --oneline) ONELINE=true; shift ;;
        --checkout=*) CHECKOUT_FILE="${1#*=}"; shift ;;
        --checkout-backup) CHECKOUT_BACKUP=true; shift ;;
        -*)
            echo "Unknown option: $1" >&2
            exit 1
            ;;
        *)
            DIR="$1"
            shift
            ;;
    esac
done

if [[ ! -d "$DIR/.git" ]] && ! git -C "$DIR" rev-parse --git-dir &>/dev/null 2>&1; then
    echo "Error: not a git repository: $DIR" >&2
    exit 1
fi

# Checkout / reset file
if [[ -n "$CHECKOUT_FILE" ]]; then
    if $CHECKOUT_BACKUP; then
        bak_file="${CHECKOUT_FILE}.bak.$(date +%s)"
        cp "$CHECKOUT_FILE" "$bak_file" 2>/dev/null || true
        echo "Backup saved to: $bak_file"
    fi
    exec git -C "$DIR" checkout -- "$CHECKOUT_FILE"
fi

if $SHORT_MODE; then
    exec git -C "$DIR" status --short
fi

if [[ -n "$LOG_COUNT" ]]; then
    LOG_FLAGS="$LOG_COUNT"
    $ONELINE && LOG_FLAGS="$LOG_FLAGS --oneline"
    exec git -C "$DIR" log -$LOG_FLAGS
fi

if $STAT_ONLY; then
    exec git -C "$DIR" diff --stat
elif $NAME_ONLY; then
    exec git -C "$DIR" diff --name-only
elif $CACHED; then
    exec git -C "$DIR" diff --cached
else
    # Default: show status + short diff
    echo "=== git status ==="
    git -C "$DIR" status --short 2>&1 || true
    echo
    echo "=== git diff (unstaged) ==="
    git -C "$DIR" diff --stat 2>&1 || true
    echo
    echo "=== recent commits ==="
    git -C "$DIR" log --oneline -5 2>&1 || true
fi
