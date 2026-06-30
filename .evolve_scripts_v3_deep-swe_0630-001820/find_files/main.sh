#!/usr/bin/env bash
set -euo pipefail

# find_files - Find files by name pattern with filtering, sorting, depth, and exclusion options.
# Usage: find_files [--dir=DIR] [directory] [options]
# Options:
#   --dir=DIR             Working directory (alternative to first positional arg)
#   -n, --name=PATTERN    Name glob (repeatable)
#   -t, --type=TYPE       f (file) or d (dir)
#   -d, --max-depth=N     Max depth
#   -l, --limit=N         Max results (default: 100)
#   -p, --path=PATH       Path glob filter
#   -x, --exclude=PATTERN Exclude path pattern
#   -i, --case-insensitive Case-insensitive matching
#   -s, --sort            Sort results alphabetically
#   --no-exclude-defaults Don't auto-exclude .git/node_modules

DIR="."
NAMES=()
FTYPE=""
MAX_DEPTH=""
LIMIT=100
PATH_PATTERN=""
EXCLUDE_PATTERNS=()
ICASE=""
NO_DEFAULTS=false
SORT_RESULTS=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dir=*)
            DIR="${1#*=}"
            shift
            ;;
        --dir)
            DIR="$2"
            shift 2
            ;;
        -n|--name)
            NAMES+=("$2")
            shift 2
            ;;
        --name=*)
            NAMES+=("${1#*=}")
            shift
            ;;
        -t|--type)
            FTYPE="$2"
            shift 2
            ;;
        --type=*)
            FTYPE="${1#*=}"
            shift
            ;;
        -d|--max-depth)
            MAX_DEPTH="$2"
            shift 2
            ;;
        --max-depth=*)
            MAX_DEPTH="${1#*=}"
            shift
            ;;
        -l|--limit)
            LIMIT="$2"
            shift 2
            ;;
        --limit=*)
            LIMIT="${1#*=}"
            shift
            ;;
        -p|--path)
            PATH_PATTERN="$2"
            shift 2
            ;;
        --path=*)
            PATH_PATTERN="${1#*=}"
            shift
            ;;
        -x|--exclude)
            EXCLUDE_PATTERNS+=("$2")
            shift 2
            ;;
        --exclude=*)
            EXCLUDE_PATTERNS+=("${1#*=}")
            shift
            ;;
        -i|--case-insensitive)
            ICASE="-iname"
            shift
            ;;
        -s|--sort)
            SORT_RESULTS=true
            shift
            ;;
        --no-exclude-defaults)
            NO_DEFAULTS=true
            shift
            ;;
        -*)
            echo "Unknown option: $1" >&2
            exit 1
            ;;
        *)
            DIR="$1"
            shift
            ;;
    esac
done

FIND_ARGS=()
[[ -n "$MAX_DEPTH" ]] && FIND_ARGS+=(-maxdepth "$MAX_DEPTH")

# Type filter
if [[ -n "$FTYPE" ]]; then
    case "$FTYPE" in
        f|file) FIND_ARGS+=(-type f) ;;
        d|dir)  FIND_ARGS+=(-type d) ;;
        l)      FIND_ARGS+=(-type l) ;;
        *) echo "Error: Invalid type '$FTYPE'. Use f, d, or l." >&2; exit 1 ;;
    esac
fi

# Name patterns
NAME_OP="-name"
[[ "$ICASE" == "-iname" ]] && NAME_OP="-iname"
if [[ ${#NAMES[@]} -gt 0 ]]; then
    FIND_ARGS+=("(")
    FIRST=true
    for pattern in "${NAMES[@]}"; do
        if [[ "$FIRST" == true ]]; then
            FIND_ARGS+=("$NAME_OP" "$pattern")
            FIRST=false
        else
            FIND_ARGS+=(-o "$NAME_OP" "$pattern")
        fi
    done
    FIND_ARGS+=(")")
fi

# Exclude defaults
EXCLUDES=()
if [[ "$NO_DEFAULTS" == false ]]; then
    EXCLUDES+=("*/node_modules/*" "*/.git/*" "*/__pycache__/*" "*/.venv/*" "*/venv/*")
fi

# Build exclusion expressions
for excl in "${EXCLUDE_PATTERNS[@]}"; do
    EXCLUDES+=("$excl")
done

for excl in "${EXCLUDES[@]}"; do
    FIND_ARGS+=(! -path "$excl")
done

# Run find with pipe to head
if [[ "$SORT_RESULTS" == true ]]; then
    find "$DIR" "${FIND_ARGS[@]}" 2>/dev/null | sort | head -n "$LIMIT"
else
    find "$DIR" "${FIND_ARGS[@]}" 2>/dev/null | head -n "$LIMIT"
fi
