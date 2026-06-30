#!/bin/bash
# Find files matching name patterns with automatic exclusions
# Usage: find-files/main.sh <path> [--cd=DIR] [--name=<glob>]
#   --cd=DIR:       Change to this directory before searching [--exclude-name=<glob>] [--max-depth=N] [--type=f|d] [--path=<glob>] [--not-path=<glob>] [--grep=<pattern>] [--sort] [--count] [--head=N] [--tail=N] [--offset=N] [--no-exclude-defaults]
#   --path=<glob>:   Positive path glob filter (like find -path, comma-separated for multiple)
#   --head=N:        Show first N results (default: 200)
#   --tail=N:        Show last N results instead of first N
#   --offset=N:      Skip N results from start (combine with --head for pagination: tail -n +N+1 | head -M)
#   --no-exclude-defaults: Don't auto-exclude node_modules, .git, .cache, .husky
#   --grep=<pattern>: Search file CONTENTS for pattern (case-insensitive), returns matching filenames
#   --path-grep=<pattern>: Filter file paths by regex (replaces find | grep -i pipe)
#   --exclude-name=<glob>: Exclude files by name glob pattern (comma-separated for multiple)

search_path="$1"
shift

name_pattern=""
max_depth=""
file_type=""
path_pattern=""
not_path=""
grep_pattern=""
path_grep=""
exclude_name=""
sort_flag=false
count_flag=false
head_n=""
tail_n=""
offset_n=""
no_exclude_defaults=false

workdir=""

for arg in "$@"; do
  if [[ "$arg" =~ ^--cd=(.*)$ ]]; then
    workdir="${BASH_REMATCH[1]}"
  elif [[ "$arg" =~ ^--name=(.*)$ ]]; then
    name_pattern="${BASH_REMATCH[1]}"
  elif [[ "$arg" =~ ^--max-depth=(.*)$ ]]; then
    max_depth="${BASH_REMATCH[1]}"
  elif [[ "$arg" =~ ^--type=(.*)$ ]]; then
    file_type="${BASH_REMATCH[1]}"
  elif [[ "$arg" =~ ^--path=(.*)$ ]]; then
    path_pattern="${BASH_REMATCH[1]}"
  elif [[ "$arg" =~ ^--not-path=(.*)$ ]]; then
    not_path="${BASH_REMATCH[1]}"
  elif [[ "$arg" =~ ^--grep=(.*)$ ]]; then
    grep_pattern="${BASH_REMATCH[1]}"
  elif [[ "$arg" =~ ^--path-grep=(.*)$ ]]; then
    path_grep="${BASH_REMATCH[1]}"
  elif [[ "$arg" =~ ^--exclude-name=(.*)$ ]]; then
    exclude_name="${BASH_REMATCH[1]}"
  elif [[ "$arg" =~ ^--head=(.*)$ ]]; then
    head_n="${BASH_REMATCH[1]}"
  elif [[ "$arg" =~ ^--tail=(.*)$ ]]; then
    tail_n="${BASH_REMATCH[1]}"
  elif [[ "$arg" =~ ^--offset=(.*)$ ]]; then
    offset_n="${BASH_REMATCH[1]}"
  elif [ "$arg" = "--sort" ]; then
    sort_flag=true
  elif [ "$arg" = "--count" ]; then
    count_flag=true
  elif [ "$arg" = "--no-exclude-defaults" ]; then
    no_exclude_defaults=true
  fi
done

# Change to working directory if specified
if [ -n "$workdir" ]; then
  cd "$workdir" 2>/dev/null || { echo "Error: directory '$workdir' not found" >&2; exit 1; }
fi

if [ -z "$search_path" ] || [ "$search_path" = "." ]; then
  search_path="."
fi

# Default head if not specified
if [ -z "$head_n" ] && [ -z "$tail_n" ] && [ -z "$offset_n" ]; then
  head_n=200
fi

# Build find command parts
cmd_parts=("find" "$search_path")

# maxdepth must come before tests
if [ -n "$max_depth" ]; then
  cmd_parts+=("-maxdepth" "$max_depth")
fi

# Build exclusion parts
exclude_parts=()
if [ "$no_exclude_defaults" = false ]; then
  exclude_parts+=("(" "-not" "-path" "*/node_modules/*" "-not" "-path" "*/.git/*" "-not" "-path" "*/.cache/*" "-not" "-path" "*/.husky/*" "-not" "-path" "*/dist/*")

  # Add user-specified exclusion patterns
  if [ -n "$not_path" ]; then
    IFS=',' read -ra extra_excludes <<< "$not_path"
    for ex in "${extra_excludes[@]}"; do
      exclude_parts+=("-not" "-path" "$ex")
    done
  fi

  exclude_parts+=(")")
  cmd_parts+=("${exclude_parts[@]}")
fi

# Positive path filter (like find -path)
if [ -n "$path_pattern" ]; then
  IFS=',' read -ra path_parts <<< "$path_pattern"
  cmd_parts+=("(")
  first=true
  for pp in "${path_parts[@]}"; do
    if [ "$first" = true ]; then
      cmd_parts+=(-path "$pp")
      first=false
    else
      cmd_parts+=(-o -path "$pp")
    fi
  done
  cmd_parts+=(")")
fi

if [ -n "$file_type" ]; then
  if [ "$file_type" = "f" ]; then
    cmd_parts+=("-type" "f")
  elif [ "$file_type" = "d" ]; then
    cmd_parts+=("-type" "d")
  fi
fi

if [ -n "$name_pattern" ]; then
  IFS=',' read -ra names <<< "$name_pattern"
  cmd_parts+=("(")
  first=true
  for name in "${names[@]}"; do
    if [ "$first" = true ]; then
      cmd_parts+=(-name "$name")
      first=false
    else
      cmd_parts+=(-o -name "$name")
    fi
  done
  cmd_parts+=(")")
fi

# Exclude by name pattern
if [ -n "$exclude_name" ]; then
  IFS="," read -ra exclude_names <<< "$exclude_name"
  for en in "${exclude_names[@]}"; do
    cmd_parts+=("!" -name "$en")
  done
fi

pipe_path_grep() {
  if [ -n "$path_grep" ]; then
    grep -i "$path_grep"
  else
    cat
  fi
}

# Helper for offset/head/tail
pipe_limit() {
  if [ -n "$offset_n" ] && [ -n "$head_n" ]; then
    tail -n +$((offset_n + 1)) | head -n "$head_n"
  elif [ -n "$offset_n" ]; then
    tail -n +$((offset_n + 1))
  elif [ -n "$tail_n" ]; then
    tail -n "$tail_n"
  elif [ -n "$head_n" ]; then
    head -n "$head_n"
  else
    cat
  fi
}

# Collect file list from find
tmpfile=$(mktemp)
"${cmd_parts[@]}" 2>/dev/null | pipe_path_grep > "$tmpfile"

if [ -n "$grep_pattern" ]; then
  # Search file CONTENTS using xargs grep -il (case-insensitive, show only filenames)
  if [ -s "$tmpfile" ]; then
    matched_file=$(mktemp)
    xargs grep -ilE "$grep_pattern" < "$tmpfile" 2>/dev/null > "$matched_file" || true
    mv "$matched_file" "$tmpfile"
  fi
fi

if [ "$count_flag" = true ]; then
  wc -l < "$tmpfile" | tr -d ' '
elif [ -n "$tail_n" ]; then
  sort "$tmpfile" | tail -n "$tail_n"
elif [ "$sort_flag" = true ]; then
  sort "$tmpfile" | pipe_limit
else
  cat "$tmpfile" | pipe_limit
fi

rm -f "$tmpfile"
