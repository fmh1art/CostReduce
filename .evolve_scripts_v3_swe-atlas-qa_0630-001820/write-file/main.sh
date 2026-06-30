#!/bin/bash
# Write file with automatic parent directory creation, supporting append mode, backup,
# or read+transform from a source file with sed.
# Usage: write-file/main.sh [--append|-a] [--backup|-b] <filepath> [content...]
#   or:  echo "content" | write-file/main.sh [--append|-a] [--backup|-b] <filepath>
#   or:  write-file/main.sh --read=SOURCE --sed='s/from/to/g' [--backup|-b] <filepath>
#   or:  write-file/main.sh --read=SOURCE --sed='s/from/to/g' --append <filepath>
#   --backup|-b: Create a .bak backup of the destination file if it already exists

append_mode=false
backup_mode=false
read_file=""
sed_pattern=""

# Parse options
while [[ $# -gt 0 ]]; do
  case "$1" in
    --append|-a) append_mode=true; shift ;;
    --backup|-b) backup_mode=true; shift ;;
    --read=*) read_file="${1#*=}"; shift ;;
    --sed=*) sed_pattern="${1#*=}"; shift ;;
    *) break ;;
  esac
done

filepath="$1"
shift

if [ -z "$filepath" ]; then
  echo "Error: file path is required" >&2
  echo "Usage: write-file/main.sh [--append|-a] [--backup|-b] [--read=FILE] [--sed='s/from/to/g'] <filepath> [content...]" >&2
  exit 1
fi

# Create parent directory
mkdir -p "$(dirname "$filepath")"

# Backup existing file if --backup is set
if [ "$backup_mode" = true ] && [ -f "$filepath" ]; then
  cp "$filepath" "${filepath}.bak"
  echo "Backup created: ${filepath}.bak"
fi

if [ -n "$read_file" ]; then
  # Read from source file, optionally transform with sed
  if [ ! -f "$read_file" ]; then
    echo "Error: source file '$read_file' not found" >&2
    exit 1
  fi
  if [ -n "$sed_pattern" ]; then
    # Use sed -i for in-place edits when source == dest; otherwise pipe with redirect
    if [ "$read_file" = "$filepath" ]; then
      # In-place edit: read entire content first to avoid truncation
      content=$(cat "$read_file")
      echo "$content" | sed "$sed_pattern" > "$filepath"
    else
      if [ "$append_mode" = true ]; then
        sed "$sed_pattern" "$read_file" >> "$filepath"
      else
        sed "$sed_pattern" "$read_file" > "$filepath"
      fi
    fi
  else
    if [ "$append_mode" = true ]; then
      cat "$read_file" >> "$filepath"
    else
      cp "$read_file" "$filepath"
    fi
  fi
elif [ $# -gt 0 ]; then
  # Write content from arguments
  if [ "$append_mode" = true ]; then
    echo "$*" >> "$filepath"
  else
    echo "$*" > "$filepath"
  fi
elif [ ! -t 0 ]; then
  # Write content from stdin (piped)
  if [ "$append_mode" = true ]; then
    cat >> "$filepath"
  else
    cat > "$filepath"
  fi
else
  echo "Error: no content provided. Provide content as args, pipe to stdin, or use --read=FILE." >&2
  exit 1
fi

echo "Written $(wc -c < "$filepath") bytes to $filepath"
