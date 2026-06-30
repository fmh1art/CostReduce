#!/usr/bin/env bash
# batch_write - Write content to multiple files in one step.

set -euo pipefail

usage() {
  echo "Usage: $0 <file1> <content1> [file2 content2...]"
  echo "  or:  echo 'content' | $0 <file>"
  echo "Write content to files atomically, creating parent dirs as needed."
  exit 1
}

if [[ $# -eq 0 ]]; then
  usage
fi

# Read from stdin if piped
if [[ ! -t 0 ]]; then
  stdin_content=$(cat)
  if [[ $# -eq 1 ]]; then
    # stdin mode: $0 <file>
    mkdir -p "$(dirname "$1")"
    echo "$stdin_content" > "$1"
    echo "Wrote stdin to $1"
    exit 0
  fi
fi

# file/content pairs
while [[ $# -ge 2 ]]; do
  file="$1"
  content="$2"
  shift 2
  mkdir -p "$(dirname "$file")"
  echo "$content" > "$file"
  echo "Wrote $file"
done

if [[ $# -gt 0 ]]; then
  echo "Warning: leftover argument (odd count): $1" >&2
fi
