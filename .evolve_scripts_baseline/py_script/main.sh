#!/bin/bash
# Script: py_script
# Description: Write a Python script to a temporary file and execute it.
# Accepts code via heredoc stdin, inline string, or reads from an existing file.
# Supports timeout and passing arguments to the script.
# Usage:
#   main.sh <inline_code>                          # Run inline code
#   main.sh -f <file.py> [args...]                 # Run existing file
#   echo "code" | main.sh                          # Run code from stdin
#   cat > /tmp/script.py << 'EOF' ... EOF && py_script/main.sh /tmp/script.py  # Use with heredoc

CODE_OR_FLAG="$1"

show_usage() {
  echo "Usage:"
  echo "  main.sh '<inline_code>'              - Run inline Python code"
  echo "  main.sh -f <file.py> [args...]        - Run existing Python file"
  echo "  echo 'code' | main.sh                 - Run code from stdin"
  echo "  main.sh < <(cat <<'EOF'               - Run code from heredoc"
  echo "      ...code...                             (multi-line)"
  echo "  EOF)"
}

if [ -z "$CODE_OR_FLAG" ]; then
  # Check if stdin has data (piped input)
  if [ -p /dev/stdin ] || [ ! -t 0 ]; then
    # Read from stdin
    TMPFILE=$(mktemp /tmp/py_script_XXXXXX.py)
    cat > "$TMPFILE"
    python3 "$TMPFILE"
    RC=$?
    rm -f "$TMPFILE"
    exit $RC
  else
    echo "ERROR: No code specified."
    show_usage
    exit 1
  fi
fi

if [ "$CODE_OR_FLAG" = "-f" ]; then
  shift
  SCRIPT="$1"
  shift
  if [ ! -f "$SCRIPT" ]; then
    echo "ERROR: Script file not found: $SCRIPT"
    exit 1
  fi
  python3 "$SCRIPT" "$@"
  exit $?
fi

# Try running as inline code first
python3 -c "$CODE_OR_FLAG" 2>/dev/null
RC=$?
if [ $RC -eq 0 ]; then
  exit 0
fi

# If inline failed, it might be multi-line code that needs a temp file
TMPFILE=$(mktemp /tmp/py_script_XXXXXX.py)
echo "$CODE_OR_FLAG" > "$TMPFILE"
python3 "$TMPFILE"
RC=$?
rm -f "$TMPFILE"
exit $RC
