#!/usr/bin/env bash
# batch_read - Read multiple files or line ranges in one native tool call.

set -euo pipefail

SHOW_NUMBER=false
COUNT_ONLY=false
SHOW_NONPRINTABLE=false
HEAD_LINES=
TAIL_LINES=
LINES_RANGE=
DIR_PATH=
INCLUDE_GLOB=
EXCLUDE_GLOB=
DIR_TYPE=
FILES=()

usage() {
  cat >&2 <<'EOF'
Usage: $0 [--head=N|--tail=N] [--lines=start-end|num1,num2,...] [--number|-n] [--count|-c] [--show-nonprintable|-A] file1 [file2...]
       $0 file1:start-end [file2...]
       $0 --dir=PATH [--include=GLOB] [--exclude=GLOB] [--type=f|d]

Read files, optionally with line ranges (range or comma-separated), line numbers, line counts, or directory listing.
Comma-separated --lines lists multiple individual lines or ranges (e.g. --lines=60,64,100 or --lines=10-20,30,40-50).
--show-nonprintable|-A shows non-printable characters like cat -A (display $ at line ends, ^I for tabs, ^M for CR).
EOF
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --head=*)
      HEAD_LINES="${1#*=}"
      shift
      ;;
    --tail=*)
      TAIL_LINES="${1#*=}"
      shift
      ;;
    --lines=*)
      LINES_RANGE="${1#*=}"
      shift
      ;;
    --number|-n)
      SHOW_NUMBER=true
      shift
      ;;
    --count|-c)
      COUNT_ONLY=true
      shift
      ;;
    --show-nonprintable|-A)
      SHOW_NONPRINTABLE=true
      shift
      ;;
    --dir=*)
      DIR_PATH="${1#*=}"
      shift
      ;;
    --include=*)
      INCLUDE_GLOB="${1#*=}"
      shift
      ;;
    --exclude=*)
      EXCLUDE_GLOB="${1#*=}"
      shift
      ;;
    --type=*)
      DIR_TYPE="${1#*=}"
      shift
      ;;
    --help|-h)
      usage
      ;;
    -*)
      echo "Unknown option: $1" >&2
      usage
      ;;
    *)
      FILES+=("$1")
      shift
      ;;
  esac
done

# Handle --dir mode
if [[ -n "$DIR_PATH" ]]; then
  if [[ ! -d "$DIR_PATH" ]]; then
    echo "Error: directory not found: $DIR_PATH" >&2
    exit 1
  fi
  type_flag="f"
  if [[ -n "$DIR_TYPE" ]]; then
    type_flag="$DIR_TYPE"
  fi
  find_args=("$DIR_PATH" -type "$type_flag")
  if [[ -n "$INCLUDE_GLOB" ]]; then
    find_args+=(-name "$INCLUDE_GLOB")
  fi
  if [[ -n "$EXCLUDE_GLOB" ]]; then
    find_args+=(! -path "$EXCLUDE_GLOB")
  fi
  find "${find_args[@]}" 2>/dev/null | sort
  exit 0
fi

if [[ ${#FILES[@]} -eq 0 ]]; then
  usage
fi

# Process a single line spec (either "N" or "START-END")
print_line_or_range() {
  local file="$1"
  local spec="$2"
  local show_num="$3"
  local show_np="${4:-false}"
  local tmp
  if [[ "$spec" =~ ^([0-9]+)-([0-9]+)$ ]]; then
    local start="${BASH_REMATCH[1]}"
    local end="${BASH_REMATCH[2]}"
    if [[ "$show_num" == "true" ]]; then
      tmp=$(nl -ba "$file" 2>/dev/null | sed -n "${start},${end}p")
    else
      tmp=$(sed -n "${start},${end}p" "$file" 2>/dev/null)
    fi
  else
    local lineno="$spec"
    if [[ "$show_num" == "true" ]]; then
      tmp=$(nl -ba "$file" 2>/dev/null | sed -n "${lineno}p")
    else
      tmp=$(sed -n "${lineno}p" "$file" 2>/dev/null)
    fi
  fi
  if [[ "$show_np" == "true" ]]; then
    echo "$tmp" | cat -A
  else
    echo "$tmp"
  fi
}

process_file() {
  local file="$1"
  local range="$2"
  local show_num="$3"
  local count="$4"
  local head_n="$5"
  local tail_n="$6"
  local show_nonprintable="${7:-false}"

  if [[ "$count" == "true" ]]; then
    if [[ -n "$range" ]]; then
      local total=0
      IFS=',' read -ra parts <<< "$range"
      for part in "${parts[@]}"; do
        if [[ "$part" =~ ^([0-9]+)-([0-9]+)$ ]]; then
          local s="${BASH_REMATCH[1]}"
          local e="${BASH_REMATCH[2]}"
          total=$((total + e - s + 1))
        else
          total=$((total + 1))
        fi
      done
      echo "$total"
    elif [[ -n "$head_n" ]]; then
      head -n "$head_n" "$file" 2>/dev/null | wc -l | tr -d ' '
    elif [[ -n "$tail_n" ]]; then
      tail -n "$tail_n" "$file" 2>/dev/null | wc -l | tr -d ' '
    else
      wc -l < "$file" 2>/dev/null | tr -d ' '
    fi
    return
  fi

  if [[ -n "$range" ]]; then
    IFS=',' read -ra parts <<< "$range"
    for part in "${parts[@]}"; do
      print_line_or_range "$file" "$part" "$show_num" "$show_nonprintable"
    done
  elif [[ -n "$head_n" ]]; then
    if [[ "$show_num" == "true" ]]; then
      out=$(nl -ba "$file" 2>/dev/null | head -n "$head_n")
    else
      out=$(head -n "$head_n" "$file" 2>/dev/null)
    fi
    if [[ "$show_nonprintable" == "true" ]]; then
      echo "$out" | cat -A
    else
      echo "$out"
    fi
  elif [[ -n "$tail_n" ]]; then
    if [[ "$show_num" == "true" ]]; then
      out=$(nl -ba "$file" 2>/dev/null | tail -n "$tail_n")
    else
      out=$(tail -n "$tail_n" "$file" 2>/dev/null)
    fi
    if [[ "$show_nonprintable" == "true" ]]; then
      echo "$out" | cat -A
    else
      echo "$out"
    fi
  else
    if [[ "$show_num" == "true" ]]; then
      out=$(nl -ba "$file" 2>/dev/null)
    else
      out=$(cat "$file" 2>/dev/null)
    fi
    if [[ "$show_nonprintable" == "true" ]]; then
      echo "$out" | cat -A
    else
      echo "$out"
    fi
  fi
}

for arg in "${FILES[@]}"; do
  file="$arg"
  range="$LINES_RANGE"

  # Check if file has embedded :line range
  if [[ "$arg" =~ ^(.+):([0-9]+-[0-9]+)$ ]]; then
    file="${BASH_REMATCH[1]}"
    range="${BASH_REMATCH[2]}"
  fi

  if [[ ! -f "$file" ]]; then
    echo "Error: File not found: $file" >&2
    continue
  fi

  process_file "$file" "$range" "$SHOW_NUMBER" "$COUNT_ONLY" "$HEAD_LINES" "$TAIL_LINES" "$SHOW_NONPRINTABLE"
done
