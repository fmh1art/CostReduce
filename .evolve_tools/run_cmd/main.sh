#!/bin/bash
# run_cmd - Run arbitrary commands with directory and environment context
# Usage: run_cmd [options] <command> [args...]
#   -C, --dir=DIR        Working directory for the command (default: current dir)
#   -e, --env=KEY=val    Set environment variable (repeatable)
#   --timeout=SECONDS    Timeout in seconds
#
# Runs an arbitrary command in a specified working directory with
# optional environment variables set. Saves steps by replacing patterns like:
#   cd /app/src && export VAR1=val1 && export VAR2=val2 && command
# with a single tool call.
#
# Examples:
#   run_cmd --dir=/app python -m scapy.tools.UTscapy -t test.uts -f text
#   run_cmd --dir=/app/src -e DJANGO_SETTINGS_MODULE=paperless.settings pytest tests/
#   run_cmd --dir=/app -e MY_VAR=hello -e OTHER=world make build
#   run_cmd --timeout=30 curl http://example.com

if [ "$1" = "--help" ] || [ "$1" = "-h" ]; then
    echo "Usage: run_cmd [options] <command> [args...]"
    echo ""
    echo "Run an arbitrary command with directory and environment context."
    echo ""
    echo "Options:"
    echo "  -C, --dir=DIR        Working directory (default: current dir)"
    echo "  -e, --env=KEY=val    Set environment variable (repeatable)"
    echo "  --timeout=SECONDS    Timeout in seconds"
    echo ""
    echo "Examples:"
    echo "  run_cmd --dir=/app python -m scapy.tools.UTscapy -t test.uts"
    echo "  run_cmd --dir=/app/src -e DJANGO_SETTINGS_MODULE=paperless.settings pytest tests/"
    echo "  run_cmd --dir=/app -e MY_VAR=hello make build"
    echo "  run_cmd --timeout=30 curl http://example.com"
    exit 0
fi

WORK_DIR=""
ENV_VARS=()
TIMEOUT=""

# Parse options
while [ $# -gt 0 ]; do
    case "$1" in
        -C)
            shift
            if [ $# -gt 0 ]; then
                WORK_DIR="$1"
                shift
            else
                echo "Error: -C requires a directory argument"
                exit 1
            fi
            ;;
        --dir=*)
            WORK_DIR="${1#*=}"
            shift
            ;;
        -e)
            shift
            if [ $# -gt 0 ]; then
                ENV_VARS+=("$1")
                shift
            else
                echo "Error: -e requires a KEY=val argument"
                exit 1
            fi
            ;;
        --env=*)
            ENV_VARS+=("${1#*=}")
            shift
            ;;
        --timeout=*)
            TIMEOUT="${1#*=}"
            shift
            ;;
        --timeout)
            shift
            if [ $# -gt 0 ]; then
                TIMEOUT="$1"
                shift
            else
                echo "Error: --timeout requires a seconds argument"
                exit 1
            fi
            ;;
        --)
            shift
            break
            ;;
        -*)
            echo "Unknown option: $1"
            echo "Usage: run_cmd [options] <command> [args...]"
            exit 1
            ;;
        *)
            break
            ;;
    esac
done

if [ $# -eq 0 ]; then
    echo "Error: No command specified"
    echo "Usage: run_cmd [options] <command> [args...]"
    exit 1
fi

# Build env prefix
ENV_PREFIX=""
for ev in "${ENV_VARS[@]}"; do
    ENV_PREFIX="$ENV_PREFIX $ev"
done
if [ -n "$ENV_PREFIX" ]; then
    ENV_PREFIX="env $ENV_PREFIX"
fi

# Build cd prefix
CD_PREFIX=""
if [ -n "$WORK_DIR" ]; then
    if [ ! -d "$WORK_DIR" ]; then
        echo "Error: Directory not found: $WORK_DIR"
        exit 1
    fi
    CD_PREFIX="cd '$WORK_DIR'"
fi

# Build the full command
FULL_CMD=""
if [ -n "$CD_PREFIX" ]; then
    FULL_CMD="$CD_PREFIX && "
fi
if [ -n "$ENV_PREFIX" ]; then
    FULL_CMD="${FULL_CMD}$ENV_PREFIX "
fi

# Quote the command and args properly
QUOTED_ARGS=()
for arg in "$@"; do
    # Use printf to properly escape
    QUOTED_ARGS+=("$(printf '%q' "$arg")")
done
FULL_CMD="${FULL_CMD}${QUOTED_ARGS[*]}"

# Add timeout if specified
if [ -n "$TIMEOUT" ]; then
    FULL_CMD="timeout $TIMEOUT bash -c '$FULL_CMD'"
fi

# Print what we're doing
if [ -n "$WORK_DIR" ]; then
    echo "=== Running in: $WORK_DIR ==="
fi
for ev in "${ENV_VARS[@]}"; do
    echo "  env: $ev"
done
echo ""

# Execute
if [ -n "$TIMEOUT" ]; then
    eval "$FULL_CMD"
    EXIT_CODE=$?
    if [ $EXIT_CODE -eq 124 ]; then
        echo ""
        echo "Command timed out after ${TIMEOUT}s"
    fi
else
    eval "$FULL_CMD"
    EXIT_CODE=$?
fi

exit $EXIT_CODE
