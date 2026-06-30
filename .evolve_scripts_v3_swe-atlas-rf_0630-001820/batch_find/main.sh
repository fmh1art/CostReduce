#!/usr/bin/env bash
# batch_find - Find files by name pattern, path glob, type, with filtering, exclusion, and sorting.

set -euo pipefail

MAX_DEPTH=
NAME_PATTERNS=()
PATH_GLOB=
FILE_TYPE=
EXCLUDES=()
EXCLUDE_DIRS=()
EXCLUDE_NAMES=()
SEARCH_DIR="."
SORT=false
LIMIT=

usage() {
  echo "Usage: $0 [dir] [--name=GLOB...] [--path=PATH_GLOB] [--type=f|d] [--exclude=PATTERN...] [--exclude-dir=DIR...] [--exclude-name=GLOB...] [--max-depth=N] [--limit=N] [--sort]"
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --name=*)
      NAME_PATTERNS+=("${1#*=}")
      shift
      ;;
    --path=*)
      PATH_GLOB="${1#*=}"
      shift
      ;;
    --type=*)
      FILE_TYPE="${1#*=}"
      shift
      ;;
    --exclude=*)
      EXCLUDES+=("${1#*=}")
      shift
      ;;
    --exclude-dir=*)
      EXCLUDE_DIRS+=("${1#*=}")
      shift
      ;;
    --exclude-name=*)
      EXCLUDE_NAMES+=("${1#*=}")
      shift
      ;;
    --max-depth=*)
      MAX_DEPTH="${1#*=}"
      shift
      ;;
    --limit=*)
      LIMIT="${1#*=}"
      shift
      ;;
    --sort)
      SORT=true
      shift
      ;;
    --help|-h)
      usage
      ;;
    -*)
      echo "Unknown option: $1" >&2
      usage
      ;;
    *)
      SEARCH_DIR="$1"
      shift
      ;;
  esac
done

if [[ ! -d "$SEARCH_DIR" ]]; then
  echo "Error: directory not found: $SEARCH_DIR" >&2
  exit 1
fi

# Build find command args array
find_args=()

# Add max depth first (must come before other tests)
if [[ -n "$MAX_DEPTH" ]]; then
  find_args+=("-maxdepth" "$MAX_DEPTH")
fi

# Add the search dir
find_args+=("$SEARCH_DIR")

# Add type filter
if [[ -n "$FILE_TYPE" ]]; then
  find_args+=("-type" "$FILE_TYPE")
fi

# Add name patterns (OR logic using -o)
if [[ ${#NAME_PATTERNS[@]} -gt 0 ]]; then
  find_args+=("(")
  first=true
  for pattern in "${NAME_PATTERNS[@]}"; do
    if [[ "$first" == "true" ]]; then
      first=false
    else
      find_args+=("-o")
    fi
    find_args+=("-name" "$pattern")
  done
  find_args+=(")")
fi

# Add path glob
if [[ -n "$PATH_GLOB" ]]; then
  find_args+=("-path" "$PATH_GLOB")
fi

# Add excludes (repeatable, matches path glob)
for exc in "${EXCLUDES[@]}"; do
  find_args+=("!" "-path" "$exc")
done

# Add exclude-dir (repeatable, matches path containing dir name)
for exc in "${EXCLUDE_DIRS[@]}"; do
  find_args+=("!" "-path" "*/$exc/*")
  find_args+=("!" "-name" "$exc")
done

# Add exclude-name (repeatable, matches file name)
for exc in "${EXCLUDE_NAMES[@]}"; do
  find_args+=("!" "-name" "$exc")
done

# Execute
if [[ "$SORT" == "true" ]]; then
  if [[ -n "$LIMIT" ]]; then
    find "${find_args[@]}" 2>/dev/null | sort | head -n "$LIMIT"
  else
    find "${find_args[@]}" 2>/dev/null | sort
  fi
else
  if [[ -n "$LIMIT" ]]; then
    find "${find_args[@]}" 2>/dev/null | head -n "$LIMIT"
  else
    find "${find_args[@]}" 2>/dev/null
  fi
fi
