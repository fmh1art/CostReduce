#!/bin/bash
# Script: multi_grep
# Description: Search for multiple patterns across specified files/directories in one call
# Usage: main.sh <patterns> <targets> [case_insensitive=true|false] [file_only=true|false] [context=N] [max_matches=N] [include=EXT1,EXT2,...]

PATTERNS="$1"
TARGETS="$2"

# Parse optional named parameters
CASE_INSENSITIVE=false
FILE_ONLY=false
CONTEXT=0
MAX_MATCHES=60
INCLUDE_PATTERNS=""

for arg in "${@:3}"; do
  case "$arg" in
    case_insensitive=*) CASE_INSENSITIVE="${arg#*=}" ;;
    file_only=*) FILE_ONLY="${arg#*=}" ;;
    context=*) CONTEXT="${arg#*=}" ;;
    max_matches=*) MAX_MATCHES="${arg#*=}" ;;
    include=*) INCLUDE_PATTERNS="${arg#*=}" ;;
  esac
done

if [ -z "$PATTERNS" ] || [ -z "$TARGETS" ]; then
  echo "ERROR: patterns and targets are required."
  echo "Usage: main.sh '<pattern1|pattern2|...>' '<file1,file2,... or /dir>' [options]"
  echo "Options: case_insensitive=true|false, file_only=true|false, context=N, max_matches=N, include=EXT1,EXT2,..."
  exit 1
fi

echo "=== Multi-Pattern Search ==="
echo "Patterns: $PATTERNS"
echo "Targets: $TARGETS"
[ -n "$INCLUDE_PATTERNS" ] && echo "Include file types: $INCLUDE_PATTERNS"
echo ""

# Build grep command
GREP_CMD="grep -rn"

if [ "$CASE_INSENSITIVE" = "true" ]; then
  GREP_CMD="$GREP_CMD -i"
fi

if [ "$FILE_ONLY" = "true" ]; then
  GREP_CMD="$GREP_CMD -l"
fi

if [ "$CONTEXT" -gt 0 ]; then
  GREP_CMD="$GREP_CMD -C $CONTEXT"
fi

# Add include patterns (e.g., "*.c,*.h" -> --include="*.c" --include="*.h")
if [ -n "$INCLUDE_PATTERNS" ]; then
  IFS=',' read -ra INC_ARRAY <<< "$INCLUDE_PATTERNS"
  for inc in "${INC_ARRAY[@]}"; do
    inc=$(echo "$inc" | xargs)
    GREP_CMD="$GREP_CMD --include=\"$inc\""
  done
fi

# Convert comma-separated targets to space-separated
TARGET_LIST=$(echo "$TARGETS" | tr ',' ' ')

# Handle the patterns - escape for grep
GREP_CMD="$GREP_CMD -E \"$PATTERNS\" $TARGET_LIST 2>/dev/null | head -$MAX_MATCHES"

eval "$GREP_CMD"

echo ""
echo "--- Search complete (showed up to $MAX_MATCHES matches) ---"
