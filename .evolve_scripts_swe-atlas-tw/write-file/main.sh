#!/usr/bin/env bash
set -euo pipefail

# write-file: Write or append content to a file, creating parent directories.
# Replaces cat > file << 'EOF' heredoc patterns with a single step.
# Usage:
#   write-file [--append|-a] <filepath> <content...>
#   echo "<content>" | write-file [--append|-a] <filepath>
#   write-file [--append|-a] <filepath> << 'EOF'
#        ...content...
#        EOF

APPEND=false
FILEPATH=""
CONTENT=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --append|-a)
      APPEND=true
      shift
      ;;
    -*)
      echo "Unknown option: $1" >&2
      echo "Usage: $0 [--append|-a] <filepath> [content...]" >&2
      exit 1
      ;;
    *)
      if [[ -z "$FILEPATH" ]]; then
        FILEPATH="$1"
      else
        CONTENT+=("$1")
      fi
      shift
      ;;
  esac
done

if [[ -z "$FILEPATH" ]]; then
  echo "Usage: $0 [--append|-a] <filepath> [content...]" >&2
  echo "  If content is omitted, reads from stdin." >&2
  exit 1
fi

# Create parent directory if needed (only for non-append or new files)
PARENT_DIR="$(dirname "$FILEPATH")"
if [[ ! -d "$PARENT_DIR" ]]; then
  mkdir -p "$PARENT_DIR"
fi

if [[ ${#CONTENT[@]} -gt 0 ]]; then
  # Content from arguments
  if $APPEND; then
    printf '%s\n' "${CONTENT[@]}" >> "$FILEPATH"
  else
    printf '%s\n' "${CONTENT[@]}" > "$FILEPATH"
  fi
elif [[ ! -t 0 ]]; then
  # Content from stdin (piped or heredoc)
  if $APPEND; then
    cat >> "$FILEPATH"
  else
    cat > "$FILEPATH"
  fi
else
  echo "Error: No content provided. Pipe content or pass as arguments." >&2
  exit 1
fi

echo "Wrote $(wc -l < "$FILEPATH") lines to $FILEPATH"
