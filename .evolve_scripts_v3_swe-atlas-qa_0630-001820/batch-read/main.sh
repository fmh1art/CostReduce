#!/bin/bash
# Batch read files - read one or more files with file headers, or all files in a directory
# Usage: batch-read/main.sh [--cd=DIR] [--lines=START-END[,START2-END2,...]] [--head=N] [--tail=N] [--offset=N] [--number] [--wc] [--dir=DIR] [--name=GLOB] [--try] [--grep=PATTERN] [--grep-after=N] [--grep-before=N] [--grep-invert] <file1> [file2 ...]
#   --lines: Single range (10-30) or comma-separated ranges (1-200,200-350,350-450) for reading multiple chunks from the same file in one step
#   Per-file ranges: Append :START-END to a file path (e.g., file.jsx:30-40) to read different ranges from different files in a single call, replacing sed -n 'X,Yp' chains
#   --try: If a file path does not exist, try common extensions (.js,.ts,.jsx,.tsx,.json,.py,.php,.css,.scss,.yml,.yaml,.md,.txt)
#   --dir=DIR: Read all files recursively in a directory (useful after find-files)
#   --name=GLOB: File name glob pattern filter when using --dir (e.g., *.ts, *.js, *.json; repeatable)
#   --wc: Show line count for each file (like wc -l) instead of file content
#   --max-depth=N: Max directory depth when using --dir (default: unlimited)
#   --safe: Auto-limit output to first 50 lines for files over 200 lines (prevents token waste from truncated observations)
#   --no-header: Suppress file header (===== filename =====) in output
#   --grep=PATTERN: Filter file content with grep pattern (replaces cat | grep chains)
#   --grep-context=N: Show N lines of context around each match (grep -C), sets both --grep-after and --grep-before
#   --grep-after=N: Show N lines after each match (grep -A)
#   --grep-before=N: Show N lines before each match (grep -B)
#   --grep-invert: Invert match (grep -v)

wc_flag=false
lines=""
head_n=""
tail_n=""
offset=""
number=false
workdir=""
dir_to_read=""
name_patterns=()
files=()
try_flag=false
max_depth=""
no_header=false
safe_mode=false
grep_pattern=""
grep_context=""
grep_after=""
grep_before=""
grep_invert=false

for arg in "$@"; do
  case "$arg" in
    --cd=*) workdir="${arg#*=}" ;;
    --lines=*) lines="${arg#*=}" ;;
    --head=*) head_n="${arg#*=}" ;;
    --tail=*) tail_n="${arg#*=}" ;;
    --offset=*) offset="${arg#*=}" ;;
    --dir=*) dir_to_read="${arg#*=}" ;;
    --name=*) name_patterns+=("${arg#*=}") ;;
    --number|--nl|-n) number=true ;;
    --wc) wc_flag=true ;;
    --try) try_flag=true ;;
    --grep=*) grep_pattern="${arg#*=}" ;;
    --grep-after=*) grep_after="${arg#*=}" ;;
    --grep-before=*) grep_before="${arg#*=}" ;;
    --grep-invert) grep_invert=true ;;
    --grep-context=*) grep_context="${arg#*=}" ;;
    --max-depth=*) max_depth="${arg#*=}" ;;
    --no-header) no_header=true ;;
    --safe) safe_mode=true ;;
    *) files+=("$arg") ;;
  esac
done

# Build grep flags for context
build_grep_flags() {
  local flags="-nE"
  if [ -n "$grep_context" ]; then
    flags="$flags -C $grep_context"
  fi
  if [ -n "$grep_after" ]; then
    flags="$flags -A $grep_after"
  fi
  if [ -n "$grep_before" ]; then
    flags="$flags -B $grep_before"
  fi
  if [ "$grep_invert" = true ]; then
    flags="$flags -v"
  fi
  printf '%s\n' "$flags"
}

# Print a specific line range from a file
print_range() {
  local f="$1" start="$2" end="$3" label="$4"
  if [ -z "$start" ] || [ -z "$end" ]; then
    return
  fi
  if [ "$number" = true ]; then
    nl -ba "$f" | sed -n "${start},${end}p"
  else
    sed -n "${start},${end}p" "$f"
  fi
}

# Change to working directory if specified
if [ -n "$workdir" ]; then
  cd "$workdir" 2>/dev/null || { echo "Error: directory '$workdir' not found" >&2; exit 1; }
fi

# If --dir is given, discover files recursively
if [ -n "$dir_to_read" ]; then
  if [ ! -d "$dir_to_read" ]; then
    echo "Error: directory '$dir_to_read' not found" >&2
    exit 1
  fi
  # Build find command for directory discovery
  find_cmd=(find "$dir_to_read")
  if [ -n "$max_depth" ]; then
    find_cmd=("${find_cmd[@]}" -maxdepth "$max_depth")
  fi
  find_cmd=("${find_cmd[@]}" -type f \( -not -path "*/node_modules/*" \) \( -not -path "*/.git/*" \))
  
  # Add name pattern filters if specified
  if [ ${#name_patterns[@]} -gt 0 ]; then
    find_cmd=("${find_cmd[@]}" \( -false)
    for np in "${name_patterns[@]}"; do
      find_cmd=("${find_cmd[@]}" -o -name "$np")
    done
    find_cmd=("${find_cmd[@]}" \))
  fi
  
  find_cmd=("${find_cmd[@]}" -print0)
  
  # Find all regular files, sorted, excluding node_modules and .git
  while IFS= read -r -d '' f; do
    files+=("$f")
  done < <("${find_cmd[@]}" 2>/dev/null | sort -z)
  if [ ${#files[@]} -eq 0 ]; then
    echo "No files found in '$dir_to_read'${name_patterns[0]:+ matching '${name_patterns[*]}'}"
    exit 0
  fi
fi

# Resolve file paths with --try: if exact path not found, try common extensions
if [ "$try_flag" = true ]; then
  resolved_files=()
  # Auto-limit output in safe mode (prevents token waste from truncated observations)
if [ "$safe_mode" = true ] && [ -z "$head_n" ] && [ -z "$tail_n" ] && [ -z "$lines" ] && [ -z "$offset" ] && [ "$wc_flag" = false ] && [ -z "$grep_pattern" ]; then
  head_n=50
fi


for f in "${files[@]}"; do
    if [ -f "$f" ]; then
      resolved_files+=("$f")
    else
      # Try common extensions
      found=""
      for ext in .js .ts .jsx .tsx .json .py .php .css .scss .yml .yaml .md .txt; do
        candidate="${f}${ext}"
        if [ -f "$candidate" ]; then
          resolved_files+=("$candidate")
          found="$candidate"
          break
        fi
      done
      if [ -z "$found" ]; then
        if [ "$no_header" != true ]; then
          echo "===== $f ====="
        fi
        echo "File not found: $f (tried: $f with .js,.ts,.jsx,.tsx,.json,.py,.php,.css,.scss,.yml,.yaml,.md,.txt extensions)" >&2
      fi
    fi
  done
  files=("${resolved_files[@]}")
fi

# If --wc is specified, just count lines
if [ "$wc_flag" = true ]; then
  # Auto-limit output in safe mode (prevents token waste from truncated observations)
if [ "$safe_mode" = true ] && [ -z "$head_n" ] && [ -z "$tail_n" ] && [ -z "$lines" ] && [ -z "$offset" ] && [ "$wc_flag" = false ] && [ -z "$grep_pattern" ]; then
  head_n=50
fi


for f in "${files[@]}"; do
    if [ ! -f "$f" ]; then
      echo "File not found: $f" >&2
      continue
    fi
    lc=$(wc -l < "$f")
    echo "$lc $f"
  done
  exit 0
fi

# Build grep args if --grep is specified
grep_flags=""
if [ -n "$grep_pattern" ]; then
  grep_flags=$(build_grep_flags)
fi

# Check if --lines has multiple comma-separated ranges
multi_range=false
IFS=',' read -ra line_ranges <<< "$lines"
if [ ${#line_ranges[@]} -gt 1 ]; then
  multi_range=true
fi

# Auto-limit output in safe mode (prevents token waste from truncated observations)
if [ "$safe_mode" = true ] && [ -z "$head_n" ] && [ -z "$tail_n" ] && [ -z "$lines" ] && [ -z "$offset" ] && [ "$wc_flag" = false ] && [ -z "$grep_pattern" ]; then
  head_n=50
fi


for f in "${files[@]}"; do
  # Check for per-file line range embedded in file path (file:start-end or file:start-end,start2-end2,...)
  per_file_range=""
  per_file_multi=false
  per_file_ranges=()
  if [[ "$f" =~ ^(.*):([0-9]+)-([0-9]+)(.*)$ ]]; then
    # Simple single range: file:start-end
    per_file_range="${BASH_REMATCH[2]}-${BASH_REMATCH[3]}"
    f="${BASH_REMATCH[1]}${BASH_REMATCH[4]}"
  elif [[ "$f" =~ ^(.*):([0-9]+-[0-9]+(,[0-9]+-[0-9]+)*)$ ]]; then
    # Multi range: file:start1-end1,start2-end2,...
    per_file_range="${BASH_REMATCH[2]}"
    f="${BASH_REMATCH[1]}"
    per_file_multi=true
    IFS=',' read -ra per_file_ranges <<< "$per_file_range"
  fi

  # Use per-file range if available, otherwise use global --lines
  active_lines="$lines"
  active_multi_range=$multi_range
  active_line_ranges=("${line_ranges[@]}")
  if [ -n "$per_file_range" ]; then
    active_lines="$per_file_range"
    if [ "$per_file_multi" = true ]; then
      active_multi_range=true
      active_line_ranges=("${per_file_ranges[@]}")
    else
      active_multi_range=false
    fi
  fi
  if [ ! -f "$f" ]; then
    if [ "$no_header" != true ]; then
      echo "===== $f ====="
    fi
    echo "File not found: $f" >&2
    continue
  fi

  # If --grep is specified, apply grep filter instead of showing full content
  if [ -n "$grep_pattern" ]; then
    # Check if there are any matches first
    if grep -qE -- "$grep_pattern" "$f" 2>/dev/null; then
      if [ "$no_header" != true ]; then
        echo "===== $f (grep: $grep_pattern) ====="
      fi
      if [ -n "$head_n" ]; then
        grep $grep_flags -- "$grep_pattern" "$f" 2>/dev/null | head -n "$head_n"
      elif [ -n "$tail_n" ]; then
        grep $grep_flags -- "$grep_pattern" "$f" 2>/dev/null | tail -n "$tail_n"
      else
        grep $grep_flags -- "$grep_pattern" "$f" 2>/dev/null
      fi
      if [ "$no_header" != true ]; then
        echo ""
      fi
    fi
    continue
  fi

  if [ -n "$active_lines" ]; then
    if [ "$active_multi_range" = true ]; then
      # Multiple line ranges: print each range with its own header
      for range in "${active_line_ranges[@]}"; do
        start="${range%-*}"
        end="${range#*-}"
        if [ -n "$start" ] && [ -n "$end" ]; then
          if [ "$no_header" != true ]; then
            echo "===== $f (lines $start-$end) ====="
          fi
          print_range "$f" "$start" "$end" "$range"
          if [ "$no_header" != true ]; then
            echo ""
          fi
        fi
      done
    else
      # Single range (original behavior)
      start="${active_lines%-*}"
      end="${active_lines#*-}"
      if [ "$no_header" != true ]; then
        echo "===== $f ====="
      fi
      print_range "$f" "$start" "$end" ""
      if [ "$no_header" != true ]; then
        echo ""
      fi
    fi
  elif [ -n "$offset" ]; then
    # Read from offset line (tail -n +N | head -M pattern)
    if [ -n "$head_n" ]; then
      # offset=N, head=M -> tail -n +N | head -M
      if [ "$number" = true ]; then
        tail -n +"$offset" "$f" | head -n "$head_n" | nl -ba -v "$offset"
      else
        tail -n +"$offset" "$f" | head -n "$head_n"
      fi
    else
      # Just offset without head limit
      if [ "$number" = true ]; then
        tail -n +"$offset" "$f" | nl -ba -v "$offset"
      else
        tail -n +"$offset" "$f"
      fi
    fi
  elif [ -n "$head_n" ]; then
    if [ "$number" = true ]; then
      head -n "$head_n" "$f" | cat -n
    else
      head -n "$head_n" "$f"
    fi
  elif [ -n "$tail_n" ]; then
    if [ "$number" = true ]; then
      tail -n "$tail_n" "$f" | cat -n
    else
      tail -n "$tail_n" "$f"
    fi
  else
    if [ "$number" = true ]; then
      cat -n "$f"
    else
      cat "$f"
    fi
  fi
  if [ "$no_header" != true ]; then
    echo ""
  fi
done
