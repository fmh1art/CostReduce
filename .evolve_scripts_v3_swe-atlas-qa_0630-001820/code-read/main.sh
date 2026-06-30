#!/bin/bash
# Read function/class/interface definitions with full body from source files.
# Supports Python (def/class), JavaScript (function/class/const arrow), TypeScript (plus interface/type).
# Usage: code-read/main.sh [--cd=DIR] --name=FUNC_OR_CLASS_NAME [--pattern=REGEX] [--lang=py|js|ts|auto] [--head=N] [--tail=N] [--context=N] [files...]
#   --name:     Name of the function/class/interface to find (supports substring match)
#   --pattern:  Custom regex pattern instead of --name
#   --lang:     Language mode: py (default), js, ts, auto (auto-detect from file extension)
#   --head=N:   Show first N lines of output
#   --tail=N:   Show last N lines of output
#   --context=N: Show N context lines before definition
#   If no files given, searches all *.py files recursively (or *.js/*.ts for --lang=js/ts)

cd_dir=""
search_name=""
search_pattern=""
head_n=""
tail_n=""
context=0
lang="py"
files=()

while [ $# -gt 0 ]; do
  case "$1" in
    --cd=*) cd_dir="${1#*=}"; shift ;;
    --name=*) search_name="${1#*=}"; shift ;;
    --pattern=*) search_pattern="${1#*=}"; shift ;;
    --lang=*) lang="${1#*=}"; shift ;;
    --head=*) head_n="${1#*=}"; shift ;;
    --tail=*) tail_n="${1#*=}"; shift ;;
    --context=*) context="${1#*=}"; shift ;;
    *) files+=("$1"); shift ;;
  esac
done

if [ -n "$cd_dir" ]; then
  cd "$cd_dir" || exit 1
fi

# Determine file globs based on language
file_globs=()
case "$lang" in
  py|python) file_globs+=("*.py") ;;
  js|javascript) file_globs+=("*.js" "*.jsx") ;;
  ts|typescript) file_globs+=("*.ts" "*.tsx") ;;
  auto) file_globs+=("*.py" "*.js" "*.jsx" "*.ts" "*.tsx") ;;
esac

# Build definition search pattern if --name given and no --pattern
if [ -z "$search_pattern" ] && [ -n "$search_name" ]; then
  case "$lang" in
    py|python)
      # Python: def name, class name, async def name
      search_pattern="^[[:space:]]*(async[[:space:]]+)?(def|class)[[:space:]]+${search_name}"
      ;;
    js|javascript)
      # JS: function name, class name, const name = (arrow/fn expr), export * variants
      search_pattern="(function[[:space:]]+${search_name}|class[[:space:]]+${search_name}|^[[:space:]]*(export[[:space:]]+default[[:space:]]+)?(async[[:space:]]+)?function[[:space:]]+${search_name}|^[[:space:]]*(export[[:space:]]+)?class[[:space:]]+${search_name}|^[[:space:]]*(export[[:space:]]+)?const[[:space:]]+${search_name}[[:space:]]*[=:])"
      ;;
    ts|typescript)
      # TS: function, class, interface, type, const (with type annotations), export * variants
      search_pattern="(function[[:space:]]+${search_name}|class[[:space:]]+${search_name}|interface[[:space:]]+${search_name}|type[[:space:]]+${search_name}|^[[:space:]]*(export[[:space:]]+default[[:space:]]+)?(async[[:space:]]+)?function[[:space:]]+${search_name}|^[[:space:]]*(export[[:space:]]+)?class[[:space:]]+${search_name}|^[[:space:]]*(export[[:space:]]+)?interface[[:space:]]+${search_name}|^[[:space:]]*(export[[:space:]]+)?type[[:space:]]+${search_name}|^[[:space:]]*(export[[:space:]]+)?const[[:space:]]+${search_name}[[:space:]]*[=:])"
      ;;
    auto)
      # Auto: use union of all patterns (Python, JS, TS)
      search_pattern="(function[[:space:]]+${search_name}|^[[:space:]]*(async[[:space:]]+)?(def|class)[[:space:]]+${search_name}|interface[[:space:]]+${search_name}|type[[:space:]]+${search_name}|^[[:space:]]*(export[[:space:]]+default[[:space:]]+)?(async[[:space:]]+)?function[[:space:]]+${search_name}|^[[:space:]]*(export[[:space:]]+)?class[[:space:]]+${search_name}|^[[:space:]]*(export[[:space:]]+)?interface[[:space:]]+${search_name}|^[[:space:]]*(export[[:space:]]+)?type[[:space:]]+${search_name}|^[[:space:]]*(export[[:space:]]+)?const[[:space:]]+${search_name}[[:space:]]*[=:])"
      ;;
  esac
fi

if [ -z "$search_pattern" ]; then
  echo "Error: Must provide --name or --pattern" >&2
  exit 1
fi

# Collect files to search
if [ ${#files[@]} -eq 0 ]; then
  find_cmd=("find" "." "-type" "f")
  # Build name pattern
  if [ ${#file_globs[@]} -gt 0 ]; then
    find_cmd+=("(" -false)
    for g in "${file_globs[@]}"; do
      find_cmd+=(-o -name "$g")
    done
    find_cmd+=(")")
  fi
  find_cmd+=(-print0)
  
  while IFS= read -r -d $'\0' f; do
    files+=("$f")
  done < <("${find_cmd[@]}" 2>/dev/null)
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
    
    # Print context lines before definition
    if [ "$context" -gt 0 ]; then
      sed -n "${ctx_start},$((line_num - 1))p" "$file" 2>/dev/null
    fi
    
    # Use awk to find body end by tracking indentation
    awk -v start="$line_num" -v indent_len="$indent_len" -v file="$file" '
    NR < start { next }
    NR == start { print; next }
    {
      stripped = $0
      gsub(/^[[:space:]]*/, "", stripped)
      if (stripped == "" || stripped ~ /^@/ || stripped ~ /^\/\// || stripped ~ /^\/\*/ || stripped ~ /^\*/) { print; next }
      # Check if we hit a new definition at same or less indentation
      if (stripped ~ /^(async[[:space:]]+)?(def|class|function|interface|type)[[:space:]]+/ ||
          stripped ~ /^(export[[:space:]]+)?(default[[:space:]]+)?(async[[:space:]]+)?(function|class|interface|type)[[:space:]]+/ ||
          stripped ~ /^export[[:space:]]+default[[:space:]]+/) {
        match($0, /^[[:space:]]*/)
        cur_indent = RLENGTH
        if (cur_indent <= indent_len) exit
      }
      # Also check for const/var/let declarations at same level
      if ((stripped ~ /^const[[:space:]]+/ || stripped ~ /^export[[:space:]]+const[[:space:]]+/) && stripped !~ /^(export[[:space:]]+)?const[[:space:]]+$/) {
        match($0, /^[[:space:]]*/)
        cur_indent = RLENGTH
        if (cur_indent <= indent_len && cur_indent >= 0) exit
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
