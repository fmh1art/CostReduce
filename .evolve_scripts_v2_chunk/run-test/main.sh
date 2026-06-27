#!/bin/bash
# run-test: Run a test/check command with timeout and trim noisy output to essential lines.
# Usage: run-test/main.sh [options] -- <command> [args...]
# Options:
#   -t, --timeout <secs>  Timeout in seconds (default: 60)
#   -p, --python-pytest   Shortcut: python3 -m pytest <args> with auto-timeout
#   -g, --go-test         Shortcut: go test <args> with auto-timeout
#   --ts, --ts-jest, --jest Shortcut: npx jest <args> with auto-timeout
#   --vitest              Shortcut: npx vitest run <args> with auto-timeout and noise filtering
#   --cargo, --cargo-test Shortcut: cargo test <args> with auto-timeout, shows only errors/summary
#   --cargo-build         Shortcut: cargo build <args> with auto-timeout, shows only errors/warnings
#   --mocha              Shortcut: npx mocha <args> with auto-timeout, shows only pass/fail summary lines
#   --cargo-check         Shortcut: cargo check <args> with auto-timeout, shows only errors/warnings
#   --prettier            Shortcut: npx prettier --check <args> with auto-timeout, shows only unformatted files
#   --grep <pattern>      Filter test names (passed to -k for pytest, -run for go, -t for jest/vitest/cargo)
#   --head <N>            Show only first N lines of output (default: all)
#   -q, --quiet           Only show pass/fail summary (strip all details)
#   -h, --help            Show usage
#   -C, --dir <dir>       Working directory to run the command in (default: .)

set -euo pipefail

workdir="."
timeout_secs=60
mode=""
cmd_parts=()
quiet=false
head_lines=0

while [ $# -gt 0 ]; do
  case "$1" in
    -t|--timeout)
      [ $# -lt 2 ] && { echo "ERROR: --timeout requires a number" >&2; exit 1; }
      timeout_secs="$2"
      shift 2
      ;;
    -C|--dir)
      [ $# -lt 2 ] && { echo "ERROR: --dir requires a directory" >&2; exit 1; }
      workdir="$2"
      shift 2
      ;;
    -p|--python-pytest)
      mode="pytest"
      shift
      ;;
    -g|--go-test)
      mode="go"
      shift
      ;;
    --ts|--ts-jest|--jest)
      mode="jest"
      shift
      ;;
    --vitest)
      mode="vitest"
      shift
      ;;
    --cargo|--cargo-test)
      mode="cargo"
      shift
      ;;
    --cargo-build)
      mode="cargo-build"
      shift
      ;;
    --cargo-check)
      mode="cargo-check"
      shift
      ;;
    --prettier)
      mode="prettier"
      shift
      ;;
    --mocha)
      mode="mocha"
      shift
      ;;
    --grep)
      [ $# -lt 2 ] && { echo "ERROR: --grep requires a pattern" >&2; exit 1; }
      grep_pattern="$2"
      shift 2
      ;;
    --head)
      [ $# -lt 2 ] && { echo "ERROR: --head requires a number" >&2; exit 1; }
      head_lines="$2"
      shift 2
      ;;
    -q|--quiet)
      quiet=true
      shift
      ;;
    -h|--help)
      echo "Usage: $0 [options] -- <command> [args...]"

      echo "  -C, --dir <dir>     Working directory to run the command in"
      echo "  -t, --timeout <secs>  Timeout in seconds (default: 60)"
      echo "  -p, --python-pytest   Shortcut: python3 -m pytest <args>"
      echo "  -g, --go-test         Shortcut: go test <args>"
      echo "  --ts, --ts-jest, --jest Shortcut: npx jest <args>"
      echo "  --vitest              Shortcut: npx vitest run <args>"
      echo "  --cargo, --cargo-test Shortcut: cargo test <args>"
      echo "  --cargo-build         Shortcut: cargo build <args>"
      echo "  --cargo-check         Shortcut: cargo check <args>"
      echo "  --prettier            Shortcut: npx prettier --check <args>"
      echo "  --mocha               Shortcut: npx mocha <args> with pass/fail summary"
      echo "  --grep <pattern>      Filter test names"
      echo "  --head <N>            Show only first N lines of output (default: all)"
      echo "  -q, --quiet           Only show pass/fail summary"
      echo "  -h, --help            Show this help"
      exit 0
      ;;
    --)
      shift
      break
      ;;
    -*)
      echo "ERROR: Unknown option: $1" >&2
      exit 1
      ;;
    *)
      cmd_parts+=("$1")
      shift
      ;;
  esac
done

# Remaining args are the command
if [ $# -gt 0 ]; then
  cmd_parts+=("$@")
fi

# Change to working directory
cd "$workdir"

# Build the actual command based on mode
if [ "$mode" = "pytest" ]; then
  cmd=(python3 -m pytest "${cmd_parts[@]}")
  if [ -n "${grep_pattern:-}" ]; then
    cmd+=(-k "$grep_pattern")
  fi
elif [ "$mode" = "go" ]; then
  cmd=(go test -v "${cmd_parts[@]}")
  if [ -n "${grep_pattern:-}" ]; then
    cmd+=(-run "$grep_pattern")
  fi
elif [ "$mode" = "jest" ]; then
  cmd=(npx jest "${cmd_parts[@]}")
  if [ -n "${grep_pattern:-}" ]; then
    cmd+=(-t "$grep_pattern")
  fi
elif [ "$mode" = "vitest" ]; then
  cmd=(npx vitest run --reporter=verbose "${cmd_parts[@]}")
  if [ -n "${grep_pattern:-}" ]; then
    cmd+=(-t "$grep_pattern")
  fi
elif [ "$mode" = "cargo" ]; then
  cmd=(cargo test "${cmd_parts[@]}")
  if [ -n "${grep_pattern:-}" ]; then
    cmd+=(-- "$grep_pattern")
  fi
elif [ "$mode" = "mocha" ]; then
  cmd=(npx mocha "${cmd_parts[@]}")
  if [ -n "${grep_pattern:-}" ]; then
    cmd+=(-g "$grep_pattern")
  fi
elif [ "$mode" = "cargo-build" ]; then
  cmd=(cargo build "${cmd_parts[@]}")
elif [ "$mode" = "cargo-check" ]; then
  cmd=(cargo check "${cmd_parts[@]}")
elif [ "$mode" = "prettier" ]; then
  if [ ${#cmd_parts[@]} -eq 0 ]; then
    echo "ERROR: --prettier requires a glob pattern (e.g. 'src/**/*.ts')" >&2
    exit 1
  fi
  cmd=(npx prettier --check "${cmd_parts[@]}")
else
  if [ ${#cmd_parts[@]} -eq 0 ]; then
    echo "ERROR: No command specified" >&2
    exit 1
  fi
  cmd=("${cmd_parts[@]}")
fi

# Run with timeout, capture output and exit code
set +e
output=$(timeout "$timeout_secs" "${cmd[@]}" 2>&1)
rc=$?
set -e

# Print exit code info if timeout or error
if [ $rc -eq 124 ]; then
  echo "TIMEOUT after ${timeout_secs}s"
fi

# Apply head limit helper
apply_head() {
  if [ "$head_lines" -gt 0 ]; then
    head -n "$head_lines"
  else
    cat
  fi
}

# Filter output based on mode and quiet flag
if [ "$quiet" = true ]; then
  # Only show pass/fail summary
  echo "$output" | grep -iE '(passed|failed|error|FAIL|ok|TIMEOUT|^[0-9]+ passed|[0-9]+ failed|test result|error\[|Error )' 2>/dev/null | apply_head || true
elif [ "$mode" = "go" ]; then
  # For go test: keep test-framework lines (RUN / PASS / FAIL / ok / --- results),
  # diff hunks (lines starting with + or - but not +++/---), and the final summary.
  # Drop verbose test-data output. Everything is capped by --head if requested.
  echo "$output" | grep -E '(^=== RUN |^--- (PASS|FAIL)|^    --- (FAIL|PASS)|^ok |^FAIL|^--- FAIL|^PASS|^\? |^=== CONT|^=== PAUSE|^=== NAME|^[+][^+]|^-[^-])' 2>/dev/null | apply_head || true
  # If nothing matched (e.g. build error before tests ran), show raw output (head-limited).
  if [ "$(echo "$output" | head -c 1)" != "" ] && [ -z "$(echo "$output" | grep -E '(^=== RUN |^ok |^FAIL|^\? )' 2>/dev/null)" ]; then
    echo "$output" | apply_head || true
  fi
elif [ "$mode" = "cargo" ]; then
  # For cargo test: show test names only for failures, plus summary
  echo "$output" | grep -iE '(^test .*(FAILED|ok)|error|FAILED|test result|^\[)' 2>/dev/null | apply_head || true
elif [ "$mode" = "mocha" ]; then
  # For mocha: show passing/failing summary lines
  echo "$output" | grep -iE '(passing|failing|[0-9]+ passing|[0-9]+ failing)' 2>/dev/null | apply_head || true
elif [ "$mode" = "cargo-build" ] || [ "$mode" = "cargo-check" ]; then
  # For cargo build/check: show errors, warnings, and final status
  echo "$output" | grep -iE '(^error|^warning|error\[|warning\[|Compiling|Finished)' 2>/dev/null | apply_head || true
elif [ "$mode" = "prettier" ]; then
  # For prettier: show only unformatted files and summary
  echo "$output" | grep -iE '(unformatted|error|warn|Code style|^\[warn)' 2>/dev/null | apply_head || true
else
  # Default: filter out common noise
  echo "$output" | grep -vE '(slowest [0-9]+ durations|^[-]+ |^-- Docs:|^Failed to (get info|multipart)|^$|LangSmith|snapshot report)' 2>/dev/null | apply_head || true
fi

exit $rc
