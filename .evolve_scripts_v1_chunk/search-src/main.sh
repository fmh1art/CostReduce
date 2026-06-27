#!/bin/bash
# Search for patterns in source files, excluding non-source directories.
# Usage: main.sh <pattern> [extension] [directory] [--files-only] [--head=N]
#   --files-only : list only filenames containing the pattern (grep -l behavior)
#   --head=N     : limit output to N results (default: 100)

set -euo pipefail

PATTERN="$1"
shift

FILES_ONLY=false
EXT=""
DIR="."
HEAD_LIMIT=100

while [ $# -gt 0 ]; do
  case "$1" in
    --files-only)
      FILES_ONLY=true
      shift
      ;;
    --head=*)
      HEAD_LIMIT="${1#--head=}"
      shift
      ;;
    *)
      if [ -z "$EXT" ]; then
        EXT="$1"
      elif [ "$DIR" = "." ]; then
        DIR="$1"
      fi
      shift
      ;;
  esac
done

GREP_OPTS="-n"
if [ "$FILES_ONLY" = true ]; then
  GREP_OPTS="-l"
fi

if [ -n "$EXT" ]; then
  find "$DIR" -type f -name "*.$EXT" \
    -not -path "*/node_modules/*" \
    -not -path "*/target/*" \
    -not -path "*/.git/*" \
    -not -path "*/venv/*" \
    -not -path "*/__pycache__/*" \
    -not -path "*.d.ts" 2>/dev/null | xargs grep $GREP_OPTS "$PATTERN" 2>/dev/null | head -n "$HEAD_LIMIT"
else
  find "$DIR" -type f \
    -not -path "*/node_modules/*" \
    -not -path "*/target/*" \
    -not -path "*/.git/*" \
    -not -path "*/venv/*" \
    -not -path "*/__pycache__/*" \
    -not -path "*.d.ts" \
    -not -path "*.min.*" \
    -not -path "*.map" 2>/dev/null | xargs grep $GREP_OPTS "$PATTERN" 2>/dev/null | head -n "$HEAD_LIMIT"
fi
