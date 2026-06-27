#!/bin/bash
# find-and-read: Search for pattern(s) in file(s) with context, or read specific line ranges.
# Usage:
#   find-and-read/main.sh <file> <pattern> [context_lines]
#   find-and-read/main.sh <file> -e <pat1> [-e <pat2> ...] [context_lines]
#   find-and-read/main.sh -r <directory> <pattern> [context_lines] [--include=<glob>]
#   find-and-read/main.sh <file:start-end> [file:start-end...]
#   find-and-read/main.sh --head-tail=N <file> [file...]
#   find-and-read/main.sh --head=N <file> [file...]
#   find-and-read/main.sh --tail=N <file> [file...]
#   find-and-read/main.sh <file> --between <start-pat> <end-pat>

set -euo pipefail

# Helper: pipe through cat -A when --show-control is active
pipe_output() {
  if [ "$show_control" = true ]; then
    cat -A
  else
    cat
  fi
}

recursive=false
include_pat=""
mode="search"  # search, lines, head-tail, head, tail, between

auto_detect_lines=false
# Check if any positional arg matches file:start-end format (auto-detect)
for arg in "$@"; do
  if [[ "$arg" == *:* ]] && [[ "$arg" != --* ]] && [[ "$arg" != -* ]]; then
    # Could be file:start-end - check if the range part is numeric
    range_part="${arg#*:}"
    if [[ "$range_part" =~ ^[0-9]+-[0-9]+$ ]]; then
      auto_detect_lines=true
      break
    fi
  fi
done
lines_count=0
show_number=false
show_control=false
files_or_ranges=()
context=5
multi_patterns=()
between_start=""
between_end=""
grep_A=""  # after context (grep -A)
grep_B=""  # before context (grep -B)
grep_C=""  # context both sides (grep -C)

# Parse options
while [ $# -gt 0 ]; do
  case "$1" in
    -r|--recursive)
      recursive=true
      shift
      ;;
    --include=*)
      include_pat="${1#*=}"
      shift
      ;;
    --lines)
      mode="lines"
      shift
      ;;
    --head-tail=*)
      mode="head-tail"
      lines_count="${1#*=}"
      shift
      ;;
    --head=*)
      mode="head"
      lines_count="${1#*=}"
      shift
      ;;
    --tail=*)
      mode="tail"
      lines_count="${1#*=}"
      shift
      ;;
    --between)
      mode="between"
      shift
      if [ $# -lt 2 ]; then
        echo "ERROR: --between requires <start-pattern> <end-pattern>" >&2
        exit 1
      fi
      between_start="$1"
      between_end="$2"
      shift 2
      ;;
    -n|--number)
      show_number=true
      shift
      ;;
    --show-control)
      show_control=true
      shift
      ;;
    --no-show-control)
      show_control=false
      shift
      ;;
    -e|--pattern)
      if [ $# -lt 2 ]; then
        echo "ERROR: -e requires a pattern argument" >&2
        exit 1
      fi
      multi_patterns+=("$2")
      shift 2
      ;;
    -A)
      if [ $# -lt 2 ]; then
        echo "ERROR: -A requires a number" >&2
        exit 1
      fi
      grep_A="$2"
      shift 2
      ;;
    -B)
      if [ $# -lt 2 ]; then
        echo "ERROR: -B requires a number" >&2
        exit 1
      fi
      grep_B="$2"
      shift 2
      ;;
    -C)
      if [ $# -lt 2 ]; then
        echo "ERROR: -C requires a number" >&2
        exit 1
      fi
      grep_C="$2"
      shift 2
      ;;
    -h|--help)
      echo "Usage: $0 [options] <file> <pattern> [context_lines]"
      echo "       $0 <file> -e <pat1> [-e <pat2> ...] [context_lines]"
      echo "       $0 -r <directory> <pattern> [context_lines] [--include=<glob>]"
      echo "       $0 <file:start-end> [file:start-end...]"
      echo "       $0 --head-tail=N <file> [file...]"
      echo "       $0 --head=N <file> [file...]"
      echo "       $0 --tail=N <file> [file...]"
      echo "       $0 <file> --between <start-pat> <end-pat>"
      echo "Options:"
      echo "  -r, --recursive        Search recursively in a directory"
      echo "  --include=<glob>       File filter for recursive search"
      echo "  <file:start-end>       Read specific line ranges (auto-detected)"
      echo "  --head-tail=N          Show first N and last N lines"
      echo "  --head=N               Show first N lines"
      echo "  --tail=N               Show last N lines"
      echo "  --between <a> <b>      Read lines between two patterns (inclusive)"
      echo "  -e, --pattern <pat>    Additional search pattern (repeatable for multi-pattern)"
      echo "  -A <N>                 Show N lines after each match (grep -A style)"
      echo "  -B <N>                 Show N lines before each match (grep -B style)"
      echo "  -C <N>                 Show N lines before and after each match (grep -C style)"
      echo "  -n, --number           Show line numbers"
      echo "  --show-control        Show control characters (tabs as ^I, etc.) like cat -A"
      exit 0
      ;;
    -*)
      echo "ERROR: Unknown option: $1" >&2
      exit 1
      ;;
    *)
      files_or_ranges+=("$1")
      shift
      ;;
  esac
done

# ---- Mode: between (read between two patterns) ----
if [ "$mode" = "between" ]; then
  if [ ${#files_or_ranges[@]} -ne 1 ]; then
    echo "ERROR: --between requires exactly one file argument" >&2
    exit 1
  fi
  file="${files_or_ranges[0]}"
  if [ ! -f "$file" ]; then
    echo "ERROR: File not found: $file" >&2
    exit 1
  fi

  # Find start line
  start_line=$(grep -n "$between_start" "$file" 2>/dev/null | head -1 | cut -d: -f1 || echo "")
  if [ -z "$start_line" ]; then
    echo "ERROR: Start pattern not found: $between_start" >&2
    exit 1
  fi

  # Find end line (the *last* occurrence for the end pattern)
  end_line=$(grep -n "$between_end" "$file" 2>/dev/null | tail -1 | cut -d: -f1 || echo "")
  if [ -z "$end_line" ]; then
    echo "ERROR: End pattern not found: $between_end" >&2
    exit 1
  fi

  if [ "$start_line" -gt "$end_line" ]; then
    echo "ERROR: Start pattern appears after end pattern ($start_line > $end_line)" >&2
    exit 1
  fi

  echo "=== $file (between \"$between_start\" and \"$between_end\", lines $start_line-$end_line) ==="
  awk -v start="$start_line" -v end="$end_line" 'NR>=start && NR<=end {printf "%6d\t%s\n", NR, $0}' "$file" | pipe_output
  exit 0
fi

# ---- Mode: lines (read specific ranges) ----
if [ "$mode" = "lines" ] || [ "$auto_detect_lines" = true ]; then
  if [ "$mode" != "lines" ]; then
    mode="lines"
  fi
  if [ ${#files_or_ranges[@]} -eq 0 ]; then
    echo "ERROR: At least one file:start-end argument required" >&2
    exit 1
  fi
  for arg in "${files_or_ranges[@]}"; do
    if [[ "$arg" != *:* ]]; then
      echo "ERROR: Expected format file:start-end, got: $arg" >&2
      exit 1
    fi
    file="${arg%%:*}"
    range="${arg#*:}"
    start="${range%-*}"
    end="${range#*-}"
    if [ ! -f "$file" ]; then
      echo "ERROR: File not found: $file" >&2
      exit 1
    fi
    echo "=== $file ($start-$end) ==="
    awk -v start="$start" -v end="$end" 'NR>=start && NR<=end {printf "%6d\t%s\n", NR, $0}' "$file" | pipe_output
    echo ""
  done
  exit 0
fi

# ---- Mode: head-tail, head, or tail ----
if [ "$mode" = "head-tail" ] || [ "$mode" = "head" ] || [ "$mode" = "tail" ]; then
  if [ ${#files_or_ranges[@]} -eq 0 ]; then
    echo "ERROR: At least one file argument required" >&2
    exit 1
  fi
  for file in "${files_or_ranges[@]}"; do
    if [ ! -f "$file" ]; then
      echo "ERROR: File not found: $file" >&2
      exit 1
    fi
    total=$(wc -l < "$file" 2>/dev/null || echo 0)
    if [ "$mode" = "head-tail" ]; then
      echo "=== $file (first $lines_count / last $lines_count of $total lines) ==="
      if [ "$total" -le $((lines_count * 2)) ]; then
        if [ "$show_number" = true ]; then nl -ba "$file" | pipe_output; else cat "$file" | pipe_output; fi
      else
        if [ "$show_number" = true ]; then
          echo "--- first $lines_count ---"
          nl -ba "$file" | head -n "$lines_count" | pipe_output
          echo "... ($((total - lines_count * 2)) lines omitted) ..."
          echo "--- last $lines_count ---"
          nl -ba "$file" | tail -n "$lines_count" | pipe_output
        else
          echo "--- first $lines_count ---"
          head -n "$lines_count" "$file" | pipe_output
          echo "... ($((total - lines_count * 2)) lines omitted) ..."
          echo "--- last $lines_count ---"
          tail -n "$lines_count" "$file" | pipe_output
        fi
      fi
    elif [ "$mode" = "head" ]; then
      echo "=== $file (first $lines_count of $total lines) ==="
      if [ "$show_number" = true ]; then
        nl -ba "$file" | head -n "$lines_count" | pipe_output
      else
        head -n "$lines_count" "$file" | pipe_output
      fi
    elif [ "$mode" = "tail" ]; then
      echo "=== $file (last $lines_count of $total lines) ==="
      if [ "$show_number" = true ]; then
        nl -ba "$file" | tail -n "$lines_count" | pipe_output
      else
        tail -n "$lines_count" "$file" | pipe_output
      fi
    fi
    echo ""
  done
  exit 0
fi

# ---- Mode: search ----
# Build grep pattern: if -e patterns given, use them; otherwise use first positional pattern
search_patterns=()
if [ ${#multi_patterns[@]} -gt 0 ]; then
  search_patterns=("${multi_patterns[@]}")
fi

# Determine context from -A/-B/-C flags if set
if [ -n "$grep_A" ]; then
  # grep -A style: only show lines after match
  context="$grep_A"
elif [ -n "$grep_B" ]; then
  context="$grep_B"
elif [ -n "$grep_C" ]; then
  context="$grep_C"
fi

if [ "$recursive" = true ]; then
  # Recursive directory search mode
  min_args=2
  if [ ${#multi_patterns[@]} -gt 0 ]; then
    min_args=1  # just dir needed, patterns from -e
  fi
  if [ ${#files_or_ranges[@]} -lt $min_args ]; then
    echo "Usage: $0 -r <directory> <pattern> [context_lines] [--include=<glob>]" >&2
    echo "       $0 -r <directory> -e <pat1> [-e <pat2> ...] [context_lines]" >&2
    exit 1
  fi
  
  dir="${files_or_ranges[0]}"
  if [ ${#multi_patterns[@]} -eq 0 ]; then
    pattern="${files_or_ranges[1]}"
    context="${files_or_ranges[2]:-5}"
    search_patterns=("$pattern")
  else
    context="${files_or_ranges[1]:-5}"
  fi
  
  if [ ! -d "$dir" ]; then
    echo "ERROR: Directory not found: $dir" >&2
    exit 1
  fi
  
  # Build grep args with -A/-B/-C flags if specified
  grep_args=(-rn)
  if [ -n "$grep_A" ]; then
    grep_args+=(-A "$grep_A")
  elif [ -n "$grep_B" ]; then
    grep_args+=(-B "$grep_B")
  elif [ -n "$grep_C" ]; then
    grep_args+=(-C "$grep_C")
  fi
  for pat in "${search_patterns[@]}"; do
    grep_args+=(-e "$pat")
  done
  grep_args+=("$dir")
  if [ -n "$include_pat" ]; then
    grep_args+=(--include="$include_pat")
  fi
  
  matches=$(grep "${grep_args[@]}" 2>/dev/null || true)
  if [ -z "$matches" ]; then
    echo "No matches found for pattern(s) in $dir" >&2
    exit 0
  fi
  
  # If -A/-B/-C was specified, just show grep output directly
  if [ -n "$grep_A" ] || [ -n "$grep_B" ] || [ -n "$grep_C" ]; then
    echo "$matches"
    exit 0
  fi
  
  echo "$matches" | while IFS=: read -r file line_num line_content; do
    if [ ! -f "$file" ]; then
      continue
    fi
    start=$((line_num - context))
    [ "$start" -lt 1 ] && start=1
    end=$((line_num + context))
    total_lines=$(wc -l < "$file" 2>/dev/null || echo 0)
    [ "$end" -gt "$total_lines" ] && end="$total_lines"
    
    echo "=== $file:$line_num (context $context lines) ==="
    sed -n "${start},${end}p" "$file" 2>/dev/null | pipe_output || true
    echo ""
  done
  
else
  # Single file mode
  min_args=2
  if [ ${#multi_patterns[@]} -gt 0 ]; then
    min_args=1  # just file needed, patterns from -e
  fi
  if [ ${#files_or_ranges[@]} -lt $min_args ]; then
    echo "Usage: $0 <file> <pattern> [context_lines]" >&2
    echo "       $0 <file> -e <pat1> [-e <pat2> ...] [context_lines]" >&2
    exit 1
  fi
  
  file="${files_or_ranges[0]}"
  if [ ${#multi_patterns[@]} -eq 0 ]; then
    pattern="${files_or_ranges[1]}"
    context="${files_or_ranges[2]:-5}"
    search_patterns=("$pattern")
  else
    context="${files_or_ranges[1]:-5}"
  fi
  
  if [ ! -f "$file" ]; then
    echo "ERROR: File not found: $file" >&2
    exit 1
  fi
  
  # Build grep command with all patterns and -A/-B/-C flags if specified
  grep_args=(-n)
  if [ -n "$grep_A" ]; then
    grep_args+=(-A "$grep_A")
  elif [ -n "$grep_B" ]; then
    grep_args+=(-B "$grep_B")
  elif [ -n "$grep_C" ]; then
    grep_args+=(-C "$grep_C")
  fi
  for pat in "${search_patterns[@]}"; do
    grep_args+=(-e "$pat")
  done
  grep_args+=("$file")
  
  matches=$(grep "${grep_args[@]}" || true)
  if [ -z "$matches" ]; then
    echo "No matches found for pattern(s) in $file" >&2
    exit 0
  fi
  
  # If -A/-B/-C was specified, just show grep output directly
  if [ -n "$grep_A" ] || [ -n "$grep_B" ] || [ -n "$grep_C" ]; then
    echo "$matches"
    exit 0
  fi
  
  echo "$matches" | while IFS=: read -r line_num line_content; do
    start=$((line_num - context))
    [ "$start" -lt 1 ] && start=1
    end=$((line_num + context))
    total_lines=$(wc -l < "$file")
    [ "$end" -gt "$total_lines" ] && end="$total_lines"
    
    echo "=== $file:$line_num (context $context lines) ==="
    sed -n "${start},${end}p" "$file" | pipe_output
    echo ""
  done
fi
