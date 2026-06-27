#!/bin/bash
# Script: source_reader
# Description: Read source files with line numbers, search for patterns, show function definitions
# Supports batch mode: pass multiple files separated by commas, each with optional :line_range suffix
# Also supports glob patterns in batch mode (e.g. "/app/internal/module/*.go" batch)
# Usage: main.sh <file_path> [action=grep|lines|functions|all|batch] [pattern_or_line_start] [line_end]
#        main.sh "file1.py:10-30,file2.py:40-60" batch  -> read specific ranges from multiple files
#        main.sh "/app/internal/module/*.go" batch       -> read all matching files (first 50 lines each)

FILE_SPEC="$1"
ACTION="${2:-all}"
ARG3="${3:-}"
ARG4="${4:-}"

# Check if spec contains glob characters
is_glob() {
  case "$1" in
    *\**|*\?*|*\[*) return 0 ;;
    *) return 1 ;;
  esac
}

# Batch mode: either action="batch" or comma-separated file specs
if [ "$ACTION" = "batch" ] || echo "$FILE_SPEC" | grep -qE ',[^/]*:'; then
  
  # If the spec is a glob pattern (contains * ? or [), expand it first
  if is_glob "$FILE_SPEC" && [ "$ACTION" = "batch" ]; then
    # Expand glob using bash's glob expansion
    shopt -s nullglob
    # Use eval to expand the glob pattern
    eval "GLOB_FILES=($FILE_SPEC)"
    shopt -u nullglob
    
    if [ ${#GLOB_FILES[@]} -eq 0 ]; then
      echo "WARNING: No files matched glob pattern: $FILE_SPEC"
      exit 0
    fi
    for filepath in "${GLOB_FILES[@]}"; do
      if [ ! -f "$filepath" ]; then
        continue
      fi
      start=1
      end=50
      total=$(wc -l < "$filepath")
      echo "========================================"
      echo "=== File: $filepath (lines $start-$end / $total total) ==="
      echo "========================================"
      nl -ba "$filepath" | sed -n "${start},${end}p"
      echo ""
    done
    exit 0
  fi
  
  IFS=',' read -ra FILES <<< "$FILE_SPEC"
  for file_spec in "${FILES[@]}"; do
    file_spec=$(echo "$file_spec" | xargs)
    if echo "$file_spec" | grep -q ':'; then
      filepath="${file_spec%%:*}"
      range="${file_spec#*:}"
      start="${range%-*}"
      end="${range#*-}"
    else
      filepath="$file_spec"
      start=1
      end=50
    fi
    if [ ! -f "$filepath" ]; then
      echo "ERROR: File not found: $filepath"
      continue
    fi
    total=$(wc -l < "$filepath")
    echo "========================================"
    echo "=== File: $filepath (lines $start-$end / $total total) ==="
    echo "========================================"
    nl -ba "$filepath" | sed -n "${start},${end}p"
    echo ""
  done
  exit 0
fi

FILE="$FILE_SPEC"

if [ -z "$FILE" ]; then
  echo "ERROR: No file specified."
  echo "Usage:"
  echo "  main.sh <file> [action] [pattern|line_start] [line_end]"
  echo "  main.sh 'file1:1-50,file2:10-30,...' batch"
  echo "  main.sh '/path/to/files/*.go' batch"
  echo "Actions: grep <pattern>, lines <start> <end>, functions, all (default)"
  exit 1
fi

if [ ! -f "$FILE" ]; then
  echo "ERROR: File not found: $FILE"
  exit 1
fi

TOTAL_LINES=$(wc -l < "$FILE")
echo "=== File: $FILE ==="
echo "Total lines: $TOTAL_LINES"
echo ""

case "$ACTION" in
  grep)
    PATTERN="${ARG3:-def }"
    echo "--- Grep for pattern: '$PATTERN' ---"
    grep -n "$PATTERN" "$FILE" | head -80
    ;;
  lines)
    START="${ARG3:-1}"
    END="${ARG4:-$TOTAL_LINES}"
    # If END > total, cap it
    [ "$END" -gt "$TOTAL_LINES" ] && END=$TOTAL_LINES
    echo "--- Lines $START-$END ---"
    nl -ba "$FILE" | sed -n "${START},${END}p"
    ;;
  functions)
    echo "--- Function/Method/Class Definitions ---"
    grep -n "def \|class \|async def \|func \|type \|struct \|interface " "$FILE" | head -80
    ;;
  all|*)
    echo "--- Function/Method/Class Definitions ---"
    grep -n "def \|class \|async def \|func \|type \|struct \|interface " "$FILE" | head -40
    echo ""
    START="${ARG3:-1}"
    END="${ARG4:-50}"
    [ "$END" -gt "$TOTAL_LINES" ] && END=$TOTAL_LINES
    echo "--- Lines $START-$END ---"
    nl -ba "$FILE" | sed -n "${START},${END}p"
    ;;
esac
