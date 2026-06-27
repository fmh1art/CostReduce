#!/bin/bash
# Script: git_utils
# Description: Git operations in one call: show commit log, view commit diffs, create branches, stage files, commit, stash, configure user. Replaces multiple sequential 'cd <repo> && git log/show/diff/add/commit' calls.
# Usage: main.sh <repo_dir> <action> [args...]
#   Actions:
#     log [--oneline] [--all] [--reverse] [--count=N] [--since=DATE] [--file=PATH] - Show commit log
#     show <commit_hash> [--stat] [--file=PATH] - Show commit details
#     diff [--stat] [--cached] [--file=PATH] [--from=REF] [--to=REF] - Show diff
#     blame <file_path> - Show file blame annotations
#     branch <branch_name> [--force] - Create and switch to a new branch (git checkout -b)
#     add <file1,file2,... or .> - Stage files (comma-separated list or '.' for all)
#     commit -m "message" [--author="Name <email>"] - Commit staged changes
#     stash [push|pop|list|drop] - Stash operations
#     status [--short] - Show working tree status
#     config <key> <value> [--global] - Set git config

REPO_DIR="${1:-.}"
ACTION="${2:-log}"
shift 2 2>/dev/null || true

if [ ! -d "$REPO_DIR/.git" ]; then
  echo "ERROR: Not a git repository: $REPO_DIR"
  exit 1
fi

# Parse remaining args
ONELINE=""
ALL=""
REVERSE=""
COUNT="20"
SINCE=""
FILE=""
STAT=""
CACHED=""
FROM=""
TO=""
BEFORE=""
MESSAGE=""
AUTHOR=""
SHORT=""
FORCE=""
EXTRA=""

while [ $# -gt 0 ]; do
  case "$1" in
    --oneline) ONELINE="--oneline" ;;
    --all) ALL="--all" ;;
    --reverse) REVERSE="--reverse" ;;
    --count=*) COUNT="${1#*=}" ;;
    --since=*) SINCE="--since=${1#*=}" ;;
    --before=*) BEFORE="--before=${1#*=}" ;;
    --file=*) FILE="${1#*=}" ;;
    --stat) STAT="--stat" ;;
    --cached) CACHED="--cached" ;;
    --from=*) FROM="${1#*=}" ;;
    --to=*) TO="${1#*=}" ;;
    -m|--message=*) 
      if [ "$1" = "-m" ]; then
        shift
        MESSAGE="$1"
      else
        MESSAGE="${1#*=}"
      fi
      ;;
    --author=*) AUTHOR="${1#*=}" ;;
    --short) SHORT="--short" ;;
    --force) FORCE="--force" ;;
    --global) EXTRA="$EXTRA --global" ;;
    --*) EXTRA="$EXTRA $1" ;;
    *) EXTRA="$EXTRA $1" ;;
  esac
  shift
done

cd "$REPO_DIR" || exit 1

case "$ACTION" in
  log)
    echo "=== Git Log ==="
    echo "Repo: $(basename $(git rev-parse --show-toplevel 2>/dev/null || echo $REPO_DIR))"
    echo ""
    CMD="git log"
    [ -n "$ONELINE" ] && CMD="$CMD --oneline"
    [ -n "$ALL" ] && CMD="$CMD --all"
    [ -n "$REVERSE" ] && CMD="$CMD --reverse"
    [ -n "$SINCE" ] && CMD="$CMD $SINCE"
    [ -n "$BEFORE" ] && CMD="$CMD $BEFORE"
    [ -n "$FILE" ] && CMD="$CMD -- $FILE"
    [ -n "$EXTRA" ] && CMD="$CMD $EXTRA"
    CMD="$CMD | head -$COUNT"
    eval "$CMD"
    ;;
  show)
    HASH="${EXTRA%% *}"
    if [ -z "$HASH" ]; then
      echo "ERROR: Commit hash required for 'show' action"
      echo "Usage: main.sh <repo_dir> show <commit_hash> [--stat] [--file=PATH]"
      exit 1
    fi
    echo "=== Git Show: $HASH ==="
    echo ""
    CMD="git show"
    [ -n "$STAT" ] && CMD="$CMD --stat"
    if [ -n "$FILE" ]; then
      CMD="$CMD $HASH -- $FILE"
    else
      CMD="$CMD $HASH"
    fi
    CMD="$CMD | head -$COUNT"
    eval "$CMD"
    ;;
  diff)
    echo "=== Git Diff ==="
    echo ""
    CMD="git diff"
    [ -n "$STAT" ] && CMD="$CMD --stat"
    [ -n "$CACHED" ] && CMD="$CMD --cached"
    [ -n "$FROM" ] && [ -n "$TO" ] && CMD="$CMD $FROM..$TO"
    [ -n "$FILE" ] && CMD="$CMD -- $FILE"
    [ -n "$EXTRA" ] && CMD="$CMD $EXTRA"
    CMD="$CMD | head -$COUNT"
    eval "$CMD"
    ;;
  blame)
    FILE_PATH="${EXTRA%% *}"
    if [ -z "$FILE_PATH" ]; then
      echo "ERROR: File path required for 'blame' action"
      exit 1
    fi
    echo "=== Git Blame: $FILE_PATH ==="
    echo ""
    git blame "$FILE_PATH" 2>/dev/null | head -"$COUNT"
    ;;
  branch)
    BRANCH_NAME="${EXTRA%% *}"
    if [ -z "$BRANCH_NAME" ]; then
      echo "=== Current Branch ==="
      git branch --show-current
      echo ""
      echo "=== All Branches ==="
      git branch -a
    else
      echo "=== Creating and switching to branch: $BRANCH_NAME ==="
      CMD="git checkout -b $BRANCH_NAME"
      [ -n "$FORCE" ] && CMD="git branch -f $BRANCH_NAME && git checkout $BRANCH_NAME"
      eval "$CMD"
      echo "Switched to new branch '$BRANCH_NAME'"
    fi
    ;;
  add)
    FILES="${EXTRA:-.}"
    echo "=== Staging Files ==="
    # Convert comma-separated to space-separated
    FILES_LIST=$(echo "$FILES" | tr ',' ' ')
    for f in $FILES_LIST; do
      echo "  Adding: $f"
      git add "$f"
    done
    echo ""
    echo "=== Staged Changes ==="
    git diff --cached --stat
    ;;
  commit)
    if [ -z "$MESSAGE" ]; then
      echo "ERROR: Commit message required (-m \"message\")"
      exit 1
    fi
    echo "=== Committing Changes ==="
    CMD="git commit -m \"$MESSAGE\""
    [ -n "$AUTHOR" ] && CMD="$CMD --author=\"$AUTHOR\""
    eval "$CMD"
    ;;
  stash)
    STASH_CMD="${EXTRA:-push}"
    echo "=== Git Stash $STASH_CMD ==="
    git stash "$STASH_CMD" 2>&1
    echo ""
    if [ "$STASH_CMD" = "list" ] || [ "$STASH_CMD" = "push" ]; then
      git stash list
    fi
    ;;
  status)
    echo "=== Git Status ==="
    git status $SHORT
    ;;
  config)
    KEY="${EXTRA%% *}"
    REST="${EXTRA#* }"
    VALUE="${REST%% *}"
    if [ -z "$KEY" ] || [ -z "$VALUE" ]; then
      echo "ERROR: key and value required for 'config' action"
      echo "Usage: main.sh <repo_dir> config <key> <value> [--global]"
      exit 1
    fi
    echo "=== Setting Git Config: $KEY = $VALUE ==="
    GLOBAL_FLAG=""
    for arg in $EXTRA; do
      [ "$arg" = "--global" ] && GLOBAL_FLAG="--global"
    done
    git config $GLOBAL_FLAG "$KEY" "$VALUE"
    echo "Done."
    ;;
  *)
    echo "ERROR: Unknown action: $ACTION"
    echo "Available actions: log, show, diff, blame, branch, add, commit, stash, status, config"
    exit 1
    ;;
esac
