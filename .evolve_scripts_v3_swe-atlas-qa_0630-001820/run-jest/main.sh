#!/bin/bash
# Run jest tests with common flags (--cache/--no-cache, --verbose, --no-coverage, --clear-cache, --env)
# Usage: run-jest/main.sh [--cd=DIR] [--config=FILE] [test_path] [--cache|--no-cache] [--verbose] [--no-coverage] [--tail=N] [--clear-cache] [--summary] [--grep=PATTERN] [--env=KEY=VALUE]...
#   --tail=N:      Show last N lines of output (default: 30)
#   --cache:       Explicitly enable jest cache (default: jest uses cache unless --no-cache is passed)
#   --no-cache:    Disable jest cache (--no-cache flag), forces full re-transform
#   --clear-cache: Remove jest cache before running (replaces rm -rf .cache/jest + run-jest)
#   --summary:     Show only test result summary lines (PASS/FAIL, Test Suites, Tests, Snapshots, Time) - filters out noise like build warnings
#   --grep=PATTERN: Filter output lines matching PATTERN (grep -E), useful for extracting specific test results
#   --env=KEY=VALUE: Set environment variable before running jest (repeatable, e.g. --env=TZ=UTC --env=NODE_ENV=test)

args=()
has_verbose_flag=false
has_coverage_flag=false
workdir=""
tail_n=30
clear_cache=false
summary_mode=false
grep_pattern=""
env_vars=()

for arg in "$@"; do
  if [[ "$arg" =~ ^--cd=(.*)$ ]]; then
    workdir="${BASH_REMATCH[1]}"
  elif [[ "$arg" =~ ^--config=(.*)$ ]]; then
    args+=("--config=${BASH_REMATCH[1]}")
  elif [[ "$arg" =~ ^--tail=(.*)$ ]]; then
    tail_n="${BASH_REMATCH[1]}"
  elif [[ "$arg" =~ ^--grep=(.*)$ ]]; then
    grep_pattern="${BASH_REMATCH[1]}"
  elif [[ "$arg" =~ ^--env=(.*)$ ]]; then
    env_vars+=("${BASH_REMATCH[1]}")
  elif [ "$arg" = "--no-cache" ] || [ "$arg" = "--cache" ]; then
    args+=("$arg")
  elif [ "$arg" = "--verbose" ] || [ "$arg" = "-v" ]; then
    has_verbose_flag=true
    args+=("$arg")
  elif [ "$arg" = "--no-coverage" ]; then
    has_coverage_flag=true
    args+=("$arg")
  elif [ "$arg" = "--clear-cache" ]; then
    clear_cache=true
  elif [ "$arg" = "--summary" ]; then
    summary_mode=true
  else
    args+=("$arg")
  fi
done

# Change to workdir first so cache path is resolved relative to it
if [ -n "$workdir" ]; then
  cd "$workdir" 2>/dev/null || { echo "Error: directory '$workdir' not found" >&2; exit 1; }
fi

# Clear jest cache before running if requested
if [ "$clear_cache" = true ]; then
  for cache_dir in "/app/.cache/jest" ".cache/jest" "$HOME/.cache/jest"; do
    if [ -d "$cache_dir" ]; then
      rm -rf "$cache_dir" 2>/dev/null
    fi
  done
fi

# Add --no-coverage by default if not explicitly set
if [ "$has_coverage_flag" = false ]; then
  args+=(--no-coverage)
fi

# Capture both stdout and stderr to temp file for filtering
output_file=$(mktemp /tmp/run_jest_XXXXXX)

if [ ${#env_vars[@]} -gt 0 ]; then
  # Export env vars, then run jest
  for ev in "${env_vars[@]}"; do
    export "$ev"
  done
  npx jest "${args[@]}" > "$output_file" 2>&1
else
  npx jest "${args[@]}" > "$output_file" 2>&1
fi
rc=$?

if [ "$summary_mode" = true ]; then
  # Show only test result summary lines (PASS/FAIL, Test Suites, Tests, Snapshots, Time)
  # This filters out noise like build/deprecation warnings
  grep -E "^(PASS|FAIL|Test Suites:|Tests:|Snapshots:|Time:|Ran all test|\w+ \|)|  \xE2\x9C\x93|  \xE2\x9C\x97|  \u00d7" "$output_file" | tail -n "$tail_n"
elif [ -n "$grep_pattern" ]; then
  grep -E "$grep_pattern" "$output_file" | tail -n "$tail_n"
else
  tail -n "$tail_n" "$output_file"
fi

rm -f "$output_file"
exit $rc
