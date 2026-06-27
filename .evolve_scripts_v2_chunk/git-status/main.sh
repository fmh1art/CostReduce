#!/bin/bash
# git-status: Show git status in compact short format with optional path filter.
# Usage: git-status/main.sh [options] [path]
# Options:
#   --short          Show git status --short (default)
#   --porcelain      Show git status --porcelain (machine-readable)
#   --branch         Include branch info (-b flag with --short)
#   --ignored        Show ignored files
#   --log <N>        Show last N commits in oneline format before status
#   --diff           Show git status --short followed by git diff --stat for changed files
#   -C <dir>         Run git in <dir>
#   -h, --help       Show usage

set -euo pipefail

mode="short"
git_dir="."
path=""
branch_flag=""
ignored_flag=""
log_count=0
show_diff=false

while [ $# -gt 0 ]; do
  case "$1" in
    --short)
      mode="short"
      shift
      ;;
    --porcelain)
      mode="porcelain"
      shift
      ;;
    --branch)
      branch_flag="-b"
      shift
      ;;
    --ignored)
      ignored_flag="--ignored"
      shift
      ;;
    --diff)
      show_diff=true
      shift
      ;;
    --log=*)
      log_count="${1#*=}"
      shift
      ;;
    --log)
      if [ $# -lt 2 ]; then
        echo "ERROR: --log requires a number" >&2
        exit 1
      fi
      log_count="$2"
      shift 2
      ;;
    -C)
      if [ $# -lt 2 ]; then
        echo "ERROR: -C requires a directory argument" >&2
        exit 1
      fi
      git_dir="$2"
      shift 2
      ;;
    -h|--help)
      echo "Usage: $0 [options] [path]"
      echo "  --short        Show git status --short (default)"
      echo "  --porcelain    Show git status --porcelain"
      echo "  --branch       Include branch info with --short"
      echo "  --ignored      Show ignored files"
      echo "  --diff         Show status + diff --stat for changed files"
      echo "  --log <N>      Show last N commits in oneline format before status"
      echo "  -C <dir>       Run git in <dir>"
      echo "  -h, --help     Show this help"
      exit 0
      ;;
    -*)
      echo "ERROR: Unknown option: $1" >&2
      exit 1
      ;;
    *)
      path="$1"
      shift
      ;;
  esac
done

cd "$git_dir"

# Show recent commits if --log was specified
if [ "$log_count" -gt 0 ]; then
  git log --oneline -"$log_count" 2>/dev/null || true
  echo "---"
fi

# Show status
if [ -n "$path" ]; then
  if [ "$mode" = "porcelain" ]; then
    git status --porcelain $ignored_flag -- "$path" 2>/dev/null
  else
    git status --short $branch_flag $ignored_flag -- "$path" 2>/dev/null
  fi
else
  if [ "$mode" = "porcelain" ]; then
    git status --porcelain $ignored_flag 2>/dev/null
  else
    git status --short $branch_flag $ignored_flag 2>/dev/null
  fi
fi

# Show diff --stat for changed files if --diff was specified
if [ "$show_diff" = true ]; then
  if [ -n "$path" ]; then
    git diff --stat -- "$path" 2>/dev/null || true
  else
    git diff --stat 2>/dev/null || true
  fi
fi
