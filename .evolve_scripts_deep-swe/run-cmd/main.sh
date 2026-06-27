#!/usr/bin/env bash
# run-cmd: Run arbitrary commands in a specified directory with optional environment variables, timeout, output limiting, and stack trace/ANSI trimming.
# Usage: run-cmd [options] <command> [args...]

set -euo pipefail

show_help() {
    cat <<'HELP_EOF'
Usage: run-cmd [options] <command> [args...]

Options:
  -C, --dir=DIR        Working directory to run command in
  -e, --env KEY=val    Set environment variable (repeatable)
  --timeout=SECONDS    Timeout in seconds
  --head=N             Show only first N lines of output (replaces | head -N pipe)
  --tail=N             Show only last N lines of output (replaces | tail -N pipe)
  --trim-stack         Strip stack trace lines from output (keeps error messages and code lines)
  --trim-ansi          Strip ANSI escape codes from output (reduces observation size)
  --help, -h           Show this help

Examples:
  run-cmd --dir=/app python -m pytest test.py
  run-cmd --dir=/app/src -e DJANGO_SETTINGS_MODULE=paperless.settings pytest tests/
  run-cmd --timeout=30 curl http://example.com
  run-cmd --head=20 npx vitest run tests/ 2>&1
  run-cmd --tail=50 --trim-ansi go test ./... 2>&1
HELP_EOF
    exit 0
}

DIR=""
ENV_VARS=()
TIMEOUT=""
TRIM_STACK=""
TRIM_ANSI=""
HEAD=""
TAIL=""
CMD_ARGS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --help|-h) show_help ;;
        -C|--dir)
            shift
            DIR="$1"
            ;;
        --dir=*) DIR="${1#*=}" ;;
        -e|--env)
            shift
            ENV_VARS+=("$1")
            ;;
        --env=*) ENV_VARS+=("${1#*=}") ;;
        --timeout=*) TIMEOUT="${1#*=}" ;;
        --head=*) HEAD="${1#*=}" ;;
        --tail=*) TAIL="${1#*=}" ;;
        --trim-stack) TRIM_STACK="1" ;;
        --trim-ansi|--no-ansi) TRIM_ANSI="1" ;;
        *) CMD_ARGS+=("$1") ;;
    esac
    shift
done

[[ ${#CMD_ARGS[@]} -eq 0 ]] && { echo "Error: No command specified" >&2; exit 1; }

# Build env prefix
ENV_PREFIX=""
for var in "${ENV_VARS[@]}"; do
    ENV_PREFIX+="export $var; "
done

# Build cd prefix
CD_PREFIX=""
if [[ -n "$DIR" ]]; then
    CD_PREFIX="cd '$DIR' && "
fi

# Build timeout prefix
TIMEOUT_PREFIX=""
if [[ -n "$TIMEOUT" ]]; then
    TIMEOUT_PREFIX="timeout $TIMEOUT "
fi

# Quote command args
QUOTED_ARGS=()
for arg in "${CMD_ARGS[@]}"; do
    QUOTED_ARGS+=("$(printf '%q' "$arg")")
done

FULL_CMD="${CD_PREFIX}${ENV_PREFIX}${TIMEOUT_PREFIX}${QUOTED_ARGS[*]}"

# Run command and capture output to temp file for processing
TMPFILE="$(mktemp)"
TMPFILE2="$(mktemp)"
trap 'rm -f "$TMPFILE" "$TMPFILE2"' EXIT

if eval "$FULL_CMD" > "$TMPFILE" 2>&1; then
    RC=0
else
    RC=$?
fi

# Apply ANSI stripping first
if [[ -n "$TRIM_ANSI" ]]; then
    sed -i -E 's/\x1b\[[0-9;]*[a-zA-Z]//g' "$TMPFILE"
fi

# Apply stack trimming (before head/tail so limiting applies to cleaned output)
if [[ -n "$TRIM_STACK" ]]; then
    grep -E -v '^[[:space:]]+at |^[[:space:]]+at\/|^  File "|^\.\.\.$|^\.\.\.<truncated>$|^---$|^goroutine [0-9]+ \[|^[[:space:]]+\.\.\.$' "$TMPFILE" > "$TMPFILE2" || true
    mv "$TMPFILE2" "$TMPFILE"
fi

# Apply output limiting
if [[ -n "$HEAD" ]] && [[ -n "$TAIL" ]]; then
    head -n "$HEAD" "$TMPFILE" | tail -n "$TAIL"
elif [[ -n "$HEAD" ]]; then
    head -n "$HEAD" "$TMPFILE"
elif [[ -n "$TAIL" ]]; then
    tail -n "$TAIL" "$TMPFILE"
else
    cat "$TMPFILE"
fi

exit $RC
