#!/bin/bash
# Script: gofmt_check
# Description: Check Go source file(s) syntax using 'gofmt -e'. Reports whether the file(s) have valid Go syntax.
# Replaces the pattern of 'cd <project> && gofmt -e <file> > /dev/null && echo "Syntax OK" || echo "Syntax errors"'.
# Supports single files, directories (check all .go files), and glob patterns.
# Usage: main.sh <project_root> [target_file_or_dir]

PROJECT_ROOT="${1:-/app}"
TARGET="${2:-.}"

if [ ! -d "$PROJECT_ROOT" ]; then
  echo "ERROR: Project root not found: $PROJECT_ROOT"
  exit 1
fi

cd "$PROJECT_ROOT" || exit 1

GO_VERSION=$(gofmt --help 2>/dev/null | head -1 || go version 2>/dev/null)
if ! command -v gofmt &>/dev/null; then
  echo "ERROR: gofmt not found"
  exit 1
fi

echo "=== Go Syntax Check ==="
echo "Project: $PROJECT_ROOT"
echo "Target: $TARGET"
echo ""

OVERALL=true
ERRORS=0
FILES=0

if [ -f "$TARGET" ]; then
  # Single file
  FILES=1
  OUTPUT=$(gofmt -e "$TARGET" 2>&1)
  if [ $? -eq 0 ] && [ -z "$OUTPUT" ]; then
    echo "OK: $TARGET"
  else
    echo "ERRORS in $TARGET:"
    echo "$OUTPUT"
    OVERALL=false
    ERRORS=$((ERRORS + 1))
  fi
elif [ -d "$TARGET" ]; then
  # Directory - find all .go files
  while IFS= read -r -d '' file; do
    FILES=$((FILES + 1))
    OUTPUT=$(gofmt -e "$file" 2>&1)
    if [ $? -ne 0 ] || [ -n "$OUTPUT" ]; then
      echo "ERRORS in $file:"
      echo "$OUTPUT" | head -10
      ERRORS=$((ERRORS + 1))
      OVERALL=false
    fi
  done < <(find "$TARGET" -name '*.go' -not -path '*/vendor/*' -not -path '*/.git/*' -print0 2>/dev/null)
fi

echo ""
echo "--- Summary: $FILES file(s) checked, $ERRORS with errors ---"

if [ "$OVERALL" = true ]; then
  echo "All Go files have valid syntax."
  exit 0
else
  echo "Some Go files have syntax errors."
  exit 1
fi
