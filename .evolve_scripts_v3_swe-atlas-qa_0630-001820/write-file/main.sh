#!/bin/bash
# Write file with automatic parent directory creation, supporting append mode, backup,
# or read+transform from a source file with sed.
# Usage: write-file/main.sh [--append|-a] [--backup|-b] <filepath> [content...]
#   or:  echo "content" | write-file/main.sh [--append|-a] [--backup|-b] <filepath>
#   or:  write-file/main.sh --read=SOURCE --sed='s/from/to/g' [--backup|-b] <filepath>
#   or:  write-file/main.sh --read=SOURCE --sed='s/from/to/g' --grep-exclude=PATTERN --append-line=LINE <filepath>
#   or:  write-file/main.sh --read=SOURCE --sed='s/from/to/g' --append <filepath>
#   --backup|-b: Create a .bak backup of the destination file if it already exists
#   --grep-exclude=PATTERN: When --read is used, exclude lines matching this regex (replaces | grep -v chains)
#   --append-line=LINE: Append extra line(s) after transformation (repeatable, collapses echo >> chains)

append_mode=false
backup_mode=false
read_file=""
sed_pattern=""
grep_exclude=""
append_lines=()

# Parse options
while [[ $# -gt 0 ]]; do
  case "$1" in
    --append|-a) append_mode=true; shift ;;
    --backup|-b) backup_mode=true; shift ;;
    --read=*) read_file="${1#*=}"; shift ;;
    --sed=*) sed_pattern="${1#*=}"; shift ;;
    --grep-exclude=*) grep_exclude="${1#*=}"; shift ;;
    --append-line=*) append_lines+=("${1#*=}"); shift ;;
    *) break ;;
  esac
done

filepath="$1"
shift

if [ -z "$filepath" ]; then
  echo "Error: file path is required" >&2
  echo "Usage: write-file/main.sh [--append|-a] [--backup|-b] [--read=FILE] [--sed='s/from/to/g'] [--grep-exclude=PATTERN] [--append-line=LINE] <filepath> [content...]" >&2
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
  # Read from source file, optionally transform with sed and/or grep-exclude
  if [ ! -f "$read_file" ]; then
    echo "Error: source file '$read_file' not found" >&2
    exit 1
  fi

  tmpfile=$(mktemp /tmp/write-file.XXXXXX)

  # Start with source content, apply sed if specified
  if [ -n "$sed_pattern" ]; then
    sed "$sed_pattern" "$read_file" > "$tmpfile"
  else
    cp "$read_file" "$tmpfile"
  fi

  # Apply grep -v exclusion if specified
  if [ -n "$grep_exclude" ]; then
    tmp2=$(mktemp /tmp/write-file.XXXXXX)
    grep -vE "$grep_exclude" "$tmpfile" > "$tmp2" 2>/dev/null
    mv "$tmp2" "$tmpfile"
  fi

  # Append extra lines if specified
  for line in "${append_lines[@]}"; do
    echo "$line" >> "$tmpfile"
  done

  # Write to destination
  if [ "$append_mode" = true ]; then
    cat "$tmpfile" >> "$filepath"
    rm -f "$tmpfile"
  else
    mv "$tmpfile" "$filepath"
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
