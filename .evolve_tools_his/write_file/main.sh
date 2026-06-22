#!/bin/bash
# write_file - Write content to a file efficiently
# Usage: main.sh <filepath> [content]
#   filepath: path to the file to create/overwrite
#   content: optional, the content to write.
#     If omitted, reads from stdin (pipe or heredoc).
#     Can also be provided as a file path via --from-file=<path>
# Creates parent directories if they don't exist.
# Shows diff-like summary of what was written.

FILEPATH=""
CONTENT=""
FROM_FILE=""

# Parse arguments
for arg in "$@"; do
    if [[ "$arg" =~ ^--from-file=(.*)$ ]]; then
        FROM_FILE="${BASH_REMATCH[1]}"
    elif [ -z "$FILEPATH" ]; then
        FILEPATH="$arg"
    fi
done

# If only one arg, it's the filepath, read content from stdin
if [ $# -eq 1 ]; then
    CONTENT="$(cat)"
elif [ $# -ge 2 ]; then
    # Check if second arg is --from-file
    if [ -n "$FROM_FILE" ]; then
        if [ ! -f "$FROM_FILE" ]; then
            echo "ERROR: Source file not found: $FROM_FILE"
            exit 1
        fi
        CONTENT="$(cat "$FROM_FILE")"
    else
        # Content is all remaining arguments (rejoin to preserve spaces)
        shift
        CONTENT="$*"
    fi
fi

if [ -z "$FILEPATH" ]; then
    echo "ERROR: No filepath provided"
    echo "Usage: main.sh <filepath> [content]"
    echo "   or: main.sh <filepath> < content.txt"
    echo "   or: echo 'content' | main.sh <filepath>"
    echo "   or: main.sh <filepath> --from-file=<source>"
    exit 1
fi

# Create parent directory
PARENT_DIR=$(dirname "$FILEPATH")
if [ ! -d "$PARENT_DIR" ]; then
    mkdir -p "$PARENT_DIR" 2>/dev/null || {
        echo "ERROR: Cannot create directory $PARENT_DIR"
        exit 1
    }
    echo "(Created directory: $PARENT_DIR)"
fi

# Check if file already exists for diff-like summary
EXISTS=0
OLD_LINES=0
if [ -f "$FILEPATH" ]; then
    EXISTS=1
    OLD_LINES=$(wc -l < "$FILEPATH" 2>/dev/null || echo 0)
fi

# Write the content, preserving content exactly (no extra trailing newline)
if [ -n "$CONTENT" ]; then
    # Use python to write content exactly, adding trailing newline only if missing
    python3 -c "
import sys
content = '''$CONTENT'''
# Actually, use proper approach with heredoc
" 2>/dev/null
    # Simpler approach: write with printf '%s' (no extra newline),
    # then check last char and add newline if needed
    printf '%s' "$CONTENT" > "$FILEPATH"
    # Check if the file ends with newline
    if [ -s "$FILEPATH" ]; then
        LAST_CHAR=$(tail -c 1 "$FILEPATH" | od -An -tx1 | tr -d ' ')
        if [ "$LAST_CHAR" != "0a" ]; then
            printf '\n' >> "$FILEPATH"
        fi
    fi
else
    # Empty content - create empty file
    : > "$FILEPATH"
fi
EXIT_CODE=$?

if [ $EXIT_CODE -ne 0 ]; then
    echo "ERROR: Failed to write to $FILEPATH"
    exit $EXIT_CODE
fi

NEW_LINES=$(wc -l < "$FILEPATH" 2>/dev/null || echo 0)
NEW_SIZE=$(wc -c < "$FILEPATH" 2>/dev/null || echo 0)

echo "=== File written: $FILEPATH ==="
echo "Size: $NEW_SIZE bytes, $NEW_LINES lines"

if [ "$EXISTS" -eq 1 ]; then
    if [ "$OLD_LINES" -ne "$NEW_LINES" ]; then
        echo "Changed: $OLD_LINES lines -> $NEW_LINES lines"
    else
        echo "Overwritten: $OLD_LINES lines (content may have changed)"
    fi
else
    echo "Created: new file"
fi

# Show first few lines as preview
if [ "$NEW_LINES" -gt 0 ]; then
    echo ""
    echo "--- Preview (first 15 lines) ---"
    head -15 "$FILEPATH"
    if [ "$NEW_LINES" -gt 15 ]; then
        echo "... ($((NEW_LINES - 15)) more lines)"
    fi
fi

exit 0
