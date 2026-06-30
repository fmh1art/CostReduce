#!/usr/bin/env bash
set -euo pipefail

# run_tests - Run tests for any language/framework with auto-detection
# Usage: run_tests [--go <pkg> | --vitest <file> | --jest <file> | --pytest <file> | --utscapy <test.uts> | --yarn <script>] [options] [-- <extra_args>]
#   -- <extra_args>  Pass remaining args through to the test runner (e.g., -- --no-cache --silent)
#   --utscapy        Run Scapy UTscapy tests with common exclusions
#   --yarn           Run a yarn script (e.g., --yarn test-packages)
#   --tz=TZ          Set timezone (e.g., --tz=UTC, --tz=America/New_York)
#   --xvfbrun        Wrap command with xvfb-run -a and set HEADLESS=true (for E2E tests)
#   --tail=N         Limit output to N lines (like piping through tail -N)
#   --failed         Show only failure output (jest/pytest/vitest/yarn/go: filter for failures)

MODE="auto"
TARGET=""
DIR=""
FAILED_MODE=false
PASSED_MODE=false
TAIL_N=""
GREP=""
COUNT=""
ENV_VARS=()
ENV_FILE=""
NO_COVERAGE=false
TAGS=""
TIMEOUT=""
VERBOSE=false
SUMMARY_MODE=false
BRIEF_MODE=false
XVFB_RUN=false

TZ=""

# Brief filter: strip sub-describe labels (e.g., "Locale: X") from jest/vitest verbose output,
# keeping only test result lines (\u2713/\u2717), section headers (low indent), and summary.
filter_brief() {
    python3 -c '
import sys
for line in sys.stdin:
    line = line.rstrip("\n")
    stripped = line.lstrip()
    # Keep test result lines (checkmarks, crosses)
    if "\u2713" in line or "\u2717" in line or "\u2715" in line:
        print(line)
        continue
    # Keep summary/framework lines
    if stripped.startswith("PASS") or stripped.startswith("FAIL") or \
       stripped.startswith("Test Suites") or stripped.startswith("Tests:") or \
       stripped.startswith("Test Files") or stripped.startswith("Files:") or \
       stripped.startswith("Results"):
        print(line)
        continue
    # Keep low-indent section headers (describes at indent < 8 spaces)
    indent = len(line) - len(stripped)
    if indent <= 4 and stripped and not stripped.startswith("\u2713") and not stripped.startswith("\u2717") and not stripped.startswith("\u2715"):
        # But skip sub-labels like "Locale: ar", "Step: X" etc
        if not stripped.startswith("      "):
            print(line)
            continue
    # Skip everything else (sub-describe labels, separators)
' || true
}

# Failed filter: show only failure details (test names with \u2717/\u25cf/FAIL prefix and error details)
filter_failed() {
    python3 -c '
import sys
in_error = False
error_lines = 0
for line in sys.stdin:
    line = line.rstrip("\n")
    stripped = line.lstrip()
    # Start of a failure: checkmarks with cross, FAIL prefix, or \u25cf (test suite failure)
    if "\u2717" in line or "FAIL" in stripped or "\u25cf" in stripped:
        in_error = True
        error_lines = 0
        print(line)
        continue
    # Error detail lines (indented, contain expect/received/at/^)
    if in_error:
        if stripped and (stripped.startswith("expect") or stripped.startswith("Expected") or \
           stripped.startswith("Received") or stripped.startswith("at ") or \
           stripped.startswith(">") or "\u2713" in line or "PASS" in stripped or "Tests:" in stripped or "Test Suites:" in stripped):
            in_error = False
            print(line)
            continue
        if stripped:
            error_lines += 1
            if error_lines <= 30:  # cap error details at 30 lines
                print(line)
            continue
        in_error = False
        print()
        continue
    # Summary lines
    if stripped.startswith("Test Suites:") or stripped.startswith("Tests:") or \
       stripped.startswith("Test Files") or stripped.startswith("Snapshots:") or \
       stripped.startswith("Time:") or stripped.startswith("Jest:"):
        print(line)
' || true
}

# Apply --tail=N limit to output
apply_tail() {
    if [[ -n "$TAIL_N" ]]; then
        tail -n "$TAIL_N"
    else
        cat
    fi
}

EXTRA_ARGS=()
PARSED_EXTRA=false

while [[ $# -gt 0 ]]; do
    if [[ "$1" == "--" ]]; then
        PARSED_EXTRA=true
        shift
        # Everything after -- is pass-through
        EXTRA_ARGS=("$@")
        break
    fi
    case "$1" in
        --go) MODE="go"; TARGET="$2"; shift 2 ;;
        --vitest) MODE="vitest"; TARGET="$2"; shift 2 ;;
        --jest) MODE="jest"; TARGET="$2"; shift 2 ;;
        --pytest) MODE="pytest"; TARGET="$2"; shift 2 ;;
        --utscapy) MODE="utscapy"; TARGET="$2"; shift 2 ;;
        --yarn) MODE="yarn"; TARGET="$2"; shift 2 ;;
        --all) MODE="all" ;;
        --grep=*) GREP="${1#*=}"; shift ;;
        --count=*) COUNT="${1#*=}"; shift ;;
        --env-file=*) ENV_FILE="${1#*=}"; shift ;;
        --env=*|-e)
            val="${1#*=}"
            [[ "$1" == "-e" ]] && { val="$2"; shift 2; } || shift
            ENV_VARS+=("$val") ;;
        --no-coverage) NO_COVERAGE=true; shift ;;
        --tags=*) TAGS="${1#*=}"; shift ;;
        --timeout=*) TIMEOUT="${1#*=}"; shift ;;
        --summary|--compact) SUMMARY_MODE=true; shift ;;
        --brief) BRIEF_MODE=true; shift ;;
        --failed) FAILED_MODE=true; shift ;;
        --passed) PASSED_MODE=true; shift ;;
        --tail=*) TAIL_N="${1#*=}"; shift ;;
        --xvfbrun) XVFB_RUN=true; shift ;;

        --tz=*) TZ="${1#*=}"; shift ;;
        --dir=*) DIR="${1#*=}"; shift ;;
        -C|--dir) DIR="$2"; shift 2 ;;
        --verbose|-v) VERBOSE=true; shift ;;
        -*)
            echo "Unknown option: $1" >&2
            exit 1
            ;;
        *)
            TARGET="$1"
            shift
            ;;
    esac
done

BUILD_ENV=""

# Source env file if specified (loads before individual --env vars)
if [[ -n "$ENV_FILE" ]]; then
    if [[ ! -f "$ENV_FILE" ]]; then
        echo "Error: env file not found: $ENV_FILE" >&2
        exit 1
    fi
    set -a
    source "$ENV_FILE"
    set +a
fi

# Change directory if specified (must happen before venv activation)
if [[ -n "$DIR" ]]; then
    if [[ ! -d "$DIR" ]]; then
        echo "Error: directory not found: $DIR" >&2
        exit 1
    fi
    cd "$DIR"
fi

# Auto-activate venv if found (replaces failed `source venv/bin/activate` with /bin/sh)
if [[ -d ".venv" ]]; then
    source .venv/bin/activate 2>/dev/null || true
elif [[ -d "venv" ]]; then
    source venv/bin/activate 2>/dev/null || true
elif [[ -d "/app/venv" ]]; then
    source /app/venv/bin/activate 2>/dev/null || true
fi

for e in "${ENV_VARS[@]}"; do
    BUILD_ENV="$BUILD_ENV $e"
done

if [[ -z "$TARGET" && "$MODE" != "auto" && "$MODE" != "all" ]]; then
    echo "Usage: $0 [--go|--vitest|--jest|--pytest|--utscapy|--yarn] <test_target> [-- <extra_args>]" >&2
    exit 1
fi

# Auto-detect if not specified
if [[ "$MODE" == "auto" && -n "$TARGET" ]]; then
    if [[ -f "$TARGET" ]]; then
        ext="${TARGET##*.}"
        case "$ext" in
            py) MODE="pytest" ;;
            ts|tsx) MODE="vitest" ;;
            js|jsx) MODE="jest" ;;
            go) MODE="go" ;;
            uts) MODE="utscapy" ;;
        esac
    elif [[ -d "$TARGET" ]]; then
        if ls "$TARGET"/*.go &>/dev/null 2>&1; then MODE="go";
        elif ls "$TARGET"/*.py &>/dev/null 2>&1; then MODE="pytest";
        elif ls "$TARGET"/*.ts &>/dev/null 2>&1; then MODE="vitest";
        elif ls "$TARGET"/*.js &>/dev/null 2>&1; then MODE="jest";
        fi
    fi
fi

# Build common flags
VERBOSE_FLAG=""
$VERBOSE && VERBOSE_FLAG="-v"

COVERAGE_FLAG=""
$NO_COVERAGE && COVERAGE_FLAG="--no-cov"

# xvfb-run wrapper: prepend xvfb-run -a and set HEADLESS=true
XVFB_PREFIX=""
if $XVFB_RUN; then
    XVFB_PREFIX="HEADLESS=true xvfb-run -a"
fi

# Apply TZ env var if set (prepends TZ=... to command)
if [[ -n "$TZ" ]]; then
    if [[ -n "$XVFB_PREFIX" ]]; then
        XVFB_PREFIX="TZ=$TZ $XVFB_PREFIX"
    else
        XVFB_PREFIX="TZ=$TZ"
    fi
fi

case "$MODE" in
    go)
        # Auto-set Go environment (PATH, GOPATH, CGO_ENABLED=0) if not already set
        if [[ -z "${GOPATH:-}" ]]; then
            export GOPATH=/root/go
        fi
        # Check if Go paths are already in PATH
        if [[ ":$PATH:" != *":/usr/local/go/bin:"* && ":$PATH:" != *":/root/go/bin:"* ]]; then
            export PATH="$PATH:/usr/local/go/bin:/root/go/bin"
        fi
        export CGO_ENABLED=0

        GO_FLAGS=""
        [[ -n "$TAGS" ]] && GO_FLAGS="-tags $TAGS"
        [[ -n "$TIMEOUT" ]] && GO_FLAGS="$GO_FLAGS -timeout $TIMEOUT"
        [[ -n "$COUNT" ]] && GO_FLAGS="$GO_FLAGS -count $COUNT"
        [[ -n "$GREP" ]] && GO_FLAGS="$GO_FLAGS -run \"$GREP\""
        $VERBOSE && GO_FLAGS="$GO_FLAGS -v"
        [[ -n "$BUILD_ENV" ]] && eval export $BUILD_ENV
        if $FAILED_MODE; then
            eval $XVFB_PREFIX exec go test $GO_FLAGS "$TARGET" "${EXTRA_ARGS[@]}" 2>&1 | grep -E "FAIL|--- FAIL|^---.*FAIL" | apply_tail || true
        elif $SUMMARY_MODE; then
            eval $XVFB_PREFIX exec go test $GO_FLAGS "$TARGET" "${EXTRA_ARGS[@]}" 2>&1 | grep -E "PASS|FAIL|ok|FAIL|---" | apply_tail || true
        else
            # Filter out noisy timestamp-warning lines (e.g., time="..." level=warning msg="...")
            # which are internal Go logger output, not test results.
            eval $XVFB_PREFIX exec go test $GO_FLAGS "$TARGET" "${EXTRA_ARGS[@]}" 2>&1 | grep -Ev '^time="[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z" level=' | apply_tail || true
        fi
        ;;

    pytest)
        PY_FLAGS=""
        [[ -n "$GREP" ]] && PY_FLAGS="$PY_FLAGS -k \"$GREP\""
        [[ -n "$TIMEOUT" ]] && PY_FLAGS="$PY_FLAGS --timeout=$TIMEOUT"
        $VERBOSE && PY_FLAGS="$PY_FLAGS -v"
        [[ -n "$COVERAGE_FLAG" ]] && PY_FLAGS="$PY_FLAGS $COVERAGE_FLAG"
        [[ -n "$BUILD_ENV" ]] && eval export $BUILD_ENV
        if $FAILED_MODE; then
            eval $XVFB_PREFIX exec python -m pytest $PY_FLAGS "$TARGET" "${EXTRA_ARGS[@]}" 2>&1 | grep -E "FAILED|ERROR|FAIL|\u2717|\u25cf|>.*expect|AssertionError" | apply_tail || true
        elif $SUMMARY_MODE; then
            eval $XVFB_PREFIX exec python -m pytest $PY_FLAGS "$TARGET" "${EXTRA_ARGS[@]}" 2>&1 | grep -E "PASSED|FAILED|ERROR|passed|failed|error|Test session|test(s)?" | apply_tail || true
        else
            eval $XVFB_PREFIX exec python -m pytest $PY_FLAGS "$TARGET" "${EXTRA_ARGS[@]}" 2>&1 | apply_tail
        fi
        ;;
    yarn)
        # Run yarn script (e.g., yarn test-packages packages/calypso-e2e/src/test/...)
        [[ -n "$BUILD_ENV" ]] && eval export $BUILD_ENV
        if $FAILED_MODE; then
            eval $XVFB_PREFIX exec yarn "$TARGET" "${EXTRA_ARGS[@]}" 2>&1 | filter_failed | apply_tail || true
        elif $SUMMARY_MODE; then
            eval $XVFB_PREFIX exec yarn "$TARGET" "${EXTRA_ARGS[@]}" 2>&1 | grep -E "PASS|FAIL|Test Suites|Tests|\u2713|\u2717|\u2715|Done|" | apply_tail || true
        elif $BRIEF_MODE; then
            eval $XVFB_PREFIX exec yarn "$TARGET" "${EXTRA_ARGS[@]}" 2>&1 | filter_brief | apply_tail
        else
            eval $XVFB_PREFIX exec yarn "$TARGET" "${EXTRA_ARGS[@]}" 2>&1 | apply_tail
        fi
        ;;
    utscapy)
        # Scapy UTscapy test runner: python -m scapy.tools.UTscapy -t <test.uts>
        # Auto-adds common exclusion flags (-K) to skip unsupported features
        # Uses -f text -N for compact output (matching pattern from samples)
        UTSCAPY_FLAGS="-f text -N"
        $VERBOSE && UTSCAPY_FLAGS="$UTSCAPY_FLAGS -v"
        [[ -n "$TIMEOUT" ]] && UTSCAPY_FLAGS="$UTSCAPY_FLAGS -n $TIMEOUT"
        [[ -n "$BUILD_ENV" ]] && eval export $BUILD_ENV
        # Build common exclusion flags
        UTSCAPY_EXCLUDES=(
            -K tcpdump -K manufdb -K wireshark -K tshark -K ci_only
            -K vcan_socket -K imports
            -K icmp_firewall -K veth -K tun -K tap
            -K needs_root -K crypto -K netaccess -K linux_only
            -K slow -K bpf -K pcap -K privileged -K external_tool
            -K ipv6 -K bluetooth -K windows -K osx -K freebsd
            -K npcap -K winpcapy -K mock_loopback -K disabled
            -K tls -K x509 -K kerberos -K radius -K snmp
            -K advanced -K complex -K unstable
        )
        # Handle --failed convenience flag (show only failed tests)
        if $FAILED_MODE; then
            eval $XVFB_PREFIX exec python -m scapy.tools.UTscapy -t "$TARGET" "${UTSCAPY_EXCLUDES[@]}" \
                $UTSCAPY_FLAGS "${EXTRA_ARGS[@]}" 2>&1 | grep -E "^failed" | apply_tail || true
        elif $PASSED_MODE; then
            eval $XVFB_PREFIX exec python -m scapy.tools.UTscapy -t "$TARGET" "${UTSCAPY_EXCLUDES[@]}" \
                $UTSCAPY_FLAGS "${EXTRA_ARGS[@]}" 2>&1 | grep -E "^passed" | apply_tail || true
        elif [[ -n "$GREP" ]]; then
            eval $XVFB_PREFIX exec python -m scapy.tools.UTscapy -t "$TARGET" "${UTSCAPY_EXCLUDES[@]}" \
                $UTSCAPY_FLAGS "${EXTRA_ARGS[@]}" 2>&1 | grep -E "$GREP" | apply_tail || true
        elif $SUMMARY_MODE; then
            eval $XVFB_PREFIX exec python -m scapy.tools.UTscapy -t "$TARGET" "${UTSCAPY_EXCLUDES[@]}" \
                $UTSCAPY_FLAGS "${EXTRA_ARGS[@]}" 2>&1 | grep -E "^(passed|failed|PASSED|FAILED|Campaign)" | apply_tail || true
        else
            eval $XVFB_PREFIX exec python -m scapy.tools.UTscapy -t "$TARGET" "${UTSCAPY_EXCLUDES[@]}" \
                $UTSCAPY_FLAGS "${EXTRA_ARGS[@]}" 2>&1 | apply_tail
        fi
        ;;
    vitest)
        VITE_FLAGS=""
        [[ -n "$GREP" ]] && VITE_FLAGS="$VITE_FLAGS -t \"$GREP\""
        [[ -n "$TIMEOUT" ]] && VITE_FLAGS="$VITE_FLAGS --testTimeout=$TIMEOUT"
        $NO_COVERAGE && VITE_FLAGS="$VITE_FLAGS --no-coverage"
        [[ -n "$BUILD_ENV" ]] && eval export $BUILD_ENV
        if $FAILED_MODE; then
            eval $XVFB_PREFIX exec npx vitest run $VITE_FLAGS "$TARGET" "${EXTRA_ARGS[@]}" 2>&1 | filter_failed | apply_tail || true
        elif $SUMMARY_MODE; then
            eval $XVFB_PREFIX exec npx vitest run $VITE_FLAGS "$TARGET" "${EXTRA_ARGS[@]}" 2>&1 | grep -E "PASS|FAIL|Tests|Files|\u2713|\u2717|\u2715" | apply_tail || true
        elif $BRIEF_MODE; then
            eval $XVFB_PREFIX exec npx vitest run $VITE_FLAGS "$TARGET" "${EXTRA_ARGS[@]}" 2>&1 | filter_brief | apply_tail
        else
            eval $XVFB_PREFIX exec npx vitest run $VITE_FLAGS "$TARGET" "${EXTRA_ARGS[@]}" 2>&1 | apply_tail
        fi
        ;;
    jest)
        JEST_FLAGS=""
        [[ -n "$GREP" ]] && JEST_FLAGS="$JEST_FLAGS -t \"$GREP\""
        [[ -n "$TIMEOUT" ]] && JEST_FLAGS="$JEST_FLAGS --testTimeout=$TIMEOUT"
        $NO_COVERAGE && JEST_FLAGS="$JEST_FLAGS --no-coverage"
        [[ -n "$BUILD_ENV" ]] && eval export $BUILD_ENV
        if $FAILED_MODE; then
            eval $XVFB_PREFIX exec npx jest $JEST_FLAGS "$TARGET" "${EXTRA_ARGS[@]}" 2>&1 | filter_failed | apply_tail || true
        elif $SUMMARY_MODE; then
            eval $XVFB_PREFIX exec npx jest $JEST_FLAGS "$TARGET" "${EXTRA_ARGS[@]}" 2>&1 | grep -E "PASS|FAIL|Test Suites|Tests|\u2713|\u2717|\u2715" | apply_tail || true
        elif $BRIEF_MODE; then
            eval $XVFB_PREFIX exec npx jest $JEST_FLAGS "$TARGET" "${EXTRA_ARGS[@]}" 2>&1 | filter_brief | apply_tail
        else
            eval $XVFB_PREFIX exec npx jest $JEST_FLAGS "$TARGET" "${EXTRA_ARGS[@]}" 2>&1 | apply_tail
        fi
        ;;
    all)
        echo "No --all implementation needed; specify a test target." >&2
        exit 1
        ;;
    *)
        echo "Error: could not detect test framework for: $TARGET" >&2
        exit 1
        ;;
esac
