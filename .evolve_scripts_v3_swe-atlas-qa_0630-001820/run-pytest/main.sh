#!/bin/bash
# Run pytest tests with venv auto-detection, config loading, env vars, output limiting, grep filtering, warning suppression, and timeout
# Usage: run-pytest/main.sh [test_path...] [--cd=DIR] [--config=FILE] [--env KEY=VALUE]... [--tail=N] [--head=N] [--quiet] [--no-coverage] [--verbose|-v] [--exitfirst|-x] [--capture=no|-s] [--timeout=N] [--grep=PATTERN] [--suppress-warnings] [--python=PATH]
#   --cd=DIR:       Change to directory before running
#   --config=FILE:  Load env config file with KEY=VALUE lines
#   --env=KEY=VALUE: Set an env var (repeatable)
#   --tail=N:       Show last N lines of output (default: 50)
#   --head=N:       Show first N lines of output
#   --quiet:        Suppress stderr
#   --no-coverage:  Disable coverage (default: on for pytest-cov)
#   --verbose/-v:   Verbose output
#   --exitfirst/-x: Stop on first failure
#   --capture=no/-s: Disable output capture
#   --timeout=N:    Run with timeout in seconds
#   --grep=PATTERN: Filter output lines matching PATTERN (grep -E), useful for extracting PASSED|FAILED|ERROR lines
#   --suppress-warnings: Filter out common Python DeprecationWarning/noise lines (site-packages warnings, pkg_resources noise)
#   --python=PATH:  Explicit path to Python interpreter (e.g., /app/venv/bin/python); overrides auto-detection

workdir=""
config=""
env_vars=()
tail_n=""
head_n=""
quiet=false
no_coverage=false
verbose_flag=false
exitfirst_flag=false
capture_no=false
timeout_n=""
grep_pattern=""
suppress_warnings=false
python_bin=""
test_paths=()
extra_args=()

for arg in "$@"; do
  case "$arg" in
    --cd=*) workdir="${arg#*=}" ;;
    --config=*) config="${arg#*=}" ;;
    --env=*) env_vars+=("${arg#*=}") ;;
    --tail=*) tail_n="${arg#*=}" ;;
    --head=*) head_n="${arg#*=}" ;;
    --timeout=*) timeout_n="${arg#*=}" ;;
    --grep=*) grep_pattern="${arg#*=}" ;;
    --python=*) python_bin="${arg#*=}" ;;
    --quiet) quiet=true ;;
    --no-coverage) no_coverage=true ;;
    --verbose|-v) verbose_flag=true ;;
    --exitfirst|-x) exitfirst_flag=true ;;
    --capture=no|-s) capture_no=true ;;
    --suppress-warnings) suppress_warnings=true ;;
    *)
      if [ -f "$arg" ] || [ -d "$arg" ]; then
        test_paths+=("$arg")
      else
        extra_args+=("$arg")
      fi
      ;;
  esac
done

# Default tail if neither head nor tail specified
if [ -z "$head_n" ] && [ -z "$tail_n" ]; then
  tail_n=50
fi

# Change to working directory
if [ -n "$workdir" ]; then
  cd "$workdir" 2>/dev/null || { echo "Error: directory '$workdir' not found" >&2; exit 1; }
fi

# Load config file
if [ -n "$config" ]; then
  if [ ! -f "$config" ]; then
    echo "Error: config file '$config' not found" >&2
    exit 1
  fi
  set -a
  source "$config"
  set +a
fi

# Apply inline env vars
for e in "${env_vars[@]}"; do
  export "$e"
done

# Find Python (venv auto-detection or explicit --python)
if [ -z "$python_bin" ]; then
  if [ -f "venv/bin/python" ]; then
    python_bin="venv/bin/python"
  elif [ -f ".venv/bin/python" ]; then
    python_bin=".venv/bin/python"
  else
    python_bin="python"
  fi
fi

# Build pytest args array
pytest_args=()

if [ "$verbose_flag" = true ]; then
  pytest_args+=(-v)
fi
if [ "$exitfirst_flag" = true ]; then
  pytest_args+=(-x)
fi
if [ "$capture_no" = true ]; then
  pytest_args+=(-s)
fi
if [ "$no_coverage" = true ]; then
  pytest_args+=(--no-cov)
fi

# Add any extra args (like -k pattern)
pytest_args+=("${extra_args[@]}")

# Add test paths
if [ ${#test_paths[@]} -gt 0 ]; then
  pytest_args+=("${test_paths[@]}")
fi

# ---- Run pytest ----
# Use a temp file to capture full output (both stdout and stderr)
capture_file=$(mktemp /tmp/run_pytest_XXXXXX)

# Run with timeout if needed
if [ -n "$timeout_n" ]; then
  if [ "$quiet" = true ]; then
    timeout "$timeout_n" "$python_bin" -m pytest "${pytest_args[@]}" > "$capture_file" 2>/dev/null
  else
    timeout "$timeout_n" "$python_bin" -m pytest "${pytest_args[@]}" > "$capture_file" 2>&1
  fi
else
  if [ "$quiet" = true ]; then
    "$python_bin" -m pytest "${pytest_args[@]}" > "$capture_file" 2>/dev/null
  else
    "$python_bin" -m pytest "${pytest_args[@]}" > "$capture_file" 2>&1
  fi
fi
rc=$?

# Apply --suppress-warnings first (filter out common Python noise lines)
if [ "$suppress_warnings" = true ]; then
  tmpfile=$(mktemp /tmp/run_pytest_filter_XXXXXX)
  # Remove lines matching common deprecation/noise patterns from site-packages
  grep -v -E \
    -e 'DeprecationWarning' \
    -e 'PendingDeprecationWarning' \
    -e 'ImportWarning' \
    -e 'setDaemon\(\) is deprecated' \
    -e 'pkg_resources.*declare_namespace' \
    -e 'Deprecated call to' \
    -e 'site-packages/.*:\d+:.*Warning' \
    -e '^\s*(venv|\/app\/venv)/lib/' \
    "$capture_file" > "$tmpfile" 2>/dev/null
  mv "$tmpfile" "$capture_file"
fi

# Apply output filtering
if [ -n "$grep_pattern" ]; then
  if [ -n "$head_n" ] && [ -n "$tail_n" ]; then
    # When both --grep and --tail are specified: grep first (across all output) then tail
    grep -E "$grep_pattern" "$capture_file" | tail -n "$tail_n" | head -n "$head_n"
  elif [ -n "$head_n" ]; then
    head -n "$head_n" "$capture_file" | grep -E "$grep_pattern"
  elif [ -n "$tail_n" ]; then
    # grep first across ALL output, then tail to get last N matching lines
    grep -E "$grep_pattern" "$capture_file" | tail -n "$tail_n"
  else
    grep -E "$grep_pattern" "$capture_file"
  fi
elif [ -n "$head_n" ] && [ -n "$tail_n" ]; then
  tail -n "$tail_n" "$capture_file"
elif [ -n "$head_n" ]; then
  head -n "$head_n" "$capture_file"
elif [ -n "$tail_n" ]; then
  tail -n "$tail_n" "$capture_file"
else
  cat "$capture_file"
fi

rm -f "$capture_file"
exit $rc
