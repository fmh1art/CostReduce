#!/usr/bin/env bash
# build-check: Run build/vet/test for Go, TypeScript (tsc/vitest/jest), Python (pytest/syntax check), 
# or Node.js (--node-run), or Go format check. Also supports --go-run and --node-run to write code from stdin and run it.
# Usage: build-check [options] <target>
#        build-check --go-run [--timeout=N] < 'GOEOF' (read Go code from stdin)
#        build-check --node-run [--timeout=N] < 'EOF' (read Node.js code from stdin)

set -euo pipefail

show_help() {
    cat <<'HELP_EOF'
Usage: build-check [options] <target>
       build-check --go-run [options]   (read Go code from stdin, write to temp file, and run it)
       build-check --node-run [options] (read Node.js code from stdin, write to temp file, and run it)

Options:
  --cd=DIR, -C DIR      Change to directory before running (replaces cd + command pattern)
  --go              Force Go build/test/vet check
  --go-run          Write Go code from stdin to temp file and run with 'go run'
  --node-run        Write Node.js code from stdin to temp file and run with 'node'
  --ts              Force TypeScript check via tsc (--noEmit)
  --vitest          Run vitest tests
  --jest            Run jest tests

  --mocha           Run mocha tests
  --pytest          Run Python pytest
  --python          Python syntax check only (ast.parse)
  --gofmt           Check Go file formatting via gofmt -e
  --timeout=N       Timeout in seconds (default 60)
  --filter=PATTERN  Test name filter (-run for Go, -t/-k for others)
  --tags=TAGS       Go build tags
  --verbose, -v     Verbose output
  --head=N          Show first N lines of output
  --tail=N          Show last N lines of output
  --quiet, --silent Suppress stdout on success (exit 0); always show stderr
  --trim-ansi       Strip ANSI escape codes from output (reduces observation size)
  --force-exit      Add --forceExit flag to jest or mocha commands
  --build-only      Only build, skip tests
  --vet-only        Only run go vet
  --test-only       Only run tests
  --compile-only    Only compile without running tests
  --list-only, --go-list  Run go list -e on the target (verifies package resolution)
  --fail-only       Show only failed tests and their error messages (filters out passing tests)
  --trim-pytest     When used with --pytest, strip durations, snapshot reports, docs links, and other noise; keep only summary line(s) and failure/error info
  --only-errors     Alias for --fail-only

Examples:
  build-check --cd=/app --go --timeout=30 --filter=TestFoo ./pkg/...
  build-check --cd=libs/core --pytest tests/
  build-check --ts lib/module.ts
  build-check --vitest tests/
  build-check --python src/module.py
  build-check --gofmt file.go
  build-check --go-run < test_snippet.go
  build-check --quiet --pytest tests/
HELP_EOF
    exit 0
}

# Defaults
CD_DIR=""
TIMEOUT=60
FILTER=""
TAGS=""
VERBOSE=""
HEAD=""
TAIL=""
MODE=""
TARGET=""
GO_RUN=""
NODE_RUN=""
QUIET=""
TRIM_ANSI=""
FORCE_EXIT=""
FAIL_ONLY=""
ONLY_ERRORS=""
TRIM_PYTEST=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --help|-h) show_help ;;
        --cd=*) CD_DIR="${1#*=}" ;;
        -C|--cd)
            shift
            [[ $# -lt 1 ]] && { echo "Error: --cd needs a directory" >&2; exit 1; }
            CD_DIR="$1"
            ;;
        --go) MODE="go" ;;
        --go-run) GO_RUN="1" ;;
        --node-run) NODE_RUN="1" ;;
        --ts) MODE="ts" ;;
        --vitest) MODE="vitest" ;;
        --jest) MODE="jest" ;;
        --pytest) MODE="pytest" ;;
        --python) MODE="python-check" ;;
        --gofmt) MODE="gofmt" ;;

        --mocha) MODE="mocha" ;;
        --timeout=*) TIMEOUT="${1#*=}" ;;
        --filter=*) FILTER="${1#*=}" ;;
        --tags=*) TAGS="${1#*=}" ;;
        --verbose|-v) VERBOSE="1" ;;
        --head=*) HEAD="${1#*=}" ;;
        --tail=*) TAIL="${1#*=}" ;;
        --quiet|--silent) QUIET="1" ;;
--trim-ansi) TRIM_ANSI="1" ;;
        --force-exit) FORCE_EXIT="1" ;;
        --fail-only) FAIL_ONLY="1" ;;
        --only-errors) ONLY_ERRORS="1" ;;
        --trim-pytest) TRIM_PYTEST="1" ;;
        --build-only) MODE="${MODE:-go}-build" ;;
        --vet-only) MODE="${MODE:-go}-vet" ;;
        --test-only) MODE="${MODE:-go}-test" ;;
        --compile-only) MODE="${MODE:-go}-compile" ;;
        --list-only|--go-list) MODE="go-list" ;;
        *) TARGET="$1" ;;
    esac
    shift
done

# Helper: run command with optional output limiting and quiet mode
run_with_output_opts() {
    local rc=0
    local tmpout="$(mktemp)"
    local tmperr="$(mktemp)"
    if "$@" > "$tmpout" 2> "$tmperr"; then
        rc=0
    else
        rc=$?
    fi
    if [[ -n "$TRIM_ANSI" ]]; then
        sed -i -E 's/\x1b\[[0-9;]*[a-zA-Z]//g' "$tmpout" 2>/dev/null || true
        sed -i -E 's/\x1b\[[0-9;]*[a-zA-Z]//g' "$tmperr" 2>/dev/null || true
    fi
    # --fail-only / --only-errors: filter test output to show only failures
    if [[ -n "$FAIL_ONLY" || -n "$ONLY_ERRORS" ]]; then
        python3 -c '
import sys, re
lines = sys.stdin.read().splitlines(True)
out_lines = []
show = False
keep_after_fail = 0
show_summary = False
had_failure = False
for i, line in enumerate(lines):
    s = line.strip()
    # Failure indicator characters: \u2717 (✗), \u2715 (✗), \u00d7 (×)
    if "\u2717" in line or "\u2715" in line or "\u00d7" in line:
        had_failure = True
        show = True
        keep_after_fail = 10
    # Passing test indicators: \u2713 (✓), \u2714 (✔)
    elif "\u2713" in line or "\u2714" in line:
        show = False
        keep_after_fail = 0
        continue
    # ● marker
    elif "\u25cf" in line:
        show = True
        keep_after_fail = 3
    # Separator lines
    elif s.startswith("\u2501") or s.startswith("\u2500"):
        show = True
        keep_after_fail = 2
    # FAIL word in test names
    elif "FAIL" in line and len(s) < 80:
        had_failure = True
        show = True
        keep_after_fail = 15
    # FAILED at end
    elif s.endswith("FAILED"):
        had_failure = True
        show = True
        keep_after_fail = 5
    # Summary lines
    elif ("Tests" in line or "Test Files" in line) and ("failed" in s.lower() or "passed" in s.lower()):
        show = True
        show_summary = True
        keep_after_fail = 3
    # Duration / Start at lines
    elif s.startswith("Start at") or s.startswith("Duration"):
        show = True
        show_summary = True
        keep_after_fail = 2
    # Error type names (AssertionError, TypeError, etc.)
    elif re.search(r"[A-Z][a-z]+(Error|Exception|Warning|Fail)", s):
        had_failure = True
        show = True
        keep_after_fail = 8
    # Arrow indicator →
    elif "\u2192" in line:
        show = True
        keep_after_fail = max(keep_after_fail, 3)
    # Indented detail lines after failure
    elif (s.startswith("  ") or s.startswith("\t")) and keep_after_fail > 0:
        show = True
    # Stack trace "at ..." lines
    elif re.match(r"^\s+at\s", line):
        show = True
        keep_after_fail = max(keep_after_fail, 2)
    # Expected/Received diff
    elif s.startswith("Expected") or s.startswith("Received"):
        show = True
        keep_after_fail = max(keep_after_fail, 3)
    # File path in stack traces
    elif re.match(r"^\s*File \"", s):
        show = True
        keep_after_fail = max(keep_after_fail, 3)
    # Line number references like "    > 123"
    elif re.match(r"^\s*>\s", line):
        show = True
        keep_after_fail = max(keep_after_fail, 2)
    # Blank lines: keep if in failure context
    elif not s:
        if keep_after_fail > 0:
            out_lines.append(line)
            keep_after_fail -= 1
        else:
            show = False
        continue
    
    if show:
        out_lines.append(line)
        # Decrement context counter only for non-trigger lines
        if keep_after_fail > 0 and not any([
            "\u2717" in line, "\u2715" in line, "\u00d7" in line,
            "\u25cf" in line,
            "FAIL" in line and len(s.strip()) < 80,
            s.strip().endswith("FAILED"),
            re.search(r"[A-Z][a-z]+(Error|Exception|Warning|Fail)", s),
        ]):
            keep_after_fail -= 1
    elif keep_after_fail <= 0:
        show = False

sys.stdout.write("".join(out_lines))
' < "$tmpout" > "${tmpout}_filtered" 2>/dev/null || true
        mv "${tmpout}_filtered" "$tmpout" 2>/dev/null || true
        # Also filter stderr
        python3 -c '
import sys, re
lines = sys.stdin.read().splitlines(True)
out_lines = []
show = False
keep_after_fail = 0
show_summary = False
had_failure = False
for i, line in enumerate(lines):
    s = line.strip()
    if "\u2717" in line or "\u2715" in line or "\u00d7" in line:
        had_failure = True
        show = True
        keep_after_fail = 10
    elif "\u2713" in line or "\u2714" in line:
        show = False
        keep_after_fail = 0
        continue
    elif "\u25cf" in line:
        show = True
        keep_after_fail = 3
    elif s.startswith("\u2501") or s.startswith("\u2500"):
        show = True
        keep_after_fail = 2
    elif "FAIL" in line and len(s) < 80:
        had_failure = True
        show = True
        keep_after_fail = 15
    elif s.endswith("FAILED"):
        had_failure = True
        show = True
        keep_after_fail = 5
    elif ("Tests" in line or "Test Files" in line) and ("failed" in s.lower() or "passed" in s.lower()):
        show = True
        show_summary = True
        keep_after_fail = 3
    elif s.startswith("Start at") or s.startswith("Duration"):
        show = True
        show_summary = True
        keep_after_fail = 2
    elif re.search(r"[A-Z][a-z]+(Error|Exception|Warning|Fail)", s):
        had_failure = True
        show = True
        keep_after_fail = 8
    elif "\u2192" in line:
        show = True
        keep_after_fail = max(keep_after_fail, 3)
    elif (s.startswith("  ") or s.startswith("\t")) and keep_after_fail > 0:
        show = True
    elif re.match(r"^\s+at\s", line):
        show = True
        keep_after_fail = max(keep_after_fail, 2)
    elif s.startswith("Expected") or s.startswith("Received"):
        show = True
        keep_after_fail = max(keep_after_fail, 3)
    elif re.match(r"^\s*File \"", s):
        show = True
        keep_after_fail = max(keep_after_fail, 3)
    elif re.match(r"^\s*>\s", line):
        show = True
        keep_after_fail = max(keep_after_fail, 2)
    elif not s:
        if keep_after_fail > 0:
            out_lines.append(line)
            keep_after_fail -= 1
        else:
            show = False
        continue
    
    if show:
        out_lines.append(line)
        if keep_after_fail > 0 and not any([
            "\u2717" in line, "\u2715" in line, "\u00d7" in line,
            "\u25cf" in line,
            "FAIL" in line and len(s.strip()) < 80,
            s.strip().endswith("FAILED"),
            re.search(r"[A-Z][a-z]+(Error|Exception|Warning|Fail)", s),
        ]):
            keep_after_fail -= 1
    elif keep_after_fail <= 0:
        show = False

sys.stdout.write("".join(out_lines))
' < "$tmperr" > "${tmperr}_filtered" 2>/dev/null || true
        mv "${tmperr}_filtered" "$tmperr" 2>/dev/null || true
    fi
    # --trim-pytest: strip pytest noise (durations, snapshot reports, docs links, etc.)
    if [[ -n "$TRIM_PYTEST" ]]; then
        python3 -c '
import sys, re
lines = sys.stdin.read().splitlines(True)
out_lines = []
keep = False
keep_count = 0
for line in lines:
    s = line.strip()
    # Always keep summary lines
    if re.search(r"\d+ passed", s) and re.search(r"\d+ failed|\d+ skipped|in [\d.]+s", s):
        out_lines.append(line)
        keep = False
        keep_count = 0
        continue
    # Keep FAILED lines
    if s.startswith("FAILED"):
        out_lines.append(line)
        keep = True
        keep_count = 20
        continue
    # Keep test names with black circle
    if "\u25cf" in s:
        out_lines.append(line)
        keep = True
        keep_count = 15
        continue
    # Keep short test summary info section
    if s == "short test summary info":
        out_lines.append(line)
        keep = True
        keep_count = 20
        continue
    # Keep failure context lines while counter lasts
    if keep and keep_count > 0:
        out_lines.append(line)
        keep_count -= 1
        continue
    if keep and keep_count <= 0:
        keep = False
        continue
sys.stdout.write("".join(out_lines))
' < "$tmpout" > "${tmpout}_filtered" 2>/dev/null || true
        mv "${tmpout}_filtered" "$tmpout" 2>/dev/null || true
    fi
    if [[ -n "$QUIET" && $rc -eq 0 ]]; then
        # On success with --quiet, show nothing on stdout but always show stderr
        cat "$tmperr" >&2 || true
    elif [[ -n "$HEAD" ]]; then
        head -n "$HEAD" "$tmpout"
        cat "$tmperr" >&2 || true
    elif [[ -n "$TAIL" ]]; then
        tail -n "$TAIL" "$tmpout"
        cat "$tmperr" >&2 || true
    else
        cat "$tmpout"
        cat "$tmperr" >&2 || true
    fi
    rm -f "$tmpout" "$tmperr"
    return $rc
}

# Change directory if --cd was specified
if [[ -n "$CD_DIR" ]]; then
    cd "$CD_DIR" || { echo "Error: Cannot cd to $CD_DIR" >&2; exit 1; }
fi

# Handle --go-run: write Go code from stdin to temp file and run it
if [[ -n "$GO_RUN" ]]; then
    if [[ -t 0 ]]; then
        echo "Error: --go-run requires Go code via stdin (pipe or heredoc)" >&2
        exit 1
    fi
    TMPFILE="$(mktemp /tmp/build_check_go_XXXXXXXX.go)"
    cat > "$TMPFILE"
    if [[ -n "$TIMEOUT" ]]; then
        run_with_output_opts timeout "$TIMEOUT" go run "$TMPFILE"
        EXIT_CODE=$?
    else
        run_with_output_opts go run "$TMPFILE"
        EXIT_CODE=$?
    fi
    rm -f "$TMPFILE"
    exit $EXIT_CODE
fi

# Handle --node-run: write Node.js code from stdin to temp file and run it
if [[ -n "$NODE_RUN" ]]; then
    if [[ -t 0 ]]; then
        echo "Error: --node-run requires Node.js code via stdin (pipe or heredoc)" >&2
        exit 1
    fi
    TMPFILE="$(mktemp /tmp/build_check_node_XXXXXXXX.js)"
    cat > "$TMPFILE"
    if [[ -n "$TIMEOUT" ]]; then
        run_with_output_opts timeout "$TIMEOUT" node "$TMPFILE"
        EXIT_CODE=$?
    else
        run_with_output_opts node "$TMPFILE"
        EXIT_CODE=$?
    fi
    rm -f "$TMPFILE"
    exit $EXIT_CODE
fi

[[ -z "$TARGET" ]] && { echo "Error: No target specified" >&2; show_help; }

# Auto-detect mode from target
if [[ -z "$MODE" ]] || [[ "$MODE" == go-* ]]; then
    if [[ "$TARGET" == *.py ]]; then
        MODE="${MODE:-python-check}"
    elif [[ "$TARGET" == *.ts ]] || [[ "$TARGET" == *.tsx ]]; then
        MODE="${MODE:-ts}"
    elif [[ -d "$TARGET" ]] || [[ "$TARGET" == ./... ]] || [[ "$TARGET" == *.go ]]; then
        MODE="${MODE:-go}"
    fi
fi

# Build the command
CMD=()

case "$MODE" in
    go|go-build)
        CMD=(go build)
        [[ -n "$TAGS" ]] && CMD+=(-tags="$TAGS")
        CMD+=("$TARGET")
        ;;
    go-vet)
        CMD=(go vet)
        [[ -n "$TAGS" ]] && CMD+=(-tags="$TAGS")
        CMD+=("$TARGET")
        ;;
    go-list)
        CMD=(go list -e)
        [[ -n "$TAGS" ]] && CMD+=(-tags="$TAGS")
        CMD+=("$TARGET")
        ;;
    go-test|go-compile)
        CMD=(go test -count=1)
        if [[ "$MODE" == go-compile ]]; then
            CMD+=(-c -o /dev/null)
        fi
        [[ -n "$TAGS" ]] && CMD+=(-tags="$TAGS")
        [[ -n "$FILTER" ]] && CMD+=(-run "$FILTER")
        [[ -n "$VERBOSE" ]] && CMD+=(-v)
        CMD+=("$TARGET")
        ;;
    ts)
        CMD=(npx --no-install tsc --noEmit)
        if [[ -f "$TARGET" ]]; then
            CMD+=("$TARGET")
        else
            CMD+=(--project "$TARGET")
        fi
        ;;
    vitest)
        CMD=(npx --no-install vitest run)
        [[ -n "$FILTER" ]] && CMD+=(-t "$FILTER")
        [[ -n "$VERBOSE" ]] && CMD+=(--reporter=verbose)
        CMD+=("$TARGET")
        ;;
    jest)
        CMD=(npx --no-install jest --no-coverage --no-cache)
        [[ -n "$FORCE_EXIT" ]] && CMD+=(--forceExit)
        [[ -n "$FILTER" ]] && CMD+=(-t "$FILTER")
        CMD+=("$TARGET")
        ;;
    mocha)
        CMD=(npx --no-install mocha)
        [[ -n "$FORCE_EXIT" ]] && CMD+=(--forceExit)
        [[ -n "$FILTER" ]] && CMD+=(--grep "$FILTER")
        [[ -n "$VERBOSE" ]] && CMD+=(--reporter spec)
        CMD+=("$TARGET")
        ;;
    pytest)
        CMD=(python3 -m pytest -x -q)
        [[ -n "$FILTER" ]] && CMD+=(-k "$FILTER")
        [[ -n "$VERBOSE" ]] && CMD+=(-v)
        [[ -n "$TIMEOUT" ]] && CMD+=(--timeout="$TIMEOUT")
        CMD+=("$TARGET")
        ;;
    python-check)
        # Check syntax via ast.parse
        ERRORS=0
        all_files=()
        if [[ -d "$TARGET" ]]; then
            while IFS= read -r -d $'\0' f; do
                all_files+=("$f")
            done < <(find "$TARGET" -name '*.py' -type f -print0)
        else
            all_files=("$TARGET")
        fi
        for f in "${all_files[@]}"; do
            if python3 -c "
import ast, sys
try:
    with open('$f') as fh:
        ast.parse(fh.read())
    print('OK - $f')
except SyntaxError as e:
    print(f'FAIL - $f: {e}')
    sys.exit(1)
" 2>&1; then
                :
            else
                ERRORS=$((ERRORS + 1))
            fi
        done
        exit $ERRORS
        ;;
    gofmt)
        # Check Go file formatting with gofmt -e
        ERRORS=0
        all_files=()
        if [[ -d "$TARGET" ]]; then
            while IFS= read -r -d $'\0' f; do
                all_files+=("$f")
            done < <(find "$TARGET" -name '*.go' -type f -print0)
        else
            all_files=("$TARGET")
        fi
        for f in "${all_files[@]}"; do
            if gofmt -e "$f" > /dev/null 2>&1; then
                echo "OK - $f"
            else
                echo "FAIL - $f has formatting issues"
                ERRORS=$((ERRORS + 1))
            fi
        done
        exit $ERRORS
        ;;
    *)
        echo "Error: Unknown mode '$MODE'" >&2
        show_help
        ;;
esac

# Run the command with timeout and output options
if [[ -n "$TIMEOUT" ]]; then
    run_with_output_opts timeout "$TIMEOUT" "${CMD[@]}"
else
    run_with_output_opts "${CMD[@]}"
fi
