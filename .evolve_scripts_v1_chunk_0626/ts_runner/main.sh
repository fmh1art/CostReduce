#!/bin/bash
# ts_runner - Run TypeScript/Node.js files or inline expressions with tsx
# Usage: ts_runner <project_root> <action> <target> [options]
#
# Actions:
#   run <file>             - Run a TypeScript file
#   eval '<code>'          - Evaluate inline TypeScript code
#   test <file>            - Run a test file with tsx
#   vitest <filter>        - Run vitest with test name filter (or 'all' for full suite)
#   vitest-file <file>     - Run a specific test file with vitest
#   vitest-paths <paths>   - Run vitest on specific paths (files or directories, comma-separated)
#   tsc <project_root>     - Run TypeScript type checking (tsc --noEmit) with timeout

PROJECT_ROOT="$1"
ACTION="$2"
TARGET="$3"
shift 3 2>/dev/null || shift $#

CONDITIONS="ark-ts"
TIMEOUT=60
MAX_LINES=100
FILTER=""
NO_CONDITIONS=false
RUNNER=""
VITEST_CONFIG=""
TSCONFIG=""
FAILURES=false

while [ $# -gt 0 ]; do
    case "$1" in
        --conditions|-c)
            CONDITIONS="$2"
            shift 2
            ;;
        --timeout)
            TIMEOUT="$2"
            shift 2
            ;;
        --max-lines|-m)
            MAX_LINES="$2"
            shift 2
            ;;
        --filter|-f)
            FILTER="$2"
            shift 2
            ;;
        --no-conditions)
            NO_CONDITIONS=true
            shift
            ;;
        --runner)
            RUNNER="$2"
            shift 2
            ;;
        --vitest-config)
            VITEST_CONFIG="$2"
            shift 2
            ;;
        --tsconfig)
            TSCONFIG="$2"
            shift 2
            ;;
        --failures|-F)
            FAILURES=true
            shift
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

if [ -z "$PROJECT_ROOT" ] || [ -z "$ACTION" ]; then
    echo "Usage: ts_runner <project_root> <action> <target> [options]"
    echo ""
    echo "Actions:"
    echo "  run <file>                  - Run a TypeScript file"
    echo "  eval '<code>'               - Evaluate inline TypeScript code"
    echo "  test <file>                 - Run a test file with tsx"
    echo "  vitest <filter>             - Run vitest with test name filter (or 'all' for full suite)"
    echo "  vitest-file <file>          - Run a specific test file with vitest"
    echo "  vitest-paths <paths>        - Run vitest on specific paths (comma-separated files/dirs)"
    echo "  tsc <project_root>          - Run TypeScript type checking (tsc --noEmit) with timeout"
    echo ""
    echo "Options:"
    echo "  --conditions, -c <val>       Node --conditions flag (default: ark-ts)"
    echo "  --timeout <sec>              Timeout in seconds (default: 60)"
    echo "  --max-lines, -m <n>          Max output lines (default: 100, 0=unlimited)"
    echo "  --filter, -f <pattern>       Filter output lines matching this pattern"
    echo "  --failures, -F               Show only test failure details with context (for vitest actions)"
    echo "  --no-conditions             Skip --conditions flag"
    echo "  --runner <cmd>              Custom runner command"
    echo "  --vitest-config <path>      Vitest config file path (e.g., vitest.config.ts)"
    echo "  --tsconfig <path>           Tsconfig file path for tsc action (e.g., tsconfig.esm.json)"
    exit 1
fi

# For tsc action, TARGET is not used as a file but could be '.' or similar
if [ "$ACTION" != "tsc" ] && [ -z "$TARGET" ]; then
    echo "Error: <target> is required for action '$ACTION'"
    exit 1
fi

if [ ! -d "$PROJECT_ROOT" ]; then
    echo "Error: Directory '$PROJECT_ROOT' does not exist"
    exit 1
fi

cd "$PROJECT_ROOT" || exit 1

# Build the base command
if [ -n "$RUNNER" ]; then
    BASE_CMD="$RUNNER"
elif [ "$NO_CONDITIONS" = true ]; then
    BASE_CMD="node --import tsx"
else
    BASE_CMD="node --conditions $CONDITIONS --import tsx"
fi

# Build vitest config flag
VITEST_CONFIG_FLAG=""
if [ -n "$VITEST_CONFIG" ]; then
    VITEST_CONFIG_FLAG="--config $VITEST_CONFIG"
fi

FAILURE_CONTEXT_BEFORE=2
FAILURE_CONTEXT_AFTER=20

case "$ACTION" in
    run)
        CMD="$BASE_CMD \"$TARGET\""
        ;;
    eval)
        CMD="$BASE_CMD -e \"$TARGET\""
        ;;
    test)
        CMD="$BASE_CMD \"$TARGET\""
        ;;
    vitest)
        if [ "$TARGET" = "all" ]; then
            CMD="npx vitest run $VITEST_CONFIG_FLAG"
        else
            CMD="npx vitest run $VITEST_CONFIG_FLAG -t \"$TARGET\""
        fi
        ;;
    vitest-file)
        CMD="npx vitest run $VITEST_CONFIG_FLAG \"$TARGET\""
        ;;
    vitest-paths)
        # Convert comma-separated paths to space-separated for shell
        PATHS=$(echo "$TARGET" | tr ',' ' ')
        CMD="npx vitest run $VITEST_CONFIG_FLAG $PATHS --reporter=verbose"
        # For vitest-paths, we use tail-style limiting (show last N lines) since
        # vitest output shows pass/fail summary at the end
        echo "Running: timeout $TIMEOUT bash -c \"cd '$PROJECT_ROOT' && $CMD 2>&1 | tail -n $MAX_LINES\""
        echo ""
        if [ "$FAILURES" = true ]; then
            # Run with failures filter: capture full output, then show failures with context
            timeout "$TIMEOUT" bash -c "cd '$PROJECT_ROOT' && $CMD 2>&1" | grep -B $FAILURE_CONTEXT_BEFORE -A $FAILURE_CONTEXT_AFTER "FAIL" | head -n "$MAX_LINES"
        else
            timeout "$TIMEOUT" bash -c "cd '$PROJECT_ROOT' && $CMD 2>&1" | tail -n "$MAX_LINES"
        fi
        EXIT_CODE=$?
        if [ $EXIT_CODE -eq 124 ]; then
            echo ""
            echo "Command timed out after ${TIMEOUT}s"
        fi
        exit $EXIT_CODE
        ;;
    tsc)
        TSC_FLAG=""
        if [ -n "$TSCONFIG" ]; then
            TSC_FLAG="-p \"$TSCONFIG\""
        fi
        CMD="npx tsc --noEmit $TSC_FLAG 2>&1 | head -${MAX_LINES}"
        # For tsc, we pipe directly without the general pipeline below
        echo "Running: timeout $TIMEOUT bash -c \"$CMD\""
        echo ""
        timeout "$TIMEOUT" bash -c "cd \"$PROJECT_ROOT\" && npx tsc --noEmit $TSC_FLAG 2>&1" | head -n "$MAX_LINES"
        EXIT_CODE=$?
        if [ $EXIT_CODE -eq 124 ]; then
            echo ""
            echo "Command timed out after ${TIMEOUT}s"
        elif [ $EXIT_CODE -ne 0 ]; then
            echo ""
            echo "Type checking found errors (exit code: $EXIT_CODE)"
        fi
        exit $EXIT_CODE
        ;;
    *)
        echo "Unknown action: $ACTION"
        echo "Valid actions: run, eval, test, vitest, vitest-file, vitest-paths, tsc"
        exit 1
        ;;
esac

echo "Running: $CMD"
echo ""

# Execute with timeout and optional output limiting
if [ "$FAILURES" = true ]; then
    # Failures mode: show only FAIL lines with context
    timeout "$TIMEOUT" bash -c "$CMD 2>&1" | grep -B $FAILURE_CONTEXT_BEFORE -A $FAILURE_CONTEXT_AFTER "FAIL" | {
        if [ "$MAX_LINES" -gt 0 ]; then
            head -n "$MAX_LINES"
        else
            cat
        fi
    }
    EXIT_CODE=$?
elif [ "$MAX_LINES" -gt 0 ]; then
    timeout "$TIMEOUT" bash -c "$CMD 2>&1" | {
        if [ -n "$FILTER" ]; then
            grep -E "$FILTER" | head -n "$MAX_LINES"
        else
            head -n "$MAX_LINES"
        fi
    }
    EXIT_CODE=$?
else
    timeout "$TIMEOUT" bash -c "$CMD 2>&1" | {
        if [ -n "$FILTER" ]; then
            grep -E "$FILTER"
        else
            cat
        fi
    }
    EXIT_CODE=$?
fi

if [ $EXIT_CODE -eq 124 ]; then
    echo ""
    echo "Command timed out after ${TIMEOUT}s"
elif [ $EXIT_CODE -ne 0 ]; then
    echo ""
    echo "Command finished (exit code: $EXIT_CODE)"
fi

exit $EXIT_CODE
