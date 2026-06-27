#!/bin/bash
# git-commit: Stage files and commit in one step, auto-configuring git identity if needed.
# Usage:
#   git-commit/main.sh <message>
#   git-commit/main.sh <file1> [file2 ...] <message>
# Options:
#   -C <dir>    Working directory (default: .)
#   -h, --help  Show usage

set -euo pipefail

workdir="."
args=()

while [ $# -gt 0 ]; do
  case "$1" in
    -C)
      [ $# -lt 2 ] && { echo "ERROR: -C requires a directory" >&2; exit 1; }
      workdir="$2"
      shift 2
      ;;
    -h|--help)
      echo "Usage: $0 [options] [file...] <message>"
      echo "  -C <dir>    Working directory"
      echo "  -h, --help  Show this help"
      exit 0
      ;;
    *)
      args+=("$1")
      shift
      ;;
  esac
done

if [ ${#args[@]} -eq 0 ]; then
  echo "ERROR: No commit message provided" >&2
  exit 1
fi

# Last argument is the commit message
msg="${args[-1]}"
files=("${args[@]:0:${#args[@]}-1}")

cd "$workdir"

# Stage files
if [ ${#files[@]} -gt 0 ]; then
  git add "${files[@]}"
else
  git add -A
fi

# Check if there is anything to commit
if git diff --cached --quiet 2>/dev/null; then
  echo "Nothing to commit, working tree clean."
  exit 0
fi

# Try to commit; auto-configure identity if it fails due to missing config
set +e
commit_output=$(git commit -m "$msg" 2>&1)
rc=$?
set -e

if echo "$commit_output" | grep -qE "(please tell me who you are|unable to auto-detect)"; then
  git config user.email "developer@project.local"
  git config user.name "Developer"
  git commit -m "$msg"
elif [ $rc -ne 0 ]; then
  echo "$commit_output" >&2
  exit $rc
else
  echo "$commit_output"
fi
