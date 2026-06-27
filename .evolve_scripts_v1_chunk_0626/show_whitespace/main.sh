#!/usr/bin/env bash
set -euo pipefail

# show_whitespace - Display file content with visible whitespace characters
# Shows tabs, spaces at end of lines, line endings, and line numbers.
# Useful for debugging indentation issues (mixing tabs and spaces, wrong tab depth).
#
# Usage: main.sh <file> [action]
#
# Actions:
#   all       - Show tabs, trailing spaces, and line endings (default)
#   tabs      - Show only tabs (displayed as →)
#   trailing  - Show only trailing spaces
#   endings   - Show only line endings ($)
#   lines     - Show line numbers only (like cat -n or nl -ba)
#
# Options:
#   --head N  - Show only first N lines
#   --tail N  - Show only last N lines
#
# Examples:
#   main.sh file.go              # Show tabs, trailing spaces, line endings
#   main.sh file.go tabs         # Show only tabs
#   main.sh file.go lines        # Show file with line numbers (nl -ba equivalent)
#   main.sh file.go all --head 20  # First 20 lines with all whitespace visible

FILE="$1"
ACTION="${2:-all}"
shift 2 2>/dev/null || shift $#

HEAD_LINES=0
TAIL_LINES=0

while [ $# -gt 0 ]; do
    case "$1" in
        --head)
            HEAD_LINES="$2"
            shift 2
            ;;
        --tail)
            TAIL_LINES="$2"
            shift 2
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

if [ -z "$FILE" ]; then
    echo "Usage: main.sh <file> [action] [--head N|--tail N]"
    echo "Actions: all (default), tabs, trailing, endings, lines"
    exit 1
fi

if [ ! -f "$FILE" ]; then
    echo "Error: File '$FILE' not found"
    exit 1
fi

# Get total line count
TOTAL_LINES=$(wc -l < "$FILE")

case "$ACTION" in
    all)
        # Show tabs, trailing spaces, and line endings
        echo "=== $FILE ($TOTAL_LINES lines) ==="
        echo "Legend: → = tab, · = space, $ = line ending"
        echo ""
        if [ "$HEAD_LINES" -gt 0 ]; then
            nl -ba "$FILE" 2>/dev/null | head -n "$HEAD_LINES" | \
                sed 's/\t/→/g' | \
                sed 's/[[:space:]]*$/·$/'
        elif [ "$TAIL_LINES" -gt 0 ]; then
            nl -ba "$FILE" 2>/dev/null | tail -n "$TAIL_LINES" | \
                sed 's/\t/→/g' | \
                sed 's/[[:space:]]*$/·$/'
        else
            nl -ba "$FILE" 2>/dev/null | \
                sed 's/\t/→/g' | \
                sed 's/[[:space:]]*$/·$/'
        fi
        ;;
    tabs)
        # Show tabs only
        echo "=== $FILE - Tabs (→) ==="
        if [ "$HEAD_LINES" -gt 0 ]; then
            nl -ba "$FILE" 2>/dev/null | head -n "$HEAD_LINES" | sed 's/\t/→/g'
        elif [ "$TAIL_LINES" -gt 0 ]; then
            nl -ba "$FILE" 2>/dev/null | tail -n "$TAIL_LINES" | sed 's/\t/→/g'
        else
            nl -ba "$FILE" 2>/dev/null | sed 's/\t/→/g'
        fi
        ;;
    trailing)
        # Show trailing spaces only
        echo "=== $FILE - Trailing spaces (·) ==="
        if [ "$HEAD_LINES" -gt 0 ]; then
            nl -ba "$FILE" 2>/dev/null | head -n "$HEAD_LINES" | sed 's/[[:space:]]*$/·$/'
        elif [ "$TAIL_LINES" -gt 0 ]; then
            nl -ba "$FILE" 2>/dev/null | tail -n "$TAIL_LINES" | sed 's/[[:space:]]*$/·$/'
        else
            nl -ba "$FILE" 2>/dev/null | sed 's/[[:space:]]*$/·$/'
        fi
        ;;
    endings)
        # Show line endings
        echo "=== $FILE - Line endings ($) ==="
        if [ "$HEAD_LINES" -gt 0 ]; then
            nl -ba "$FILE" 2>/dev/null | head -n "$HEAD_LINES" | sed 's/$/$/'
        elif [ "$TAIL_LINES" -gt 0 ]; then
            nl -ba "$FILE" 2>/dev/null | tail -n "$TAIL_LINES" | sed 's/$/$/'
        else
            nl -ba "$FILE" 2>/dev/null | sed 's/$/$/'
        fi
        ;;
    lines)
        # Show with line numbers only (like nl -ba or cat -n)
        echo "=== $FILE ($TOTAL_LINES lines) ==="
        if [ "$HEAD_LINES" -gt 0 ]; then
            nl -ba "$FILE" 2>/dev/null | head -n "$HEAD_LINES"
        elif [ "$TAIL_LINES" -gt 0 ]; then
            nl -ba "$FILE" 2>/dev/null | tail -n "$TAIL_LINES"
        else
            nl -ba "$FILE" 2>/dev/null
        fi
        ;;
    *)
        echo "Unknown action: $ACTION"
        echo "Valid actions: all, tabs, trailing, endings, lines"
        exit 1
        ;;
esac
