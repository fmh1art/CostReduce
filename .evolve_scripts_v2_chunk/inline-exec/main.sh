#!/bin/bash
# inline-exec: Write inline code to a temp file and execute it with the appropriate runner,
#              or run a Go package directly with --pkg.
# Usage:
#   inline-exec/main.sh <runner> '<inline_code>'
#   inline-exec/main.sh <runner> --file <path>
#   inline-exec/main.sh go --pkg <package> [--go-flags <flags>] [-- <args>...]
#   inline-exec/main.sh -C <dir> <runner> '<inline_code>'
# Runners: go, node, python3, cargo, tsx, or custom binary
# Options:
#   -f, --file <path>   Read code from a file
#   -e, --ext <ext>     File extension (e.g. .go, .js, .py); auto-detected from runner
#   -C, --dir <dir>     Working directory (default: .)
#   --timeout <secs>    Timeout in seconds (default: 30)
#   --keep              Keep temp file after execution
#   --pkg <path>        Run a Go package directly (e.g. ./cmd/task/) instead of inline code
#   --go-flags <flags>  Go build flags like "-tags debug" (only with --pkg)
#   --                  Separates runner/program args from program arguments
#   -h, --help          Show usage

set -euo pipefail

workdir="."
timeout_secs=30
keep=false
runner=""
code=""
file_input=""
ext=""
pkg=""
go_flags=""
program_args=()
parsing_program_args=false

# Parse options
while [ $# -gt 0 ]; do
  if [ "$parsing_program_args" = true ]; then
    program_args+=("$1")
    shift
    continue
  fi
  case "$1" in
    -C|--dir)
      [ $# -lt 2 ] && { echo "ERROR: --dir requires a directory" >&2; exit 1; }
      workdir="$2"
      shift 2
      ;;
    -f|--file)
      [ $# -lt 2 ] && { echo "ERROR: --file requires a path" >&2; exit 1; }
      file_input="$2"
      shift 2
      ;;
    -e|--ext)
      [ $# -lt 2 ] && { echo "ERROR: --ext requires an extension like .go" >&2; exit 1; }
      ext="$2"
      shift 2
      ;;
    --timeout)
      [ $# -lt 2 ] && { echo "ERROR: --timeout requires a number" >&2; exit 1; }
      timeout_secs="$2"
      shift 2
      ;;
    --keep)
      keep=true
      shift
      ;;
    --pkg|--package)
      [ $# -lt 2 ] && { echo "ERROR: --pkg requires a package path" >&2; exit 1; }
      pkg="$2"
      shift 2
      ;;
    --go-flags)
      [ $# -lt 2 ] && { echo "ERROR: --go-flags requires a value" >&2; exit 1; }
      go_flags="$2"
      shift 2
      ;;
    --)
      parsing_program_args=true
      shift
      ;;
    -h|--help)
      echo "Usage: $0 [options] <runner> '<inline_code>'"
      echo "       $0 [options] <runner> --file <path>"
      echo "       $0 [options] go --pkg <package> [--go-flags <flags>] [-- <args>...]"
      echo ""
      echo "Runners: go, node, python3, cargo, tsx, or custom binary"
      echo ""
      echo "Options:"
      echo "  -C, --dir <dir>     Working directory (default: .)"
      echo "  -f, --file <path>   Read code from a file"
      echo "  -e, --ext <ext>     File extension, auto-detected from runner"
      echo "  --timeout <secs>    Timeout in seconds (default: 30)"
      echo "  --keep              Keep temp file after execution"
      echo "  --pkg <path>        Run a Go package directly (e.g. ./cmd/task/)"
      echo "  --go-flags <flags>  Go build flags like \"-tags debug\" (with --pkg)"
      echo "  --                  Separates runner options from program arguments"
      echo "  -h, --help          Show this help"
      exit 0
      ;;
    -*)
      echo "ERROR: Unknown option: $1" >&2
      exit 1
      ;;
    *)
      if [ -z "$runner" ]; then
        runner="$1"
      elif [ -z "$code" ] && [ -z "$file_input" ] && [ -z "$pkg" ]; then
        code="$1"
      fi
      shift
      ;;
  esac
done

if [ -z "$runner" ]; then
  echo "ERROR: No runner specified (e.g. go, node, python3, cargo, tsx)" >&2
  exit 1
fi

cd "$workdir"

# ---- Package mode: run Go package directly ----
if [ -n "$pkg" ]; then
  if [ "$runner" != "go" ]; then
    echo "ERROR: --pkg is only supported with the 'go' runner" >&2
    exit 1
  fi

  # Build go run command
  go_cmd=(go run)
  if [ -n "$go_flags" ]; then
    # Split go_flags by spaces
    IFS=' ' read -r -a go_flag_arr <<< "$go_flags"
    go_cmd+=("${go_flag_arr[@]}")
  fi
  go_cmd+=("$pkg")
  # Append program arguments
  if [ ${#program_args[@]} -gt 0 ]; then
    go_cmd+=("${program_args[@]}")
  fi

  set +e
  output=$(timeout "$timeout_secs" "${go_cmd[@]}" 2>&1)
  rc=$?
  set -e

  if [ $rc -eq 124 ]; then
    echo "TIMEOUT after ${timeout_secs}s (command: ${go_cmd[*]})" >&2
  fi

  echo "$output"
  exit $rc
fi

# ---- Inline code mode (original behavior) ----
if [ -z "$file_input" ] && [ -z "$code" ]; then
  echo "ERROR: No code provided - pass inline code, use --file <path>, or use --pkg" >&2
  exit 1
fi

# Auto-detect extension from runner if not specified
if [ -z "$ext" ]; then
  case "$runner" in
    go)       ext=".go" ;;
    node)     ext=".js" ;;
    python3|python) ext=".py" ;;
    cargo)    ext=".rs" ;;
    tsx)      ext=".ts" ;;
    swift)    ext=".swift" ;;
    ruby)     ext=".rb" ;;
    rustc)    ext=".rs" ;;
    deno)     ext=".ts" ;;
    cc|gcc)   ext=".c" ;;
    g++)      ext=".cpp" ;;
    *)        ext=".tmp" ;;
  esac
fi

# Create temp file
TMPFILE=$(mktemp "/tmp/inline-exec-XXXXXX$ext")

if [ -n "$file_input" ]; then
  if [ ! -f "$file_input" ]; then
    echo "ERROR: File not found: $file_input" >&2
    rm -f "$TMPFILE"
    exit 1
  fi
  cp "$file_input" "$TMPFILE"
else
  printf '%s\n' "$code" > "$TMPFILE"
fi

# Run the temp file with the appropriate runner
set +e
case "$runner" in
  go)
    output=$(timeout "$timeout_secs" go run "$TMPFILE" 2>&1)
    rc=$?
    ;;
  node)
    output=$(timeout "$timeout_secs" node "$TMPFILE" 2>&1)
    rc=$?
    ;;
  python3|python)
    output=$(timeout "$timeout_secs" "$runner" "$TMPFILE" 2>&1)
    rc=$?
    ;;
  cargo)
    # Use rustc directly since cargo needs a full project structure
    OUTFILE="/tmp/inline-exec-binary"
    compile_out=$(timeout "$timeout_secs" rustc "$TMPFILE" -o "$OUTFILE" 2>&1)
    rc=$?
    if [ $rc -eq 0 ]; then
      output=$("$OUTFILE" 2>&1)
      rc=$?
      rm -f "$OUTFILE"
    else
      output="$compile_out"
    fi
    ;;
  tsx)
    output=$(timeout "$timeout_secs" npx tsx "$TMPFILE" 2>&1)
    rc=$?
    ;;
  *)
    # Custom runner - pass temp file as argument
    output=$(timeout "$timeout_secs" "$runner" "$TMPFILE" 2>&1)
    rc=$?
    ;;
esac
set -e

if [ $rc -eq 124 ]; then
  echo "TIMEOUT after ${timeout_secs}s" >&2
fi

if [ "$keep" = false ]; then
  rm -f "$TMPFILE"
else
  echo "Temp file kept at: $TMPFILE" >&2
fi

echo "$output"
exit $rc
