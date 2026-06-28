#!/usr/bin/env bash
set -euo pipefail

# run-tests: Auto-detect project type and run tests with standard flags.
# Usage: run-tests [--verbose] [--brief] [--timeout=SECS] [--count=N] [--grep=PATTERN] <path>
# Detects: go test, pytest, vitest/jest, or falls back to a generic test command.

VERBOSE=false
BRIEF=false
TIMEOUT=""
COUNT=""
GREP=""
TEST_PATH=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --verbose|-v)
      VERBOSE=true
      shift
      ;;
    --brief|-b)
      BRIEF=true
      shift
      ;;
    --timeout=*)
      TIMEOUT="${1#*=}"
      shift
      ;;
    --count=*)
      COUNT="${1#*=}"
      shift
      ;;
    --grep=*)
      GREP="${1#*=}"
      shift
      ;;
    -*)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
    *)
      if [[ -z "$TEST_PATH" ]]; then
        TEST_PATH="$1"
      else
        echo "Error: unexpected argument: $1" >&2
        exit 1
      fi
      shift
      ;;
  esac
done

if [[ -z "$TEST_PATH" ]]; then
  echo "Usage: $0 [--verbose] [--brief] [--timeout=SECS] [--count=N] [--grep=PATTERN] <test_path>" >&2
  exit 1
fi

# Build args array from flags
ARGS=()
if $VERBOSE; then ARGS+=(--verbose); fi

# Determine project root for framework detection
PROJECT_ROOT="$PWD"
if [[ -f "go.mod" ]]; then
  PROJECT_ROOT="$PWD"
elif [[ -f "${TEST_PATH}/go.mod" ]]; then
  PROJECT_ROOT="$TEST_PATH"
fi

run_go_test() {
  local path="$1"
  local base_dir
  if [[ -f "go.mod" ]]; then
    base_dir="."
  elif [[ -f "${path}/go.mod" ]]; then
    base_dir="$path"
    path="."
  else
    # Find nearest go.mod
    base_dir=$(cd "$path" 2>/dev/null && while [[ "$PWD" != "/" ]]; do if [[ -f "go.mod" ]]; then echo "$PWD"; break; fi; cd ..; done)
    if [[ -z "$base_dir" ]]; then
      base_dir="."
    fi
  fi
  local cmd="cd ${base_dir} && go test"
  if $VERBOSE; then cmd+=" -v"; fi
  if [[ -n "$TIMEOUT" ]]; then cmd+=" -timeout ${TIMEOUT}s"; fi
  if [[ -n "$COUNT" ]]; then cmd+=" -count=${COUNT}"; fi
  if [[ -n "$GREP" ]]; then cmd+=" -run '${GREP}'"; fi
  cmd+=" ${path}"
  if $BRIEF; then
    eval "$cmd" 2>&1 | grep -E '(FAIL|ERROR|--- |ok |^\?|panic:|PASS|^$)' || true
  else
    eval "$cmd"
  fi
}

run_pytest() {
  local path="$1"
  local cmd="python -m pytest"
  if $VERBOSE; then cmd+=" -v"; fi
  if [[ -n "$TIMEOUT" ]]; then cmd+=" --timeout=${TIMEOUT}"; fi
  if [[ -n "$GREP" ]]; then cmd+=" -k '${GREP}'"; fi
  cmd+=" ${path}"
  if $BRIEF; then
    # Only show failures, errors, and the summary footer
    eval "$cmd" 2>&1 | grep -E '(FAILED|ERRORS|FAILURES|failed|error|^=.*=.*passed|^=.*=.*failed|^short test summary|^[0-9]+ passed|^[0-9]+ failed|PASSED|FAIL$)' || true
  else
    eval "$cmd"
  fi
}

run_jest() {
  local path="$1"
  local cmd="npx $2"
  cmd+=" ${path}"
  if $VERBOSE; then cmd+=" --verbose"; fi
  if [[ -n "$GREP" ]]; then cmd+=" -t '${GREP}'"; fi
  if $BRIEF; then
    eval "$cmd" 2>&1 | grep -v 'jest-haste-map\|Browserslist\|DeprecationWarning\|deprecated' | grep -E '(FAIL|PASS|Tests:|Test Suites:|FAILED)' || true
  else
    eval "$cmd"
  fi
}

# Detect and run
if [[ -f "$PROJECT_ROOT/go.mod" ]] || find "$TEST_PATH" -maxdepth 1 -name "go.mod" 2>/dev/null | grep -q .; then
  run_go_test "$TEST_PATH"
  exit $?
fi

if find "$TEST_PATH" -maxdepth 2 -name "*.py" 2>/dev/null | grep -q .; then
  run_pytest "$TEST_PATH"
  exit $?
fi

if find "$TEST_PATH" -maxdepth 2 \( -name "*.ts" -o -name "*.js" -o -name "*.tsx" -o -name "*.jsx" \) 2>/dev/null | grep -q .; then
  if command -v npx &>/dev/null; then
    runner=""
    if [[ -f "package.json" ]] && grep -q '"vitest"' package.json 2>/dev/null; then
      runner="vitest run"
    elif [[ -f "package.json" ]] && grep -q '"jest"' package.json 2>/dev/null; then
      runner="jest"
    fi
    if [[ -n "$runner" ]]; then
      run_jest "$TEST_PATH" "$runner"
      exit $?
    fi
  fi
fi

echo "Error: Could not auto-detect test framework for ${TEST_PATH}" >&2
exit 1
