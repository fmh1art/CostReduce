#!/bin/bash
# find_files - Find files by name pattern efficiently
# Usage: find_files [directory] [options] <pattern1> [pattern2] ...
#        find_files [directory] -p <glob_pattern1> [glob_pattern2 ...]
#        find_files [directory] --path="*/src/*" -name "*.rs" -name "*.c"
#
# Finds files matching name patterns in a single pass. Supports multiple
# name patterns (OR'd), path filtering, depth control, and result limits.
# Saves steps by replacing multiple separate find commands with different
# -name/-o -name combinations.
#
# Options:
#   -n, --name=PATTERN     Match file names against this glob (may repeat)
#   -t, --type=TYPE        File type: f (file, default) or d (directory)
#   -d, --max-depth=N      Maximum directory depth (default: unlimited)
#   -l, --limit=N          Limit number of results (default: 100)
#   -p, --path=PATH        Only include files matching this path glob
#   -x, --exclude=PATTERN  Exclude paths matching this pattern
#   -i, --case-insensitive Case-insensitive name matching
#   --no-exclude-defaults  Don't auto-exclude .git, node_modules, etc.
#
# Examples:
#   find_files . -n "*.go"                    # Find all Go files
#   find_files . -n "*.rs" -n "*.c" -n "*.h" # Find Rust/C/header files
#   find_files . -n "*test*" -t f             # Find test files
#   find_files /project -n "*.go" -d 4            # Go files, max depth 4
#   find_files /project -n "*.rs" --path="*/rust/src/*"  # Rust files in specific path
#   find_files . -n "*rust-context*"           # Find files with 'rust-context' in name
#   find_files . -n "Makefile*" -n "*.toml"   # Find Makefiles and TOML files

DIR="."
MAX_DEPTH=""
NAME_PATTERNS=()
PATH_PATTERNS=()
EXCLUDE_PATTERNS=()
FILE_TYPE="f"
LIMIT=100
CASE_INSENSITIVE=false
NO_EXCLUDE_DEFAULTS=false

# Parse arguments
while [ $# -gt 0 ]; do
    case "$1" in
        --name=*|-n=*)
            NAME_PATTERNS+=("${1#*=}")
            shift
            ;;
        --name|-n)
            shift
            if [ $# -gt 0 ]; then
                NAME_PATTERNS+=("$1")
                shift
            fi
            ;;
        --path=*|-p=*)
            PATH_PATTERNS+=("${1#*=}")
            shift
            ;;
        --path|-p)
            shift
            if [ $# -gt 0 ]; then
                PATH_PATTERNS+=("$1")
                shift
            fi
            ;;
        --exclude=*|-x=*)
            EXCLUDE_PATTERNS+=("${1#*=}")
            shift
            ;;
        --exclude|-x)
            shift
            if [ $# -gt 0 ]; then
                EXCLUDE_PATTERNS+=("$1")
                shift
            fi
            ;;
        --type=*|-t=*)
            FILE_TYPE="${1#*=}"
            shift
            ;;
        --type|-t)
            shift
            if [ $# -gt 0 ]; then
                FILE_TYPE="$1"
                shift
            fi
            ;;
        --max-depth=*|-d=*)
            MAX_DEPTH="${1#*=}"
            shift
            ;;
        --max-depth|-d)
            shift
            if [ $# -gt 0 ]; then
                MAX_DEPTH="$1"
                shift
            fi
            ;;
        --limit=*|-l=*)
            LIMIT="${1#*=}"
            shift
            ;;
        --limit|-l)
            shift
            if [ $# -gt 0 ]; then
                LIMIT="$1"
                shift
            fi
            ;;
        --case-insensitive|-i)
            CASE_INSENSITIVE=true
            shift
            ;;
        --no-exclude-defaults)
            NO_EXCLUDE_DEFAULTS=true
            shift
            ;;
        --help|-h)
            echo "Usage: find_files [directory] [options] <pattern1> [pattern2] ..."
            echo ""
            echo "Options:"
            echo "  -n, --name=PATTERN       Match file names against this glob"
            echo "  -t, --type=TYPE          File type: f (file) or d (directory)"
            echo "  -d, --max-depth=N        Maximum directory depth"
            echo "  -l, --limit=N            Limit number of results (default: 100)"
            echo "  -p, --path=PATH          Only include files matching this path glob"
            echo "  -x, --exclude=PATTERN    Exclude paths matching this pattern"
            echo "  -i, --case-insensitive   Case-insensitive name matching"
            echo "  --no-exclude-defaults    Don't auto-exclude .git, node_modules..."
            echo ""
            echo "Examples:"
            echo "  find_files . -n \"*.go\""
            echo "  find_files . -n \"*.rs\" -n \"*.c\" -n \"*.h\""
            echo "  find_files /project -n \"*.rs\" --path=\"*/rust/src/*\""
            echo "  find_files . -n \"*rust-context*\""
            echo "  find_files . -n \"Makefile*\" -n \"*.toml\" -l 20"
            exit 0
            ;;
        -*)
            echo "Unknown option: $1"
            exit 1
            ;;
        *)
            # First positional arg is the directory (if it's a directory)
            if [ -z "$DIR" ] || [ "$DIR" = "." ]; then
                if [ -d "$1" ]; then
                    DIR="$1"
                else
                    NAME_PATTERNS+=("$1")
                fi
            else
                NAME_PATTERNS+=("$1")
            fi
            shift
            ;;
    esac
done

if [ ${#NAME_PATTERNS[@]} -eq 0 ]; then
    echo "Error: At least one name pattern is required."
    echo "Usage: find_files [directory] -n <pattern1> [-n pattern2 ...]"
    exit 1
fi

if [ ! -d "$DIR" ]; then
    echo "Error: Directory '$DIR' not found"
    exit 1
fi

# Build find command
FIND_CMD="find \"$DIR\""

# Add max depth if specified
if [ -n "$MAX_DEPTH" ]; then
    FIND_CMD="$FIND_CMD -maxdepth $MAX_DEPTH"
fi

# Add file type
if [ "$FILE_TYPE" = "f" ]; then
    FIND_CMD="$FIND_CMD -type f"
elif [ "$FILE_TYPE" = "d" ]; then
    FIND_CMD="$FIND_CMD -type d"
fi

# Add name patterns (OR'd)
if [ ${#NAME_PATTERNS[@]} -gt 0 ]; then
    FIND_CMD="$FIND_CMD \\( -false"
    for pat in "${NAME_PATTERNS[@]}"; do
        if [ "$CASE_INSENSITIVE" = true ]; then
            FIND_CMD="$FIND_CMD -o -iname \"$pat\""
        else
            FIND_CMD="$FIND_CMD -o -name \"$pat\""
        fi
    done
    FIND_CMD="$FIND_CMD \\)"
fi

# Add path filter if specified
for pat in "${PATH_PATTERNS[@]}"; do
    FIND_CMD="$FIND_CMD -path \"$pat\""
done

# Add exclude patterns
for pat in "${EXCLUDE_PATTERNS[@]}"; do
    FIND_CMD="$FIND_CMD -not -path \"$pat\""
done

# Add default excludes
if [ "$NO_EXCLUDE_DEFAULTS" = false ]; then
    FIND_CMD="$FIND_CMD -not -path \"*/.git/*\" -not -path \"*/node_modules/*\" -not -path \"*/__pycache__/*\" -not -path \"*/.venv/*\" -not -path \"*/venv/*\""
fi

# Execute and limit results
echo "=== Find Results ==="
echo "Directory: $DIR"
echo "Patterns: ${NAME_PATTERNS[*]}"
echo "Type: $FILE_TYPE"
[ -n "$MAX_DEPTH" ] && echo "Max depth: $MAX_DEPTH"
echo ""

eval "$FIND_CMD" 2>/dev/null | head -n "$LIMIT" | while IFS= read -r line; do
    if [ -d "$line" ]; then
        echo "[DIR]  $line"
    else
        size=$(stat -c%s "$line" 2>/dev/null || echo "?")
        echo "[FILE] $line (${size} bytes)"
    fi
done

# Count total
TOTAL=$(eval "$FIND_CMD" 2>/dev/null | wc -l)
if [ "$TOTAL" -gt "$LIMIT" ]; then
    echo ""
    echo "... (showing $LIMIT of $TOTAL results)"
else
    echo ""
    echo "Total: $TOTAL file(s)"
fi
