#!/bin/bash
# Read specific line ranges or head of a file (supports multiple files).
# Usage: main.sh [--numbered] <file> [start_line] [end_line]
#        main.sh [--numbered] <file1> <file2> ...   (head -200 of each file)
#        main.sh [--numbered] <file> <start>-<end>  (line range like "30-60")
# Without line args, shows head -200. With one numeric arg, shows that single line.
# With two numeric args, shows that line range.
# --numbered or -n: prefix output with line numbers via nl -ba

set -euo pipefail

NUMBERED=false
ARGS=()

for arg in "$@"; do
  if [ "$arg" = "--numbered" ] || [ "$arg" = "-n" ]; then
    NUMBERED=true
  else
    ARGS+=("$arg")
  fi
done

set -- "${ARGS[@]}"

if [ $# -eq 0 ]; then
  echo "Usage: main.sh [--numbered|-n] <file> [start_line] [end_line]" >&2
  echo "       main.sh [--numbered|-n] <file1> <file2> ..." >&2
  exit 1
fi

show_lines() {
  local file="$1" start="$2" end="$3"
  if [ "$NUMBERED" = true ]; then
    if [ -n "$end" ]; then
      nl -ba "$file" | sed -n "${start},${end}p"
    else
      nl -ba "$file" | sed -n "${start}p"
    fi
  else
    if [ -n "$end" ]; then
      sed -n "${start},${end}p" "$file"
    else
      sed -n "${start}p" "$file"
    fi
  fi
}

FILE="$1"
shift

# Case: remaining args look like a range "30-60"
if [ $# -ge 1 ] && [[ "$1" =~ ^[0-9]+-[0-9]+$ ]]; then
  RANGE="${1/-/,}"
  if [ "$NUMBERED" = true ]; then
    nl -ba "$FILE" | sed -n "${RANGE}p"
  else
    sed -n "${RANGE}p" "$FILE"
  fi
  exit 0
fi

# Case: two numeric args (start and end)
if [ $# -ge 2 ] && [[ "$1" =~ ^[0-9]+$ ]] && [[ "$2" =~ ^[0-9]+$ ]]; then
  show_lines "$FILE" "$1" "$2"
  exit 0
fi

# Case: one numeric arg (single line)
if [ $# -ge 1 ] && [[ "$1" =~ ^[0-9]+$ ]]; then
  show_lines "$FILE" "$1" ""
  exit 0
fi

# Case: multiple files or no numeric args - show head -200 of each
if [ "$NUMBERED" = true ]; then
  for f in "$FILE" "$@"; do
    echo "=== $f ==="
    nl -ba "$f" | head -200
    echo ""
  done
else
  for f in "$FILE" "$@"; do
    echo "=== $f ==="
    head -200 "$f"
    echo ""
  done
fi
