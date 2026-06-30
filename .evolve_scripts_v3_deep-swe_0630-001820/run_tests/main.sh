#!/usr/bin/env bash
set -euo pipefail

# run_tests - Run tests for any language/framework. Auto-detects framework.
# Usage: run_tests [options] <test_path1> [test_path2 ...]
# Options:
#   --dir=DIR        Working directory to cd into before running tests
#   --go <pkg>       Force Go test
#   --vitest <file>  Force vitest
#   --jest <file>    Force jest
#   --pytest <file>  Force pytest
#   --testtools      Force Python testtools.run
#   --all            Run all available test commands
#   --grep=PATTERN   Filter tests by name
#   --count=N        Test repetition (Go only)
#   -e, --env=KEY=val Set env var (repeatable)
#   --no-coverage    Disable coverage
#   --tags=TAGS      Go build tags
#   --timeout=SECONDS Hard timeout via timeout command (e.g., 60)
#   --build-command=CMD  Run a build command before tests (e.g. "pnpm build")
#   -v, --verbose    Verbose output
#   --brief          Show only essential output: strip config noise, show failures + summary
#   --stash          Stash uncommitted changes before running tests and pop them after
#   --exitfirst,-x   Exit on first failure (pytest -x)
#   --head=N         Show only first N lines of output (truncates after N lines)
#   --tail=N         Show only last N lines of output

WORKDIR=""
TEST_PATHS=()
FRAMEWORK=""
GREP=""
COUNT=""
ENVS=()
NO_COV=false
TAGS=""
TIMEOUT=""
VERBOSE=""
RUN_ALL=false
BUILD_COMMAND=""
BRIEF=false
STASH=false
EXITFIRST=false
HEAD_LINES=""
TAIL_LINES=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dir=*)
            WORKDIR="${1#*=}"
            shift
            ;;
        --dir)
            WORKDIR="$2"
            shift 2
            ;;
        --go)
            FRAMEWORK="go"
            shift
            ;;
        --vitest)
            FRAMEWORK="vitest"
            shift
            ;;
        --jest)
            FRAMEWORK="jest"
            shift
            ;;
        --pytest)
            FRAMEWORK="pytest"
            shift
            ;;
        --testtools)
            FRAMEWORK="testtools"
            shift
            ;;
        --all)
            RUN_ALL=true
            shift
            ;;
        --grep=*)
            GREP="${1#*=}"
            shift
            ;;
        --grep)
            GREP="$2"
            shift 2
            ;;
        --count=*)
            COUNT="${1#*=}"
            shift
            ;;
        --count)
            COUNT="$2"
            shift 2
            ;;
        -e|--env)
            ENVS+=("$2")
            shift 2
            ;;
        --env=*)
            ENVS+=("${1#*=}")
            shift
            ;;
        --no-coverage)
            NO_COV=true
            shift
            ;;
        --tags=*)
            TAGS="${1#*=}"
            shift
            ;;
        --tags)
            TAGS="$2"
            shift 2
            ;;
        --timeout=*)
            TIMEOUT="${1#*=}"
            shift
            ;;
        --timeout)
            TIMEOUT="$2"
            shift 2
            ;;
        -v|--verbose)
            VERBOSE="-v"
            shift
            ;;
        --build-command=*)
            BUILD_COMMAND="${1#*=}"
            shift
            ;;
        --build-command)
            BUILD_COMMAND="$2"
            shift 2
            ;;
        --brief)
            BRIEF=true
            shift
            ;;

        --stash)
            STASH=true
            shift
            ;;
        --exitfirst|-x)
            EXITFIRST=true
            shift
            ;;
        --head=*)
            HEAD_LINES="${1#*=}"
            shift
            ;;
        --head)
            HEAD_LINES="$2"
            shift 2
            ;;
        --tail=*)
            TAIL_LINES="${1#*=}"
            shift
            ;;
        --tail)
            TAIL_LINES="$2"
            shift 2
            ;;
        --)
            shift
            TEST_PATHS+=("$@")
            break
            ;;
        -*)
            echo "Unknown option: $1" >&2
            exit 1
            ;;
        *)
            TEST_PATHS+=("$1")
            shift
            ;;
    esac
done

# Change to working directory if specified
if [[ -n "$WORKDIR" ]]; then
    cd "$WORKDIR" || { echo "Error: Cannot cd to $WORKDIR" >&2; exit 1; }
fi

# Set timeout prefix
TIMEOUT_PREFIX=""
if [[ -n "$TIMEOUT" ]]; then
    TIMEOUT_PREFIX="timeout $TIMEOUT"
fi

# Stash uncommitted changes if requested (do this after cd into WORKDIR, before tests)
if [[ "$STASH" == true ]]; then
    if git stash --include-untracked 2>/dev/null; then
        STASH_POP=true
    else
        STASH_POP=false
    fi
fi

# Ensure stash is popped on exit if we stashed
if [[ "$STASH" == true ]]; then
    trap 'if [[ "$STASH_POP" == true ]]; then git stash pop 2>/dev/null || true; fi' EXIT
fi


# Export environment variables
for env in "${ENVS[@]}"; do
    export "${env?}"
done

# Apply head/tail filter to output
apply_head_tail() {
    if [[ -n "$HEAD_LINES" && -n "$TAIL_LINES" ]]; then
        # If both specified, head first then tail (or vice versa? head first is usual)
        head -n "$HEAD_LINES" | tail -n "$TAIL_LINES"
    elif [[ -n "$HEAD_LINES" ]]; then
        head -n "$HEAD_LINES"
    elif [[ -n "$TAIL_LINES" ]]; then
        tail -n "$TAIL_LINES"
    else
        cat
    fi
}

# Detect package manager for npm-based projects
detect_package_manager() {
    if command -v pnpm &>/dev/null && [[ -f "pnpm-lock.yaml" ]]; then
        echo "pnpm"
    elif command -v yarn &>/dev/null && [[ -f "yarn.lock" ]]; then
        echo "yarn"
    else
        echo "npx"
    fi
}

PM="npx"
if [[ -f "package.json" ]]; then
    PM=$(detect_package_manager)
fi

# Build the npm runner prefix (pnpm vitest, yarn vitest, npx vitest, etc.)
npm_run() {
    local cmd="$1"
    shift
    if [[ "$PM" == "pnpm" ]]; then
        pnpm "$cmd" "$@"
    elif [[ "$PM" == "yarn" ]]; then
        yarn "$cmd" "$@"
    else
        npx "$cmd" "$@"
    fi
}

# Run build command before tests if specified
if [[ -n "$BUILD_COMMAND" ]]; then
    echo "=== Running build: $BUILD_COMMAND ==="
    eval "$BUILD_COMMAND" 2>&1 || { echo "Build failed" >&2; exit 1; }
fi

# Auto-detect framework if not specified
if [[ -z "$FRAMEWORK" && "$RUN_ALL" == false ]]; then
    if [[ -f "go.mod" ]]; then
        FRAMEWORK="go"
    elif [[ -f "package.json" ]]; then
        if grep -q '"vitest"' package.json 2>/dev/null; then
            FRAMEWORK="vitest"
        else
            FRAMEWORK="jest"
        fi
    elif [[ -f "pyproject.toml" || -f "setup.py" || -f "setup.cfg" ]]; then
        FRAMEWORK="pytest"
    elif [[ ${#TEST_PATHS[@]} -gt 0 ]]; then
        case "${TEST_PATHS[0]}" in
            *.) FRAMEWORK="testtools";;
            *.go) FRAMEWORK="go";;
            *.py) FRAMEWORK="pytest";;
            *) FRAMEWORK="pytest";;
        esac
    fi
fi

# If RUN_ALL, run all frameworks
run_all_tests() {
    echo "=== Running all test frameworks ==="
    if [[ -f "go.mod" ]]; then
        echo "--- Go tests ---"
        go test ./... 2>&1 || true
    fi
    if [[ -f "package.json" ]]; then
        if grep -q '"vitest"' package.json 2>/dev/null; then
            echo "--- Vitest ---"
            npm_run vitest run 2>&1 || true
        else
            echo "--- Jest ---"
            npm_run jest 2>&1 || true
        fi
    fi
    if [[ -f "pyproject.toml" || -f "setup.py" || -f "setup.cfg" ]]; then
        echo "--- Pytest ---"
        python3 -m pytest 2>&1 || true
    fi
}

if [[ "$RUN_ALL" == true ]]; then
    run_all_tests
    exit 0
fi

# Run a single testtools path
run_testtools_path() {
    local path="$1"
    if [[ "$BRIEF" == true ]]; then
        local output
        output=$($TIMEOUT_PREFIX python3 -m testtools.run "$path" 2>&1) || true
        echo "$output" | awk '
BEGIN { in_failure=0 }
/^Tests running\.\.\.$/ { print; next }
/^Unable to parse config/ { next }
/^Cannot resolve file path/ { next }
/^Cannot resolve where/ { next }
/^Skipping directory/ { next }
/^FAILED / { print; next }
/^ERROR$/ { print; in_failure=1; next }
/^Traceback \(most recent call last\)/ { in_failure=1; print; next }
/^  File / { if (in_failure) print; next }
/^    / { if (in_failure) print; next }
/^[A-Za-z_][A-Za-z0-9_.]*: / { if (in_failure) print; next }
/^Ran [0-9]+ tests in/ { print; in_failure=0; next }
/^OK$/ { print; next }
/^FAILED \(/ { print; next }
' || true
    else
        $TIMEOUT_PREFIX python3 -m testtools.run "$path" 2>&1 || true
    fi
}

# Run a single pytest path
run_pytest_path() {
    local path="$1"
    PY_ARGS=()
    [[ -n "$VERBOSE" ]] && PY_ARGS+=("$VERBOSE")
    [[ -n "$GREP" ]] && PY_ARGS+=("-k" "$GREP")
    [[ -n "$TIMEOUT" ]] && PY_ARGS+=("--timeout=$TIMEOUT")
    [[ "$EXITFIRST" == true ]] && PY_ARGS+=("-x")
    # When --brief, skip --cov by default (coverage output adds noise)
    if [[ "$BRIEF" != true ]]; then
        [[ "$NO_COV" == true ]] || PY_ARGS+=("--cov")
    fi

    if [[ "$BRIEF" == true ]]; then
        local output
        output=$($TIMEOUT_PREFIX python3 -m pytest "${PY_ARGS[@]}" "$path" 2>&1) || true
        echo "$output" | awk '
BEGIN { in_failure=0; in_captured=0 }
/^=+ .* test session starts =+/ { next }
/^platform / { next }
/^cachedir/ { next }
/^hypothesis / { next }
/^benchmark:/ { next }
/^rootdir/ { next }
/^configfile/ { next }
/^plugins:/ { next }
/^timeout:/ { next }
/^timeout method/ { next }
/^timeout func/ { next }
/^Werkzeug/ { next }
# Strip captured stdout/stderr sections (noise from test fixtures)
/^---.*Captured (stdout|stderr)/ { in_captured=1; next }
/^---*$|^---* / { if (in_captured) { in_captured=0; next } }
# Strip INFO/ERROR log lines from test output
/^\[   INFO \]/ { next }
/^\[  ERROR \]/ { next }
/^\[config\]/ { next }
/^Output: b''/ { next }
/^b''/ { next }
/^collecting/ { print; next }
/^tests?\// {
    if (/ FAILED / || / ERROR / || / FAILED$/ || / ERROR$/ || /FAILED/) {
        print
    } else if (/ PASSED / || / PASSED$/) {
        next
    } else {
        print
    }
    next
}
/^--- coverage:/ { next }
/^TOTAL/ { next }
/^─+ / { next }
/^─+ / { next }
/______+.*_+/ {
    in_failure=1
    print
    next
}
/^>       / { if (in_failure) print; next }
/^E       / { if (in_failure) print; next }
/AssertionError/ { if (in_failure) print; next }
/^FAILED / { if (in_failure) { in_failure=0; print; next } }
/^=+ short test summary/ { print; next }
/^=+ .* in / { print; next }
{ if (in_failure) print }
' | sed '/^$/d' || true
    else
        $TIMEOUT_PREFIX python3 -m pytest "${PY_ARGS[@]}" "$path" 2>&1 || true
    fi
}

case "$FRAMEWORK" in
    go)
        GO_ARGS=()
        [[ -n "$VERBOSE" ]] && GO_ARGS+=("$VERBOSE")
        [[ -n "$COUNT" ]] && GO_ARGS+=("-count=$COUNT")
        [[ -n "$TAGS" ]] && GO_ARGS+=("-tags=$TAGS")
        [[ -n "$TIMEOUT" ]] && GO_ARGS+=("-timeout=${TIMEOUT}s")
        [[ -n "$GREP" ]] && GO_ARGS+=("-run=$GREP")
        # When --brief, skip -cover by default (coverage output adds noise)
        if [[ "$BRIEF" != true ]]; then
            [[ "$NO_COV" == true ]] || GO_ARGS+=("-cover")
        fi
        for path in "${TEST_PATHS[@]}"; do
            if [[ ${#TEST_PATHS[@]} -gt 1 ]]; then
                echo "=== go test $path ==="
            fi
            if [[ "$BRIEF" == true ]]; then
                local output
                output=$(go test "${GO_ARGS[@]}" "./$path" 2>&1) || true
                echo "$output" | awk '
/^ok  / { print; next }
/^FAIL / { print; next }
/^--- FAIL:/ { print; next }
/^FAIL$|^exit status/ { print; next }
/^[[:alnum:]_.]+\.go:[0-9]+:/ { print; next }
' | sed '/^$/d' | apply_head_tail || true
            else
                go test "${GO_ARGS[@]}" "./$path" 2>&1 | apply_head_tail || true
            fi
        done
        ;;

    pytest)
        for path in "${TEST_PATHS[@]}"; do
            if [[ ${#TEST_PATHS[@]} -gt 1 ]]; then
                echo "=== pytest $path ==="
            fi
            run_pytest_path "$path" | apply_head_tail
        done
        ;;

    testtools)
        for path in "${TEST_PATHS[@]}"; do
            if [[ ${#TEST_PATHS[@]} -gt 1 ]]; then
                echo "=== testtools $path ==="
            fi
            run_testtools_path "$path" | apply_head_tail
        done
        ;;

    vitest)
        VITEST_ARGS=("run")
        [[ -n "$GREP" ]] && VITEST_ARGS+=("-t" "$GREP")
        [[ -n "$TIMEOUT" ]] && VITEST_ARGS+=("--testTimeout=${TIMEOUT}000")
        [[ -n "$VERBOSE" ]] && VITEST_ARGS+=("--reporter=verbose")
        for path in "${TEST_PATHS[@]}"; do
            if [[ ${#TEST_PATHS[@]} -gt 1 ]]; then
                echo "=== vitest $path ==="
            fi
            npm_run vitest "${VITEST_ARGS[@]}" "$path" 2>&1 | apply_head_tail || true
        done
        ;;

    jest)
        JEST_ARGS=()
        [[ -n "$GREP" ]] && JEST_ARGS+=("-t" "$GREP")
        [[ -n "$VERBOSE" ]] && JEST_ARGS+=("--verbose")
        for path in "${TEST_PATHS[@]}"; do
            if [[ ${#TEST_PATHS[@]} -gt 1 ]]; then
                echo "=== jest $path ==="
            fi
            npm_run jest "${JEST_ARGS[@]}" "$path" 2>&1 | apply_head_tail || true
        done
        ;;

    *)
        echo "Error: Could not auto-detect test framework. Use --go, --pytest, --vitest, --jest, or --testtools." >&2
        exit 1
        ;;
esac
