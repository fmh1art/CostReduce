#!/bin/bash
# gofmt-fix: Check and fix Go source formatting with gofmt -l then gofmt -w.
# Usage:
#   gofmt-fix/main.sh [files...] [options]
#   gofmt-fix/main.sh --fix [files...]
# Options:
#   --fix, -w        Fix unformatted files in place after listing them
#   -C, --dir <dir>  Working directory to run gofmt in
#   -h, --help       Show usage

set -euo pipefail

fix=false
workdir="."
files=()

while [ $# -gt 0 ]; do
  case "$1" in
    --fix|-w)
      fix=true
      shift
      ;;
    -C|--dir)
      [ $# -lt 2 ] && { echo "ERROR: --dir requires a directory" >&2; exit 1; }
      workdir="$2"
      shift 2
      ;;
    -h|--help)
      echo "Usage: $0 [options] [files...]"
      echo "  --fix, -w        Fix unformatted files in place with gofmt -w"
      echo "  -C, --dir <dir>  Working directory to run gofmt in"
      echo "  -h, --help       Show this help"
      echo ""
      echo "Examples:"
      echo "  $0 "
      echo "  $0 --fix batch.go db.go"
      exit 0
      ;;
    -*)
      echo "ERROR: Unknown option: $1" >&2
      exit 1
      ;;
    *)
      files+=("$1")
      shift
      ;;
  esac
done

cd "$workdir"

# Determine which files/dirs to check
if [ ${#files[@]} -eq 0 ]; then
  files=(".")
fi

# Run gofmt -l to list unformatted files
unformatted=$(gofmt -l "${files[@]}" 2>/dev/null || true)

if [ -z "$unformatted" ]; then
  echo "All files are properly formatted."
  exit 0
fi

echo "$unformatted"

echo "---"
file_count=$(echo "$unformatted" | wc -l | tr -d ' ')
echo "$file_count file(s) need formatting"

if [ "$fix" = true ]; then
  # Build list of files from unformatted output
  while IFS= read -r f; do
    [ -n "$f" ] && gofmt -w "$f" 2>/dev/null || true
  done <<< "$unformatted"
  echo "Formatted $file_count file(s)"
fi
