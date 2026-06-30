#!/usr/bin/env bash
set -euo pipefail

# multi_search - Search multiple patterns in a single filesystem pass.
# Usage: multi_search [options] <file_or_dir> <pattern1> [pattern2...]
# Options:
#   --dir=DIR               Working directory to cd into before searching
#   --include=GLOB          File type filter (e.g. *.go)
#   -i, --ignore-case       Case-insensitive search
#   -l, --files-with-matches  List filenames only
#   -v, --exclude-pattern=PATTERN  Exclude matching lines (repeatable)
#   --head=N, --max-results=N  Limit output to first N matching lines per pattern

WORKDIR=""
INCLUDE=""
ICASE=""
FM=""
EXCLUDE=()
HEAD_LINES=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dir=*) WORKDIR="${1#*=}"; shift ;;
        --dir) WORKDIR="$2"; shift 2 ;;
        --include=*) INCLUDE="${1#*=}"; shift ;;
        -i|--ignore-case) ICASE="-i"; shift ;;
        -l|--files-with-matches|--names-only) FM="-l"; shift ;;
        -v|--exclude-pattern) EXCLUDE+=("$2"); shift 2 ;;
        -v=*|--exclude-pattern=*) EXCLUDE+=("${1#*=}"); shift ;;
        --head=*|--max-results=*) HEAD_LINES="${1#*=}"; shift ;;
        --head|--max-results) HEAD_LINES="$2"; shift 2 ;;
        --) shift; break ;;
        -*) echo "Unknown option: $1" >&2; exit 1 ;;
        *) break ;;
    esac
done

if [[ $# -lt 2 ]]; then
    echo "Usage: multi_search [options] <file_or_dir> <pattern1> [pattern2...]" >&2
    exit 1
fi

# Change to working directory if specified
if [[ -n "$WORKDIR" ]]; then
    cd "$WORKDIR" || { echo "Error: Cannot cd to $WORKDIR" >&2; exit 1; }
fi

TARGET="$1"
shift

# Build grep args
GREP_ARGS=(-n)
if [[ -d "$TARGET" ]]; then GREP_ARGS+=("-r"); fi
[[ -n "$INCLUDE" ]] && GREP_ARGS+=("--include=$INCLUDE")
[[ -n "$ICASE" ]] && GREP_ARGS+=("$ICASE")
[[ -n "$FM" ]] && GREP_ARGS+=("$FM")

# For each pattern, search and optionally exclude
for pattern in "$@"; do
    # First get matching lines
    if [[ ${#EXCLUDE[@]} -gt 0 ]]; then
        excl_args=()
        for excl in "${EXCLUDE[@]}"; do
            excl_args+=(-v -e "$excl")
        done
        if [[ -n "$HEAD_LINES" ]]; then
            grep "${GREP_ARGS[@]}" -e "$pattern" "$TARGET" 2>/dev/null | grep "${excl_args[@]}" | head -n "$HEAD_LINES" || true
        else
            grep "${GREP_ARGS[@]}" -e "$pattern" "$TARGET" 2>/dev/null | grep "${excl_args[@]}" || true
        fi
    else
        if [[ -n "$HEAD_LINES" ]]; then
            grep "${GREP_ARGS[@]}" -e "$pattern" "$TARGET" 2>/dev/null | head -n "$HEAD_LINES" || true
        else
            grep "${GREP_ARGS[@]}" -e "$pattern" "$TARGET" 2>/dev/null || true
        fi
    fi
done
