#!/usr/bin/env bash
# py-exec: Run Python code with auto-venv detection, environment variables,
# script execution, stdin input, directory switching, or timeout in one step.
# Usage: py-exec [options] <code>
#        py-exec [options] -f <script.py> [args...]
#        py-exec [options] (reads from stdin if no args)

set -euo pipefail

show_help() {
    cat << 'EOF'
Usage: py-exec [options] <inline_code>
       py-exec [options] -f <script.py> [args...]
       cat script.py | py-exec [options]     (reads from stdin)

Options:
  -f, --file <script> [args...]  Run Python script file with optional args
  -e, --env KEY=value            Set environment variable (repeatable)
  --venv <path>                  Activate specific virtual environment
  --cd=<dir>, -C <dir>           Change to directory before running
  --timeout=N                    Timeout in seconds (wraps with timeout N)
  --help, -h                     Show this help

For Python syntax checking, use: build-check --python <file.py>

If no arguments except options are given, reads Python code from stdin.
Examples:
  py-exec 'print("hello")'
  py-exec -e MY_VAR=123 'import os; print(os.environ["MY_VAR"])'
  py-exec -f script.py arg1 arg2
  py-exec --cd=/app --timeout=30 'print("hello")'
  echo 'print("hello")' | py-exec --timeout=10
EOF
    exit 0
}

MODE=""
SCRIPT=""
SCRIPT_ARGS=()
INLINE_CODE=""
ENV_VARS=()
VENV=""
CD_DIR=""
TIMEOUT=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --help|-h) show_help ;;
        -f|--file)
            MODE="script"
            shift
            [[ $# -lt 1 ]] && { echo "Error: -f needs a script path" >&2; exit 1; }
            SCRIPT="$1"
            shift
            SCRIPT_ARGS=("$@")
            break
            ;;
        --check|--check-syntax)
            echo "Error: --check/--check-syntax has been removed. Use: build-check --python <file.py>" >&2
            exit 1
            ;;
        -e|--env)
            shift
            [[ $# -lt 1 ]] && { echo "Error: -e needs KEY=value" >&2; exit 1; }
            ENV_VARS+=("$1")
            ;;
        --env=*) ENV_VARS+=("${1#*=}") ;;
        --venv)
            shift
            [[ $# -lt 1 ]] && { echo "Error: --venv needs a path" >&2; exit 1; }
            VENV="$1"
            ;;
        --venv=*) VENV="${1#*=}" ;;
        --cd=*) CD_DIR="${1#*=}" ;;
        -C|--cd)
            shift
            [[ $# -lt 1 ]] && { echo "Error: --cd needs a directory" >&2; exit 1; }
            CD_DIR="$1"
            ;;
        --timeout=*) TIMEOUT="${1#*=}" ;;
        --timeout)
            shift
            [[ $# -lt 1 ]] && { echo "Error: --timeout needs seconds" >&2; exit 1; }
            TIMEOUT="$1"
            ;;
        *)
            # If no mode set, treat as inline code
            if [[ -z "$MODE" ]]; then
                MODE="inline"
                INLINE_CODE="$1"
            fi
            ;;
    esac
    shift
done

# Change directory if --cd was specified
if [[ -n "$CD_DIR" ]]; then
    cd "$CD_DIR" || { echo "Error: Cannot cd to $CD_DIR" >&2; exit 1; }
fi

# Export env vars
for var in "${ENV_VARS[@]}"; do
    export "$var"
done

# Build timeout command prefix
TIMEOUT_CMD=()
if [[ -n "$TIMEOUT" ]]; then
    TIMEOUT_CMD=(timeout "$TIMEOUT")
fi

# Detect Python interpreter
PYTHON_BIN=""
if [[ -n "$VENV" ]]; then
    if [[ -f "$VENV/bin/activate" ]]; then
        source "$VENV/bin/activate"
        PYTHON_BIN="python"
    else
        echo "Error: venv not found at $VENV" >&2
        exit 1
    fi
elif [[ -f "venv/bin/activate" ]]; then
    source "venv/bin/activate"
    PYTHON_BIN="python"
elif command -v python3 &>/dev/null; then
    PYTHON_BIN="python3"
elif command -v python &>/dev/null; then
    PYTHON_BIN="python"
else
    echo "Error: Python not found" >&2
    exit 1
fi

case "$MODE" in
    inline)
        if [[ -z "${INLINE_CODE:-}" ]]; then
            # No inline code given - try to read from stdin
            if [[ ! -t 0 ]]; then
                "${TIMEOUT_CMD[@]}" "$PYTHON_BIN" -
            else
                echo "Error: No code provided and no stdin input" >&2
                show_help
            fi
        else
            "${TIMEOUT_CMD[@]}" "$PYTHON_BIN" -c "$INLINE_CODE"
        fi
        ;;
    script)
        [[ ! -f "$SCRIPT" ]] && { echo "Error: Script not found: $SCRIPT" >&2; exit 1; }
        "${TIMEOUT_CMD[@]}" "$PYTHON_BIN" "$SCRIPT" "${SCRIPT_ARGS[@]}"
        ;;
    *)
        # No mode specified and no stdin - error
        if [[ ! -t 0 ]]; then
            # stdin has data
            "${TIMEOUT_CMD[@]}" "$PYTHON_BIN" -
        else
            echo "Error: No mode specified" >&2
            show_help
        fi
        ;;
esac
