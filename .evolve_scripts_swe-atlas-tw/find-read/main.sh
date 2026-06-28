#!/usr/bin/env bash
set -euo pipefail

# find-read: Find files by name pattern and optionally read or grep their contents in one step.
# Merges the common find + cat pattern into a single call.
# With --names-only, returns just file paths (replaces multi-find).
# With --grep, searches file contents for a pattern (replaces find + xargs grep).
# Usage: find-read <directory> --name=<glob> [--name=<glob2> ...] [options]

DIR=""
NAMES=()
MAX_DEPTH=
EXCLUDE=
PATH_PATTERN=
LIMIT=
FTYPE=
SORT=false
NAMES_ONLY=false
NO_DEFAULT_EXCLUDES=false
GREP_PATTERN=
GREP_IGNORE_CASE=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --name=*|-n=*)
      NAMES+=("${1#*=}")
      shift
      ;;
    --max-depth=*|-d=*)
      MAX_DEPTH="${1#*=}"
      shift
      ;;
    --path=*)
      PATH_PATTERN="${1#*=}"
      shift
      ;;
    --exclude=*)
      EXCLUDE="${1#*=}"
      shift
      ;;
    --limit=*|-l=*)
      LIMIT="${1#*=}"
      shift
      ;;
    --type=*|-t=*)
      FTYPE="${1#*=}"
      shift
      ;;
    --sort|-s)
      SORT=true
      shift
      ;;
    --names-only)
      NAMES_ONLY=true
      shift
      ;;
    --no-exclude-defaults)
      NO_DEFAULT_EXCLUDES=true
      shift
      ;;
    --grep=*)
      GREP_PATTERN="${1#*=}"
      shift
      ;;
    --grep)
      GREP_PATTERN="$2"
      shift 2
      ;;
    --grep-ignore-case|--grep-i)
      GREP_IGNORE_CASE=true
      shift
      ;;
    -*)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
    *)
      if [[ -z "$DIR" ]]; then
        DIR="$1"
      else
        echo "Error: unexpected argument: $1" >&2
        exit 1
      fi
      shift
      ;;
  esac
done

if [[ -z "$DIR" ]]; then
  echo "Usage: $0 <directory> --name=<glob> [--name=<glob2> ...] [--max-depth=N] [--path=PATTERN] [--exclude=PATTERN] [--limit=N] [--sort] [--names-only] [--type=f|d] [--no-exclude-defaults] [--grep=PATTERN] [--grep-ignore-case]" >&2
  exit 1
fi

if [[ ! -d "$DIR" ]]; then
  echo "Error: Directory not found: $DIR" >&2
  exit 1
fi

# Build find command
FIND_CMD=(find "$DIR")

if [[ -n "$MAX_DEPTH" ]]; then
  FIND_CMD+=(-maxdepth "$MAX_DEPTH")
fi

# Add type filter (default to files when reading or grepping)
if [[ "$FTYPE" == "d" ]]; then
  FIND_CMD+=(-type d)
elif [[ -z "$FTYPE" ]] && ( $NAMES_ONLY || [[ -z "$GREP_PATTERN" ]] ); then
  FIND_CMD+=(-type f)
elif [[ "$FTYPE" == "f" ]]; then
  FIND_CMD+=(-type f)
fi

# Add name patterns with proper grouping
if [[ ${#NAMES[@]} -gt 0 ]]; then
  NAME_COND=()
  for name in "${NAMES[@]}"; do
    if [[ ${#NAME_COND[@]} -eq 0 ]]; then
      NAME_COND+=(-name "$name")
    else
      NAME_COND+=(-o -name "$name")
    fi
  done
  if [[ ${#NAME_COND[@]} -gt 0 ]]; then
    FIND_CMD+=( \( "${NAME_COND[@]}" \) )
  fi
fi

# Add path pattern (like -path "*query*")
if [[ -n "$PATH_PATTERN" ]]; then
  FIND_CMD+=(-path "$PATH_PATTERN")
fi

# Add exclude
if [[ -n "$EXCLUDE" ]]; then
  FIND_CMD+=(-not -path "$EXCLUDE")
fi

# Default excludes (can be disabled with --no-exclude-defaults)
if ! $NO_DEFAULT_EXCLUDES; then
  FIND_CMD+=(-not -path '*/node_modules/*')
  FIND_CMD+=(-not -path '*/.git/*')
  FIND_CMD+=(-not -path '*/__pycache__/*')
  FIND_CMD+=(-not -path '*/venv/*')
fi

# Collect matching files
if $SORT; then
  if [[ -n "$LIMIT" ]]; then
    mapfile -t FILES < <("${FIND_CMD[@]}" 2>/dev/null | sort | head -n "$LIMIT" || true)
  else
    mapfile -t FILES < <("${FIND_CMD[@]}" 2>/dev/null | sort || true)
  fi
else
  if [[ -n "$LIMIT" ]]; then
    mapfile -t FILES < <("${FIND_CMD[@]}" 2>/dev/null | head -n "$LIMIT" || true)
  else
    mapfile -t FILES < <("${FIND_CMD[@]}" 2>/dev/null || true)
  fi
fi

if [[ ${#FILES[@]} -eq 0 ]]; then
  echo "No files matched the search criteria."
  exit 0
fi

# --names-only: just print paths and exit
if $NAMES_ONLY; then
  printf '%s\n' "${FILES[@]}"
  exit 0
fi

# --grep: search contents for pattern
if [[ -n "$GREP_PATTERN" ]]; then
  GREP_ARGS=(-n)
  if $GREP_IGNORE_CASE; then
    GREP_ARGS+=(-i)
  fi
  GREP_ARGS+=(-e "$GREP_PATTERN")
  grep "${GREP_ARGS[@]}" "${FILES[@]}" 2>/dev/null || true
  exit 0
fi

# Read each file with header
for file in "${FILES[@]}"; do
  echo "--- $file ---"
  cat "$file"
done
