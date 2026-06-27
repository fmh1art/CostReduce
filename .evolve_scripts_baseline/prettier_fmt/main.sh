#!/bin/bash
# Script: prettier_fmt
# Description: Format or check code formatting with Prettier. Supports checking (--check),
# writing (--write), and targeting specific files, directories, or glob patterns.
# Replaces 'cd /app && npx prettier --write/--check <files>' with a single script call.
# Usage: main.sh <project_root> <action> <targets> [options]
#   Actions:
#     --write       Format files in-place (default)
#     --check       Only check formatting, don't modify files
#   Targets: space-separated file paths, directories, or glob patterns
#   Options:
#     --no-cd       Stay in current directory instead of cd'ing to project_root

PROJECT_ROOT="$1"
ACTION="$2"
shift 2

# Defaults
NO_CD=false
TARGETS=""

# Collect remaining args as targets
TARGETS="$@"

# If no action specified, default to --write
if [ -z "$ACTION" ] || { [ "$ACTION" != "--write" ] && [ "$ACTION" != "--check" ]; }; then
  # If first arg is not an action, treat it as a target and default action
  if [ -n "$ACTION" ] && [ "${ACTION#--}" = "$ACTION" ]; then
    TARGETS="$ACTION $TARGETS"
  fi
  ACTION="--write"
fi

# Check for --no-cd in targets and extract it
for token in $TARGETS; do
  if [ "$token" = "--no-cd" ]; then
    NO_CD=true
    # Remove --no-cd from TARGETS
    TARGETS=$(echo "$TARGETS" | sed 's/--no-cd//g')
    break
  fi
done

if [ -z "$PROJECT_ROOT" ]; then
  echo "ERROR: project_root is required. Usage: prettier_fmt <project_root> [--write|--check] <targets>"
  exit 1
fi

if [ ! -d "$PROJECT_ROOT" ]; then
  echo "ERROR: Directory not found: $PROJECT_ROOT"
  exit 1
fi

echo "=== Prettier Formatting ==="
echo "Project: $PROJECT_ROOT"
echo "Action: ${ACTION#--}"
[ -n "$TARGETS" ] && echo "Targets: $TARGETS"
echo ""

if [ "$NO_CD" = true ]; then
  CMD="npx prettier $ACTION $TARGETS"
else
  CMD="cd \"$PROJECT_ROOT\" && npx prettier $ACTION $TARGETS"
fi

echo "Running: $CMD"
echo "--- Output ---"
eval "$CMD" 2>&1
EXIT_CODE=$?

# For --check, prettier exits with 0 if all files formatted, 1 if some are unformatted, 2 if error
if [ "$ACTION" = "--check" ]; then
  if [ $EXIT_CODE -eq 0 ]; then
    echo ""
    echo "All matched files are properly formatted."
  elif [ $EXIT_CODE -eq 1 ]; then
    echo ""
    echo "Some files are not formatted (see above)."
  else
    echo ""
    echo "Prettier encountered an error."
  fi
fi

exit $EXIT_CODE
