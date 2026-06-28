#!/bin/bash
# run-in-env - Activate venv, set env vars (from -e flags or --env-file), install packages, and run a command
# Usage: run-in-env [--dir=DIR] [--venv=PATH] [--env KEY=val ...] [--env-file=PATH] [--pip-install=PKG] [--pip-requirements=FILE] [--python-code=CODE] [--node-code=CODE] [--python-stdin] [--timeout=N] [--] <command> [args...]
# Supports stdin pipe:
#   echo 'print(1+1)' | run-in-env --python-stdin       # stdin as Python code
#   cat data.json | run-in-env --python-code="import sys; import json; d=json.load(sys.stdin); ..."

set -euo pipefail

DIR=""
VENV_PATH=""
ENV_VARS=()
ENV_FILES=()
PYTHON_CODE=""
NODE_CODE=""
PYTHON_STDIN=false
COMMAND=()
STDIN_FILE=""
TIMEOUT=""
PIP_PACKAGES=()
PIP_REQUIREMENTS=""
PYTHONPATH_VAL=""
PIP_ONLY=false
PIP_LIST=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dir=*)
            DIR="${1#*=}"
            shift
            ;;
        --venv=*)
            VENV_PATH="${1#*=}"
            shift
            ;;
        --env-file=*)
            ENV_FILES+=("${1#*=}")
            shift
            ;;
        --env=*)
            ENV_VARS+=("${1#*=}")
            shift
            ;;
        --env)
            shift
            ENV_VARS+=("$1")
            shift
            ;;
        -e)
            shift
            ENV_VARS+=("$1")
            shift
            ;;
        --pip-install=*)
            PIP_PACKAGES+=("${1#*=}")
            shift
            ;;
        --pip-requirements=*)
            PIP_REQUIREMENTS="${1#*=}"
            shift
            ;;
        --pip-only)
            PIP_ONLY=true
            shift
            ;;
        --python-path=*)
            PYTHONPATH_VAL="${1#*=}"
            shift
            ;;
        --python-path)
            shift
            PYTHONPATH_VAL="$1"
            shift
            ;;
        --py-path=*)
            PYTHONPATH_VAL="${1#*=}"
            shift
            ;;
        --py-path)
            shift
            PYTHONPATH_VAL="$1"
            shift
            ;;
        --pip-list=*)
            PIP_LIST="${1#*=}"
            shift
            ;;
        --pip-list)
            shift
            PIP_LIST="$1"
            shift
            ;;
        --python-code=*)
            PYTHON_CODE="${1#*=}"
            shift
            ;;
        --node-code=*)
            NODE_CODE="${1#*=}"
            shift
            ;;
        --python-stdin)
            PYTHON_STDIN=true
            shift
            ;;
        --timeout=*)
            TIMEOUT="${1#*=}"
            shift
            ;;
        --)
            shift
            COMMAND+=("$@")
            break
            ;;
        --help|-h)
            echo "Usage: $0 [--dir=DIR] [--venv=PATH] [--env KEY=val ...] [--env-file=PATH] [--pip-install=PKG] [--pip-requirements=FILE] [--python-code=CODE] [--node-code=CODE] [--python-stdin] [--timeout=N] [--] <command> [args...]" >&2
            echo ""
            echo "  --dir=DIR             Change to DIR before running"
            echo "  --venv=PATH           Path to virtual env activate script (default: venv/bin/activate)"
            echo "  --env KEY=val         Set environment variable (repeatable, or -e KEY=val)"
            echo "  --env-file=PATH       Load env vars from file (.env / shell config format)"
            echo "  --pip-install=PKG     Install Python package(s) via pip (comma-separated or repeatable)"
            echo "  --pip-requirements=FILE Install packages from requirements.txt"
            echo "  --pip-only            Only install packages (--pip-install/--pip-requirements) without running a command"
            echo "  --python-path=DIR     Set PYTHONPATH to DIR (or --py-path=DIR for short)"
            echo "  --pip-list=PATTERN    List installed packages matching pattern (grep -i)"
            echo "  --python-code=CODE    Inline Python code to run (written to temp file)"
            echo "  --node-code=CODE      Inline Node.js code to run (written to temp file)"
            echo "  --python-stdin        Read Python script from stdin (pipe heredoc/echo)"
            echo "  --timeout=N           Kill command after N seconds (wraps with timeout)"
            echo "  --                    Separator before command"
            echo "  <command>             Command to run (remaining args are passed as-is)"
            echo ""
            echo "  Examples:"
            echo "    cat <<'PYEOF' | run-in-env --dir=/app --env CONFIG=test.env --python-stdin"
            echo "    import os"
            echo "    print(os.environ.get('CONFIG'))"
            echo "    PYEOF"
            echo ""
            echo "  Piped stdin is forwarded as data to --python-code/--node-code scripts."
            exit 0
            ;;
        *)
            COMMAND+=("$1")
            shift
            ;;
    esac
done

if [ -n "$DIR" ]; then
    cd "$DIR"
fi

# Source venv if it exists
if [ -z "$VENV_PATH" ]; then
    if [ -f "venv/bin/activate" ]; then
        VENV_PATH="venv/bin/activate"
    fi
fi

if [ -n "$VENV_PATH" ] && [ -f "$VENV_PATH" ]; then
    # shellcheck disable=SC1090
    source "$VENV_PATH"
fi

# Export environment variables from --env flags
for ev in "${ENV_VARS[@]}"; do
    export "$ev"
done

# Export environment variables from --env-file files
# Supports .env format (KEY=val) and shell config format (export KEY=val, #comments)
for env_file in "${ENV_FILES[@]}"; do
    if [ -f "$env_file" ]; then
        while IFS= read -r line || [ -n "$line" ]; do
            # Skip comments and empty lines
            [[ "$line" =~ ^[[:space:]]*# ]] && continue
            [[ "$line" =~ ^[[:space:]]*$ ]] && continue
            # Skip lines that don't contain =
            [[ "$line" != *"="* ]] && continue
            # Remove leading 'export ' if present
            cleaned="${line#export }"
            # Remove leading whitespace
            cleaned="${cleaned##[[:space:]]}"
            # Extract key and value
            key="${cleaned%%=*}"
            # Remove trailing whitespace from key
            key="${key%%[[:space:]]}"
            # Skip if key is empty
            [ -z "$key" ] && continue
            # Export using eval to handle quoted values properly
            eval "export $cleaned" 2>/dev/null || true
        done < "$env_file"
    fi
done

# Install pip packages if specified
if [ ${#PIP_PACKAGES[@]} -gt 0 ]; then
    for pkg in "${PIP_PACKAGES[@]}"; do
        # Support comma-separated packages in a single --pip-install value
        IFS=',' read -ra PKGS <<< "$pkg"
        for single_pkg in "${PKGS[@]}"; do
            single_pkg="$(echo "$single_pkg" | xargs)"  # trim
            [ -n "$single_pkg" ] && pip install "$single_pkg" 2>&1
        done
    done
fi

# Install from requirements file if specified
if [ -n "$PIP_REQUIREMENTS" ]; then
    if [ -f "$PIP_REQUIREMENTS" ]; then
        pip install -r "$PIP_REQUIREMENTS" 2>&1
    fi
fi

# Handle --pip-list: list installed packages matching pattern
if [ -n "$PIP_LIST" ]; then
    pip list 2>/dev/null | grep -i "$PIP_LIST" || echo "[NO MATCH] No packages matching: $PIP_LIST"
    if [ ${#COMMAND[@]} -eq 0 ] && [ -z "$PYTHON_CODE" ] && [ -z "$NODE_CODE" ] && [ "$PYTHON_STDIN" = false ]; then
        exit 0
    fi
fi

# If --pip-only is set and no command/code is provided, just exit after pip operations
if [ "$PIP_ONLY" = true ] && [ ${#COMMAND[@]} -eq 0 ] && [ -z "$PYTHON_CODE" ] && [ -z "$NODE_CODE" ] && [ "$PYTHON_STDIN" = false ]; then
    exit 0
fi

# Set PYTHONPATH if specified
if [ -n "$PYTHONPATH_VAL" ]; then
    if [ -d "$PYTHONPATH_VAL" ]; then
        export PYTHONPATH="$PYTHONPATH_VAL"
    else
        # Try resolving relative to current dir
        if [ -d "$(pwd)/$PYTHONPATH_VAL" ]; then
            export PYTHONPATH="$(pwd)/$PYTHONPATH_VAL"
        else
            export PYTHONPATH="$PYTHONPATH_VAL"
        fi
    fi
fi


# Build timeout prefix
TIMEOUT_PREFIX=""
if [ -n "$TIMEOUT" ]; then
    TIMEOUT_PREFIX="timeout $TIMEOUT"
fi

# Handle --python-stdin: read piped stdin as Python script source
if [ "$PYTHON_STDIN" = true ]; then
    if [ -t 0 ]; then
        echo "ERROR: --python-stdin requires piped stdin (pipe a heredoc or echo)" >&2
        exit 1
    fi
    # Read all stdin into a temp file and execute as Python script
    TMPFILE="$(mktemp /tmp/run_python_XXXXXX.py)"
    cat > "$TMPFILE"
    $TIMEOUT_PREFIX python "$TMPFILE" "${COMMAND[@]+${COMMAND[@]}}"
    rc=$?
    rm -f "$TMPFILE"
    exit $rc
fi

# Capture stdin if piped (for --python-code/--node-code as data forwarding)
if [ ! -t 0 ]; then
    STDIN_FILE="$(mktemp /tmp/run_stdin_XXXXXX)"
    cat > "$STDIN_FILE"
fi

# Helper to run and clean up temp files
run_and_cleanup() {
    local tmpfile="$1"
    shift
    if [ -n "$STDIN_FILE" ]; then
        $TIMEOUT_PREFIX python "$tmpfile" "$@" < "$STDIN_FILE"
    else
        $TIMEOUT_PREFIX python "$tmpfile" "$@"
    fi
    rc=$?
    rm -f "$tmpfile"
    [ -n "$STDIN_FILE" ] && rm -f "$STDIN_FILE"
    exit $rc
}

# Handle --python-code: write to temp file and run with python
if [ -n "$PYTHON_CODE" ]; then
    TMPFILE="$(mktemp /tmp/run_python_XXXXXX.py)"
    printf '%s\n' "$PYTHON_CODE" > "$TMPFILE"
    run_and_cleanup "$TMPFILE" "${COMMAND[@]+${COMMAND[@]}}"
fi

# Handle --node-code: write to temp file and run with node
if [ -n "$NODE_CODE" ]; then
    TMPFILE="$(mktemp /tmp/run_node_XXXXXX.js)"
    printf '%s\n' "$NODE_CODE" > "$TMPFILE"
    if [ -n "$STDIN_FILE" ]; then
        $TIMEOUT_PREFIX node "$TMPFILE" "${COMMAND[@]+${COMMAND[@]}}" < "$STDIN_FILE"
    else
        $TIMEOUT_PREFIX node "$TMPFILE" "${COMMAND[@]+${COMMAND[@]}}"
    fi
    rc=$?
    rm -f "$TMPFILE"
    [ -n "$STDIN_FILE" ] && rm -f "$STDIN_FILE"
    exit $rc
fi

# Clean up stdin temp file if no --python-code/--node-code (not needed)
[ -n "$STDIN_FILE" ] && rm -f "$STDIN_FILE"

if [ ${#COMMAND[@]} -eq 0 ]; then
    echo "Usage: $0 [--dir=DIR] [--venv=PATH] [--env KEY=val ...] [--env-file=PATH] [--pip-install=PKG] [--pip-requirements=FILE] [--python-code=CODE] [--node-code=CODE] [--python-stdin] [--timeout=N] [--] <command> [args...]" >&2
    exit 1
fi

# Run the command with optional timeout
if [ -n "$TIMEOUT_PREFIX" ]; then
    exec $TIMEOUT_PREFIX "${COMMAND[@]}"
else
    exec "${COMMAND[@]}"
fi
