#!/bin/bash
# Script: go_vet
# Description: Run go vet on a Go project to check for common errors and suspicious constructs.
# Replaces the pattern of 'cd <project> && go vet ./... 2>&1 | head -30'.
# Supports package paths, build tags, and output limiting.
# Usage: main.sh <project_root> [package_path=./...] [--tags=TAGS] [--max=30]

PROJECT_ROOT="${1:-/app}"
PACKAGE="${2:-./...}"
TAGS=""
MAX_LINES=30

for arg in "${@:3}"; do
  case "$arg" in
    --tags=*) TAGS="${arg#*=}" ;;
    --max=*) MAX_LINES="${arg#*=}" ;;
  esac
done

if [ ! -d "$PROJECT_ROOT" ]; then
  echo "ERROR: Project root not found: $PROJECT_ROOT"
  exit 1
fi

if [ ! -f "$PROJECT_ROOT/go.mod" ]; then
  echo "WARNING: No go.mod found at $PROJECT_ROOT. May not be a Go project."
fi

echo "=== Go Vet ==="
echo "Project: $PROJECT_ROOT"
echo "Package: $PACKAGE"
echo ""

cd "$PROJECT_ROOT" || exit 1

GO_VERSION=$(go version 2>/dev/null)
if [ $? -ne 0 ]; then
  echo "ERROR: Go is not installed or not in PATH"
  exit 1
fi
echo "Go version: $GO_VERSION"
echo ""

CMD="go vet"
[ -n "$TAGS" ] && CMD="$CMD -tags \"$TAGS\""
CMD="$CMD $PACKAGE 2>&1"

echo "Running: $CMD"
echo "--- Vet Output ---"
eval "$CMD" | head -"$MAX_LINES"
STATUS=$?

echo ""
if [ $STATUS -eq 0 ]; then
  echo "=== Vet PASSED ==="
else
  echo "=== Vet FAILED (exit code: $STATUS) ==="
fi

exit $STATUS
