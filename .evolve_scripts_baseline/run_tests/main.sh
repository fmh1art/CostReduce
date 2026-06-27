#!/bin/bash
# Script: run_tests
# Description: Run Python/Django, Go, Rust, or TypeScript tests with proper environment setup.
# Supports pytest, Django test runner, Go test runner, Cargo test runner, and vitest/npm test runner.
# Automatically creates required directories and sets environment.
# Usage: main.sh <test_target> [options]
#   test_target: file path, module path, directory, or test name pattern
#   Options:
#     --go                Use Go test runner instead of Python
#     --rust              Use Cargo test runner for Rust projects
#     --django            Use Django test runner instead of pytest
#     --ts                Use TypeScript/Node test runner (vitest, jest, or npm test)
#     --settings=MODULE   Django settings module (default: paperless.settings)
#     --app_dir=DIR       Application directory (default: /app/src for Python, /app for Go/Rust/TS)
#     --verbose, -v       Verbose output
#     --grep=PATTERN      Filter tests by name (pytest -k, Go -run, Rust testname filter, vitest -t)
#     --timeout=SECS      Timeout in seconds
#     --count=N           Test repetition count (Go only)
#     --tags=TAGS         Go build tags (Go only)
#     --runner=CMD        Explicit test runner command (e.g., "npx vitest", "npx jest", "npm test")
#     --args="..."        Additional arguments passed to the test runner

TEST_TARGET="$1"
shift

# Defaults
TEST_RUNNER="auto"
DJANGO=false
SETTINGS_MODULE="paperless.settings"
APP_DIR=""
VERBOSE=""
GREP=""
TIMEOUT=""
COUNT=""
TAGS=""
EXTRA_ARGS=""
RUNNER_CMD=""

# Parse options
while [ $# -gt 0 ]; do
  case "$1" in
    --go) TEST_RUNNER="go" ;;
    --rust) TEST_RUNNER="rust" ;;
    --django) DJANGO=true ; TEST_RUNNER="django" ;;
    --ts) TEST_RUNNER="ts" ;;
    --js) TEST_RUNNER="ts" ;;
    --settings=*) SETTINGS_MODULE="${1#*=}" ;;
    --app_dir=*) APP_DIR="${1#*=}" ;;
    --verbose|-v) VERBOSE="-v" ;;
    --grep=*) GREP="${1#*=}" ;;
    --timeout=*) TIMEOUT="${1#*=}" ;;
    --count=*) COUNT="${1#*=}" ;;
    --tags=*) TAGS="${1#*=}" ;;
    --runner=*) RUNNER_CMD="${1#*=}" ;;
    --args=*) EXTRA_ARGS="${1#*=}" ;;
    *) EXTRA_ARGS="$EXTRA_ARGS $1" ;;
  esac
  shift
done

if [ -z "$TEST_TARGET" ]; then
  echo "ERROR: Test target is required."
  echo "Usage: run_tests <test_target> [--go|--rust|--django|--ts] [--settings=MODULE] [--app_dir=DIR] [--verbose] [--grep=PATTERN] [--timeout=SECS] [--count=N] [--tags=TAGS] [--runner=CMD] [--args=\"...\"]"
  exit 1
fi

# Auto-detect test runner if not specified
if [ "$TEST_RUNNER" = "auto" ]; then
  # Check for Go patterns
  if echo "$TEST_TARGET" | grep -qE '\.go$|^\.\/|\.\.\.$'; then
    TEST_RUNNER="go"
  # Check for Python file patterns
  elif echo "$TEST_TARGET" | grep -qE '\.py$'; then
    TEST_RUNNER="pytest"
  # Check for Rust project (Cargo.toml exists) or package::test pattern
  elif [ -f "${APP_DIR:-/app}/Cargo.toml" ] || echo "$TEST_TARGET" | grep -qE '::'; then
    TEST_RUNNER="rust"
  elif [ -f "/app/src/Cargo.toml" ]; then
    TEST_RUNNER="rust"
  # Check for TypeScript project (package.json exists)
  elif [ -f "${APP_DIR:-/app}/package.json" ] || [ -f "/app/package.json" ]; then
    TEST_RUNNER="ts"
  else
    TEST_RUNNER="pytest"
  fi
fi

# --- TypeScript/Node test runner ---
if [ "$TEST_RUNNER" = "ts" ]; then
  APP_DIR="${APP_DIR:-/app}"

  cd "$APP_DIR" || { echo "ERROR: Cannot cd to $APP_DIR"; exit 1; }

  if [ ! -f "package.json" ]; then
    echo "WARNING: No package.json found at $APP_DIR. This may not be a Node/TypeScript project."
  fi

  # Determine test runner command
  if [ -z "$RUNNER_CMD" ]; then
    # Auto-detect: prefer vitest, then jest, then mocha, then npm test
    if [ -f "node_modules/.bin/vitest" ] || grep -q '"vitest"' package.json 2>/dev/null; then
      RUNNER_CMD="npx vitest run"
    elif [ -f "node_modules/.bin/jest" ] || grep -q '"jest"' package.json 2>/dev/null; then
      RUNNER_CMD="npx jest"
    elif [ -f "node_modules/.bin/mocha" ] || grep -q '"mocha"' package.json 2>/dev/null; then
      RUNNER_CMD="npx mocha"
    else
      RUNNER_CMD="npm test --"
    fi
  fi

  echo "=== Running TypeScript/Node Tests ==="
  echo "Target: $TEST_TARGET"
  echo "App dir: $APP_DIR"
  echo "Runner: $RUNNER_CMD"
  [ -n "$GREP" ] && echo "Filter: $GREP"
  echo ""

  # Build the test command
  CMD="$RUNNER_CMD"
  CMD="$CMD $TEST_TARGET"
  [ -n "$VERBOSE" ] && CMD="$CMD --reporter=verbose"
  # Handle --grep differently for mocha vs vitest
  if [ -n "$GREP" ]; then
    if echo "$RUNNER_CMD" | grep -qi "mocha"; then
      CMD="$CMD --grep \"$GREP\""
    else
      CMD="$CMD -t \"$GREP\""
    fi
  fi
  [ -n "$EXTRA_ARGS" ] && CMD="$CMD $EXTRA_ARGS"
  [ -n "$TIMEOUT" ] && CMD="timeout ${TIMEOUT}s $CMD"

  echo "Running: $CMD"
  echo "--- Output ---"
  eval "$CMD" 2>&1 || echo "Tests exited with code $?"
  exit $?
fi

# --- Rust/Cargo test runner ---
if [ "$TEST_RUNNER" = "rust" ]; then
  APP_DIR="${APP_DIR:-/app}"

  cd "$APP_DIR" || { echo "ERROR: Cannot cd to $APP_DIR"; exit 1; }

  if [ ! -f "Cargo.toml" ]; then
    echo "WARNING: No Cargo.toml found at $APP_DIR. This may not be a Rust project."
  fi

  echo "=== Running Rust Tests (cargo test) ==="
  echo "Target: $TEST_TARGET"
  echo "App dir: $APP_DIR"
  [ -n "$GREP" ] && echo "Filter: $GREP"
  echo ""

  # Build the cargo test command
  CMD="cargo test"
  [ -n "$VERBOSE" ] && CMD="$CMD -v"
  
  # If TEST_TARGET looks like a package::test_name pattern, extract package
  if echo "$TEST_TARGET" | grep -q '::'; then
    PACKAGE="${TEST_TARGET%%::*}"
    TEST_NAME="${TEST_TARGET#*::}"
    CMD="$CMD -p $PACKAGE"
    if [ -n "$TEST_NAME" ]; then
      CMD="$CMD -- $TEST_NAME"
    fi
  elif echo "$TEST_TARGET" | grep -qE '^[a-zA-Z_][a-zA-Z0-9_]*$'; then
    # Single test name
    CMD="$CMD -- $TEST_TARGET"
  else
    # It's a path or other target
    CMD="$CMD $TEST_TARGET"
  fi
  
  [ -n "$EXTRA_ARGS" ] && CMD="$CMD $EXTRA_ARGS"
  [ -n "$TIMEOUT" ] && CMD="timeout ${TIMEOUT}s $CMD"

  echo "Running: $CMD"
  echo "--- Output ---"
  eval "$CMD" 2>&1 || echo "Tests exited with code $?"
  exit $?
fi

# --- Go test runner ---
if [ "$TEST_RUNNER" = "go" ]; then
  APP_DIR="${APP_DIR:-/app}"
  
  cd "$APP_DIR" || { echo "ERROR: Cannot cd to $APP_DIR"; exit 1; }
  
  echo "=== Running Go Tests ==="
  echo "Target: $TEST_TARGET"
  echo "App dir: $APP_DIR"
  [ -n "$GREP" ] && echo "Filter: $GREP"
  [ -n "$TAGS" ] && echo "Tags: $TAGS"
  [ -n "$COUNT" ] && echo "Count: $COUNT"
  echo ""
  
  # Build the go test command
  CMD="go test"
  [ -n "$VERBOSE" ] && CMD="$CMD -v"
  [ -n "$COUNT" ] && CMD="$CMD -count=$COUNT"
  [ -n "$TAGS" ] && CMD="$CMD -tags \"$TAGS\""
  [ -n "$GREP" ] && CMD="$CMD -run \"$GREP\""
  CMD="$CMD $TEST_TARGET"
  [ -n "$EXTRA_ARGS" ] && CMD="$CMD $EXTRA_ARGS"
  [ -n "$TIMEOUT" ] && CMD="timeout ${TIMEOUT}s $CMD"
  
  echo "Running: $CMD"
  echo "--- Output ---"
  eval "$CMD" 2>&1 || echo "Tests exited with code $?"
  exit $?
fi

# --- Python/Django test runner ---
# Ensure /app/consume exists (needed by some Django apps like Paperless)
mkdir -p /app/consume 2>/dev/null

APP_DIR="${APP_DIR:-/app/src}"
cd "$APP_DIR" || { echo "ERROR: Cannot cd to $APP_DIR"; exit 1; }

echo "=== Running Tests ==="
echo "Target: $TEST_TARGET"
echo "App dir: $APP_DIR"
[ "$DJANGO" = true ] && echo "Runner: Django" && echo "Settings: $SETTINGS_MODULE"
[ -n "$GREP" ] && echo "Filter: $GREP"
echo ""

if [ "$DJANGO" = true ]; then
  CMD="DJANGO_SETTINGS_MODULE=$SETTINGS_MODULE python -m django test $TEST_TARGET --verbosity=2"
  [ -n "$EXTRA_ARGS" ] && CMD="$CMD $EXTRA_ARGS"
else
  CMD="python -m pytest $TEST_TARGET -xvs"
  [ -n "$GREP" ] && CMD="$CMD -k \"$GREP\""
  [ -n "$EXTRA_ARGS" ] && CMD="$CMD $EXTRA_ARGS"
  [ -n "$TIMEOUT" ] && CMD="timeout ${TIMEOUT}s $CMD"
fi

echo "Running: $CMD"
echo "--- Output ---"
eval "$CMD" 2>&1 || echo "Tests exited with code $?"
