#!/bin/bash
# python_runner - Run Python files or inline Python expressions
# Usage: python_runner <project_root> <action> <target> [options]
# Actions: run, eval, script, test
#   script - Read Python code from stdin (for heredoc usage) and execute it
#
# Features:
#   - Auto-detects virtual environment (venv/, .venv/, env/, virtualenv/) in project root
#   - Activates the virtual environment before running Python code
#   - Supports --env-file to safely load .env files (avoids shell export issues)
#   - Reports which venv and env file were used

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOAD_ENV_SCRIPT="$SCRIPT_DIR/load_env.py"

PROJECT_ROOT="$1"
ACTION="$2"
TARGET="$3"
shift 3 2>/dev/null || shift $#

TIMEOUT=60
MAX_LINES=100
ENV_FILE=""

while [ $# -gt 0 ]; do
    case "$1" in
        --timeout)
            TIMEOUT="$2"
            shift 2
            ;;
        --timeout=*)
            TIMEOUT="${1#*=}"
            shift
            ;;
        --max-lines|-m)
            MAX_LINES="$2"
            shift 2
            ;;
        --max-lines=*|-m=*)
            MAX_LINES="${1#*=}"
            shift
            ;;
        --env-file)
            ENV_FILE="$2"
            shift 2
            ;;
        --env-file=*)
            ENV_FILE="${1#*=}"
            shift
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

if [ -z "$PROJECT_ROOT" ] || [ -z "$ACTION" ]; then
    echo "Usage: python_runner <project_root> <action> [target] [options]"
    echo ""
    echo "Actions:"
    echo "  run       Execute a .py file (target is path relative to project_root)"
    echo "  eval      Run inline Python code (target is the code string)"
    echo "  script    Read Python code from stdin and execute (target is optional description)"
    echo "  test      Run Python tests (target is path or test discovery)"
    echo ""
    echo "Options:"
    echo "  --timeout N          Timeout in seconds (default: 60)"
    echo "  --max-lines/-m N     Maximum output lines (default: 100, 0=unlimited)"
    echo "  --env-file <path>    Load environment variables from a .env file safely"
    echo "                       (uses Python to parse, avoiding shell export issues)"
    exit 1
fi

if [ ! -d "$PROJECT_ROOT" ]; then
    echo "Error: Directory '$PROJECT_ROOT' does not exist"
    exit 1
fi

# --- Virtual environment detection ---
VENV_DIR=""
for venv_candidate in "venv" ".venv" "env" "virtualenv"; do
    if [ -f "$PROJECT_ROOT/$venv_candidate/bin/activate" ]; then
        VENV_DIR="$venv_candidate"
        break
    fi
done

# --- .env file resolution ---
if [ -z "$ENV_FILE" ]; then
    # Auto-detect .env file
    for env_candidate in ".env" "env" ".env.example" "example.env"; do
        if [ -f "$PROJECT_ROOT/$env_candidate" ]; then
            ENV_FILE="$PROJECT_ROOT/$env_candidate"
            break
        fi
    done
else
    # User-specified env file, resolve relative to project root if not absolute
    case "$ENV_FILE" in
        /*) ;;  # absolute path, use as-is
        *) ENV_FILE="$PROJECT_ROOT/$ENV_FILE" ;;
    esac
fi

# Generate Python code to load env file (if needed)
generate_env_loader() {
    if [ -n "$ENV_FILE" ] && [ -f "$ENV_FILE" ]; then
        rel_path="${ENV_FILE#$PROJECT_ROOT/}"
        echo "# Load environment variables from $rel_path"
        echo "import os"
        echo "with open('$ENV_FILE') as f:"
        echo "    loaded = 0"
        echo "    for line in f:"
        echo "        line = line.strip()"
        echo "        if not line or line.startswith('#'):"
        echo "            continue"
        echo "        if '=' not in line:"
        echo "            continue"
        echo "        key, _, val = line.partition('=')"
        echo "        key = key.strip()"
        echo "        val = val.strip()"
        echo "        if len(val) >= 2 and val[0] == val[-1] and val[0] in ('\"', \"'\"):"
        echo "            val = val[1:-1]"
        echo "        os.environ[key] = val"
        echo "        loaded += 1"
        echo "print(f'[Loaded {loaded} env vars from {repr(\"$rel_path\")}]')"
        echo ""
    fi
}

# Build the shell command prefix (cd + venv activation)
build_shell_prefix() {
    local prefix="cd \"$PROJECT_ROOT\""
    if [ -n "$VENV_DIR" ]; then
        echo "[Using virtualenv: $VENV_DIR/bin/activate]" >&2
        prefix="$prefix && . \"$PROJECT_ROOT/$VENV_DIR/bin/activate\""
    fi
    echo "$prefix"
}

SHELL_PREFIX=$(build_shell_prefix)

create_temp_script() {
    local content="$1"
    local tmpfile
    tmpfile=$(mktemp /tmp/python_runner_XXXXXX.py)
    # Write env loader + user code to temp file
    generate_env_loader >> "$tmpfile"
    printf '%s\n' "$content" >> "$tmpfile"
    echo "$tmpfile"
}

EXIT_CODE=0

case "$ACTION" in
    run)
        if [ -z "$TARGET" ]; then
            echo "Error: run action requires a file path as target"
            exit 1
        fi
        echo "Running: python3 $TARGET"
        echo ""
        
        # For run action, we need to load env before running the file
        if [ -n "$ENV_FILE" ] && [ -f "$ENV_FILE" ]; then
            rel_path="${ENV_FILE#$PROJECT_ROOT/}"
            echo "[Loading env vars from: $rel_path]"
            # Create a wrapper that loads env then executes the target file
            WRAPPER=$(mktemp /tmp/python_runner_wrapper_XXXXXX.py)
            generate_env_loader >> "$WRAPPER"
            echo "import runpy" >> "$WRAPPER"
            echo "runpy.run_path('$PROJECT_ROOT/$TARGET', run_name='__main__')" >> "$WRAPPER"
            FULL_CMD="$SHELL_PREFIX && python3 \"$WRAPPER\" 2>&1"
        else
            FULL_CMD="$SHELL_PREFIX && python3 \"$PROJECT_ROOT/$TARGET\" 2>&1"
        fi
        
        if [ "$MAX_LINES" -gt 0 ]; then
            timeout "$TIMEOUT" bash -c "$FULL_CMD" | head -n "$MAX_LINES"
        else
            timeout "$TIMEOUT" bash -c "$FULL_CMD"
        fi
        EXIT_CODE=$?
        ;;
    eval)
        if [ -z "$TARGET" ]; then
            echo "Error: eval action requires inline Python code as target"
            exit 1
        fi
        echo "Running inline Python code..."
        echo ""
        
        EVAL_SCRIPT=$(create_temp_script "$TARGET")
        FULL_CMD="$SHELL_PREFIX && python3 \"$EVAL_SCRIPT\" 2>&1"
        
        if [ "$MAX_LINES" -gt 0 ]; then
            timeout "$TIMEOUT" bash -c "$FULL_CMD" | head -n "$MAX_LINES"
        else
            timeout "$TIMEOUT" bash -c "$FULL_CMD"
        fi
        EXIT_CODE=$?
        ;;
    script)
        if [ -n "$TARGET" ]; then
            echo "Running piped Python script: $TARGET"
        else
            echo "Running piped Python script..."
        fi
        echo ""
        
        # For script, we prepend env loading to stdin
        # We create a temp script that includes env loading + stdin content
        SCRIPT_FILE=$(mktemp /tmp/python_runner_script_XXXXXX.py)
        generate_env_loader >> "$SCRIPT_FILE"
        # Read stdin and append to the script file
        cat >> "$SCRIPT_FILE"
        
        FULL_CMD="$SHELL_PREFIX && python3 \"$SCRIPT_FILE\" 2>&1"
        
        if [ "$MAX_LINES" -gt 0 ]; then
            timeout "$TIMEOUT" bash -c "$FULL_CMD" | head -n "$MAX_LINES"
        else
            timeout "$TIMEOUT" bash -c "$FULL_CMD"
        fi
        EXIT_CODE=$?
        ;;
    test)
        echo "Running: python3 -m pytest $TARGET"
        echo ""
        
        FULL_CMD="$SHELL_PREFIX && python3 -m pytest \"$TARGET\" -v 2>&1"
        
        if [ "$MAX_LINES" -gt 0 ]; then
            timeout "$TIMEOUT" bash -c "$FULL_CMD" | head -n "$MAX_LINES"
        else
            timeout "$TIMEOUT" bash -c "$FULL_CMD"
        fi
        EXIT_CODE=$?
        ;;
    *)
        echo "Unknown action: $ACTION"
        echo "Use: run, eval, script, or test"
        exit 1
        ;;
esac

if [ $EXIT_CODE -eq 124 ]; then
    echo ""
    echo "Command timed out after ${TIMEOUT}s"
elif [ $EXIT_CODE -ne 0 ]; then
    echo ""
    echo "Command failed (exit code: $EXIT_CODE)"
fi

exit $EXIT_CODE
