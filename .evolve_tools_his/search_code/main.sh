#!/bin/bash
# search_code - Search for patterns in source code efficiently
# Usage: main.sh <pattern> [directory|file] [file_extension] [context_lines] [--files-only]
#   pattern: regex pattern to search for
#   directory|file: directory to search OR a specific file path (default: .)
#     If the second argument is a file (not a directory), searches only that file.
#   file_extension: optional, filter by file extension (e.g., "rs", "ts", "py")
#     Ignored when searching a specific file.
#   context_lines: optional, number of context lines before/after match (default: 0)
#   --files-only: optional, only show matching file names (like grep -rl)
# Examples:
#   main.sh 'Context' . rs
#   main.sh 'TODO' . ts 2
#   main.sh 'pub fn' ./src rs 1
#   main.sh 'Context' . rs --files-only
#   main.sh 'def test_' /path/to/test_file.py    # single file search
#   main.sh 'class.*Handler' ./src/handler.py 2  # single file with context

PATTERN="$1"
TARGET="${2:-.}"
EXT="${3:-}"
CONTEXT="${4:-0}"
FILES_ONLY=false

# Check for --files-only flag, which can be in any position after pattern
for arg in "$@"; do
    if [ "$arg" = "--files-only" ]; then
        FILES_ONLY=true
    fi
done

# If --files-only is set, context doesn't apply
if [ "$FILES_ONLY" = true ]; then
    CONTEXT=0
fi

if [ -z "$PATTERN" ]; then
    echo "ERROR: No search pattern provided"
    echo "Usage: main.sh <pattern> [directory|file] [file_extension] [context_lines] [--files-only]"
    echo "Examples:"
    echo "  main.sh 'require|module|Cache' . go"
    echo "  main.sh 'pub fn' . rs 1"
    echo "  main.sh 'TODO' /workspace/src"
    echo "  main.sh 'Context' . rs --files-only"
    echo "  main.sh 'def test_' /path/to/test_file.py"
    exit 1
fi

EXCLUDE_DIRS="-not -path '*/node_modules/*' -not -path '*/.git/*' -not -path '*/vendor/*' -not -path '*/dist/*' -not -path '*/.next/*' -not -path '*/.venv/*' -not -path '*/__pycache__/*' -not -path '*/target/*' -not -path '*/build/*'"

# Check if TARGET is a file (not a directory)
if [ -f "$TARGET" ]; then
    # Single file search mode
    FILEPATH="$TARGET"
    echo "=== Searching for: '$PATTERN' in $FILEPATH ==="
    echo ""

    if [ "$FILES_ONLY" = true ]; then
        # For files-only mode with a single file, just check if file matches
        if grep -l --color=never "$PATTERN" "$FILEPATH" > /dev/null 2>&1; then
            echo "$FILEPATH"
            echo ""
            echo "--- Matching files: 1 ---"
        else
            echo ""
            echo "--- Matching files: 0 ---"
        fi
    else
        # Count total matches first
        TOTAL_MATCHES=$(grep -c "$PATTERN" "$FILEPATH" 2>/dev/null; true)
        echo "Total matches: $TOTAL_MATCHES"
        echo ""

        if [ "$TOTAL_MATCHES" -gt 0 ]; then
            # Use grep with context if requested
            if [ "$CONTEXT" -gt 0 ]; then
                grep -n -B "$CONTEXT" -A "$CONTEXT" --color=never "$PATTERN" "$FILEPATH" 2>/dev/null | head -200
            else
                grep -n --color=never "$PATTERN" "$FILEPATH" 2>/dev/null | head -100
            fi
            echo ""
            echo "--- Results shown: limited to protect against huge output ---"
        fi
    fi
    exit 0
fi

# Check if TARGET exists at all
if [ ! -e "$TARGET" ]; then
    echo "ERROR: Target not found: $TARGET"
    exit 1
fi

# Directory-based search (original behavior)
DIR="$TARGET"

if [ -n "$EXT" ]; then
    # Remove leading dot if present
    EXT="${EXT#.}"
    FIND_CMD="find \"$DIR\" -name \"*.$EXT\" $EXCLUDE_DIRS 2>/dev/null"
else
    FIND_CMD="find \"$DIR\" $EXCLUDE_DIRS -type f 2>/dev/null"
fi

echo "=== Searching for: '$PATTERN' in $DIR ==="
echo ""

if [ "$FILES_ONLY" = true ]; then
    # Files-only mode (grep -rl equivalent)
    eval "$FIND_CMD" | xargs grep -l --color=never "$PATTERN" 2>/dev/null | head -50

    MATCH_COUNT=$(eval "$FIND_CMD" | xargs grep -l --color=never "$PATTERN" 2>/dev/null | wc -l)
    echo ""
    echo "--- Matching files: $MATCH_COUNT ---"
else
    # Count total matches first
    TOTAL_MATCHES=$(eval "$FIND_CMD" | xargs grep -c "$PATTERN" 2>/dev/null | awk -F: '{s+=$NF} END {print s}' 2>/dev/null || echo 0)
    echo "Total matches: $TOTAL_MATCHES"
    echo ""

    if [ "$TOTAL_MATCHES" -gt 0 ]; then
        # Use grep with context if requested
        if [ "$CONTEXT" -gt 0 ]; then
            eval "$FIND_CMD" | xargs grep -n -B "$CONTEXT" -A "$CONTEXT" --color=never "$PATTERN" 2>/dev/null | head -200
        else
            eval "$FIND_CMD" | xargs grep -n --color=never "$PATTERN" 2>/dev/null | head -100
        fi

        echo ""
        echo "--- Results shown: limited to protect against huge output ---"
        echo "--- To see more matches, narrow your search with more specific pattern or file extension ---"
    fi
fi
