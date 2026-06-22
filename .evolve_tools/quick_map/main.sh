#!/bin/bash
# quick_map - Quickly map the project structure
# Usage: quick_map [directory] [max_depth=4]
#        quick_map [directory] --filter='*.c,*.h' [max_depth=4]
#        quick_map [directory] -f '*.py' [max_depth=4]
#
# Generates a compact tree view of the project, showing file types and sizes.
# Use --filter or -f to show only specific file types (comma-separated globs).
# Saves steps by replacing multiple `ls`, `find`, and tree exploration commands.

DIR=""
DEPTH=""
FILTER_GLOBS=""

# Parse arguments
while [ $# -gt 0 ]; do
    case "$1" in
        --help|-h)
            echo "Usage: quick_map [directory] [max_depth=4]"
            echo "       quick_map [directory] --filter=GLOBS [max_depth]"
            echo "       quick_map [directory] -f GLOBS [max_depth]"
            echo ""
            echo "Generates a compact tree view of the project structure."
            echo ""
            echo "Parameters:"
            echo "  [directory]       Directory to map (default: current dir)"
            echo "  [max_depth=4]     Maximum directory depth"
            echo "  --filter=GLOBS    Show only specific file types (comma-separated globs)"
            echo "  -f GLOBS          Short form of --filter"
            echo ""
            echo "Examples:"
            echo "  quick_map . 3"
            echo "  quick_map /project 5"
            echo "  quick_map . -f '*.go' 3"
            echo "  quick_map /project -f '*.c,*.h,*.py'"
            exit 0
            ;;
        --filter=*|-f=*)
            FILTER_GLOBS="${1#*=}"
            shift
            ;;
        --filter|-f)
            shift
            if [ $# -gt 0 ]; then
                FILTER_GLOBS="$1"
                shift
            fi
            ;;
        [0-9]*)
            DEPTH="$1"
            shift
            ;;
        -*)
            echo "Unknown option: $1"
            echo "Use --help for usage information."
            exit 1
            ;;
        *)
            # First non-option argument is the directory
            if [ -z "$DIR" ]; then
                DIR="$1"
            fi
            shift
            ;;
    esac
done

# Default directory to current directory if not specified
DIR="${DIR:-.}"
DEPTH="${DEPTH:-4}"

if [ ! -d "$DIR" ]; then
    echo "Error: Directory '$DIR' not found"
    exit 1
fi

echo "=== Project Structure (depth=$DEPTH) ==="

# Build the find command as a string (simpler approach)
FIND_CMD="find \"$DIR\" -maxdepth $DEPTH"

# Add filter if specified
if [ -n "$FILTER_GLOBS" ]; then
    # Convert comma-separated globs to find -name conditions
    IFS=',' read -ra GLOBS <<< "$FILTER_GLOBS"
    FIND_CMD="$FIND_CMD \( -type f \( -false"
    for g in "${GLOBS[@]}"; do
        FIND_CMD="$FIND_CMD -o -name \"$g\""
    done
    FIND_CMD="$FIND_CMD \) -o -type d \)"
fi

# Common exclusions
FIND_CMD="$FIND_CMD -not -path \"*/node_modules/*\" -not -path \"*/.git/*\" -not -path \"*/__pycache__/*\" -not -path \"*/.venv/*\" -not -path \"*/venv/*\""

# Execute find and format output
eval "$FIND_CMD" 2>/dev/null | sort | while IFS= read -r line; do
    indent=$(echo "$line" | sed 's/[^/]//g' | wc -c)
    indent=$((indent - 1))
    prefix=$(printf '%*s' $((indent * 2)) '' | tr ' ' ' ')
    name=$(basename "$line")
    if [ -d "$line" ]; then
        echo "${prefix}[${name}]"
    else
        size=$(stat -c%s "$line" 2>/dev/null || echo "?")
        echo "${prefix}${name} (${size} bytes)"
    fi
done

echo ""
echo "=== File Count by Extension ==="
find "$DIR" -type f \
    -not -path '*/node_modules/*' -not -path '*/.git/*' \
    -not -path '*/__pycache__/*' -not -path '*/.venv/*' -not -path '*/venv/*' | \
    sed 's/.*\.//' | sort | uniq -c | sort -rn | head -20
echo ""
echo "Tip: Use quick_map <dir> -f '*.c,*.h' to filter by file extension"
