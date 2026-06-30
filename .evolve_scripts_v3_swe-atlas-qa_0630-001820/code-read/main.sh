#!/bin/bash
# Read Python function/class/async-function definitions with full body from source files.
# Usage: code-read/main.sh [--cd=DIR] --name=FUNC_OR_CLASS_NAME [--pattern=REGEX] [--head=N] [--tail=N] [--context=N] [files...]
#   Searches for function/class definitions by name or regex pattern and prints the full body.
#   If no files given, searches all *.py files recursively from current dir or --cd=DIR.

cd_dir=""
search_name=""
search_pattern=""
head_n=""
tail_n=""
context=0
files=()

while [ $# -gt 0 ]; do
  case "$1" in
    --cd=*) cd_dir="${1#*=}"; shift ;;
    --name=*) search_name="${1#*=}"; shift ;;
    --pattern=*) search_pattern="${1#*=}"; shift ;;
    --head=*) head_n="${1#*=}"; shift ;;
    --tail=*) tail_n="${1#*=}"; shift ;;
    --context=*) context="${1#*=}"; shift ;;
    *) files+=("$1"); shift ;;
  esac
done

if [ -n "$cd_dir" ]; then
  cd "$cd_dir" || exit 1
fi

# Determine search pattern
if [ -z "$search_pattern" ] && [ -n "$search_name" ]; then
  search_pattern="^[[:space:]]*(async[[:space:]]+)?(def|class)[[:space:]]+${search_name}"
fi

if [ -z "$search_pattern" ]; then
  echo "Error: Must provide --name or --pattern" >&2
  exit 1
fi

# Collect files to search
if [ ${#files[@]} -eq 0 ]; then
  while IFS= read -r -d '' f; do
    files+=("$f")
  done < <(find . -name "*.py" -type f -print0 2>/dev/null)
fi

if [ ${#files[@]} -eq 0 ]; then
  echo "No files to search" >&2
  exit 1
fi

out=$(mktemp)

for file in "${files[@]}"; do
  [ ! -f "$file" ] && continue
  
  grep -nE "$search_pattern" "$file" 2>/dev/null | while IFS=: read -r line_num line_content; do
    [ -z "$line_num" ] && continue
    
    # Get indentation of this definition
    indent="${line_content%%[^[:space:]]*}"
    indent_len=${#indent}
    
    # Context start
    ctx_start=$((line_num - context))
    [ "$ctx_start" -lt 1 ] && ctx_start=1
    
    echo "=== ${file}:${line_num} ==="
    
    # Print from context_start, then use awk to find body end
    if [ "$context" -gt 0 ]; then
      sed -n "${ctx_start},$((line_num - 1))p" "$file" 2>/dev/null
    fi
    
    awk -v start="$line_num" -v indent_len="$indent_len" -v file="$file" '
    NR < start { next }
    NR == start { print; next }
    {
      stripped = $0
      gsub(/^[[:space:]]*/, "", stripped)
      if (stripped == "" || stripped ~ /^@/) { print; next }
      if (stripped ~ /^(async[[:space:]]+)?(def|class)[[:space:]]+/) {
        match($0, /^[[:space:]]*/)
        cur_indent = RLENGTH
        if (cur_indent <= indent_len) exit
      }
      print
    }' "$file"
    echo ""
  done
done > "$out"

if grep -q . "$out" 2>/dev/null; then
  if [ -n "$tail_n" ]; then
    tail -n "$tail_n" "$out"
  elif [ -n "$head_n" ]; then
    head -n "$head_n" "$out"
  else
    cat "$out"
  fi
else
  echo "No matching definitions found" >&2
  rm -f "$out"
  exit 1
fi

rm -f "$out"
