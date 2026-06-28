#!/bin/bash
# run-tests - Run tests with standardized options, supporting pytest and jest
# Usage: run-tests [--dir=DIR] [--env KEY=val ...] [--grep=PATTERN] [--pytest|--jest] [--tail=N] [--verbose] [--coverage] [--no-header] [--no-cache] <test-path>

set -euo pipefail

DIR=""
GREP_PATTERN=""
TAIL_N=""
TEST_PATH=""
FRAMEWORK=""
NO_COVERAGE=true
VERBOSE=""
NO_HEADER=false
NO_CACHE=false
ENV_VARS=()
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dir=*)
            DIR="${1#*=}"
            shift
            ;;
        --grep=*)
            GREP_PATTERN="${1#*=}"
            shift
            ;;
        --tail=*)
            TAIL_N="${1#*=}"
            shift
            ;;
        --env=*)
            ENV_VARS+=("${1#*=}")
            shift
            ;;
        --env)
            shift
            ENV_VARS+=("$1")
            shift
            ;;
        -e)
            shift
            ENV_VARS+=("$1")
            shift
            ;;
        --pytest)
            FRAMEWORK="pytest"
            shift
            ;;
        --jest)
            FRAMEWORK="jest"
            shift
            ;;
        --coverage)
            NO_COVERAGE=false
            shift
            ;;
        --no-header)
            NO_HEADER=true
            shift
            ;;
        --no-cache)
            NO_CACHE=true
            shift
            ;;
        --verbose|-v)
            VERBOSE="-v"
            shift
            ;;
        --)
            shift
            EXTRA_ARGS+=("$@")
            break
            ;;
        --*)
            echo "Unknown option: $1" >&2
            exit 1
            ;;
        *)
            TEST_PATH="$1"
            shift
            ;;
    esac
done

if [ -z "$TEST_PATH" ]; then
    echo "Usage: $0 [--dir=DIR] [--env KEY=val ...] [--grep=PATTERN] [--pytest|--jest] [--tail=N] [--verbose] [--coverage] [--no-header] [--no-cache] <test-path>" >&2
    exit 1
fi

if [ -n "$DIR" ]; then
    cd "$DIR"
fi

# Export environment variables
for ev in "${ENV_VARS[@]}"; do
    export "$ev"
done

# Auto-detect framework if not specified
if [ -z "$FRAMEWORK" ]; then
    if [ -f "pyproject.toml" ] || ls pytest.ini setup.cfg tox.ini 2>/dev/null | grep -q .; then
        FRAMEWORK="pytest"
    elif [ -f "package.json" ] && grep -q '"jest"' package.json 2>/dev/null; then
        FRAMEWORK="jest"
    else
        # Check test file extension
        case "$TEST_PATH" in
            *.py) FRAMEWORK="pytest" ;;
            *.ts|*.js|*.tsx|*.jsx) FRAMEWORK="jest" ;;
            *) FRAMEWORK="pytest" ;;  # default to pytest
        esac
    fi
fi

if [ "$FRAMEWORK" = "pytest" ]; then
    CMD="python -m pytest"
    [ "$NO_COVERAGE" = true ] && CMD="$CMD --no-cov"
    [ -n "$VERBOSE" ] && CMD="$CMD $VERBOSE"
    [ -n "$GREP_PATTERN" ] && CMD="$CMD -k \"$GREP_PATTERN\""
    # -x stops on first failure, --tb=short for shorter tracebacks
    CMD="$CMD -x --tb=short"
elif [ "$FRAMEWORK" = "jest" ]; then
    CMD="npx jest --no-coverage"
    [ "$NO_CACHE" = true ] && CMD="$CMD --no-cache"
    [ -n "$VERBOSE" ] && CMD="$CMD --verbose"
    [ -n "$GREP_PATTERN" ] && CMD="$CMD --grep=\"$GREP_PATTERN\""
fi

if [ ${#EXTRA_ARGS[@]} -gt 0 ]; then
    CMD="$CMD ${EXTRA_ARGS[*]}"
fi

CMD="$CMD $TEST_PATH"

if [ "$NO_HEADER" = false ]; then
    echo "[run-tests] Framework: $FRAMEWORK" >&2
    [ -n "$DIR" ] && echo "[run-tests] Dir: $DIR" >&2
    [ -n "$GREP_PATTERN" ] && echo "[run-tests] Filter: $GREP_PATTERN" >&2
fi

if [ -n "$TAIL_N" ]; then
    eval "$CMD" 2>&1 | tail -n "$TAIL_N"
else
    eval "$CMD" 2>&1
fi
