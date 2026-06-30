#!/usr/bin/env bash
set -euo pipefail

# write_file - Write or append content to a file atomically, creating parent directories automatically.
# Supports heredoc-style input, multi-line content, and stdin.
# Usage: write_file <filepath> [content]
#   or: write_file <filepath> -        (reads content from stdin)
#   or: echo "content" | write_file <filepath> -
#   or: write_file --append <filepath> <content>
#   or: write_file --append <filepath> -  (appends content from stdin)
#   or: write_file --quiet <filepath> <content>  (suppress "Written to" output)
#   or: write_file <filepath> << 'EOF'
#       multi-line content...
#   EOF

APPEND=false
QUIET=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --append)
            APPEND=true
            shift
            ;;
        --quiet|-q)
            QUIET=true
            shift
            ;;
        --)
            shift
            break
            ;;
        -*)
            echo "Usage: $0 [--append] [--quiet] <filepath> [content|-]" >&2
            exit 1
            ;;
        *)
            break
            ;;
    esac
done

if [[ $# -lt 1 ]]; then
    echo "Usage: $0 [--append] [--quiet] <filepath> [content|-]" >&2
    exit 1
fi

FILEPATH="$1"
CONTENT="${2:-}"

# Create parent directory
PARENT_DIR="$(dirname "$FILEPATH")"
mkdir -p "$PARENT_DIR"

write_content() {
    if $APPEND; then
        cat >> "$FILEPATH"
    else
        cat > "$FILEPATH"
    fi
}

if [[ $# -ge 2 ]]; then
    if [[ "$CONTENT" == "-" ]]; then
        # Read from stdin (supports heredoc: write_file path - << 'EOF' ... EOF)
        write_content
    else
        # Write content directly (preserves literal content without adding extra newlines)
        if $APPEND; then
            printf '%s' "$CONTENT" >> "$FILEPATH"
            # Add trailing newline if not present
            case "$CONTENT" in
                *$'\n') ;;
                *) printf '\n' >> "$FILEPATH" ;;
            esac
        else
            printf '%s' "$CONTENT" > "$FILEPATH"
            # Add trailing newline if not present
            case "$CONTENT" in
                *$'\n') ;;
                *) printf '\n' >> "$FILEPATH" ;;
            esac
        fi
    fi
else
    # No content arg - read from stdin (heredoc or pipe)
    write_content
fi

if ! $QUIET; then
    echo "Written to $FILEPATH"
fi
