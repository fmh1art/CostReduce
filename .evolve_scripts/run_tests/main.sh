#!/bin/bash
# run_tests - Run tests for a project efficiently in one step
# Usage: run_tests [options] <test_path>
#        run_tests --go <package_path>
#        run_tests --vitest <test_file_or_dir>
#        run_tests --jest <test_file_or_dir>
#        run_tests --pytest <test_file_or_dir>
#        run_tests --all
#        run_tests --env="KEY=value" --pytest <test_file_or_dir>
#        run_tests --env="ENV1=a" --env="ENV2=b" --go ./pkg/...
#        run_tests --go --tags="kqueue,dev" ./lib/executor/
#        run_tests --go --timeout=90s --verbose ./pkg/...
#
# Runs tests for the specified language/test framework.
# Auto-detects test framework from file extensions if not specified.
# Supports custom environment variables via --env for frameworks like Django
# that require specific settings modules or configuration.
# Filters output to show relevant results (pass/fail summary).
# Saves steps by replacing multiple test invocation attempts.
#
# Examples:
#   run_tests lib/modules/manager/circleci/          # Auto-detect: vitest
#   run_tests lib/modules/manager/circleci/ --vitest # Force vitest
#   run_tests -e MYAPP_SETTINGS=production --pytest tests/
#   run_tests --env="PIP_BREAK_SYSTEM_PACKAGES=1" --pytest tests/
#   run_tests ./pkg/tsdb/elasticsearch/client/ --go  # Go tests
#   run_tests tests/test_api.py --pytest             # Python tests
#   run_tests --go ./pkg/registry/apis/iam/...       # Go tests with ... pattern
#   run_tests --vitest --grep="circleci" lib/        # Run specific test pattern
#   run_tests --go --count=1 ./pkg/...               # Go tests with count=1
#   run_tests --go --tags="kqueue,dev" ./lib/executor/  # Go tests with build tags
#   run_tests --go --timeout=120s ./pkg/...          # Go tests with timeout
#   run_tests --go --verbose ./pkg/...               # Go tests with verbose output

if [ $# -eq 0 ] || [ "$1" = "--help" ]; then
    echo "Usage: run_tests [options] <test_path>"
    echo ""
    echo "Options:"
    echo "  --go             Run Go tests (go test)"
    echo "  --vitest         Run vitest tests (npx vitest run)"
    echo "  --jest           Run jest tests (npx jest)"
    echo "  --pytest         Run Python tests (python -m pytest)"
    echo "  --all            Run all available test commands"
    echo "  --count=N        Set test count (for Go tests)"
    echo "  --grep=PATTERN   Filter tests by name pattern"
    echo "  --no-coverage    Disable coverage"
    echo "  --env=KEY=val    Set environment variable (can repeat)"
    echo "  -e KEY=val       Short form of --env"
    echo "  --tags=TAGS      Go build tags (e.g., kqueue,dev,integration)"
    echo "  --timeout=DUR    Test timeout (e.g., 90s, 5m, default: none)"
    echo "  --verbose, -v    Verbose output (show full test output)"
    echo ""
    echo "Examples:"
    echo "  run_tests lib/modules/manager/circleci/"
    echo "  run_tests lib/modules/manager/circleci/ --vitest"
    echo "  run_tests ./pkg/tsdb/elasticsearch/client/ --go"
    echo "  run_tests tests/test_api.py --pytest"
    echo "  run_tests --env="DATABASE_URL=postgres://user:pass@localhost/db" --pytest tests/"
    echo "  run_tests -e PIP_BREAK_SYSTEM_PACKAGES=1 --pytest tests/"
    echo "  run_tests --go ./pkg/registry/apis/iam/..."
    echo "  run_tests --go --tags=\"kqueue,dev\" ./lib/executor/"
    echo "  run_tests --go --timeout=120s --verbose ./pkg/..."
    exit 0
fi

FRAMEWORK=""
TEST_PATH=""
COUNT="1"
GREP=""
NO_COVERAGE=false
ALL=false
ENV_VARS=()
GO_TAGS=""
GO_TIMEOUT=""
GO_VERBOSE=false

# Parse arguments
while [ $# -gt 0 ]; do
    case "$1" in
        --go) FRAMEWORK="go" ;;
        --vitest) FRAMEWORK="vitest" ;;
        --jest) FRAMEWORK="jest" ;;
        --pytest) FRAMEWORK="pytest" ;;
        --all) ALL=true ;;
        --count=*) COUNT="${1#*=}" ;;
        --grep=*) GREP="${1#*=}" ;;
        --no-coverage) NO_COVERAGE=true ;;
        --env=*) ENV_VARS+=("${1#*=}") ;;
        --tags=*) GO_TAGS="${1#*=}" ;;
        --timeout=*) GO_TIMEOUT="${1#*=}" ;;
        --verbose|-v) GO_VERBOSE=true ;;
        -e)
            shift
            if [ $# -gt 0 ]; then
                ENV_VARS+=("$1")
            fi
            ;;
        --help) "$0" --help; exit 0 ;;
        -*)
            echo "Unknown option: $1"
            exit 1
            ;;
        *)
            if [ -z "$TEST_PATH" ]; then
                TEST_PATH="$1"
            fi
            ;;
    esac
    shift
done

# Build environment variable prefix
ENV_PREFIX=""
if [ ${#ENV_VARS[@]} -gt 0 ]; then
    for env_var in "${ENV_VARS[@]}"; do
        ENV_PREFIX="$ENV_PREFIX $env_var"
    done
    ENV_PREFIX="env ${ENV_PREFIX# }"
fi

# Find repo root for proper cd
REPO_ROOT=""
if command -v git >/dev/null 2>&1; then
    REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || echo "")
fi

if [ -n "$REPO_ROOT" ]; then
    cd "$REPO_ROOT" || true
fi

# Auto-detect framework if not specified
if [ -z "$FRAMEWORK" ] && [ "$ALL" = false ]; then
    if [ -d "$TEST_PATH" ] || [ -f "$TEST_PATH" ]; then
        # Check for Go test files
        if ls "$TEST_PATH"/*_test.go 2>/dev/null | head -1 >/dev/null 2>&1; then
            FRAMEWORK="go"
        elif [ -f "$TEST_PATH" ] && echo "$TEST_PATH" | grep -q '_test\.go$'; then
            FRAMEWORK="go"
        # Check for vitest config
        elif [ -f "vitest.config.ts" ] || [ -f "vitest.config.js" ] || [ -f "vitest.config.mjs" ]; then
            FRAMEWORK="vitest"
        # Check for jest config
        elif [ -f "jest.config.ts" ] || [ -f "jest.config.js" ] || grep -q '"jest"' package.json 2>/dev/null; then
            # Check if jest is actually vitest (renovate pattern)
            if grep -q '"jest".*vitest' package.json 2>/dev/null; then
                FRAMEWORK="vitest"
            else
                FRAMEWORK="jest"
            fi
        # Check for pytest config
        elif [ -f "pytest.ini" ] || [ -f "pyproject.toml" ] && grep -q "pytest" pyproject.toml 2>/dev/null; then
            FRAMEWORK="pytest"
        else
            # Default: try to guess from the test file extension
            if echo "$TEST_PATH" | grep -q '\.spec\.\|\.test\.'; then
                if echo "$TEST_PATH" | grep -q '\.ts\|\.tsx\|\.js\|\.jsx'; then
                    # Check if it's vitest or jest
                    if command -v npx >/dev/null 2>&1; then
                        if npx vitest --version >/dev/null 2>&1; then
                            FRAMEWORK="vitest"
                        else
                            FRAMEWORK="jest"
                        fi
                    else
                        FRAMEWORK="jest"
                    fi
                elif echo "$TEST_PATH" | grep -q '\.py$'; then
                    FRAMEWORK="pytest"
                fi
            fi
        fi
    fi
    
    if [ -z "$FRAMEWORK" ]; then
        echo "Error: Cannot auto-detect test framework for $TEST_PATH"
        echo "Please specify with --go, --vitest, --jest, or --pytest"
        exit 1
    fi
fi

if [ "$ALL" = true ]; then
    echo "=== Running all available tests ==="
    echo ""
fi

OVERALL_SUCCESS=true

run_go_tests() {
    local path="$1"
    local count="$2"
    local grep_filter="$3"
    local env_prefix="$4"
    local go_tags="$5"
    local go_timeout="$6"
    local go_verbose="$7"
    
    echo "=== go test $path ==="
    CMD="$env_prefix go test ./$path -count=$count"
    [ -n "$grep_filter" ] && CMD="$CMD -run \"$grep_filter\""
    [ -n "$go_tags" ] && CMD="$CMD -tags \"$go_tags\""
    [ -n "$go_timeout" ] && CMD="$CMD -timeout $go_timeout"
    [ "$go_verbose" = true ] && CMD="$CMD -v"
    CMD="$CMD 2>&1"
    
    OUTPUT=$(eval "$CMD")
    RET=$?
    
    if [ "$go_verbose" = true ]; then
        # Show full output when verbose
        echo "$OUTPUT"
    elif [ $RET -eq 0 ]; then
        echo "$OUTPUT" | grep -E "^(ok|FAIL|---)" || echo "$OUTPUT" | tail -5
        echo "GO TEST OK"
    else
        echo "$OUTPUT" | grep -E "^(ok|FAIL|---)" || echo "$OUTPUT" | tail -20
        echo "GO TEST FAILED (exit code $RET)"
        OVERALL_SUCCESS=false
    fi
    echo ""
}

run_vitest() {
    local path="$1"
    local grep_filter="$2"
    local no_coverage="$3"
    local env_prefix="$4"
    
    echo "=== vitest run $path ==="
    CMD="$env_prefix npx vitest run"
    [ -n "$path" ] && CMD="$CMD $path"
    [ -n "$grep_filter" ] && CMD="$CMD -t \"$grep_filter\""
    [ "$no_coverage" = true ] && CMD="$CMD --coverage.enabled=false"
    CMD="$CMD 2>&1"
    
    OUTPUT=$(eval "$CMD")
    RET=$?
    
    # Show a compact summary
    echo "$OUTPUT" | grep -E "(Test Files|Tests|✓|×|FAIL|PASS)" | head -30
    echo ""
    if [ $RET -eq 0 ]; then
        echo "VITEST OK"
    else
        echo "VITEST FAILED (exit code $RET)"
        OVERALL_SUCCESS=false
    fi
    echo ""
}

run_jest() {
    local path="$1"
    local grep_filter="$2"
    local env_prefix="$3"
    
    echo "=== jest $path ==="
    CMD="$env_prefix npx jest --no-coverage"
    [ -n "$path" ] && CMD="$CMD $path"
    [ -n "$grep_filter" ] && CMD="$CMD --testNamePattern=\"$grep_filter\""
    CMD="$CMD 2>&1"
    
    OUTPUT=$(eval "$CMD")
    RET=$?
    
    echo "$OUTPUT" | grep -E "(Tests|Suites|✓|✕|FAIL)" | head -20
    echo ""
    if [ $RET -eq 0 ]; then
        echo "JEST OK"
    else
        echo "JEST FAILED (exit code $RET)"
        OVERALL_SUCCESS=false
    fi
    echo ""
}

run_pytest() {
    local path="$1"
    local grep_filter="$2"
    local env_prefix="$3"
    
    echo "=== pytest $path ==="
    CMD="$env_prefix python -m pytest"
    [ -n "$path" ] && CMD="$CMD $path"
    [ -n "$grep_filter" ] && CMD="$CMD -k \"$grep_filter\""
    CMD="$CMD -v 2>&1"
    
    OUTPUT=$(eval "$CMD")
    RET=$?
    
    echo "$OUTPUT" | grep -E "(passed|failed|error|FAILED|PASSED|ERROR)" | tail -20
    echo ""
    if [ $RET -eq 0 ]; then
        echo "PYTEST OK"
    else
        echo "PYTEST FAILED (exit code $RET)"
        OVERALL_SUCCESS=false
    fi
    echo ""
}

if [ "$ALL" = true ] || [ "$FRAMEWORK" = "go" ]; then
    run_go_tests "$TEST_PATH" "$COUNT" "$GREP" "$ENV_PREFIX" "$GO_TAGS" "$GO_TIMEOUT" "$GO_VERBOSE"
fi

if [ "$ALL" = true ] || [ "$FRAMEWORK" = "vitest" ]; then
    run_vitest "$TEST_PATH" "$GREP" "$NO_COVERAGE" "$ENV_PREFIX"
fi

if [ "$ALL" = true ] || [ "$FRAMEWORK" = "jest" ]; then
    run_jest "$TEST_PATH" "$GREP" "$ENV_PREFIX"
fi

if [ "$ALL" = true ] || [ "$FRAMEWORK" = "pytest" ]; then
    run_pytest "$TEST_PATH" "$GREP" "$ENV_PREFIX"
fi

if [ "$OVERALL_SUCCESS" = true ]; then
    echo "All tests passed."
    exit 0
else
    echo "Some tests FAILED. See above for details."
    exit 1
fi
