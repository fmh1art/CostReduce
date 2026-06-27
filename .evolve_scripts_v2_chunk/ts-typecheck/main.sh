#!/bin/bash
# ts-typecheck: Run TypeScript type-checking with tsc, trim output to essential errors.
# Usage:
#   ts-typecheck/main.sh [tsconfig] [options]
#   ts-typecheck/main.sh --file <single.ts> [options]
# Options:
#   -C, --dir <dir>    Directory to run in (default: .)
#   --noEmit           Do not emit outputs (default: true)
#   --skipLibCheck     Skip .d.ts checking (default: true)
#   --head <N>         Show first N lines (default: 30)
#   --file <file.ts>   Check a single .ts file inline
#   -h, --help         Show usage

set -euo pipefail

tsconfig=""
single_file=""
workdir="."
no_emit_flag="--noEmit"
skip_lib_flag="--skipLibCheck"
head_lines=30

while [ $# -gt 0 ]; do
  case "$1" in
    -C|--dir)
      [ $# -lt 2 ] && { echo "ERROR: --dir requires a directory" >&2; exit 1; }
      workdir="$2"
      shift 2
      ;;
    --file)
      [ $# -lt 2 ] && { echo "ERROR: --file requires a .ts path" >&2; exit 1; }
      single_file="$2"
      shift 2
      ;;
    --noEmit)
      shift
      ;;
    --skipLibCheck)
      shift
      ;;
    --head)
      [ $# -lt 2 ] && { echo "ERROR: --head requires a number" >&2; exit 1; }
      head_lines="$2"
      shift 2
      ;;
    -h|--help)
      echo "Usage: $0 [tsconfig] [options]"
      echo "  [tsconfig]              Path to tsconfig.json (optional, auto-detected)"
      echo "  -C, --dir <dir>         Working directory"
      echo "  --file <file.ts>        Check a single .ts file inline"
      echo "  --noEmit                Pass --noEmit (default: yes)"
      echo "  --skipLibCheck          Pass --skipLibCheck (default: yes)"
      echo "  --head <N>              Show first N lines (default: 30)"
      echo "  -h, --help              Show this help"
      exit 0
      ;;
    -*)
      echo "ERROR: Unknown option: $1" >&2
      exit 1
      ;;
    *)
      if [ -z "$tsconfig" ]; then
        tsconfig="$1"
      fi
      shift
      ;;
  esac
done

cd "$workdir"

# Build the tsc command
if [ -n "$single_file" ]; then
  if [ ! -f "$single_file" ]; then
    echo "ERROR: File not found: $single_file" >&2
    exit 1
  fi
  # Check the SPECIFIED file. Use a project tsconfig if available so the
  # file is type-checked against the project's settings (module resolution,
  # paths, libs); otherwise fall back to standalone lib/strict flags.
  if [ -z "$tsconfig" ]; then
    for cfg in tsconfig.json tsconfig.app.json global.tsconfig.json; do
      if [ -f "$cfg" ]; then
        tsconfig="$cfg"
        break
      fi
    done
  fi
  set +e
  if [ -n "$tsconfig" ]; then
    # -p reads project settings; the file is checked in the project context.
    output=$(timeout 30 npx tsc -p "$tsconfig" "$no_emit_flag" "$skip_lib_flag" "$single_file" 2>&1 | head -n "$head_lines")
  else
    output=$(timeout 30 npx tsc "$no_emit_flag" "$skip_lib_flag" --lib es2022,dom --strict "$single_file" 2>&1 | head -n "$head_lines")
  fi
  rc=$?
  set -e
  echo "$output"
  exit $rc
fi

# Full project type-check
if [ -z "$tsconfig" ]; then
  for cfg in tsconfig.json tsconfig.app.json global.tsconfig.json; do
    if [ -f "$cfg" ]; then
      tsconfig="$cfg"
      break
    fi
  done
fi

if [ -z "$tsconfig" ]; then
  cmd=(npx tsc "$no_emit_flag" "$skip_lib_flag")
else
  cmd=(npx tsc -p "$tsconfig" "$no_emit_flag" "$skip_lib_flag")
fi

set +e
output=$(timeout 60 "${cmd[@]}" 2>&1)
rc=$?
set -e

echo "$output" | head -n "$head_lines"
exit $rc
