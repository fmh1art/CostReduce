#!/usr/bin/env bash
set -euo pipefail

# py_exec - Run Python or Node.js inline code or scripts with auto-venv activation, env vars,
#          --cd, --venv, --env-file, and --save-env
# Usage: py_exec [--cd=DIR] [--venv=PATH] [--node] [--env=KEY=val] <code_string>
#   or: py_exec [--cd=DIR] [--venv=PATH] [--node] [--env=KEY=val] -f <script.py> [args...]
#   or: py_exec [--cd=DIR] [--venv=PATH] [--node] [--env=KEY=val] -f - <args...> << 'EOF'
#       ...script content...
#   EOF
#   or: py_exec --check <file.py> [file2...]
#   or: py_exec --env=KEY=val --env-file=PATH -f <script.py>
#   or: py_exec --env=KEY=val --save-env=PATH -f <script.py>
#   or: py_exec [--cd=DIR] [--venv=PATH] [--pythonpath=PATH] [--node] [--env=KEY=val] -f <script.py> [args...]
#   or: py_exec [--cd=DIR] [--venv=PATH] [--pythonpath=PATH] [--node] [--env=KEY=val] <code_string>
#   or: py_exec [--cd=DIR] [--venv=PATH] [--pythonpath=PATH] [--node] [--env=KEY=val] -f - <args...> << 'EOF'
#       ...script content...
#   EOF
#   or: py_exec --check <file.py> [file2...]
#   or: py_exec --env=KEY=val --env-file=PATH -f <script.py>
#   or: py_exec --env=KEY=val --save-env=PATH -f <script.py>

MODE="run"
SCRIPT=""
FILES=()
ENV_VARS=()
ENV_FILE=""
SAVE_ENV_FILE=""
INTERPRETER="python"
CD_DIR=""
VENV_DIR=""
VENV_DIR=""
PYTHONPATH_DIR=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --node)
            INTERPRETER="node"
            shift
            ;;
        --cd=*)
            CD_DIR="${1#*=}"
            shift
            ;;
        --venv=*)
            VENV_DIR="${1#*=}"
            shift
            ;;
        --pythonpath=*)
            PYTHONPATH_DIR="${1#*=}"
            shift
            ;;
        -f)
            MODE="file"
            SCRIPT="$2"
            shift 2
            break
            ;;
        --check|--check-syntax)
            MODE="check"
            shift
            while [[ $# -gt 0 && "$1" != -* ]]; do
                FILES+=("$1")
                shift
            done
            break
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
        -*)
            echo "Unknown option: $1" >&2
            exit 1
            ;;
        *)
            MODE="inline"
            CODE="$1"
            shift
            break
            ;;
    esac
done

# Change directory if specified
if [[ -n "$CD_DIR" ]]; then
    if [[ ! -d "$CD_DIR" ]]; then
        echo "Error: directory not found: $CD_DIR" >&2
        exit 1
    fi
    cd "$CD_DIR"
fi

# Activate venv if specified (only for Python)
if [[ "$INTERPRETER" == "python" ]]; then
    if [[ -n "$VENV_DIR" ]]; then
        ACTIVATE_SCRIPT="$VENV_DIR/bin/activate"
        if [[ -f "$ACTIVATE_SCRIPT" ]]; then
            . "$ACTIVATE_SCRIPT"
        else
            echo "Warning: venv activate script not found: $ACTIVATE_SCRIPT" >&2
        fi
    else
        # Auto-detect common venv locations
        if [[ -d "venv" ]]; then
            . venv/bin/activate 2>/dev/null || true
        elif [[ -d "/app/venv" ]]; then
            . /app/venv/bin/activate 2>/dev/null || true
        fi
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

# Export env vars (may override env-file vars)
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

case "$MODE" in
    inline)
        if [[ "$INTERPRETER" == "node" ]]; then
            exec node -e "$CODE"
        else
            exec python3 -c "$CODE"
        fi
        ;;
    file)
        if [[ "$SCRIPT" == "-" ]]; then
            # Read script from stdin (heredoc or pipe)
            if [[ "$INTERPRETER" == "node" ]]; then
                exec node -e "$(cat)" "$@"
            else
                exec python3 -c "$(cat)" "$@"
            fi
        else
            if [[ "$INTERPRETER" == "node" ]]; then
                exec node "$SCRIPT" "$@"
            else
                exec python3 "$SCRIPT" "$@"
            fi
        fi
        ;;
    check)
        if [[ "$INTERPRETER" == "node" ]]; then
            HAS_ERROR=false
            for f in "${FILES[@]}"; do
                if [[ ! -f "$f" ]]; then
                    echo "File not found: $f" >&2
                    HAS_ERROR=true
                    continue
                fi
                node --check "$f" 2>&1 || HAS_ERROR=true
            done
            $HAS_ERROR && exit 1
            echo "Syntax OK"
        else
            HAS_ERROR=false
            for f in "${FILES[@]}"; do
                if [[ ! -f "$f" ]]; then
                    echo "File not found: $f" >&2
                    HAS_ERROR=true
                    continue
                fi
                python3 -m py_compile "$f" 2>&1 || HAS_ERROR=true
            done
            $HAS_ERROR && exit 1
            echo "Syntax OK"
        fi
        ;;
esac
