#!/usr/bin/env bash
set -euo pipefail

# batch_read - Read multiple files with clear separators
# Usage: main.sh <file1> [file2 ...] [--lines|-n <lines>] [--head|-h <count>] [--tail|-t <count>]
#
# Reads multiple files and outputs them with clear filename headers and separators.
# Supports limiting output per file with --head or --tail.
# Supports showing line numbers with --lines.
#
# Examples:
#   main.sh src/main.py src/utils.py
#   main.sh src/*.ts --head 30
#   main.sh --lines src/main.go src/lib.go --tail 20

SHOW_LINES=false
HEAD_COUNT=""
TAIL_COUNT=""
FILES=()

while [ $# -gt 0 ]; do
    case "$1" in
        --lines|-n)
            SHOW_LINES=true
            shift
            ;;
        --head|-h)
            shift
            HEAD_COUNT="$1"
            shift
            ;;
        --tail|-t)
            shift
            TAIL_COUNT="$1"
            shift
            ;;
        *)
            FILES+=("$1")
            shift
            ;;
    esac
done

if [ ${#FILES[@]} -eq 0 ]; then
    echo "Usage: main.sh <file1> [file2 ...] [--lines|-n] [--head|-h <count>] [--tail|-t <count>]"
    echo "  --lines, -n    Show line numbers"
    echo "  --head, -h     Show only first N lines of each file"
    echo "  --tail, -t     Show only last N lines of each file"
    exit 1
fi

first=true
for file in "${FILES[@]}"; do
    if [ ! -f "$file" ]; then
        echo "Error: File '$file' not found." >&2
        exit 1
    fi

    if [ "$first" = true ]; then
        first=false
    else
        echo ""
    fi

    echo "===== $file ====="

    if [ "$SHOW_LINES" = true ]; then
        if [ -n "$HEAD_COUNT" ]; then
            nl -ba "$file" | head -n "$HEAD_COUNT"
        elif [ -n "$TAIL_COUNT" ]; then
            nl -ba "$file" | tail -n "$TAIL_COUNT"
        else
            nl -ba "$file"
        fi
    else
        if [ -n "$HEAD_COUNT" ]; then
            head -n "$HEAD_COUNT" "$file"
        elif [ -n "$TAIL_COUNT" ]; then
            tail -n "$TAIL_COUNT" "$file"
        else
            cat "$file"
        fi
    fi
done
