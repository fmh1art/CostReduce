#!/bin/bash
# stash-test: Stash working tree, run a command, then pop stash in one step.
# Usage: stash-test/main.sh [options] -- <command> [args...]
# Options:
#   -m <msg>    Stash message (default: "auto-stash by stash-test")
#   -C <dir>    Run git in <dir>
#   -h, --help  Show usage

set -euo pipefail

stash_msg="auto-stash by stash-test"
git_dir="."

while [ $# -gt 0 ]; do
  case "$1" in
    -m)
      [ $# -lt 2 ] && { echo "ERROR: -m requires an argument" >&2; exit 1; }
      stash_msg="$2"
      shift 2
      ;;
    -C)
      [ $# -lt 2 ] && { echo "ERROR: -C requires a directory argument" >&2; exit 1; }
      git_dir="$2"
      shift 2
      ;;
    -h|--help)
      echo "Usage: $0 [options] -- <command> [args...]"
      echo "  -m <msg>    Stash message"
      echo "  -C <dir>    Run git in <dir>"
      echo "  -h, --help  Show this help"
      exit 0
      ;;
    --)
      shift
      break
      ;;
    -*)
      echo "ERROR: Unknown option: $1" >&2
      exit 1
      ;;
    *)
      # No -- separator; treat all remaining as command
      break
      ;;
  esac
done

if [ $# -eq 0 ]; then
  echo "ERROR: No command specified" >&2
  exit 1
fi

# Determine git args
git_cmd=(git)
[ "$git_dir" != "." ] && git_cmd+=(-C "$git_dir")

# Check if there are changes to stash
if "${git_cmd[@]}" diff --quiet 2>/dev/null && "${git_cmd[@]}" diff --cached --quiet 2>/dev/null; then
  # No changes, no need to stash
  stashed=false
else
  stashed=true
  "${git_cmd[@]}" stash push -m "$stash_msg" 2>&1
fi

# Run command
"$@"
rc=$?

# Pop stash only if we stashed something
if [ "$stashed" = true ]; then
  "${git_cmd[@]}" stash pop 2>&1 || true
fi

exit $rc
