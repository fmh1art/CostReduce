#!/usr/bin/env bash
set -euo pipefail

# batch-read: Read multiple files or line ranges in one go
# Usage: batch-read [--lines=start-end] [--head=N] [--tail=N] [--number] [--dir=PATH --include=GLOB] file1 [file2 ...]
#   file:start-end  to read a specific line range for that file
#   --lines=start-end  applies the same line range to ALL files
#   --dir=PATH --include=GLOB  reads all matching files in a directory

SHOW_NUMBER=false
HEAD_LINES=
TAIL_LINES=
LINES_RANGE=
DIR_PATH=
INCLUDE_GLOB=
FILES=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --head=*)
      HEAD_LINES="${1#*=}"
      shift
      ;;
    --tail=*)
      TAIL_LINES="${1#*=}"
      shift
      ;;
    --lines=*)
      LINES_RANGE="${1#*=}"
      shift
      ;;
    --number|-n)
      SHOW_NUMBER=true
      shift
      ;;
    --dir=*)
      DIR_PATH="${1#*=}"
      shift
      ;;
    --include=*)
      INCLUDE_GLOB="${1#*=}"
      shift
      ;;
    -*)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
    *)
      FILES+=("$1")
      shift
      ;;
  esac
done

# If --dir is given, find files in that directory
if [[ -n "$DIR_PATH" ]]; then
  if [[ ! -d "$DIR_PATH" ]]; then
    echo "Error: Directory not found: $DIR_PATH" >&2
    exit 1
  fi
  if [[ -z "$INCLUDE_GLOB" ]]; then
    INCLUDE_GLOB="*"
  fi
  while IFS= read -r -d '' f; do
    FILES+=("$f")
  done < <(find "$DIR_PATH" -type f -name "$INCLUDE_GLOB" -print0 2>/dev/null | sort -z)
fi

if [[ ${#FILES[@]} -eq 0 ]]; then
  echo "Usage: $0 [--lines=start-end] [--head=N] [--tail=N] [--number] [--dir=PATH --include=GLOB] file1 [file2 ...]" >&2
  exit 1
fi

for file_spec in "${FILES[@]}"; do
  # Check if it has line range specifier (file:start-end)
  if [[ "$file_spec" =~ ^(.+):([0-9]+)-([0-9]+)$ ]]; then
    file="${BASH_REMATCH[1]}"
    start_line="${BASH_REMATCH[2]}"
    end_line="${BASH_REMATCH[3]}"
    if [[ ! -f "$file" ]]; then
      echo "Error: File not found: $file" >&2
      continue
    fi
    if [[ ${#FILES[@]} -gt 1 ]]; then
      echo "--- $file (lines $start_line-$end_line) ---"
    fi
    if $SHOW_NUMBER; then
      nl -ba "$file" | sed -n "${start_line},${end_line}p"
    else
      sed -n "${start_line},${end_line}p" "$file"
    fi
  elif [[ -n "$LINES_RANGE" ]]; then
    file="$file_spec"
    if [[ ! -f "$file" ]]; then
      echo "Error: File not found: $file" >&2
      continue
    fi
    local_start="${LINES_RANGE%%-*}"
    local_end="${LINES_RANGE#*-}"
    if [[ ${#FILES[@]} -gt 1 ]]; then
      echo "--- $file (lines $local_start-$local_end) ---"
    fi
    if $SHOW_NUMBER; then
      nl -ba "$file" | sed -n "${local_start},${local_end}p"
    else
      sed -n "${local_start},${local_end}p" "$file"
    fi
  else
    file="$file_spec"
    if [[ ! -f "$file" ]]; then
      echo "Error: File not found: $file" >&2
      continue
    fi
    if [[ ${#FILES[@]} -gt 1 ]]; then
      echo "--- $file ---"
    fi
    if [[ -n "$HEAD_LINES" ]]; then
      if $SHOW_NUMBER; then
        nl -ba "$file" | head -n "$HEAD_LINES"
      else
        head -n "$HEAD_LINES" "$file"
      fi
    elif [[ -n "$TAIL_LINES" ]]; then
      if $SHOW_NUMBER; then
        nl -ba "$file" | tail -n "$TAIL_LINES"
      else
        tail -n "$TAIL_LINES" "$file"
      fi
    else
      if $SHOW_NUMBER; then
        nl -ba "$file"
      else
        cat "$file"
      fi
    fi
  fi
done
