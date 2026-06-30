#!/usr/bin/env bash
# batch_python - Run inline Python code or script files without the python3 -c wrapper.

set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage: batch_python <code> [args...]
       batch_python -f <script.py> [args...]
       echo "code" | batch_python

Run inline Python code or a Python script file. Without -f, treats the first
argument as inline code. With -f, runs the specified script file.
If piped via stdin, reads code from stdin.

Examples:
  /app/.preinstalled_scripts/batch_python/main.sh "import sys; print(sys.version)"
  /app/.preinstalled_scripts/batch_python/main.sh -f myscript.py arg1 arg2
  echo "print('hello')" | /app/.preinstalled_scripts/batch_python/main.sh
EOF
  exit 1
}

SCRIPT_FILE=""
CODE=""
ARGS=()

# Check if stdin has data
STDIN_CODE=""
if [[ ! -t 0 ]]; then
  STDIN_CODE=$(cat)
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    -f)
      if [[ $# -lt 2 ]]; then
        echo "Error: -f requires a script file path" >&2
        exit 1
      fi
      SCRIPT_FILE="$2"
      shift 2
      ;;
    --help|-h)
      usage
      ;;
    --)
      shift
      # Treat remaining args as positional (options disabled)
      while [[ $# -gt 0 ]]; do
        if [[ -z "$CODE" ]]; then
          CODE="$1"
        else
          ARGS+=("$1")
        fi
        shift
      done
      ;;
    -*)
      echo "Unknown option: $1" >&2
      usage
      ;;
    *)
      if [[ -z "$CODE" ]]; then
        CODE="$1"
      else
        ARGS+=("$1")
      fi
      shift
      ;;
  esac
done

if [[ -n "$SCRIPT_FILE" ]]; then
  # Run a script file
  if [[ ! -f "$SCRIPT_FILE" ]]; then
    echo "Error: script file not found: $SCRIPT_FILE" >&2
    exit 1
  fi
  python3 "$SCRIPT_FILE" "${ARGS[@]}"
elif [[ -n "$STDIN_CODE" ]]; then
  # Run code from stdin
  python3 -c "$STDIN_CODE" "${ARGS[@]}"
elif [[ -n "$CODE" ]]; then
  # Run inline code
  python3 -c "$CODE" "${ARGS[@]}"
else
  usage
fi
