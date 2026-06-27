#!/usr/bin/env bash
# write-file: Write content to a file atomically (or append), creating parent directories automatically.
# Usage: write-file [--append] <filepath> [<content>]  OR  write-file <filepath> - (read from stdin)

set -euo pipefail

show_help() {
    cat << 'HELP_EOF'
Usage: write-file [--append] <filepath> [<content>]
       write-file <filepath> -   (read content from stdin)

Writes content to a file atomically, creating parent directories as needed.
Use --append to append to an existing file instead of overwriting.
If content is empty or "-", reads from stdin.

Options:
  --append, -a   Append to file instead of overwriting
  --help, -h     Show this help

Examples:
  write-file /tmp/out.txt "Hello World"
  write-file --append /tmp/out.txt "Another line"
  write-file /project/main.py "print('hello')\nprint('world')"
  echo "content" | write-file /tmp/out.txt -
HELP_EOF
    exit 0
}

APPEND=""
FILEPATH=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --help|-h) show_help ;;
        --append|-a) APPEND="1" ;;
        *) 
            if [[ -z "$FILEPATH" ]]; then
                FILEPATH="$1"
            else
                # Second positional arg = content
                CONTENT="$1"
            fi
            ;;
    esac
    shift
done

[[ -z "$FILEPATH" ]] && { echo "Error: No filepath specified" >&2; show_help; }

# Create parent directories
mkdir -p "$(dirname "$FILEPATH")"

if [[ -n "${CONTENT:-}" ]]; then
    if [[ "$CONTENT" == "-" ]]; then
        # Read from stdin
        if [[ -n "$APPEND" ]]; then
            cat >> "$FILEPATH"
        else
            cat > "$FILEPATH"
        fi
    else
        if [[ -n "$APPEND" ]]; then
            printf '%s' "$CONTENT" >> "$FILEPATH"
        else
            printf '%s' "$CONTENT" > "$FILEPATH"
        fi
    fi
else
    # Read from stdin
    if [[ -n "$APPEND" ]]; then
        cat >> "$FILEPATH"
    else
        cat > "$FILEPATH"
    fi
fi

echo "Wrote $FILEPATH"
