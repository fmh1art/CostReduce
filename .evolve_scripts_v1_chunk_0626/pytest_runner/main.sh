#!/bin/bash
# pytest_runner - Run Python pytest tests with filtering, timeout, and output control
# Usage: pytest_runner <project_root> [test_path...] [options]

set -euo pipefail

PROJECT_ROOT="${1:-}"
shift 1 2>/dev/null || true

TEST_PATHS=()
GREP_PATTERN=""
VERBOSE=""
TIMEOUT=60
TAIL_LINES=0
HEAD_LINES=0
NO_HEADER=""
EXITFIRST=""
QUIET=""
SUMMARY_ONLY=""

while [ $# -gt 0 ]; do
    case "$1" in
        --grep)
            GREP_PATTERN="$2"
            shift 2
            ;;
        --verbose|-v)
            VERBOSE="-v"
            shift
            ;;
        --timeout)
            TIMEOUT="$2"
            shift 2
            ;;
        --tail)
            TAIL_LINES="$2"
            shift 2
            ;;
        --head)
            HEAD_LINES="$2"
            shift 2
            ;;
        --no-header)
            NO_HEADER="--no-header"
            shift
            ;;
        --exitfirst|-x)
            EXITFIRST="-x"
            shift
            ;;
        --quiet|-q)
            QUIET="-q"
            shift
            ;;
        --summary-only)
            SUMMARY_ONLY="1"
            shift
            ;;
        *)
            TEST_PATHS+=("$1")
            shift
            ;;
    esac
done

if [ -z "$PROJECT_ROOT" ]; then
    echo "Usage: pytest_runner <project_root> [test_path...] [options]"
    echo "Options:"
    echo "  --grep PATTERN    Filter tests by name (-k)"
    echo "  --verbose, -v     Verbose output"
    echo "  --timeout N       Timeout in seconds (default: 60)"
    echo "  --tail N          Show last N lines of output"
    echo "  --head N          Show first N lines of output"
    echo "  --no-header       Suppress pytest header"
    echo "  --exitfirst, -x   Stop on first failure"
    echo "  --quiet, -q       Less verbose output"
    echo "  --summary-only    Show only summary lines (passed/failed/error/slowest durations)"
    exit 1
fi

if [ ! -d "$PROJECT_ROOT" ]; then
    echo "Error: Directory '$PROJECT_ROOT' does not exist"
    exit 1
fi

cd "$PROJECT_ROOT" || exit 1

# Build command
CMD="python -m pytest"

if [ ${#TEST_PATHS[@]} -gt 0 ]; then
    CMD="$CMD ${TEST_PATHS[*]}"
fi

if [ -n "$VERBOSE" ]; then
    CMD="$CMD $VERBOSE"
fi

if [ -n "$QUIET" ]; then
    CMD="$CMD $QUIET"
fi

if [ -n "$EXITFIRST" ]; then
    CMD="$CMD $EXITFIRST"
fi

if [ -n "$GREP_PATTERN" ]; then
    CMD="$CMD -k '$GREP_PATTERN'"
fi

if [ -n "$NO_HEADER" ]; then
    CMD="$CMD $NO_HEADER"
fi

echo "Running: timeout $TIMEOUT $CMD 2>&1"
echo ""

# Run the command with appropriate output filtering
if [ -n "$SUMMARY_ONLY" ]; then
    # Show only summary-relevant lines: passed/failed/error/slowest/short summary/FAILURES
    timeout "$TIMEOUT" bash -c "$CMD 2>&1" | grep -E "(passed|failed|FAILED|PASSED|ERROR|error|FAILURES|short test summary info|slowest|warnings? summary|== )" || true
elif [ "$TAIL_LINES" -gt 0 ]; then
    timeout "$TIMEOUT" bash -c "$CMD 2>&1" | tail -n "$TAIL_LINES"
elif [ "$HEAD_LINES" -gt 0 ]; then
    timeout "$TIMEOUT" bash -c "$CMD 2>&1" | head -n "$HEAD_LINES"
else
    timeout "$TIMEOUT" bash -c "$CMD 2>&1"
fi

EXIT_CODE=$?

if [ $EXIT_CODE -eq 124 ]; then
    echo ""
    echo "Test timed out after ${TIMEOUT}s"
elif [ $EXIT_CODE -ne 0 ]; then
    echo ""
    echo "Tests failed (exit code: $EXIT_CODE)"
fi

exit $EXIT_CODE
