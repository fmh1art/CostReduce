#!/bin/bash
# read_file - Read source files efficiently with line numbers and context
# Usage: main.sh <filepath> [start_line] [end_line]
#   filepath: path to the file to read
#   start_line: optional, starting line number
#   end_line: optional, ending line number (if omitted, reads to end)
# If only filepath is given, shows entire file with line numbers
# If start_line is given without end_line, shows from start_line to end of file
# Supports stdin with "--" as filepath
# Shows file metadata (size, type) for quick orientation

FILE="$1"
START="${2:-}"
END="${3:-}"

if [ "$FILE" = "--" ]; then
    # Read from stdin with line numbers
    nl -ba -
    exit 0
fi

if [ -z "$FILE" ] || [ ! -f "$FILE" ]; then
    echo "ERROR: File not found: $FILE"
    echo "Usage: main.sh <filepath> [start_line] [end_line]"
    exit 1
fi

TOTAL_LINES=$(wc -l < "$FILE" 2>/dev/null || echo 0)
FILE_SIZE=$(wc -c < "$FILE" 2>/dev/null || echo 0)
FILE_TYPE=$(file "$FILE" 2>/dev/null | cut -d: -f2- | sed 's/^ //')

# Show file metadata
echo "=== File: $FILE ==="
echo "Size: $FILE_SIZE bytes, $TOTAL_LINES lines"
echo "Type: $FILE_TYPE"
echo ""

# Check if file is binary
if file "$FILE" 2>/dev/null | grep -q "binary"; then
    echo "WARNING: Binary file. Showing file info only."
    ls -la "$FILE"
    exit 0
fi

if [ -z "$START" ]; then
    # Show entire file with line numbers
    nl -ba "$FILE"
    echo ""
    echo "--- Total: $TOTAL_LINES lines ---"
elif [ -n "$START" ] && [ -z "$END" ]; then
    # Show from START to end
    nl -ba "$FILE" | sed -n "${START},\$p"
    echo ""
    echo "--- Lines $START to $TOTAL_LINES (total: $TOTAL_LINES lines) ---"
else
    # Show range
    nl -ba "$FILE" | sed -n "${START},${END}p"
    echo ""
    echo "--- Lines $START to $END (total: $TOTAL_LINES lines) ---"
fi
