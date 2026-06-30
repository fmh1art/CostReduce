#!/usr/bin/env bash
set -euo pipefail

# quick_map - Generate a compact tree view of project structure with file sizes and extension stats.
# Usage: quick_map [directory] [max_depth=4] [options]
# Options:
#   --filter=GLOBS    Show only specific file types (comma-separated globs)

DIR="."
MAX_DEPTH=4
FILTER=""
POS_ARGS=()

# Collect positional args first
while [[ $# -gt 0 ]]; do
    case "$1" in
        --filter=*)
            FILTER="${1#*=}"
            shift
            ;;
        -f)
            FILTER="$2"
            shift 2
            ;;
        --filter)
            FILTER="$2"
            shift 2
            ;;
        -*)
            echo "Unknown option: $1" >&2
            exit 1
            ;;
        *)
            POS_ARGS+=("$1")
            shift
            ;;
    esac
done

# Parse positional args
if [[ ${#POS_ARGS[@]} -ge 1 ]]; then
    DIR="${POS_ARGS[0]}"
fi
if [[ ${#POS_ARGS[@]} -ge 2 ]]; then
    MAX_DEPTH="${POS_ARGS[1]}"
fi

if [[ -n "$FILTER" ]]; then
    IFS=',' read -ra GLOBS <<< "$FILTER"
    FIND_EXPR=()
    FIRST=true
    for glob in "${GLOBS[@]}"; do
        if [[ "$FIRST" == true ]]; then
            FIND_EXPR+=(-name "$glob")
            FIRST=false
        else
            FIND_EXPR+=(-o -name "$glob")
        fi
    done
    find "$DIR" -maxdepth "$MAX_DEPTH" \( "${FIND_EXPR[@]}" \) ! -path "*/.git/*" ! -path "*/node_modules/*" 2>/dev/null | sort | head -100
else
    find "$DIR" -maxdepth "$MAX_DEPTH" ! -path "*/.git/*" ! -path "*/node_modules/*" 2>/dev/null | sort | head -100
fi
