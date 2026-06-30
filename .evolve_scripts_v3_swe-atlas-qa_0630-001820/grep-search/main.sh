#!/bin/bash
# Search file contents with grep, supporting single files or directories
# Usage: grep-search/main.sh <pattern> [path...] [--include=GLOB1,GLOB2...] [--exclude=GLOB] [--head=N] [--tail=N] [--context=N] [--after-context=N|-A=N] [--before-context=N|-B=N] [--filter=PATTERN] [--exclude-line=PATTERN]... [--exclude-path=PATTERN]... [--sort] [--perl-regexp|-P] [--defs] [--classes] [--functions] [--name-only|-l]
#   --perl-regexp|-P: Use Perl-compatible regex (grep -P) for advanced patterns like lookaheads and \\d shortcuts
#   --exclude-line: Repeatable; each pattern filters matching lines OUT (replaces | grep -v chains)
#   --exclude-path: Repeatable; each pattern filters matching path lines OUT
#   --name-only|-l: Output only filenames (like grep -l) instead of file:line:content
#   --sort: Sort results alphabetically before applying head/tail (replaces grep -l | sort chains)

# Check for definition-mode flags first (no explicit pattern needed)
perl_regexp=false

defs_mode=false
classes_mode=false
functions_mode=false
name_only=false
sort_flag=false
remaining_args=()
for arg in "$@"; do
  case "$arg" in
    --defs) defs_mode=true ;;
    --classes) classes_mode=true ;;
    --functions) functions_mode=true ;;
    --name-only|-l) name_only=true ;;
    --sort) sort_flag=true ;;
    --perl-regexp|-P) perl_regexp=true ;;

    *) remaining_args+=("$arg") ;;
  esac
done
set -- "${remaining_args[@]}"

if [ "$defs_mode" = true ] || [ "$classes_mode" = true ] || [ "$functions_mode" = true ]; then
  # Build definition pattern based on language-specific keywords
  parts=()
  if [ "$defs_mode" = true ] || [ "$classes_mode" = true ]; then
    parts+=("class " "struct " "interface " "trait " "enum ")
  fi
  if [ "$defs_mode" = true ] || [ "$functions_mode" = true ]; then
    parts+=("def " "func " "function " "fn ")
  fi
  IFS='|' def_parts="${parts[*]}"
  pattern="^[[:space:]]*($def_parts)"
  shift 0
else
  pattern="$1"
  shift
  # Convert basic-regex \| to extended-regex | for -E compatibility
  pattern="${pattern//\\|/|}"
fi

search_paths=()
include_globs=()
exclude_glob=""
head_n=""
tail_n=""
context=""
after_context=""
before_context=""
filter_pattern=""
exclude_lines=()
exclude_paths=()

for arg in "$@"; do
  if [[ "$arg" =~ ^--include=(.*)$ ]]; then
    IFS=',' read -ra globs <<< "${BASH_REMATCH[1]}"
    for g in "${globs[@]}"; do
      include_globs+=("$g")
    done
  elif [[ "$arg" =~ ^--exclude=(.*)$ ]]; then
    exclude_glob="${BASH_REMATCH[1]}"
  elif [[ "$arg" =~ ^--head=(.*)$ ]]; then
    head_n="${BASH_REMATCH[1]}"
  elif [[ "$arg" =~ ^--tail=(.*)$ ]]; then
    tail_n="${BASH_REMATCH[1]}"
  elif [[ "$arg" =~ ^--context=(.*)$ ]]; then
    context="${BASH_REMATCH[1]}"
  elif [[ "$arg" =~ ^--after-context=(.*)$ ]] || [[ "$arg" =~ ^-A=(.*)$ ]]; then
    after_context="${BASH_REMATCH[1]}"
  elif [[ "$arg" =~ ^--before-context=(.*)$ ]] || [[ "$arg" =~ ^-B=(.*)$ ]]; then
    before_context="${BASH_REMATCH[1]}"
  elif [[ "$arg" =~ ^--filter=(.*)$ ]]; then
    filter_pattern="${BASH_REMATCH[1]}"
  elif [[ "$arg" =~ ^--exclude-line=(.*)$ ]]; then
    exclude_lines+=("${BASH_REMATCH[1]}")
  elif [[ "$arg" =~ ^--exclude-path=(.*)$ ]]; then
    exclude_paths+=("${BASH_REMATCH[1]}")
  else
    search_paths+=("$arg")
  fi
done

# Default to head=100 if neither head nor tail specified
if [ -z "$head_n" ] && [ -z "$tail_n" ]; then
  head_n=100
fi

# Determine if we are searching directories or specific files
is_dir_search=false
if [ ${#search_paths[@]} -eq 0 ]; then
  is_dir_search=true
elif [ ${#search_paths[@]} -eq 1 ] && [ -d "${search_paths[0]}" ]; then
  is_dir_search=true
fi

# Choose regex flag -E (extended) or -P (Perl)
if [ "$perl_regexp" = true ]; then
  regex_flag="-P"
else
  regex_flag="-E"
fi

# Build grep command
if [ "$is_dir_search" = true ]; then

  # Directory search - use grep -r with includes
  if [ "$name_only" = true ]; then
    cmd=(grep -r -l "$regex_flag")
  else
    cmd=(grep -r -n "$regex_flag")
  fi

  if [ -n "$context" ]; then
    cmd+=(-C "$context")
  fi
  if [ -n "$after_context" ]; then
    cmd+=(-A "$after_context")
  fi
  if [ -n "$before_context" ]; then
    cmd+=(-B "$before_context")
  fi
  if [ -n "$exclude_glob" ]; then
    cmd+=(--exclude="$exclude_glob")
  fi

  cmd+=("$pattern")

  if [ ${#search_paths[@]} -eq 0 ]; then
    cmd+=(".")
  else
    cmd+=("${search_paths[0]}")
  fi

  if [ ${#include_globs[@]} -gt 0 ]; then
    for g in "${include_globs[@]}"; do
      cmd+=(--include="$g")
    done
  else
    cmd+=(--include="*.js" --include="*.ts" --include="*.tsx" --include="*.jsx" --include="*.json" --include="*.scss" --include="*.css" --include="*.py" --include="*.sh" --include="*.yml" --include="*.yaml" --include="*.env" --include="*.md")
  fi

  # Run grep and capture output to temp file for filtering
  tmpfile=$(mktemp)
  "${cmd[@]}" 2>/dev/null | grep -vE "node_modules|\.git/|venv/|__pycache__/" > "$tmpfile"

  # Apply --filter (secondary case-insensitive grep)
  if [ -n "$filter_pattern" ]; then
    tmp2=$(mktemp)
    grep -iE "$filter_pattern" "$tmpfile" > "$tmp2" 2>/dev/null
    mv "$tmp2" "$tmpfile"
  fi

  # Apply each --exclude-line (repeatable, each is a grep -vE)
  # Skip exclude-line filtering in name-only mode (no line content to filter)
  if [ "$name_only" = false ]; then
    for excl in "${exclude_lines[@]}"; do
      tmp2=$(mktemp)
      grep -vE "$excl" "$tmpfile" > "$tmp2" 2>/dev/null
      mv "$tmp2" "$tmpfile"
    done
  fi

  # Apply each --exclude-path (repeatable)
  for exp in "${exclude_paths[@]}"; do
    tmp2=$(mktemp)
    grep -vE "$exp" "$tmpfile" > "$tmp2" 2>/dev/null
    mv "$tmp2" "$tmpfile"
  done

  # Apply sort before head/tail if --sort is set
  if [ "$sort_flag" = true ]; then
    tmp2=$(mktemp)
    sort "$tmpfile" > "$tmp2" 2>/dev/null
    mv "$tmp2" "$tmpfile"
  fi

  # Apply head/tail
  if [ -n "$tail_n" ]; then
    tail -n "$tail_n" "$tmpfile"
  else
    head -n "$head_n" "$tmpfile"
  fi

  rm -f "$tmpfile"
else
  # Specific files - no recursive search, no include filtering, no auto-exclusion
  if [ "$name_only" = true ]; then
    cmd=(grep -l "$regex_flag")
  else
    cmd=(grep -n "$regex_flag")
  fi

  if [ -n "$context" ]; then
    cmd+=(-C "$context")
  fi
  if [ -n "$after_context" ]; then
    cmd+=(-A "$after_context")
  fi
  if [ -n "$before_context" ]; then
    cmd+=(-B "$before_context")
  fi
  if [ -n "$exclude_glob" ]; then
    cmd+=(--exclude="$exclude_glob")
  fi

  cmd+=("$pattern")
  cmd+=("${search_paths[@]}")

  # Run grep and capture to temp file
  tmpfile=$(mktemp)
  "${cmd[@]}" 2>/dev/null > "$tmpfile"

  # Apply --filter
  if [ -n "$filter_pattern" ]; then
    tmp2=$(mktemp)
    grep -iE "$filter_pattern" "$tmpfile" > "$tmp2" 2>/dev/null
    mv "$tmp2" "$tmpfile"
  fi

  # Apply each --exclude-line (skip in name-only mode)
  if [ "$name_only" = false ]; then
    for excl in "${exclude_lines[@]}"; do
      tmp2=$(mktemp)
      grep -vE "$excl" "$tmpfile" > "$tmp2" 2>/dev/null
    mv "$tmp2" "$tmpfile"
    done
  fi

  # Apply each --exclude-path
  for exp in "${exclude_paths[@]}"; do
    tmp2=$(mktemp)
    grep -vE "$exp" "$tmpfile" > "$tmp2" 2>/dev/null
    mv "$tmp2" "$tmpfile"
  done

  # Apply sort before head/tail if --sort is set
  if [ "$sort_flag" = true ]; then
    tmp2=$(mktemp)
    sort "$tmpfile" > "$tmp2" 2>/dev/null
    mv "$tmp2" "$tmpfile"
  fi

  # Apply head/tail
  if [ -n "$tail_n" ]; then
    tail -n "$tail_n" "$tmpfile"
  else
    head -n "$head_n" "$tmpfile"
  fi

  rm -f "$tmpfile"
fi
