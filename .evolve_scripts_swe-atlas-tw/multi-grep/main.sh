#!/usr/bin/env bash
set -euo pipefail

# multi-grep: Search for multiple patterns across files or in a single file in one call
# Usage:
#   multi-grep [--include=GLOB] [--ignore-case] [--files-with-matches] [--context=N] [--exclude-pattern=PATTERN] <target> <pattern1> [pattern2 ...]
#   multi-grep [--ignore-case] [--files-with-matches] [--context=N] [--exclude-pattern=PATTERN] <file> <pattern1> [pattern2 ...]

INCLUDE_GLOB=
IGNORE_CASE=false
FILES_WITH_MATCHES=false
CONTEXT_LINES=0
EXCLUDE_PATTERN=
TARGET=
PATTERNS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --include=*)
      INCLUDE_GLOB="${1#*=}"
      shift
      ;;
    --ignore-case|-i)
      IGNORE_CASE=true
      shift
      ;;
    --files-with-matches|-l)
      FILES_WITH_MATCHES=true
      shift
      ;;
    --context=*|-C=*)
      CONTEXT_LINES="${1#*=}"
      shift
      ;;
    --context|-C)
      CONTEXT_LINES="$2"
      shift 2
      ;;
    --exclude-pattern=*|-v=*)
      EXCLUDE_PATTERN="${1#*=}"
      shift
      ;;
    --exclude-pattern|-v)
      EXCLUDE_PATTERN="$2"
      shift 2
      ;;
    -*)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
    *)
      if [[ -z "$TARGET" ]]; then
        TARGET="$1"
      else
        PATTERNS+=("$1")
      fi
      shift
      ;;
  esac
done

if [[ -z "$TARGET" || ${#PATTERNS[@]} -eq 0 ]]; then
  echo "Usage: $0 [--include=GLOB] [--ignore-case] [--files-with-matches] [--context=N] [--exclude-pattern=PATTERN] <target> <pattern1> [pattern2 ...]" >&2
  exit 1
fi

if [[ ! -e "$TARGET" ]]; then
  echo "Error: Target not found: $TARGET" >&2
  exit 1
fi

# Build the pattern expression
if [[ ${#PATTERNS[@]} -eq 1 ]]; then
  PATTERN_EXPR="${PATTERNS[0]}"
else
  PATTERN_STR=""
  sep=""
  for p in "${PATTERNS[@]}"; do
    PATTERN_STR="${PATTERN_STR}${sep}${p}"
    sep="|"
  done
  PATTERN_EXPR="$PATTERN_STR"
fi

# Build grep args
GREP_ARGS=(-n)
GREP_ARGS+=(-E)
if $IGNORE_CASE; then
  GREP_ARGS+=(-i)
fi
if $FILES_WITH_MATCHES; then
  GREP_ARGS+=(-l)
fi
if [[ "$CONTEXT_LINES" -gt 0 ]]; then
  GREP_ARGS+=(-C "$CONTEXT_LINES")
fi
if [[ -n "$EXCLUDE_PATTERN" ]]; then
  # Use pipe through grep -v for exclusion since -v with -e and -E is tricky
  if [[ -f "$TARGET" ]]; then
    GREP_ARGS+=(-e "$PATTERN_EXPR")
    grep "${GREP_ARGS[@]}" "$TARGET" 2>/dev/null | grep -v -E "$EXCLUDE_PATTERN" || true
    exit 0
  elif [[ -d "$TARGET" ]]; then
    FIND_CMD=(find "$TARGET" -type f)
    if [[ -n "$INCLUDE_GLOB" ]]; then
      FIND_CMD+=(-name "$INCLUDE_GLOB")
    fi
    "${FIND_CMD[@]}" -exec grep "${GREP_ARGS[@]}" -e "$PATTERN_EXPR" {} + 2>/dev/null | grep -v -E "$EXCLUDE_PATTERN" || true
    exit 0
  fi
fi

GREP_ARGS+=(-e "$PATTERN_EXPR")

if [[ -f "$TARGET" ]]; then
  # Single file mode
  grep "${GREP_ARGS[@]}" "$TARGET" 2>/dev/null || true
elif [[ -d "$TARGET" ]]; then
  # Directory mode: find files then grep
  FIND_CMD=(find "$TARGET" -type f)
  if [[ -n "$INCLUDE_GLOB" ]]; then
    FIND_CMD+=(-name "$INCLUDE_GLOB")
  fi
  "${FIND_CMD[@]}" -exec grep "${GREP_ARGS[@]}" {} + 2>/dev/null || true
else
  echo "Error: Target is neither a file nor a directory: $TARGET" >&2
  exit 1
fi
