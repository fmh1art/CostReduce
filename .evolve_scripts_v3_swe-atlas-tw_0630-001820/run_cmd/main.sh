#!/usr/bin/env bash
set -euo pipefail

# run_cmd - Run commands or scripts with --dir, --env, --venv, --env-file, --save-env, --timeout, and stdin/heredoc support.
# Usage:
#   run_cmd [--dir=DIR] [--venv=PATH] [--env=KEY=val] [--env-file=PATH] [--save-env=PATH] [--timeout=SECS] <command> [args...]
#   run_cmd [--dir=DIR] [--venv=PATH] [--env=KEY=val] [--timeout=SECS] --interpreter=bash|python|node|ts-node|npx << 'SCRIPT'
#       multi-line script...
#   SCRIPT
#   echo 'commands' | run_cmd [--dir=DIR] [--venv=PATH] [--env=KEY=val] [--interpreter=bash|python|node|ts-node|npx]
#   or: run_cmd [--dir=DIR] [--venv=PATH] [--pythonpath=PATH] [--env=KEY=val] [--env-file=PATH] [--save-env=PATH] [--timeout=SECS] <command> [args...]
#   or: run_cmd [--dir=DIR] [--venv=PATH] [--pythonpath=PATH] [--env=KEY=val] [--timeout=SECS] --interpreter=bash|python|node|ts-node|npx << 'SCRIPT'
#       multi-line script...
#   SCRIPT
#   echo 'commands' | run_cmd [--dir=DIR] [--venv=PATH] [--pythonpath=PATH] [--env=KEY=val] [--interpreter=bash|python|node|ts-node|npx]

DIR=""
ENV_VARS=()
TIMEOUT=""
ENV_FILE=""
SAVE_ENV_FILE=""
INTERPRETER="bash"
VENV_PATH=""
VENV_PATH=""
PYTHONPATH_DIR=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dir=*)
            DIR="${1#*=}"
            shift
            ;;
        -C)
            DIR="$2"
            shift 2
            ;;
        --venv=*)
            VENV_PATH="${1#*=}"
            shift
            ;;
        --pythonpath=*)
            PYTHONPATH_DIR="${1#*=}"
            shift
            ;;
        --env=*)
            ENV_VARS+=("${1#*=}")
            shift
            ;;
        -e|--env)
            ENV_VARS+=("$2")
            shift 2
            ;;
        --env-file=*)
            ENV_FILE="${1#*=}"
            shift
            ;;
        --save-env=*)
            SAVE_ENV_FILE="${1#*=}"
            shift
            ;;
        --timeout=*)
            TIMEOUT="${1#*=}"
            shift
            ;;
        --interpreter=*)
            INTERPRETER="${1#*=}"
            shift
            ;;
        --)
            shift
            break
            ;;
        -*)
            echo "Unknown option: $1" >&2
            exit 1
            ;;
        *)
            break
            ;;
    esac
done

# Change directory if specified
if [[ -n "$DIR" ]]; then
    if [[ ! -d "$DIR" ]]; then
        echo "Error: directory not found: $DIR" >&2
        exit 1
    fi
    cd "$DIR"
fi

# Source venv if explicitly specified via --venv
if [[ -n "$VENV_PATH" ]]; then
    if [[ -f "$VENV_PATH/bin/activate" ]]; then
        source "$VENV_PATH/bin/activate" 2>/dev/null || true
    else
        echo "Warning: venv not found at $VENV_PATH" >&2
    fi
fi

# Auto-detect venv for Python interpreter (fallback if --venv not set)
if [[ -z "$VENV_PATH" && "$INTERPRETER" == "python" ]]; then
    if [[ -d "venv" ]]; then
        source venv/bin/activate 2>/dev/null || true
    elif [[ -d "/app/venv" ]]; then
        source /app/venv/bin/activate 2>/dev/null || true
    fi
fi

# Source env file if specified
if [[ -n "$ENV_FILE" ]]; then
    if [[ ! -f "$ENV_FILE" ]]; then
        echo "Error: env file not found: $ENV_FILE" >&2
        exit 1
    fi
    set -a
    source "$ENV_FILE"
    set +a
fi

# Export individual env vars (may override env-file vars)
for e in "${ENV_VARS[@]}"; do
    export "$e"
done

# Set PYTHONPATH if --pythonpath was specified
if [[ -n "$PYTHONPATH_DIR" ]]; then
    if [[ -n "${PYTHONPATH:-}" ]]; then
        export PYTHONPATH="$PYTHONPATH_DIR:$PYTHONPATH"
    else
        export PYTHONPATH="$PYTHONPATH_DIR"
    fi
fi

# Save env vars to file if requested
if [[ -n "$SAVE_ENV_FILE" ]]; then
    mkdir -p "$(dirname "$SAVE_ENV_FILE")"
    for e in "${ENV_VARS[@]}"; do
        key="${e%%=*}"
        printf '%s=%s\n' "$key" "${!key}" >> "$SAVE_ENV_FILE"
    done
    echo "Saved ${#ENV_VARS[@]} env vars to $SAVE_ENV_FILE"
fi

# If we have a command argument, run it directly
if [[ $# -gt 0 ]]; then
    if [[ -n "$TIMEOUT" ]]; then
        exec timeout "$TIMEOUT" "$@"
    else
        exec "$@"
    fi
fi

# No command args - check for stdin/heredoc
if [[ -t 0 ]]; then
    # Terminal input (not piped/heredoc) - show usage
    echo "Usage: run_cmd [--dir=DIR] [--venv=PATH] [--env=KEY=val] [--env-file=PATH] [--save-env=PATH] [--timeout=SECS] [--interpreter=bash|python|node|ts-node|npx] <command> [args...]" >&2
    echo "   or: run_cmd [opts] << 'SCRIPT'        # multi-line script via heredoc" >&2
    echo "   or: echo 'commands' | run_cmd [opts]  # multi-line script via pipe" >&2
    exit 1
fi

# Read stdin into a temp file and execute
TMPFILE=$(mktemp)
cat > "$TMPFILE"

if [[ "$INTERPRETER" == "python" ]]; then
    if [[ -n "$TIMEOUT" ]]; then
        timeout "$TIMEOUT" python3 "$TMPFILE" "$@"
    else
        python3 "$TMPFILE" "$@"
    fi
elif [[ "$INTERPRETER" == "node" ]]; then
    if [[ -n "$TIMEOUT" ]]; then
        timeout "$TIMEOUT" node "$TMPFILE" "$@"
    else
        node "$TMPFILE" "$@"
    fi
elif [[ "$INTERPRETER" == "ts-node" ]]; then
    if command -v npx &>/dev/null; then
        if [[ -n "$TIMEOUT" ]]; then
            timeout "$TIMEOUT" npx ts-node "$TMPFILE" "$@"
        else
            npx ts-node "$TMPFILE" "$@"
        fi
    elif command -v ts-node &>/dev/null; then
        if [[ -n "$TIMEOUT" ]]; then
            timeout "$TIMEOUT" ts-node "$TMPFILE" "$@"
        else
            ts-node "$TMPFILE" "$@"
        fi
    else
        echo "Error: ts-node not found. Install with: npm install -g ts-node or npx ts-node" >&2
        exit 1
    fi
else
    if [[ -n "$TIMEOUT" ]]; then
        timeout "$TIMEOUT" bash "$TMPFILE" "$@"
    else
        bash "$TMPFILE" "$@"
    fi
fi
RC=$?
rm -f "$TMPFILE"
exit $RC
