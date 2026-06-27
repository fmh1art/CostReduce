#!/bin/bash
# diff-filter: Show only added and removed lines from git diff, stripping metadata.
# Usage: diff-filter/main.sh [options] [file]
# Options:
#   -c, --cached      Show staged changes
#   --added-only      Show only added lines
#   --removed-only    Show only removed lines
#   --stat            Show git diff --stat output (file change summary)
#   --name-only       Show only names of changed files
#   --head <N>        Show only first N lines of output (like | head -N)
#   -C <dir>          Run git in <dir> (like git -C)
#   -h, --help        Show usage

set -euo pipefail

show_added=true
show_removed=true
cached=""
git_dir="."
file=""
diff_mode=""  # empty=lines, --stat, --name-only
head_lines=0

while [ $# -gt 0 ]; do
  case "$1" in
    -c|--cached)
      cached="--cached"
      shift
      ;;
    --added-only)
      show_removed=false
      shift
      ;;
    --removed-only)
      show_added=false
      shift
      ;;
    --stat)
      diff_mode="--stat"
      shift
      ;;
    --name-only)
      diff_mode="--name-only"
      shift
      ;;
    --head)
      if [ $# -lt 2 ]; then
        echo "ERROR: --head requires a number" >&2
        exit 1
      fi
      head_lines="$2"
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
      echo "Usage: $0 [options] [file]"
      echo "  -c, --cached      Show staged changes"
      echo "  --added-only      Show only added (+) lines"
      echo "  --removed-only    Show only removed (-) lines"
      echo "  --stat            Show git diff --stat output"
      echo "  --name-only       Show only names of changed files"
      echo "  --head <N>        Show only first N lines"
      echo "  -C <dir>          Run git in <dir>"
      echo "  -h, --help        Show this help"
      exit 0
      ;;
    -*)
      echo "ERROR: Unknown option: $1" >&2
      exit 1
      ;;
    *)
      file="$1"
      shift
      ;;
  esac
done

# Build git args
git_args=(git)
if [ "$git_dir" != "." ]; then
  git_args+=(-C "$git_dir")
fi
if [ -n "$cached" ]; then
  git_args+=("$cached")
fi

# Helper to apply head limit
apply_head() {
  if [ "$head_lines" -gt 0 ]; then
    head -n "$head_lines"
  else
    cat
  fi
}

# Handle --stat and --name-only modes
if [ "$diff_mode" = "--stat" ]; then
  git_args+=(diff --stat)
  if [ -n "$file" ]; then
    git_args+=("--" "$file")
  fi
  "${git_args[@]}" | apply_head
elif [ "$diff_mode" = "--name-only" ]; then
  git_args+=(diff --name-only)
  if [ -n "$file" ]; then
    git_args+=("--" "$file")
  fi
  "${git_args[@]}" | apply_head
else
  git_args+=(diff)
  if [ -n "$file" ]; then
    git_args+=("--" "$file")
  fi

  # Run git diff and filter output
  "${git_args[@]}" | {
    if [ "$show_added" = true ] && [ "$show_removed" = true ]; then
      grep '^[+-]' | grep -vE '^(\+\+\+|---)' | grep -v '^@@'
    elif [ "$show_added" = true ]; then
      grep '^[+]' | grep -v '^+++'
    else
      grep '^[-]' | grep -v '^---'
    fi
  } | apply_head
fi
