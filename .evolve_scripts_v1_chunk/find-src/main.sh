#!/bin/bash
# Find source files by extension, excluding common non-source directories.
# Usage: main.sh <extension(s)> [directory] [--sort|--head N|--count]
# Extensions can be comma-separated (e.g. "ts,js") for multiple types.
# --count: only print the number of matching files.

set -euo pipefail

EXT_ARG="$1"
DIR="${2:-.}"
OPT="${3:-}"

# Check for --count
COUNT=false
if [ "$OPT" = "--count" ]; then
  COUNT=true
  OPT=""
fi

# Build a temp script to avoid eval
EXTS=()
IFS=',' read -ra EXT_LIST <<< "$EXT_ARG"

# Generate find command with proper arguments
if [ ${#EXT_LIST[@]} -eq 1 ]; then
  # Single extension
  EXT="${EXT_LIST[0]}"
  if [ "$COUNT" = true ]; then
    find "$DIR" -type f -name "*.$EXT" \
      -not -path "*/node_modules/*" \
      -not -path "*/target/*" \
      -not -path "*/.git/*" \
      -not -path "*/venv/*" \
      -not -path "*/__pycache__/*" \
      -not -path "*.d.ts" 2>/dev/null | wc -l
  elif [ "$OPT" = "--sort" ]; then
    find "$DIR" -type f -name "*.$EXT" \
      -not -path "*/node_modules/*" \
      -not -path "*/target/*" \
      -not -path "*/.git/*" \
      -not -path "*/venv/*" \
      -not -path "*/__pycache__/*" \
      -not -path "*.d.ts" 2>/dev/null | sort
  elif [[ "$OPT" == --head=* ]]; then
    N="${OPT#--head=}"
    find "$DIR" -type f -name "*.$EXT" \
      -not -path "*/node_modules/*" \
      -not -path "*/target/*" \
      -not -path "*/.git/*" \
      -not -path "*/venv/*" \
      -not -path "*/__pycache__/*" \
      -not -path "*.d.ts" 2>/dev/null | head -n "$N"
  else
    find "$DIR" -type f -name "*.$EXT" \
      -not -path "*/node_modules/*" \
      -not -path "*/target/*" \
      -not -path "*/.git/*" \
      -not -path "*/venv/*" \
      -not -path "*/__pycache__/*" \
      -not -path "*.d.ts" 2>/dev/null
  fi
else
  # Multiple extensions - use find with -o
  NAME_ARGS=()
  for ext in "${EXT_LIST[@]}"; do
    if [ ${#NAME_ARGS[@]} -eq 0 ]; then
      NAME_ARGS=(-name "*.$ext")
    else
      NAME_ARGS+=(-o -name "*.$ext")
    fi
  done

  EXCLUDE_ARGS=(-not -path "*/node_modules/*" -not -path "*/target/*" -not -path "*/.git/*" \
                -not -path "*/venv/*" -not -path "*/__pycache__/*" -not -path "*.d.ts")

  if [ "$COUNT" = true ]; then
    find "$DIR" -type f \( "${NAME_ARGS[@]}" \) "${EXCLUDE_ARGS[@]}" 2>/dev/null | wc -l
  elif [ "$OPT" = "--sort" ]; then
    find "$DIR" -type f \( "${NAME_ARGS[@]}" \) "${EXCLUDE_ARGS[@]}" 2>/dev/null | sort
  elif [[ "$OPT" == --head=* ]]; then
    N="${OPT#--head=}"
    find "$DIR" -type f \( "${NAME_ARGS[@]}" \) "${EXCLUDE_ARGS[@]}" 2>/dev/null | head -n "$N"
  else
    find "$DIR" -type f \( "${NAME_ARGS[@]}" \) "${EXCLUDE_ARGS[@]}" 2>/dev/null
  fi
fi
