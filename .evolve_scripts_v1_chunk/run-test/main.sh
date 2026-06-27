#!/bin/bash
# Run tests with concise output: summarize results to reduce token cost.
# Usage: main.sh <tool> <directory> [test-args...]
#   go        <dir> [pkg...]     - Runs go test with filtered PASS/FAIL/--- output
#   vitest    <dir> [test-file]  - Runs npx vitest with concise summary via tail
#   jest      <dir> [test-file]  - Runs npx jest with concise summary via tail
#   pytest    <dir> [test-file]  - Runs python -m pytest with short summary
#   cargo     <dir> [test-name]  - Runs cargo test with filtered output
#   npm       <dir> [script]     - Runs npm test <script> with tail -5 output
# Without tool detection, falls back to running the command with output tail.

set -euo pipefail

usage() {
  echo "Usage: main.sh <tool> <directory> [test-args...]" >&2
  echo "Tools: go, vitest, jest, pytest, cargo, npm" >&2
  exit 1
}

[ $# -ge 2 ] || usage

TOOL="$1"
DIR="$2"
shift 2

cd "$DIR"

case "$TOOL" in
  go)
    PKG="${1:-./...}"
    shift 2>/dev/null || true
    timeout 120 go test "$PKG" -count=1 -v 2>&1 | grep -E "^(--- |ok |FAIL|--- PASS|--- FAIL|^[?] )" || true
    echo "---"
    go test "$PKG" -count=1 2>&1 | tail -3
    ;;
  vitest)
    TEST_FILE="${1:-}"
    if [ -n "$TEST_FILE" ]; then
      timeout 120 npx vitest run --reporter=verbose "$TEST_FILE" 2>&1 | tail -15
    else
      timeout 120 npx vitest run 2>&1 | tail -15
    fi
    ;;
  jest)
    TEST_FILE="${1:-}"
    if [ -n "$TEST_FILE" ]; then
      timeout 120 npx jest --verbose "$TEST_FILE" 2>&1 | tail -15
    else
      timeout 120 npx jest --verbose 2>&1 | tail -15
    fi
    ;;
  pytest)
    TEST_FILE="${1:-}"
    if [ -n "$TEST_FILE" ]; then
      timeout 120 python3 -m pytest "$TEST_FILE" -v 2>&1 | tail -20
    else
      timeout 120 python3 -m pytest -v 2>&1 | tail -20
    fi
    ;;
  cargo)
    TEST_NAME="${1:-}"
    if [ -n "$TEST_NAME" ]; then
      timeout 180 cargo test "$TEST_NAME" 2>&1 | grep -E "(test |running|result|FAILED|ok)" || true
    else
      timeout 180 cargo test 2>&1 | grep -E "(test |running|result|FAILED|ok)" || true
    fi
    ;;
  npm)
    SCRIPT="${1:-test}"
    timeout 120 npm run "$SCRIPT" 2>&1 | tail -5
    ;;
  *)
    echo "Unknown tool: $TOOL" >&2
    usage
    ;;
esac

# Capture and return the exit code
EXIT_CODE=$?
exit $EXIT_CODE
