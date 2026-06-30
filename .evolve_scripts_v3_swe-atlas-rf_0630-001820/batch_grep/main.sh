#!/usr/bin/env bash
# batch_grep - Search multiple patterns in files, with file type filtering and exclusion.

set -euo pipefail

IGNORE_CASE=false
MATCHES_ONLY=false
COUNT_ONLY=false
CONTEXT_LINES=
BEFORE_CONTEXT=
AFTER_CONTEXT=
INCLUDE_GLOBS=()
EXCLUDE_DIRS=()
EXCLUDE_PATTERNS=()
EXCLUDE_NAMES=()
HEAD_COUNT=
WORK_DIR=""
SEARCH_DIR=
PATTERNS=()
FILES=()

usage() {
  cat >&2 <<'EOF'
Usage: batch_grep [--dir=DIR] <pattern1> [pattern2...] [options]
       batch_grep [dir] <pattern1> [pattern2...] [options]
       batch_grep file1 [file2...] <pattern1> [pattern2...] [options]
Options:
  --dir=DIR                Working directory to cd into before searching
  --include=GLOB           File name glob filter (repeatable, OR logic)
  --exclude-dir=DIR        Exclude directory name (repeatable, e.g. node_modules .git)
  --exclude-name=GLOB     Exclude file names matching glob (repeatable, e.g. *_test.go)
  --exclude-pattern=PAT    Exclude lines matching pattern (repeatable, like grep -v)
  --ignore-case|-i         Case-insensitive search
  --files-with-matches|-l  List filenames only
  --count|-c               Count matching lines per file (like grep -c)
  --context=N|-C=N         Show N lines of context (before and after)
  --before-context=N|-B=N  Show N lines of context before match
  --after-context=N|-A=N   Show N lines of context after match
  --head=N                 Show first N matching results
EOF
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir=*)
      WORK_DIR="${1#*=}"
      shift
      ;;

    --include=*)
      INCLUDE_GLOBS+=("${1#*=}")
      shift
      ;;
    --exclude-dir=*)
      EXCLUDE_DIRS+=("${1#*=}")
      shift
      ;;
    --exclude-pattern=*)
      EXCLUDE_PATTERNS+=("${1#*=}")
      shift
    ;;
    --exclude-name=*)
      EXCLUDE_NAMES+=("${1#*=}")
      shift
      ;;
    --ignore-case|-i)
      IGNORE_CASE=true
      shift
      ;;
    --files-with-matches|-l)
      MATCHES_ONLY=true
      shift
      ;;
    --count|-c)
      COUNT_ONLY=true
      shift
      ;;
    --context=*|-C=*)
      CONTEXT_LINES="${1#*=}"
      shift
      ;;
    -C|--context)
      shift
      CONTEXT_LINES="$1"
      shift
      ;;
    --before-context=*|-B=*)
      BEFORE_CONTEXT="${1#*=}"
      shift
      ;;
    -B|--before-context)
      shift
      BEFORE_CONTEXT="$1"
      shift
      ;;
    --after-context=*|-A=*)
      AFTER_CONTEXT="${1#*=}"
      shift
      ;;
    -A|--after-context)
      shift
      AFTER_CONTEXT="$1"
      shift
      ;;
    --head=*)
      HEAD_COUNT="${1#*=}"
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
      if [[ -z "$SEARCH_DIR" && -d "$1" && ${#FILES[@]} -eq 0 && ${#PATTERNS[@]} -eq 0 ]]; then
        SEARCH_DIR="$1"
      elif [[ -f "$1" && ${#PATTERNS[@]} -eq 0 ]]; then
        FILES+=("$1")
      else
        PATTERNS+=("$1")
      fi
      shift
      ;;
  esac
done

if [[ -n "$WORK_DIR" ]]; then
  cd "$WORK_DIR"
fi

if [[ ${#PATTERNS[@]} -eq 0 ]]; then
  usage
fi

if [[ -z "$SEARCH_DIR" && ${#FILES[@]} -eq 0 ]]; then
  SEARCH_DIR="."
fi

# Build base grep args
if [[ "$COUNT_ONLY" == "true" ]]; then
  grep_base=("-c")
else
  grep_base=("-n")
fi
[[ "$IGNORE_CASE" == "true" ]] && grep_base+=("-i")
[[ "$MATCHES_ONLY" == "true" ]] && grep_base+=("-l")
[[ -n "$CONTEXT_LINES" ]] && grep_base+=("-C" "$CONTEXT_LINES")
[[ -n "$BEFORE_CONTEXT" ]] && grep_base+=("-B" "$BEFORE_CONTEXT")
[[ -n "$AFTER_CONTEXT" ]] && grep_base+=("-A" "$AFTER_CONTEXT")
for pattern in "${PATTERNS[@]}"; do
  grep_base+=("-e" "$pattern")
done

# Temp file for intermediate results
tmpfile=$(mktemp /tmp/batch_grep_XXXXXX)
trap 'rm -f "$tmpfile"' EXIT

# Run grep
if [[ ${#FILES[@]} -gt 0 ]]; then
  grep "${grep_base[@]}" "${FILES[@]}" > "$tmpfile" 2>/dev/null || true
else
  find_args=("$SEARCH_DIR" "-type" "f")
  for exc_dir in "${EXCLUDE_DIRS[@]}"; do
    find_args+=("!" "-path" "*/$exc_dir/*")
    find_args+=("!" "-name" "$exc_dir")
  done
  for exc_name in "${EXCLUDE_NAMES[@]}"; do
    find_args+=("!" "-name" "$exc_name")
  done
  if [[ ${#INCLUDE_GLOBS[@]} -gt 0 ]]; then
    if [[ ${#INCLUDE_GLOBS[@]} -eq 1 ]]; then
      find_args+=("-name" "${INCLUDE_GLOBS[0]}")
    else
      find_args+=("(")
      first=true
      for glob in "${INCLUDE_GLOBS[@]}"; do
        [[ "$first" == "true" ]] && first=false || find_args+=("-o")
        find_args+=("-name" "$glob")
      done
      find_args+=(")")
    fi
  fi
  find "${find_args[@]}" -exec grep "${grep_base[@]}" {} + > "$tmpfile" 2>/dev/null || true
fi

# Apply exclude patterns
for exc in "${EXCLUDE_PATTERNS[@]}"; do
  grep -v -e "$exc" "$tmpfile" > "${tmpfile}_2" 2>/dev/null || true
  mv "${tmpfile}_2" "$tmpfile"
done

# Apply head limit
if [[ -n "$HEAD_COUNT" ]]; then
  head -n "$HEAD_COUNT" "$tmpfile"
else
  cat "$tmpfile"
fi
