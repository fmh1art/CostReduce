#!/usr/bin/env bash
# batch_go - Run Go build, test, vet, fmt, or check-syntax on multiple packages in one call.
# Supports --fmt (gofmt -w), --run (test filter), --timeout, --count, --grep, --head for output filtering.

set -euo pipefail

ACTIONS=()
PACKAGES=()
EXTRA_ARGS=()
RUN_FILTER=""
TIMEOUT=""
GREP_PATTERNS=()
HEAD_COUNT=""
WORK_DIR=""

usage() {
  cat >&2 <<'EOF'
Usage: $0 [--dir=DIR] [--build] [--test] [--vet] [--test-compile] [--check-syntax]
          [--tags=TAGS] [--count=N] [--race] [--verbose|-v]
          [--run=PATTERN] [--timeout=DURATION]
          [--grep=PATTERN] [--head=N]
          pkg1 [pkg2...]

Actions (combine multiple): --build, --test, --vet, --test-compile, --check-syntax
Format actions: --fmt (run gofmt -w to format .go files in-place)
Test options: --run=PATTERN (e.g. "TestFoo|TestBar"), --timeout=60s, --count=N
Output filtering: --grep=PATTERN (filter output lines, repeatable), --head=N (first N lines)
EOF
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --build)
      ACTIONS+=("build")
      shift
      ;;
    --test)
      ACTIONS+=("test")
      shift
      ;;
    --vet)
      ACTIONS+=("vet")
      shift
      ;;
    --test-compile|-c)
      ACTIONS+=("test")
      EXTRA_ARGS+=("-c")
      shift
      ;;
    --tags=*)
      EXTRA_ARGS+=("-tags" "${1#*=}")
      shift
      ;;
    --count=*)
      EXTRA_ARGS+=("-count" "${1#*=}")
      shift
      ;;
    --race)
      EXTRA_ARGS+=("-race")
      shift
      ;;
    --verbose|-v)
      EXTRA_ARGS+=("-v")
      shift
      ;;
    --check-syntax)
      ACTIONS+=("check-syntax")
      shift
      ;;
    --fmt)
      ACTIONS+=("fmt")
      shift
      ;;
    --run=*)
      RUN_FILTER="${1#*=}"
      shift
      ;;
    --timeout=*)
      TIMEOUT="${1#*=}"
      shift
      ;;
    --grep=*)
      GREP_PATTERNS+=("${1#*=}")
      shift
      ;;
    --head=*)
      HEAD_COUNT="${1#*=}"
      shift
      ;;
    --help|-h)
      usage
      ;;
    --dir=*)
      WORK_DIR="${1#*=}"
      shift
      ;;
    -*)
      echo "Unknown option: $1" >&2
      usage
      ;;
    *)
      PACKAGES+=("$1")
      shift
      ;;
  esac
done

# Change to working directory if specified
if [[ -n "$WORK_DIR" ]]; then
  cd "$WORK_DIR"
fi

if [[ ${#ACTIONS[@]} -eq 0 ]]; then
  ACTIONS=("build")
fi

if [[ ${#PACKAGES[@]} -eq 0 ]]; then
  PACKAGES=("./...")
fi

for pkg in "${PACKAGES[@]}"; do
  for action in "${ACTIONS[@]}"; do
    output=""
    case "$action" in
      build)
        output=$(go build "${EXTRA_ARGS[@]}" "$pkg" 2>&1) || true
        ;;
      test)
        TEST_ARGS=("${EXTRA_ARGS[@]}")
        if [[ -n "$RUN_FILTER" ]]; then
          TEST_ARGS+=("-run" "$RUN_FILTER")
        fi
        if [[ -n "$TIMEOUT" ]]; then
          TEST_ARGS+=("-timeout" "$TIMEOUT")
        fi
        output=$(go test "${TEST_ARGS[@]}" "$pkg" 2>&1) || true
        ;;
      vet)
        output=$(go vet "${EXTRA_ARGS[@]}" "$pkg" 2>&1) || true
        ;;
      check-syntax)
        if [[ -f "$pkg" ]]; then
          if gofmt -e "$pkg" > /dev/null 2>&1; then
            output="Syntax OK: $pkg"
          else
            output=$(gofmt -e "$pkg" 2>&1) || true
          fi
        else
          # For packages, find .go files and check each
          output=$(find "$pkg" -name '*.go' ! -path '*/vendor/*' 2>/dev/null | while read -r f; do
            if ! gofmt -e "$f" > /dev/null 2>&1; then
              gofmt -e "$f" 2>&1 || true
            fi
          done)
          if [[ -z "$output" ]]; then
            output="Syntax OK for $pkg"
          fi
        fi
        ;;
      fmt)
        if [[ -f "$pkg" ]]; then
          if gofmt -w "$pkg" 2>&1; then
            output="Formatted: $pkg"
          else
            output=$(gofmt -w "$pkg" 2>&1) || true
          fi
        else
          # For packages/directories, format .go files in-place
          formatted_count=0
          while IFS= read -r -d '' f; do
            if gofmt -w "$f" 2>/dev/null; then
              formatted_count=$((formatted_count + 1))
            fi
          done < <(find "$pkg" -name '*.go' ! -path '*/vendor/*' -print0 2>/dev/null)
          if [[ "$formatted_count" -gt 0 ]]; then
            output="Formatted $formatted_count file(s) in $pkg"
          else
            output="No .go files found in $pkg"
          fi
        fi
        ;;
    esac

    # Apply grep filters
    if [[ -n "$output" && ${#GREP_PATTERNS[@]} -gt 0 ]]; then
      filtered="$output"
      for pat in "${GREP_PATTERNS[@]}"; do
        filtered=$(echo "$filtered" | grep -E "$pat" 2>/dev/null || true)
      done
      output="$filtered"
    fi

    # Apply head limit
    if [[ -n "$output" && -n "$HEAD_COUNT" ]]; then
      output=$(echo "$output" | head -n "$HEAD_COUNT")
    fi

    if [[ -n "$output" ]]; then
      echo "$output"
    fi
  done
done
