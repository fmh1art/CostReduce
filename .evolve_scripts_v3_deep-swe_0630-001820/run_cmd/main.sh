#!/usr/bin/env bash
set -euo pipefail

# run_cmd - Run arbitrary commands in a specified directory with optional environment variables.
# Usage: run_cmd [options] <command> [args...]
# Options:
#   -C, --dir=DIR      Working directory
#   -e, --env=KEY=val  Set environment variable (repeatable)
#   --timeout=SECONDS  Command timeout
#   --write=FILEPATH   Write stdin content to FILEPATH before running command
#                      (creates parent directories automatically)

DIR=""
ENVS=()
TIMEOUT=""
WRITE_FILE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        -C|--dir)
            DIR="$2"
            shift 2
            ;;
        --dir=*)
            DIR="${1#*=}"
            shift
            ;;
        -e|--env)
            ENVS+=("$2")
            shift 2
            ;;
        --env=*)
            ENVS+=("${1#*=}")
            shift
            ;;
        --timeout)
            TIMEOUT="$2"
            shift 2
            ;;
        --timeout=*)
            TIMEOUT="${1#*=}"
            shift
            ;;
        --write)
            WRITE_FILE="$2"
            shift 2
            ;;
        --write=*)
            WRITE_FILE="${1#*=}"
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

if [[ $# -eq 0 ]]; then
    echo "Usage: run_cmd [options] <command> [args...]" >&2
    exit 1
fi

# Change directory if specified
if [[ -n "$DIR" ]]; then
    cd "$DIR" 2>/dev/null || { echo "Error: Cannot change to directory $DIR" >&2; exit 1; }
fi

# Export environment variables
for env_var in "${ENVS[@]}"; do
    export "$env_var" 2>/dev/null || true
done

# Write stdin to file if --write is specified
if [[ -n "$WRITE_FILE" ]]; then
    mkdir -p "$(dirname "$WRITE_FILE")"
    cat > "$WRITE_FILE"
fi

# Run command with optional timeout
if [[ -n "$TIMEOUT" ]]; then
    timeout "$TIMEOUT" "$@" 2>&1 || true
else
    "$@" 2>&1 || true
fi
