#!/bin/bash
# Run tests (pytest or jest) with auto-detection or explicit --framework flag.
# Auto-detects: pytest if pytest.ini/setup.cfg/pyproject.toml exists; jest if jest.config.* exists.
# Can also be forced: --framework=pytest or --framework=jest
# Usage: run-tests/main.sh [--cd=DIR] [--config=FILE] [--env=KEY=VALUE]... [--framework=auto|pytest|jest]
#                       [--tail=N] [--head=N] [--grep=PATTERN] [--no-coverage] [--verbose]
#                       [test_path...]
#
# Common flags for both frameworks:
#   --cd=DIR:        Change to directory before running
#   --config=FILE:   Load env config file (KEY=VALUE) or jest config
#   --env=KEY=VALUE: Set env var (repeatable)
#   --tail=N:        Show last N lines (pytest default: 50, jest default: 30)
#   --head=N:        Show first N lines
#   --grep=PATTERN:  Filter output lines matching PATTERN (grep -E)
#   --no-coverage:   Disable coverage
#   --verbose/-v:    Verbose output
#   --quiet:         Suppress stderr (pytest only)
#   --timeout=N:     Run with timeout in seconds (pytest only)
#   --exitfirst/-x:  Stop on first failure (pytest only)
#   -s:              Disable output capture (pytest only)
#   --suppress-warnings: Filter Python deprecation noise (pytest only)
#   --python=PATH:   Explicit Python path (pytest only)
#   --clear-cache:   Remove jest cache before running (jest only)
#   --summary:       Show only test summary lines (jest only)
#   --cache/--no-cache: Enable/disable jest cache (jest only)

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
framework="auto"
clear_cache=false
summary_mode=false
cache_flag=""
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
    --framework=*) framework="${arg#*=}" ;;
    --quiet) quiet=true ;;
    --no-coverage) no_coverage=true ;;
    --verbose|-v) verbose_flag=true ;;
    --exitfirst|-x) exitfirst_flag=true ;;
    --capture=no|-s) capture_no=true ;;
    --suppress-warnings) suppress_warnings=true ;;
    --clear-cache) clear_cache=true ;;
    --summary) summary_mode=true ;;
    --no-cache) cache_flag="--no-cache" ;;
    --cache) cache_flag="--cache" ;;
    *)
      if [ -f "$arg" ] || [ -d "$arg" ]; then
        test_paths+=("$arg")
      else
        extra_args+=("$arg")
      fi
      ;;
  esac
done

# Change to working directory
if [ -n "$workdir" ]; then
  cd "$workdir" 2>/dev/null || { echo "Error: directory '$workdir' not found" >&2; exit 1; }
fi

# Auto-detect framework if set to auto
if [ "$framework" = "auto" ]; then
  if [ -f "pytest.ini" ] || [ -f "setup.cfg" ] || [ -f "pyproject.toml" ] || [ -f "tox.ini" ]; then
    # Check if pyproject.toml has pytest config
    if [ -f "pyproject.toml" ] && grep -q '\[tool.pytest' "pyproject.toml" 2>/dev/null; then
      framework="pytest"
    elif [ -f "pytest.ini" ]; then
      framework="pytest"
    elif [ -f "setup.cfg" ] && grep -q '\[tool:pytest' "setup.cfg" 2>/dev/null; then
      framework="pytest"
    fi
  fi
  if [ "$framework" = "auto" ]; then
    for f in jest.config.ts jest.config.js jest.config.mjs jest.config.cjs package.json; do
      if [ -f "$f" ]; then
        framework="jest"
        break
      fi
    done
  fi
  if [ "$framework" = "auto" ]; then
    # Default to pytest for python projects, jest for node projects
    if [ -f "venv/bin/python" ] || [ -f ".venv/bin/python" ]; then
      framework="pytest"
    elif [ -f "node_modules/.bin/jest" ]; then
      framework="jest"
    elif [ -f "package.json" ]; then
      framework="jest"
    else
      framework="pytest"
    fi
  fi
fi

# Apply inline env vars
for e in "${env_vars[@]}"; do
  export "$e"
done

# ---- Run tests ----
if [ "$framework" = "pytest" ]; then
  # Load env config file (only for pytest - jest uses --config as CLI arg)
  if [ -n "$config" ]; then
    if [ ! -f "$config" ]; then
      echo "Error: config file '$config' not found" >&2
      exit 1
    fi
    set -a
    source "$config"
    set +a
  fi
  # Default tail
  if [ -z "$head_n" ] && [ -z "$tail_n" ]; then
    tail_n=50
  fi

  # Find Python
  if [ -z "$python_bin" ]; then
    if [ -f "venv/bin/python" ]; then
      python_bin="venv/bin/python"
    elif [ -f ".venv/bin/python" ]; then
      python_bin=".venv/bin/python"
    else
      python_bin="python"
    fi
  fi

  # Build pytest args
  pytest_args=()
  [ "$verbose_flag" = true ] && pytest_args+=(-v)
  [ "$exitfirst_flag" = true ] && pytest_args+=(-x)
  [ "$capture_no" = true ] && pytest_args+=(-s)
  [ "$no_coverage" = true ] && pytest_args+=(--no-cov)
  pytest_args+=("${extra_args[@]}")
  pytest_args+=("${test_paths[@]}")

  capture_file=$(mktemp /tmp/run_tests_XXXXXX)

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

  # Apply --suppress-warnings
  if [ "$suppress_warnings" = true ]; then
    tmpfile=$(mktemp /tmp/run_tests_filter_XXXXXX)
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

  # Output filtering
  if [ -n "$grep_pattern" ]; then
    if [ -n "$head_n" ] && [ -n "$tail_n" ]; then
      grep -E "$grep_pattern" "$capture_file" | tail -n "$tail_n" | head -n "$head_n"
    elif [ -n "$head_n" ]; then
      head -n "$head_n" "$capture_file" | grep -E "$grep_pattern"
    elif [ -n "$tail_n" ]; then
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

elif [ "$framework" = "jest" ]; then
  # Default tail
  if [ -z "$tail_n" ]; then
    tail_n=30
  fi

  # Build jest args
  jest_args=()
  [ -n "$config" ] && jest_args+=("--config=$config")
  [ "$verbose_flag" = true ] && jest_args+=("--verbose")
  [ "$no_coverage" = true ] && jest_args+=("--no-coverage")
  [ -n "$cache_flag" ] && jest_args+=("$cache_flag")
  jest_args+=("${extra_args[@]}")
  jest_args+=("${test_paths[@]}")

  # Add --no-coverage by default
  if [ "$no_coverage" = false ] && ! echo "${jest_args[@]}" | grep -q -- '--coverage'; then
    jest_args+=(--no-coverage)
  fi

  # Clear cache
  if [ "$clear_cache" = true ]; then
    for cache_dir in "/app/.cache/jest" ".cache/jest" "$HOME/.cache/jest"; do
      [ -d "$cache_dir" ] && rm -rf "$cache_dir" 2>/dev/null
    done
  fi

  output_file=$(mktemp /tmp/run_tests_XXXXXX)
  npx jest "${jest_args[@]}" > "$output_file" 2>&1
  rc=$?

  if [ "$summary_mode" = true ]; then
    grep -E "^(PASS|FAIL|Test Suites:|Tests:|Snapshots:|Time:|Ran all test|\w+ \||  \xE2\x9C\x93|  \xE2\x9C\x97|  \u00d7)" "$output_file" | tail -n "$tail_n"
  elif [ -n "$grep_pattern" ]; then
    grep -E "$grep_pattern" "$output_file" | tail -n "$tail_n"
  else
    tail -n "$tail_n" "$output_file"
  fi

  rm -f "$output_file"
  exit $rc
fi
