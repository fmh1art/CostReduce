#!/usr/bin/env bash
set -euo pipefail

# batch-replace: Perform multiple string replacements in a file in one call
# Usage: batch-replace [--regex] <file> <old1> <new1> [old2 new2 ...]

USE_REGEX=false
FILE=""
PAIRS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --regex)
      USE_REGEX=true
      shift
      ;;
    -*)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
    *)
      if [[ -z "$FILE" ]]; then
        FILE="$1"
      else
        PAIRS+=("$1")
      fi
      shift
      ;;
  esac
done

if [[ -z "$FILE" || ${#PAIRS[@]} -lt 2 || $((${#PAIRS[@]} % 2)) -ne 0 ]]; then
  echo "Usage: $0 [--regex] <file> <old1> <new1> [old2 new2 ...]" >&2
  exit 1
fi

if [[ ! -f "$FILE" ]]; then
  echo "Error: File not found: $FILE" >&2
  exit 1
fi

# Read file content
content=$(cat "$FILE")

# Apply replacements
for ((i=0; i<${#PAIRS[@]}; i+=2)); do
  old="${PAIRS[$i]}"
  new="${PAIRS[$((i+1))]}"
  if $USE_REGEX; then
    content=$(echo "$content" | sed "s/$old/$new/g")
  else
    # Escape for sed literal replacement
    old_escaped=$(echo "$old" | sed 's/[\/&]/\\&/g')
    new_escaped=$(echo "$new" | sed 's/[\/&]/\\&/g')
    content=$(echo "$content" | sed "s/$old_escaped/$new_escaped/g")
  fi
done

# Write back
echo "$content" > "$FILE"
echo "Applied ${#PAIRS[@]} replacements to $FILE"
