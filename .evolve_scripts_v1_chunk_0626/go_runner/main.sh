#!/bin/bash
# go_runner - Run Go commands (build, test, run, vet, fmt, fmtcheck, fmtdiff, syncheck, gofmt, mod) in a project directory
# Usage: go_runner <project_root> <action> [target] [options]
#
# Actions:
#   build [pkg]       - Build a Go package (default: .)
#   test [pkg]        - Run Go tests (default: ./...)
#   run [file/pkg]    - Run a Go file or package
#   vet [pkg]         - Run go vet (default: ./...)
#   fmt [pkg]         - Run go fmt (format in place, default: ./...)
#   fmtcheck [file]   - Run gofmt -e on a specific file (display formatted output, no modifications)
#   fmtdiff [file]    - Run gofmt -d on a specific file (show diff without modifying)
#   syncheck [file]   - Check Go syntax with gofmt -e silently (exit code only, no formatted output)
#   gofmt [files...]  - Format Go files with gofmt -w (one or more files, space-separated)
#   mod [args]        - Run go mod commands (tidy, download, etc.)
#
# Options:
#   --timeout N       Timeout in seconds for the command wrapper (default: 60)
#   --go-timeout D    Go test/duration timeout passed as -timeout flag (e.g., 60s, 5m)
#   --rewrite         Pass -rewrite flag to go test (for data-driven test frameworks like datadriven)
#   --verbose, -v     Verbose output (-v)
#   --count N         Run tests N times (go test -count=N)
#   --run PATTERN     Run only tests matching PATTERN (go test -run=PATTERN)
#   --race            Enable race detector
#   --clean-cache     Run 'go clean -testcache' before test
#   --stash           Stash working changes, run command, then pop stash
#   --head N          Show only first N lines of output
#   --tail N          Show only last N lines of output

PROJECT_ROOT="$1"
ACTION="$2"
TARGET="$3"
shift 3 2>/dev/null || shift $#

TIMEOUT=60
GO_TIMEOUT=""
REWRITE=false
VERBOSE=""
TEST_COUNT=0
TEST_RUN=""
CLEAN_CACHE=false
STASH=false
HEAD_LINES=0
TAIL_LINES=0
EXTRA_ARGS=""

while [ $# -gt 0 ]; do
    case "$1" in
        --timeout)
            TIMEOUT="$2"
            shift 2
            ;;
        --go-timeout)
            GO_TIMEOUT="$2"
            shift 2
            ;;
        --rewrite)
            REWRITE=true
            shift
            ;;
        --verbose|-v)
            VERBOSE="-v"
            shift
            ;;
        --count)
            TEST_COUNT="$2"
            shift 2
            ;;
        --run)
            TEST_RUN="$2"
            shift 2
            ;;
        --race)
            EXTRA_ARGS="$EXTRA_ARGS -race"
            shift
            ;;
        --clean-cache)
            CLEAN_CACHE=true
            shift
            ;;
        --stash)
            STASH=true
            shift
            ;;
        --head)
            HEAD_LINES="$2"
            shift 2
            ;;
        --tail)
            TAIL_LINES="$2"
            shift 2
            ;;
        *)
            EXTRA_ARGS="$EXTRA_ARGS $1"
            shift
            ;;
    esac
done

if [ -z "$PROJECT_ROOT" ] || [ -z "$ACTION" ]; then
    echo "Usage: go_runner <project_root> <action> [target] [options]"
    echo ""
    echo "Actions:"
    echo "  build [pkg]          - Build a Go package (default: .)"
    echo "  test [pkg]           - Run Go tests (default: ./...)"
    echo "  run [file/pkg]       - Run a Go file or package"
    echo "  vet [pkg]            - Run go vet (default: ./...)"
    echo "  fmt [pkg]            - Run go fmt (format in place, default: ./...)"
    echo "  fmtcheck [file]      - Check formatting of a Go file (gofmt -e, display only)"
    echo "  fmtdiff [file]       - Show formatting diff for a Go file (gofmt -d)"
    echo "  syncheck [file]      - Check Go syntax with gofmt -e, silent mode (exit code only)"
    echo "  gofmt [file...]        - Format Go files with gofmt -w (one or more files)"
    echo "  mod [args]           - Run go mod commands (tidy, download, etc.)"
    echo ""
    echo "Options:"
    echo "  --timeout N          Timeout in seconds for the command wrapper (default: 60)"
    echo "  --go-timeout D       Go test/duration timeout passed as -timeout flag (e.g., 60s, 5m)"
    echo "  --rewrite            Pass -rewrite flag to go test (for data-driven test frameworks)"
    echo "  --verbose, -v        Verbose output (-v)"
    echo "  --count N            Run tests N times (go test -count=N)"
    echo "  --run PATTERN        Run only tests matching PATTERN (go test -run=PATTERN)"
    echo "  --race               Enable race detector"
    echo "  --clean-cache        Run 'go clean -testcache' before test"
    echo "  --stash              Stash changes, run command, pop stash"
    echo "  --head N             Show only first N lines of output"
    echo "  --tail N             Show only last N lines of output"
    exit 1
fi

if [ ! -d "$PROJECT_ROOT" ]; then
    echo "Error: Directory '$PROJECT_ROOT' does not exist"
    exit 1
fi

cd "$PROJECT_ROOT" || exit 1

# Check if go.mod exists
if [ ! -f "go.mod" ] && [ "$ACTION" != "mod" ]; then
    echo "Warning: No go.mod found in $PROJECT_ROOT"
fi

# If --stash is used, stash working changes first
STASHED=false
if [ "$STASH" = true ]; then
    if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        if ! git diff --quiet 2>/dev/null || ! git diff --cached --quiet 2>/dev/null; then
            echo "Stashing working changes..."
            git stash push -m "go_runner auto-stash" 2>&1
            STASHED=true
        else
            echo "No uncommitted changes to stash."
        fi
    else
        echo "Warning: Not a git repository, --stash ignored."
    fi
fi

case "$ACTION" in
    build)
        CMD="go build"
        if [ -n "$VERBOSE" ]; then
            CMD="$CMD $VERBOSE"
        fi
        PKG="${TARGET:-.}"
        CMD="$CMD $EXTRA_ARGS $PKG"
        ;;
    test)
        # Run clean test cache if requested
        if [ "$CLEAN_CACHE" = true ]; then
            echo "Cleaning test cache..."
            go clean -testcache 2>&1
        fi
        
        CMD="go test"
        if [ -n "$VERBOSE" ]; then
            CMD="$CMD $VERBOSE"
        fi
        if [ -n "$TEST_RUN" ]; then
            CMD="$CMD -run=\"$TEST_RUN\""
        fi
        # Add --rewrite flag for data-driven test frameworks
        if [ "$REWRITE" = true ]; then
            CMD="$CMD -rewrite"
        fi
        # Add Go native timeout
        if [ -n "$GO_TIMEOUT" ]; then
            CMD="$CMD -timeout=$GO_TIMEOUT"
        fi
        PKG="${TARGET:-./...}"
        CMD="$CMD $EXTRA_ARGS"
        if [ "$TEST_COUNT" -gt 0 ]; then
            CMD="$CMD -count=$TEST_COUNT"
        fi
        CMD="$CMD $PKG"
        ;;
    run)
        CMD="go run"
        TARGET="${TARGET:-.}"
        CMD="$CMD $EXTRA_ARGS $TARGET"
        ;;
    vet)
        CMD="go vet"
        PKG="${TARGET:-./...}"
        CMD="$CMD $EXTRA_ARGS $PKG"
        ;;
    fmt)
        CMD="go fmt"
        PKG="${TARGET:-./...}"
        CMD="$CMD $EXTRA_ARGS $PKG"
        ;;
    fmtcheck)
        # Run gofmt -e on specific file(s) to check formatting without modifying
        FILE="${TARGET:-.}"
        if [ ! -f "$FILE" ]; then
            echo "Error: File '$FILE' not found"
            if [ "$STASHED" = true ]; then
                git stash pop 2>&1
            fi
            exit 1
        fi
        CMD="gofmt -e '$FILE'"
        echo "--- gofmt -e $FILE ---"
        ;;
    fmtdiff)
        # Run gofmt -d on specific file(s) to show formatting diff without modifying
        FILE="${TARGET:-.}"
        if [ ! -f "$FILE" ]; then
            echo "Error: File '$FILE' not found"
            if [ "$STASHED" = true ]; then
                git stash pop 2>&1
            fi
            exit 1
        fi
        CMD="gofmt -d '$FILE'"
        echo "--- gofmt -d $FILE ---"
        ;;
    syncheck)
        # Check Go syntax with gofmt -e silently
        FILE="${TARGET:-.}"
        if [ ! -f "$FILE" ]; then
            echo "Error: File '$FILE' not found"
            if [ "$STASHED" = true ]; then
                git stash pop 2>/dev/null || true
            fi
            exit 1
        fi
        if gofmt -e "$FILE" > /dev/null 2>&1; then
            echo "Syntax OK: $FILE"
            RESULT=0
        else
            echo "Syntax errors found: $FILE"
            RESULT=1
        fi
        # Pop stash if we stashed earlier
        if [ "$STASHED" = true ]; then
            echo ""
            echo "Restoring stashed changes..."
            git stash pop 2>/dev/null || true
        fi
        exit $RESULT
        ;;
    gofmt)
        # Format specific Go file(s) with gofmt -w
        # Supports one or more files as positional arguments
        if [ -z "$TARGET" ]; then
            echo "Error: gofmt requires at least one file path"
            exit 1
        fi
        # TARGET can contain multiple files separated by spaces or be passed as multiple args
        # Already handled by shell argument parsing
        FILES="$TARGET"
        for extra in $EXTRA_ARGS; do
            FILES="$FILES $extra"
        done
        CMD="gofmt -w $FILES"
        echo "Formatting: $FILES"
        ;;

    mod)
        CMD="go mod"
        MOD_ARGS="${TARGET:-tidy}"
        CMD="$CMD $MOD_ARGS $EXTRA_ARGS"
        ;;
    *)
        echo "Unknown action: $ACTION"
        echo "Valid actions: build, test, run, vet, fmt, fmtcheck, fmtdiff, syncheck, gofmt, mod"
        # Pop stash on error before exiting
        if [ "$STASHED" = true ]; then
            git stash pop 2>&1
        fi
        exit 1
        ;;
esac

if [ "$ACTION" != "fmtcheck" ] && [ "$ACTION" != "fmtdiff" ]; then
    echo "Running: timeout $TIMEOUT $CMD 2>&1"
    echo ""
fi

# Run with output limiting support
if [ "$TAIL_LINES" -gt 0 ]; then
    timeout "$TIMEOUT" bash -c "$CMD 2>&1" | tail -n "$TAIL_LINES"
elif [ "$HEAD_LINES" -gt 0 ]; then
    timeout "$TIMEOUT" bash -c "$CMD 2>&1" | head -n "$HEAD_LINES"
else
    timeout "$TIMEOUT" bash -c "$CMD 2>&1"
fi
EXIT_CODE=$?

# For tail/head mode, the exit code from pipe may not reflect the go command's exit code
# Use PIPESTATUS to get the actual exit code from the timeout command
if [ "$TAIL_LINES" -gt 0 ] || [ "$HEAD_LINES" -gt 0 ]; then
    EXIT_CODE=${PIPESTATUS[0]}
fi

if [ $EXIT_CODE -eq 124 ]; then
    echo ""
    echo "Command timed out after ${TIMEOUT}s"
elif [ $EXIT_CODE -ne 0 ]; then
    echo ""
    echo "Command failed (exit code: $EXIT_CODE)"
fi

# Pop stash if we stashed earlier
if [ "$STASHED" = true ]; then
    echo ""
    echo "Restoring stashed changes..."
    git stash pop 2>&1
fi

exit $EXIT_CODE
