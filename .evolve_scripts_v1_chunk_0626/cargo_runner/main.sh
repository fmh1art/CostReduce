#!/usr/bin/env bash
set -euo pipefail

# cargo_runner - Run Cargo commands with automatic output limiting
# Usage: main.sh <project_root> <action> [options]
#
# Actions:
#   build     - Compile the project
#   test      - Run tests
#   check     - Fast compilation check
#   clippy    - Run clippy linter
#   run       - Execute a binary/example
#
# Options:
#   --errors-only       - Show only error lines (grep for "error" lines)
#   --package|-p <name> - Workspace package to target
#   --release           - Build in release mode
#   --tail|-t <N>       - Show only last N lines of output
#   --filter|-f <pat>   - Test name filter (for test action)
#
# Examples:
#   main.sh /app build --package boa_engine --tail 30
#   main.sh /app build --package boa_engine --errors-only
#   main.sh /app test --filter cancellation --tail 50
#   main.sh /app check --package boa_engine
#   main.sh /app check --stash    # Stash changes, run check, pop stash

PROJECT_ROOT="${1:-}"
ACTION="${2:-}"
shift 2 2>/dev/null || shift $#

if [ -z "$PROJECT_ROOT" ] || [ -z "$ACTION" ]; then
    echo "Usage: main.sh <project_root> <action> [options]"
    echo ""
    echo "Actions: build, test, check, clippy, run"
    echo ""
    echo "Options:"
    echo "  --errors-only             Show only error lines (grep for error)"
    echo "  --package|-p <name>       Workspace package to target"
    echo "  --release                 Build in release mode"
    echo "  --tail|-t <N>             Show only last N lines of output"
    echo "  --filter|-f <pattern>     Test name filter (for test action)"
    echo "  --features <list>         Comma-separated features"
    echo "  --target <triple>         Build target triple"
    echo "  --all-features            Enable all features"
    echo "  --no-default-features     Disable default features"
    echo "  --manifest-path <path>    Path to specific Cargo.toml"
    echo "  --args <extra>            Extra cargo arguments"
    echo "  --stash                   Stash working changes, run command, then pop stash"
    exit 1
fi

if [ ! -d "$PROJECT_ROOT" ]; then
    echo "Error: Directory '$PROJECT_ROOT' does not exist"
    exit 1
fi

# Parse options
PACKAGE=""
RELEASE=false
TAIL_LINES=""
FILTER=""
FEATURES=""
TARGET=""
ALL_FEATURES=false
NO_DEFAULT_FEATURES=false
MANIFEST_PATH=""
EXTRA_ARGS=""
STASH=false
ERRORS_ONLY=false

while [ $# -gt 0 ]; do
    case "$1" in
        --package|-p)
            PACKAGE="$2"
            shift 2
            ;;
        --release)
            RELEASE=true
            shift
            ;;
        --tail|-t)
            TAIL_LINES="$2"
            shift 2
            ;;
        --filter|-f)
            FILTER="$2"
            shift 2
            ;;
        --features)
            FEATURES="$2"
            shift 2
            ;;
        --target)
            TARGET="$2"
            shift 2
            ;;
        --all-features)
            ALL_FEATURES=true
            shift
            ;;
        --no-default-features)
            NO_DEFAULT_FEATURES=true
            shift
            ;;
        --manifest-path)
            MANIFEST_PATH="$2"
            shift 2
            ;;
        --args)
            EXTRA_ARGS="$2"
            shift 2
            ;;
        --stash)
            STASH=true
            shift
            ;;
        --errors-only)
            ERRORS_ONLY=true
            shift
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

cd "$PROJECT_ROOT" || exit 1

# If --stash is used, stash working changes first
STASHED=false
if [ "$STASH" = true ]; then
    if git diff --quiet 2>/dev/null && git diff --cached --quiet 2>/dev/null; then
        echo "No uncommitted changes to stash."
    else
        echo "Stashing working changes..."
        git stash push -m "cargo_runner auto-stash" 2>&1
        STASHED=true
    fi
fi

# Set default tail if not specified based on action
if [ -z "$TAIL_LINES" ]; then
    case "$ACTION" in
        build|check|clippy)
            TAIL_LINES=30
            ;;
        test|run)
            TAIL_LINES=0  # unlimited for test/run
            ;;
        *)
            TAIL_LINES=30
            ;;
    esac
fi

# Build the cargo command
CARGO_CMD=(cargo)

case "$ACTION" in
    build) CARGO_CMD+=(build) ;;
    test)  CARGO_CMD+=(test) ;;
    check) CARGO_CMD+=(check) ;;
    clippy) CARGO_CMD+=(clippy) ;;
    run)   CARGO_CMD+=(run) ;;
    *)
        echo "Error: Unknown action '$ACTION'. Valid: build, test, check, clippy, run"
        exit 1
        ;;
esac

# Add package filter
if [ -n "$PACKAGE" ]; then
    CARGO_CMD+=(-p "$PACKAGE")
fi

# Add release flag
if [ "$RELEASE" = true ]; then
    CARGO_CMD+=(--release)
fi

# Add features
if [ -n "$FEATURES" ]; then
    CARGO_CMD+=(--features "$FEATURES")
fi

# Add all-features
if [ "$ALL_FEATURES" = true ]; then
    CARGO_CMD+=(--all-features)
fi

# Add no-default-features
if [ "$NO_DEFAULT_FEATURES" = true ]; then
    CARGO_CMD+=(--no-default-features)
fi

# Add target
if [ -n "$TARGET" ]; then
    CARGO_CMD+=(--target "$TARGET")
fi

# Add manifest-path
if [ -n "$MANIFEST_PATH" ]; then
    CARGO_CMD+=(--manifest-path "$MANIFEST_PATH")
fi

# Add test filter
if [ "$ACTION" = "test" ] && [ -n "$FILTER" ]; then
    CARGO_CMD+=("$FILTER")
fi

# Add extra args
if [ -n "$EXTRA_ARGS" ]; then
    # shellcheck disable=SC2206
    CARGO_CMD+=($EXTRA_ARGS)
fi

# Run the cargo command
echo "Running: ${CARGO_CMD[*]} 2>&1"
echo ""

EXIT_CODE=0
OUTPUT=$("${CARGO_CMD[@]}" 2>&1) || EXIT_CODE=$?

if [ "$ERRORS_ONLY" = true ]; then
    # Filter to show only lines containing "error" (common Rust error markers)
    echo "$OUTPUT" | grep -i -E "^error|^\s+error|error\[|error:" | head -20
    echo ""
    echo "(showing only error lines; exit status: $EXIT_CODE)"
elif [ "$TAIL_LINES" -gt 0 ] 2>/dev/null; then
    echo "$OUTPUT" | tail -n "$TAIL_LINES"
    echo ""
    echo "(showing last $TAIL_LINES lines; exit status: $EXIT_CODE)"
else
    echo "$OUTPUT"
fi

# Pop stash if we stashed earlier
if [ "$STASHED" = true ]; then
    echo ""
    echo "Restoring stashed changes..."
    git stash pop 2>&1
fi

exit $EXIT_CODE
