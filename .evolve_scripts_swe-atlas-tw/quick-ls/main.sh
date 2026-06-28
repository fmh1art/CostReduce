#!/usr/bin/env bash
set -euo pipefail

# quick-ls: List directory with compact tree view and file sizes
# Usage: quick-ls [<directory>] [<depth>] [--filter=GLOB] [--all]

DIR="."
DEPTH=2
FILTER=
ALL=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --filter=*)
      FILTER="${1#*=}"
      shift
      ;;
    --all|-a)
      ALL=true
      shift
      ;;
    -*)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
    *)
      if [[ "$1" =~ ^[0-9]+$ ]]; then
        DEPTH="$1"
      else
        DIR="$1"
      fi
      shift
      ;;
  esac
done

if [[ ! -d "$DIR" ]]; then
  echo "Error: Directory not found: $DIR" >&2
  exit 1
fi

# Use tree if available, otherwise fall back to find-based approach
if command -v tree &>/dev/null; then
  TREE_ARGS=("$DIR")
  TREE_ARGS+=(-L "$DEPTH")
  TREE_ARGS+=(--du -h)
  if ! $ALL; then
    TREE_ARGS+=(-I 'node_modules|.git')
  fi
  if [[ -n "$FILTER" ]]; then
    TREE_ARGS+=(-P "$FILTER" --prune)
  fi
  tree "${TREE_ARGS[@]}"
else
  # Fallback: find-based listing
  FIND_ARGS=("$DIR" -maxdepth "$DEPTH")
  if ! $ALL; then
    FIND_ARGS+=(-not -path '*/node_modules/*' -not -path '*/.git/*')
  fi
  if [[ -n "$FILTER" ]]; then
    FIND_ARGS+=(-name "$FILTER")
  fi
  FIND_ARGS+=(-printf '%s %p\n')
  find "${FIND_ARGS[@]}" 2>/dev/null | sort -t/ -k2 | while read -r size path; do
    if [[ -f "$path" ]]; then
      if [[ $size -ge 1048576 ]]; then
        size_str="$(echo "scale=1; $size/1048576" | bc)M"
      elif [[ $size -ge 1024 ]]; then
        size_str="$(echo "scale=1; $size/1024" | bc)K"
      else
        size_str="${size}B"
      fi
    else
      size_str="DIR"
    fi
    printf "%6s %s\n" "$size_str" "$path"
  done
fi
