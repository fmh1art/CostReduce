#!/bin/bash
# Script: find_files
# Description: Find files by extension/type under a given path with filters
# Usage: main.sh <root_dir> [extensions] [include_path=...] [exclude_path=...] [max_results=50] [sort=true|false]

ROOT_DIR="$1"
EXTENSIONS="${2:-py}"

# Parse optional named parameters
INCLUDE_PATH=""
EXCLUDE_PATH=""
MAX_RESULTS=50
SORT=false

for arg in "${@:3}"; do
  case "$arg" in
    include_path=*) INCLUDE_PATH="${arg#*=}" ;;
    exclude_path=*) EXCLUDE_PATH="${arg#*=}" ;;
    max_results=*) MAX_RESULTS="${arg#*=}" ;;
    sort=*) SORT="${arg#*=}" ;;
  esac
done

if [ -z "$ROOT_DIR" ]; then
  echo "ERROR: root_dir is required. Usage: main.sh <root_dir> [extensions] [options]"
  exit 1
fi

if [ ! -d "$ROOT_DIR" ]; then
  echo "ERROR: Directory not found: $ROOT_DIR"
  exit 1
fi

echo "=== Find Files ==="
echo "Root: $ROOT_DIR"
echo "Extensions: $EXTENSIONS"
[ -n "$INCLUDE_PATH" ] && echo "Include path: $INCLUDE_PATH"
[ -n "$EXCLUDE_PATH" ] && echo "Exclude path: $EXCLUDE_PATH"
echo "Max results: $MAX_RESULTS"
echo ""

# Build find command
FIND_CMD="find \"$ROOT_DIR\" -type f"

# Build extension conditions
IFS=',' read -ra EXT_ARRAY <<< "$EXTENSIONS"
EXT_COND=""
for ext in "${EXT_ARRAY[@]}"; do
  ext=$(echo "$ext" | xargs)  # trim
  if [ -z "$EXT_COND" ]; then
    EXT_COND="-name \"*.$ext\""
  else
    EXT_COND="$EXT_COND -o -name \"*.$ext\""
  fi
done

FIND_CMD="$FIND_CMD \( $EXT_COND \)"

# Add path filters
if [ -n "$INCLUDE_PATH" ]; then
  FIND_CMD="$FIND_CMD -path \"*/$INCLUDE_PATH/*\""
fi

# Default exclusions
DEFAULT_EXCLUDES="venv|__pycache__|.git|node_modules|.venv"
if [ -n "$EXCLUDE_PATH" ]; then
  EXCLUDE_PATH="$DEFAULT_EXCLUDES|$EXCLUDE_PATH"
else
  EXCLUDE_PATH="$DEFAULT_EXCLUDES"
fi

# Build grep -v patterns for exclusion
IFS='|' read -ra EXCL_ARRAY <<< "$EXCLUDE_PATH"
for excl in "${EXCL_ARRAY[@]}"; do
  excl=$(echo "$excl" | xargs)
  FIND_CMD="$FIND_CMD | grep -v '/$excl/' | grep -v '/$excl$'"
done

# Add sorting
if [ "$SORT" = "true" ]; then
  FIND_CMD="$FIND_CMD | sort"
fi

# Add limit
if [ "$MAX_RESULTS" -gt 0 ]; then
  FIND_CMD="$FIND_CMD | head -$MAX_RESULTS"
fi

eval "$FIND_CMD"

# Show total count
TOTAL_CMD="find \"$ROOT_DIR\" -type f \( $EXT_COND \)"
if [ -n "$INCLUDE_PATH" ]; then
  TOTAL_CMD="$TOTAL_CMD -path \"*/$INCLUDE_PATH/*\""
fi
for excl in "${EXCL_ARRAY[@]}"; do
  excl=$(echo "$excl" | xargs)
  TOTAL_CMD="$TOTAL_CMD | grep -v '/$excl/' | grep -v '/$excl$'"
done
TOTAL_CMD="$TOTAL_CMD | wc -l"

TOTAL=$(eval "$TOTAL_CMD" 2>/dev/null || echo "?")

echo ""
echo "--- Total matching files: $TOTAL ---"
