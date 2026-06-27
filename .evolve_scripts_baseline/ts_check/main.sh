#!/bin/bash
# Script: ts_check
# Description: Run TypeScript compiler type checking (tsc --noEmit) on a project or specific files.
# Replaces 'cd /app && npx tsc --noEmit 2>&1' with a single script call.
# Supports filtering output to show only errors matching specific patterns,
# and limiting the number of errors shown.
# Usage: main.sh <project_root> [options]
#   Options:
#     --grep=PATTERN      Show only errors matching this pattern
#     --max=N             Maximum number of errors to show (default: 50)
#     --tsconfig=FILE     Path to tsconfig.json relative to project root (default: tsconfig.json)
#     --show-all          Show all errors without limit
#     --no-cd             Stay in current directory instead of cd'ing to project_root

PROJECT_ROOT="$1"
shift

# Defaults
GREP=""
MAX_ERRORS=50
TSCONFIG="tsconfig.json"
SHOW_ALL=false
NO_CD=false

while [ $# -gt 0 ]; do
  case "$1" in
    --grep=*) GREP="${1#*=}" ;;
    --max=*) MAX_ERRORS="${1#*=}" ;;
    --tsconfig=*) TSCONFIG="${1#*=}" ;;
    --show-all) SHOW_ALL=true ;;
    --no-cd) NO_CD=true ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
  shift
done

if [ -z "$PROJECT_ROOT" ]; then
  echo "ERROR: project_root is required. Usage: ts_check <project_root> [--grep=PATTERN] [--max=N]"
  exit 1
fi

if [ ! -d "$PROJECT_ROOT" ]; then
  echo "ERROR: Directory not found: $PROJECT_ROOT"
  exit 1
fi

if [ ! -f "$PROJECT_ROOT/$TSCONFIG" ]; then
  echo "WARNING: $TSCONFIG not found at $PROJECT_ROOT. TypeScript may not be configured."
fi

if [ "$NO_CD" = true ]; then
  TSCMD="npx tsc --noEmit"
else
  TSCMD="cd \"$PROJECT_ROOT\" && npx tsc --noEmit"
fi

# Add tsconfig if non-default
if [ "$TSCONFIG" != "tsconfig.json" ]; then
  TSCMD="$TSCMD --project \"$TSCONFIG\""
fi

TSCMD="$TSCMD 2>&1"

echo "=== TypeScript Type Check ==="
echo "Project: $PROJECT_ROOT"
echo "Config: $TSCONFIG"
[ -n "$GREP" ] && echo "Filter: $GREP"
[ "$SHOW_ALL" = false ] && echo "Max errors shown: $MAX_ERRORS"
echo ""

OUTPUT=$(eval "$TSCMD")
EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
  echo "No type errors found."
  exit 0
fi

# Extract file:line:col - error lines
ERRORS=$(echo "$OUTPUT" | grep -E '^[^ ]+\.ts\([0-9]+,[0-9]+\)' 2>/dev/null)
if [ -z "$ERRORS" ]; then
  # Try alternative format: file.ts:line:col - error
  ERRORS=$(echo "$OUTPUT" | grep -E '\.tsx?:\d+:\d+ - error' 2>/dev/null)
fi
if [ -z "$ERRORS" ]; then
  ERRORS="$OUTPUT"
fi

if [ -n "$GREP" ]; then
  ERRORS=$(echo "$ERRORS" | grep -i "$GREP")
fi

if [ "$SHOW_ALL" = true ]; then
  echo "$ERRORS"
  echo ""
  echo "--- Total errors: $(echo "$ERRORS" | grep -c .) ---"
else
  echo "$ERRORS" | head -$MAX_ERRORS
  TOTAL=$(echo "$ERRORS" | grep -c .)
  echo ""
  echo "--- Showing up to $MAX_ERRORS of $TOTAL error(s) ---"
fi

exit $EXIT_CODE
