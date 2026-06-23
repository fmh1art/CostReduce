#!/bin/bash
# py_exec - Run Python code efficiently
# Usage: py_exec <code_string>
#        py_exec -f <script.py> [args...]
#        py_exec --check <file.py> [file2.py ...]
#        py_exec --check-syntax <file.py> [file2.py ...]
#        py_exec --env="KEY=value" <code_string>
#        py_exec -e KEY=value <code_string>
#
# Runs Python code in one step. Handles common setup like:
# - Activating virtual environments automatically
# - Adding sys.path entries
# - Setting environment variables via --env / -e
# - Capturing and formatting output/errors
#
# Use --check or --check-syntax to validate Python syntax without executing.
# This replaces the pattern of running `python3 -c "import py_compile; py_compile.compile(...)"`.
#
# Saves steps by combining script creation, environment setup, and execution into one call,
# and by avoiding trial-and-error with Python environment setup.
#
# Examples:
#   py_exec "print('hello world')"
#   py_exec "import sys; print(sys.version)"
#   py_exec -f test.py
#   py_exec --env="MY_SETTINGS=production" "import os; print(os.environ.get("MY_SETTINGS"))"
#   py_exec -e DB_USER=admin -e DB_NAME=testdb "import os; print(os.environ.get("DB_USER"), os.environ.get("DB_NAME"))"
#   py_exec --check file.py
#   py_exec --check-syntax file1.py file2.py

ENV_VARS=()

# Collect --env / -e options before processing other args
ARGS=()
while [ $# -gt 0 ]; do
    case "$1" in
        --env=*)
            ENV_VARS+=("${1#*=}")
            shift
            ;;
        -e)
            shift
            if [ $# -gt 0 ]; then
                ENV_VARS+=("$1")
                shift
            fi
            ;;
        --help|-h)
            echo "Usage: py_exec [options] <code_string>"
            echo "       py_exec -f <script.py> [args...]"
            echo "       py_exec --check <file.py> [file2.py ...]"
            echo ""
            echo "Options:"
            echo "  -h, --help         Show this help message"
            echo "  -f <script.py>     Run a Python script file"
            echo "  --check, --check-syntax  Check Python syntax of file(s) without executing"
            echo "  --env=KEY=val      Set environment variable (can repeat)"
            echo "  -e KEY=val         Short form of --env"
            echo ""
            echo "Examples:"
            echo '  py_exec "print('"'hello world'"')"'
            echo "  py_exec -f test.py arg1 arg2"
            echo '  py_exec "import sys; print(sys.version)"'
            echo '  py_exec --env="MY_SETTINGS=production" "import os; print(os.environ.get("MY_SETTINGS"))"'
            echo "  py_exec --check file.py"
            exit 0
            ;;
        *)
            ARGS+=("$1")
            shift
            ;;
    esac
done

set -- "${ARGS[@]}"

# Build environment variable prefix
ENV_PREFIX=""
if [ ${#ENV_VARS[@]} -gt 0 ]; then
    for env_var in "${ENV_VARS[@]}"; do
        ENV_PREFIX="$ENV_PREFIX $env_var"
    done
    ENV_PREFIX="env ${ENV_PREFIX# }"
fi

if [ $# -eq 0 ]; then
    echo "Error: No code or command specified."
    exit 1
fi

# Check for syntax checking mode
if [ "$1" = "--check" ] || [ "$1" = "--check-syntax" ]; then
    shift
    if [ $# -eq 0 ]; then
        echo "Error: --check requires at least one Python file."
        exit 1
    fi
    OVERALL=true
    for file in "$@"; do
        if [ ! -f "$file" ]; then
            echo "=== $file (FILE NOT FOUND) ==="
            OVERALL=false
            continue
        fi
        eval "$ENV_PREFIX python3 -c \"
import py_compile
import sys
try:
    py_compile.compile('$file', doraise=True)
    print('$file: Syntax OK')
except py_compile.PyCompileError as e:
    print('$file: SYNTAX ERROR')
    print(str(e))
    sys.exit(1)
\" 2>&1"
        if [ $? -ne 0 ]; then
            OVERALL=false
        fi
    done
    if [ "$OVERALL" = true ]; then
        echo "All files passed syntax check."
        exit 0
    else
        echo "Some files FAILED syntax check."
        exit 1
    fi
fi

# Try to activate virtual environment if present
VENV_PATHS=(".venv" "venv" "env" "../.venv" "../venv" "../env")
for venv in "${VENV_PATHS[@]}"; do
    if [ -f "$venv/bin/activate" ]; then
        source "$venv/bin/activate" 2>/dev/null || true
        break
    fi
done

if [ "$1" = "-f" ]; then
    shift
    SCRIPT="$1"
    shift
    if [ ! -f "$SCRIPT" ]; then
        echo "Error: Script file not found: $SCRIPT"
        exit 1
    fi
    eval "$ENV_PREFIX python3 \"$SCRIPT\" \"$@\""
else
    # Run inline code with env vars
    eval "$ENV_PREFIX python3 -c \"$@\""
fi
