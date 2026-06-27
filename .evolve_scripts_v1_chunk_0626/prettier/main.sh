#!/bin/bash
# prettier - Run prettier formatting check/write on TypeScript/JS/JSON files
# Usage: prettier <project_root> <action> [patterns...] [options]
#
# Actions:
#   check [patterns]   - Check if files are formatted (like prettier --check)
#   write [patterns]   - Format files in place (like prettier --write)
#   list-unformatted   - List unformatted files (grep for "unformatted" in check output)
#
# Patterns:
#   Glob patterns to match files. Default: 'src/**/*.{ts,js,tsx,jsx,json,css,md}'
#   Examples: 'src/**/*.ts', 'src/schema/**/*.ts'
#
# Options:
#   --max-lines, -m <n>     Max output lines (default: 100, 0=unlimited)
#   --timeout <sec>         Timeout in seconds (default: 60)

PROJECT_ROOT="$1"
ACTION="$2"
shift 2 2>/dev/null || shift $#

MAX_LINES=100
TIMEOUT=60
PATTERNS=()

while [ $# -gt 0 ]; do
    case "$1" in
        --max-lines|-m)
            MAX_LINES="$2"
            shift 2
            ;;
        --timeout)
            TIMEOUT="$2"
            shift 2
            ;;
        --*)
            echo "Unknown option: $1"
            exit 1
            ;;
        *)
            PATTERNS+=("$1")
            shift
            ;;
    esac
done

if [ -z "$PROJECT_ROOT" ] || [ -z "$ACTION" ]; then
    echo "Usage: prettier <project_root> <action> [patterns...] [options]"
    echo ""
    echo "Actions:"
    echo "  check [patterns]     - Check if files are formatted (non-zero exit if unformatted)"
    echo "  write [patterns]     - Format files in place"
    echo "  list-unformatted     - List only unformatted file names"
    echo ""
    echo "Patterns:"
    echo "  Glob patterns to match files. Default: 'src/**/*.{ts,js,tsx,jsx,json,css,md}'"
    echo ""
    echo "Options:"
    echo "  --max-lines, -m <n>  Max output lines (default: 100, 0=unlimited)"
    echo "  --timeout <sec>      Timeout in seconds (default: 60)"
    exit 1
fi

if [ ! -d "$PROJECT_ROOT" ]; then
    echo "Error: Directory '$PROJECT_ROOT' does not exist"
    exit 1
fi

# Default patterns if none provided
if [ ${#PATTERNS[@]} -eq 0 ]; then
    PATTERNS=("'src/**/*.{ts,js,tsx,jsx,json,css,md}'")
fi

# Build pattern string for prettier
PATTERN_STR=""
for p in "${PATTERNS[@]}"; do
    PATTERN_STR="$PATTERN_STR $p"
done

cd "$PROJECT_ROOT" || exit 1

case "$ACTION" in
    check)
        echo "Checking formatting for patterns:${PATTERN_STR}"
        echo ""
        if [ "$MAX_LINES" -gt 0 ]; then
            timeout "$TIMEOUT" bash -c "npx prettier --check ${PATTERN_STR} 2>&1" | head -n "$MAX_LINES"
        else
            timeout "$TIMEOUT" bash -c "npx prettier --check ${PATTERN_STR} 2>&1"
        fi
        EXIT_CODE=$?
        if [ $EXIT_CODE -eq 124 ]; then
            echo ""
            echo "Command timed out after ${TIMEOUT}s"
        elif [ $EXIT_CODE -ne 0 ]; then
            echo ""
            echo "Some files are not formatted (exit code: $EXIT_CODE)"
        else
            echo ""
            echo "All files are formatted correctly!"
        fi
        exit $EXIT_CODE
        ;;
    write)
        echo "Formatting files for patterns:${PATTERN_STR}"
        echo ""
        if [ "$MAX_LINES" -gt 0 ]; then
            timeout "$TIMEOUT" bash -c "npx prettier --write ${PATTERN_STR} 2>&1" | head -n "$MAX_LINES"
        else
            timeout "$TIMEOUT" bash -c "npx prettier --write ${PATTERN_STR} 2>&1"
        fi
        EXIT_CODE=$?
        if [ $EXIT_CODE -eq 124 ]; then
            echo ""
            echo "Command timed out after ${TIMEOUT}s"
        fi
        exit $EXIT_CODE
        ;;
    list-unformatted)
        echo "Finding unformatted files for patterns:${PATTERN_STR}"
        echo ""
        if [ "$MAX_LINES" -gt 0 ]; then
            timeout "$TIMEOUT" bash -c "npx prettier --check ${PATTERN_STR} 2>&1 | grep -E 'unformatted|warn' | head -n $MAX_LINES"
        else
            timeout "$TIMEOUT" bash -c "npx prettier --check ${PATTERN_STR} 2>&1 | grep -E 'unformatted|warn'"
        fi
        EXIT_CODE=$?
        if [ $EXIT_CODE -eq 124 ]; then
            echo ""
            echo "Command timed out after ${TIMEOUT}s"
        fi
        # grep returns 1 when no match, which means all files are formatted
        if [ $EXIT_CODE -eq 1 ]; then
            echo ""
            echo "All files are formatted correctly!"
            exit 0
        fi
        exit $EXIT_CODE
        ;;
    *)
        echo "Unknown action: $ACTION"
        echo "Valid actions: check, write, list-unformatted"
        exit 1
        ;;
esac
