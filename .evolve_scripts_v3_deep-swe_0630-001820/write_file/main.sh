#!/usr/bin/env bash
set -euo pipefail

# write_file - Write content to a file atomically. Creates parent directories automatically.
# Usage: write_file [--dir=DIR] <filepath> [content]
#        write_file [--dir=DIR] -                    (read content from stdin)
#        write_file [--dir=DIR] <filepath> -         (read content from stdin, alias)

WORKDIR=""
FILEPATH=""

# Parse options
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dir=*)
            WORKDIR="${1#*=}"
            shift
            ;;
        --dir)
            WORKDIR="$2"
            shift 2
            ;;
        -*)
            echo "Usage: write_file [--dir=DIR] <filepath> [content]" >&2
            exit 1
            ;;
        *)
            break
            ;;
    esac
done

if [[ $# -lt 1 ]]; then
    echo "Usage: write_file [--dir=DIR] <filepath> [content]" >&2
    echo "       write_file [--dir=DIR] <filepath> -  (read content from stdin)" >&2
    exit 1
fi

# Change to working directory if specified
if [[ -n "$WORKDIR" ]]; then
    cd "$WORKDIR" || { echo "Error: Cannot cd to $WORKDIR" >&2; exit 1; }
fi

# First argument is always the filepath
# Special case: if first arg is "-", read filepath from second arg
if [[ "$1" == "-" ]]; then
    if [[ $# -lt 2 ]]; then
        echo "Usage: write_file - <filepath>" >&2
        exit 1
    fi
    FILEPATH="$2"
    mkdir -p "$(dirname "$FILEPATH")"
    cat > "$FILEPATH"
    echo "Written $(wc -c < "$FILEPATH") bytes to $FILEPATH"
    exit 0
fi

FILEPATH="$1"
shift

mkdir -p "$(dirname "$FILEPATH")"

if [[ $# -eq 0 || "$1" == "-" ]]; then
    # Read content from stdin (supports heredoc: write_file path << 'EOF')
    cat > "$FILEPATH"
    echo "Written $(wc -c < "$FILEPATH") bytes to $FILEPATH"
else
    # Write content from arguments
    printf '%s\n' "$*" > "$FILEPATH"
    echo "Written to $FILEPATH"
fi
