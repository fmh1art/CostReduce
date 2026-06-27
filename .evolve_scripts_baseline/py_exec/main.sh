#!/bin/bash
# Script: py_exec
# Description: Run Python code from inline string or file, with proper sys.path setup
# Usage: main.sh <code_string>
#        main.sh -f <script.py> [args...]

CODE="$1"

if [ -z "$CODE" ]; then
  echo "ERROR: No code specified."
  echo "Usage: main.sh <code_string>"
  echo "       main.sh -f <script.py> [args...]"
  exit 1
fi

# Run inline code or file
if [ "$CODE" = "-f" ]; then
  shift
  SCRIPT="$1"
  shift
  if [ ! -f "$SCRIPT" ]; then
    echo "ERROR: Script file not found: $SCRIPT"
    exit 1
  fi
  python3 "$SCRIPT" "$@"
else
  python3 -c "$CODE"
fi
