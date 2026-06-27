#!/usr/bin/env bash
set -euo pipefail

# create_file - Create a new file with given content
# Usage: main.sh <file_path> [content]
#   <file_path> - Path to the new file (parent directories are auto-created)
#   [content]   - Content to write. Can be:
#                 - Literal text with escape sequences (use quotes, \n for newlines, \t for tabs)
#                 - Path to a content file prefixed with @ (e.g., @/tmp/snippet.txt)
#                 - '-' to read from stdin
#                 - Empty or omitted: creates an empty file
#
# Escape sequences in content string: \n (newline), \t (tab), \\ (backslash)

file_path="$1"
content_src="${2:-}"

# Create parent directory if needed
parent_dir="$(dirname "$file_path")"
if [ "$parent_dir" != "." ]; then
    mkdir -p "$parent_dir"
fi

if [ "$content_src" = "-" ]; then
    # Read from stdin
    cat > "$file_path"
elif [ "${content_src:0:1}" = "@" ]; then
    # Read from file
    src_file="${content_src:1}"
    if [ ! -f "$src_file" ]; then
        echo "Error: Source file '$src_file' not found."
        exit 1
    fi
    cp "$src_file" "$file_path"
elif [ -z "$content_src" ]; then
    # Empty content - create empty file
    : > "$file_path"
else
    # Write literal content with escape sequence interpretation
    printf '%b' "$content_src" > "$file_path"
fi

echo "Created $(wc -l < "$file_path" | tr -d ' ') lines: $file_path"
