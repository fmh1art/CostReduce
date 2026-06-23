#!/bin/bash
# write_file - Write content to a file in one step
# Usage: write_file <filepath> <content>
#        write_file <filepath> -   (reads from stdin)
#
# Writes content to a file atomically. Handles multi-line content,
# special characters, and creates parent directories automatically.
# Saves steps by avoiding heredoc escaping issues and separate mkdir commands.
#
# For multi-line content, pass the entire content as a single argument
# with actual newlines (not \n escapes).
#
# Examples:
#   write_file /path/to/output.txt "Hello World"
#   write_file /tmp/test.py "print('hello')
# print('world')"
#   echo "content" | write_file /tmp/out.txt -

if [ $# -lt 2 ]; then
    echo "Usage: write_file <filepath> <content>"
    echo "       write_file <filepath> -   (reads from stdin)"
    echo ""
    echo "Examples:"
    echo '  write_file /tmp/out.txt "Hello World"'
    echo '  write_file /tmp/out.txt "line1'
    echo 'line2'
    echo 'line3"'
    echo '  echo "content" | write_file /tmp/out.txt -'
    exit 1
fi

FILEPATH="$1"
shift

# Create parent directory if needed
PARENT_DIR=$(dirname "$FILEPATH")
if [ ! -d "$PARENT_DIR" ]; then
    mkdir -p "$PARENT_DIR" 2>/dev/null || {
        echo "Warning: Could not create directory $PARENT_DIR"
    }
fi

if [ "$1" = "-" ]; then
    # Read from stdin
    cat > "$FILEPATH"
else
    # Write all remaining arguments as-is (preserving spaces, newlines, etc.)
    # Use printf with %s to write exactly what was passed, no extra processing
    if [ $# -eq 1 ]; then
        printf '%s' "$1" > "$FILEPATH"
    else
        # Multiple arguments: join with newlines
        for arg in "$@"; do
            printf '%s\n' "$arg"
        done > "$FILEPATH"
    fi
fi

echo "Written to $FILEPATH ($(wc -c < "$FILEPATH") bytes)"
