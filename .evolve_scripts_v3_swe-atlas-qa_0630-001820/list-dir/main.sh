#!/bin/bash
# List directory contents with file details, summary, and multiple output modes.
# Usage: list-dir/main.sh [directory] [--head=N] [--tail=N] [--dirs-only|-d] [--files-only|-f] [--tree|-t] [--sort|-s]
#   --dirs-only|-d: List subdirectories only (like find -maxdepth 1 -type d | sort)
#   --files-only|-f: List files only (like find -maxdepth 1 -type f | sort)
#   --tree|-t:   Show recursive tree view (like find | sort)
#   --sort|-s:   Sort output alphabetically
#   --head=N:    Show first N entries
#   --tail=N:    Show last N entries

dir=""
head_n=""
tail_n=""
dirs_only=false
files_only=false
tree_mode=false
sort_flag=false

for arg in "$@"; do
  case "$arg" in
    --head=*) head_n="${arg#*=}" ;;
    --tail=*) tail_n="${arg#*=}" ;;
    --dirs-only|-d) dirs_only=true ;;
    --files-only|-f) files_only=true ;;
    --tree|-t) tree_mode=true ;;
    --sort|-s) sort_flag=true ;;
    *)
      if [ -z "$dir" ] && [ "$arg" != "${arg#/}" -o "$arg" = "." ]; then
        dir="$arg"
      elif [ -z "$dir" ]; then
        dir="$arg"
      fi
      ;;
  esac
done

# Default to current directory
if [ -z "$dir" ]; then
  dir="."
fi

if [ ! -d "$dir" ]; then
  echo "Error: '$dir' is not a directory" >&2
  exit 1
fi

# Helper function for head/tail limiting
pipe_limit() {
  if [ -n "$tail_n" ]; then
    tail -n "$tail_n"
  elif [ -n "$head_n" ]; then
    head -n "$head_n"
  else
    cat
  fi
}

# Tree mode: recursive listing like find | sort
if [ "$tree_mode" = true ]; then
  echo "=== Tree: $dir ==="
  echo ""
  find "$dir" \( -type f -o -type d \) \
    -not -path "*/node_modules/*" \
    -not -path "*/.git/*" \
    -not -path "*/.cache/*" \
    -not -path "*/.husky/*" \
    -not -path "*/dist/*" \
    -not -name ".*" 2>/dev/null |
    if [ "$sort_flag" = true ]; then
      sort
    else
      cat
    fi |
    pipe_limit
  echo ""
  # Show summary
  total=$(find "$dir" -type f \
    -not -path '*/node_modules/*' \
    -not -path '*/.git/*' \
    -not -path '*/.cache/*' \
    -not -path '*/.husky/*' \
    -not -path '*/dist/*' 2>/dev/null | wc -l)
  echo "--- Total files: $total ---"
  exit 0
fi

# Dirs-only mode: list subdirectories
if [ "$dirs_only" = true ]; then
  echo "=== Subdirectories: $dir ==="
  echo ""
  find "$dir" -maxdepth 1 -type d \
    -not -name "." |
    if [ "$sort_flag" = true ]; then
      sort
    else
      cat
    fi |
    pipe_limit
  echo ""
  count=$(find "$dir" -maxdepth 1 -type d -not -name "." 2>/dev/null | wc -l)
  echo "--- $count subdirectories ---"
  exit 0
fi

# Files-only mode: list files only
if [ "$files_only" = true ]; then
  echo "=== Files: $dir ==="
  echo ""
  find "$dir" -maxdepth 1 -type f |
    if [ "$sort_flag" = true ]; then
      sort
    else
      cat
    fi |
    pipe_limit
  echo ""
  count=$(find "$dir" -maxdepth 1 -type f 2>/dev/null | wc -l)
  echo "--- $count files ---"
  exit 0
fi

# Default mode: detailed listing with ls -la
echo "=== Directory: $dir ==="
echo ""

if [ -n "$head_n" ]; then
  ls -la "$dir" 2>/dev/null | head -n "$head_n"
elif [ -n "$tail_n" ]; then
  ls -la "$dir" 2>/dev/null | tail -n "$tail_n"
else
  ls -la "$dir" 2>/dev/null
fi

echo ""

# Show summary
files=$(find "$dir" -maxdepth 1 -type f 2>/dev/null | wc -l)
dirs=$(find "$dir" -maxdepth 1 -type d 2>/dev/null | wc -l)
total_size=$(du -sh "$dir" 2>/dev/null | cut -f1)

echo "--- Summary ---"
echo "Files: $files"
echo "Dirs:  $((dirs - 1))"  # subtract the directory itself
echo "Size:  $total_size"
