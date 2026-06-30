#!/bin/bash
# Batch-query multiple files or directories matching a pattern.
# Iterates over paths matching a glob, runs an action on each, and outputs results compactly.
# Usage: multi-query/main.sh <glob_pattern> [--cd=DIR] [--grep=PATTERN] [--json-key=KEY] [--json-keys] [--name-only] [--head=N] [--tail=N] [--sort]
#   glob_pattern:  Glob pattern matching files/dirs to query
#   --cd=DIR:      Change to directory before resolving glob
#   --grep=PATTERN: grep for PATTERN in each matched file's content
#   --json-key=KEY: Extract this dot-separated JSON key from each matched file
#   --json-keys:    List top-level JSON keys from each matched file
#   --name-only:    Just list matched paths, no query
#   --head=N:       Show first N results
#   --tail=N:       Show last N results
#   --sort:         Sort output alphabetically

pattern=""
workdir=""
grep_pattern=""
json_key=""
json_list_keys=false
name_only=false
head_n=""
tail_n=""
sort_flag=false

for arg in "$@"; do
  case "$arg" in
    --cd=*) workdir="${arg#*=}" ;;
    --grep=*) grep_pattern="${arg#*=}" ;;
    --json-key=*) json_key="${arg#*=}" ;;
    --json-keys) json_list_keys=true ;;
    --name-only) name_only=true ;;
    --head=*) head_n="${arg#*=}" ;;
    --tail=*) tail_n="${arg#*=}" ;;
    --sort) sort_flag=true ;;
    *)
      if [ -z "$pattern" ]; then
        pattern="$arg"
      fi
      ;;
  esac
done

if [ -z "$pattern" ]; then
  echo "Error: glob pattern is required" >&2
  echo "Usage: multi-query/main.sh <glob_pattern> [--cd=DIR] [--grep=PATTERN] [--json-key=KEY] [--json-keys] [--name-only] [--head=N] [--tail=N] [--sort]" >&2
  exit 1
fi

# Change to working directory if specified
if [ -n "$workdir" ]; then
  cd "$workdir" 2>/dev/null || { echo "Error: directory '$workdir' not found" >&2; exit 1; }
fi

# Resolve the glob pattern
shopt -s nullglob
matched_files=($pattern)
shopt -u nullglob

if [ ${#matched_files[@]} -eq 0 ]; then
  echo "No files matching pattern: $pattern"
  exit 0
fi

# Apply sort if requested
if [ "$sort_flag" = true ]; then
  IFS=$'\n' matched_files=($(sort <<<"${matched_files[*]}"))
  unset IFS
fi

# Apply head/tail
if [ -n "$head_n" ] && [ -n "$tail_n" ]; then
  IFS=$'\n' matched_files=($(tail -n "$tail_n" <<<"${matched_files[*]}"))
  unset IFS
elif [ -n "$head_n" ]; then
  IFS=$'\n' matched_files=($(head -n "$head_n" <<<"${matched_files[*]}"))
  unset IFS
elif [ -n "$tail_n" ]; then
  IFS=$'\n' matched_files=($(tail -n "$tail_n" <<<"${matched_files[*]}"))
  unset IFS
fi

# Function to write matched files list to a tmp file for Python to read
list_file=$(mktemp /tmp/multi_query_files_XXXXXX)
for f in "${matched_files[@]}"; do
  echo "$f" >> "$list_file"
done

cleanup() {
  rm -f "$list_file"
}
trap cleanup EXIT

# Name-only mode: just list matched paths
if [ "$name_only" = true ]; then
  cat "$list_file"
  exit 0
fi

# Grep mode: grep for pattern in each file
if [ -n "$grep_pattern" ]; then
  for f in "${matched_files[@]}"; do
    if [ -f "$f" ]; then
      results=$(grep -n "$grep_pattern" "$f" 2>/dev/null | head -20)
      if [ -n "$results" ]; then
        echo "=== $f ==="
        echo "$results"
        echo ""
      fi
    fi
  done
  exit 0
fi

# JSON key extraction mode
if [ -n "$json_key" ]; then
  python3 -c "
import json, sys

list_file = '$list_file'
json_key = '$json_key'

with open(list_file) as f:
    files = [line.strip() for line in f if line.strip()]

for fp in files:
    try:
        with open(fp) as f:
            data = json.load(f)
        parts = json_key.split('.')
        cur = data
        for p in parts:
            if isinstance(cur, dict):
                cur = cur.get(p)
            elif isinstance(cur, list):
                try:
                    cur = cur[int(p)]
                except (ValueError, IndexError):
                    cur = None
            else:
                cur = None
            if cur is None:
                break
        if cur is not None:
            print(f'=== {fp} ===')
            if isinstance(cur, str):
                print(cur)
            else:
                print(json.dumps(cur, indent=2))
            print()
    except Exception as e:
        print(f'=== {fp} ===')
        print(f'Error: {e}')
        print()
"
  exit 0
fi

# JSON list-keys mode
if [ "$json_list_keys" = true ]; then
  python3 -c "
import json, sys

list_file = '$list_file'

with open(list_file) as f:
    files = [line.strip() for line in f if line.strip()]

for fp in files:
    try:
        with open(fp) as f:
            data = json.load(f)
        if isinstance(data, dict):
            print(f'=== {fp} ===')
            for k in data.keys():
                print(f'  {k}')
            print()
        else:
            print(f'=== {fp} === (not a dict)')
            print()
    except Exception as e:
        print(f'=== {fp} === Error: {e}')
        print()
"
  exit 0
fi

# Default: show file name and a preview
for f in "${matched_files[@]}"; do
  echo "=== $f ==="
  if [ -f "$f" ]; then
    line_count=$(wc -l < "$f" 2>/dev/null || echo 0)
    echo "  Lines: $line_count"
    head -5 "$f" 2>/dev/null
  elif [ -d "$f" ]; then
    file_count=$(find "$f" -type f 2>/dev/null | wc -l)
    echo "  Directory with $file_count files"
  fi
  echo ""
done
