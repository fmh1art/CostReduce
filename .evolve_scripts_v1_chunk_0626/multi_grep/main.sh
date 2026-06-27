#!/usr/bin/env bash
set -euo pipefail

# multi_grep - Search for multiple patterns with automatic noise exclusion
# Usage: main.sh <patterns> <target> [--include <glob>] [--no-exclude] [--context|-C <n>] [--max|-m <n>] [--file-only|-l] [--case-insensitive|-i] [--no-tests] [--no-dts]
#
# Searches for pipe-separated patterns in the target with automatic exclusion
# of common noise directories (node_modules, __pycache__, .git, vendor, .venv,
# dist, build, .pnpm). Use --no-exclude to search inside these directories
# (e.g., searching for type definitions in node_modules).
#
# Examples:
#   main.sh "parseJsonSchema|jsonSchemaToType" ark/
#   main.sh "TODO|FIXME|HACK" . --include "*.py,*.ts"
#   main.sh "enumerated" ark/ --no-tests -C 2
#   main.sh "interface FindOptions" node_modules/@mikro-orm/ --include "*.d.ts" --no-exclude --max 10

PATTERNS="${1:-}"
TARGET="${2:-}"
INCLUDE=""
EXCLUDE_DIRS="node_modules:__pycache__:.git:vendor:.venv:dist:build:.pnpm"
CONTEXT=0
MAX_MATCHES=60
FILE_ONLY=false
CASE_INSENSITIVE=false
NO_TESTS=false
NO_DTS=false
NO_EXCLUDE=false

shift 2 2>/dev/null || true

while [ $# -gt 0 ]; do
    case "$1" in
        --include)
            shift
            INCLUDE="$1"
            shift
            ;;
        --no-exclude)
            NO_EXCLUDE=true
            shift
            ;;
        --context|-C)
            shift
            CONTEXT="$1"
            shift
            ;;
        --max|-m)
            shift
            MAX_MATCHES="$1"
            shift
            ;;
        --file-only|-l)
            FILE_ONLY=true
            shift
            ;;
        --case-insensitive|-i)
            CASE_INSENSITIVE=true
            shift
            ;;
        --no-tests)
            NO_TESTS=true
            shift
            ;;
        --no-dts)
            NO_DTS=true
            shift
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

if [ -z "$PATTERNS" ] || [ -z "$TARGET" ]; then
    echo "Usage: main.sh <patterns> <target> [options]"
    echo "  patterns: pipe-separated patterns (e.g., 'foo|bar|baz')"
    echo "  target:   file or directory to search"
    echo "Options:"
    echo "  --include <glob>        Comma-separated file globs (e.g., '*.ts,*.js')"
    echo "  --no-exclude            Disable automatic exclusion of noise dirs"
    echo "                          (use to search inside node_modules, .git, etc.)"
    echo "  --context|-C <n>        Lines of context before/after each match"
    echo "  --max|-m <n>            Maximum matches to show (default: 60)"
    echo "  --file-only|-l          Only show filenames"
    echo "  --case-insensitive|-i   Case-insensitive search"
    echo "  --no-tests              Exclude test files (*.test.*, __tests__/, *.spec.*)"
    echo "  --no-dts                Exclude .d.ts files"
    echo ""
    echo "Examples:"
    echo "  main.sh \"parseJsonSchema|jsonSchemaToType\" ark/ --include \"*.ts\""
    echo "  main.sh \"interface FindOptions\" node_modules/@mikro-orm/ --include \"*.d.ts\" --no-exclude --max 10"
    exit 1
fi

# Build grep command
GREP_CMD=(grep -rn)

if [ "$CASE_INSENSITIVE" = true ]; then
    GREP_CMD+=(-i)
fi

if [ "$FILE_ONLY" = true ]; then
    GREP_CMD+=(-l)
fi

if [ "$CONTEXT" -gt 0 ]; then
    GREP_CMD+=(-C "$CONTEXT")
fi

# Build include args
if [ -n "$INCLUDE" ]; then
    IFS=',' read -ra GLOBS <<< "$INCLUDE"
    for glob in "${GLOBS[@]}"; do
        GREP_CMD+=(--include="$glob")
    done
fi

# Build exclude args (unless --no-exclude)
EXCLUDE_CMD=()
if [ "$NO_EXCLUDE" = false ]; then
    IFS=':' read -ra DIRS <<< "$EXCLUDE_DIRS"
    for dir in "${DIRS[@]}"; do
        EXCLUDE_CMD+=(--exclude-dir="$dir")
    done
fi

if [ "$NO_TESTS" = true ]; then
    EXCLUDE_CMD+=(--exclude="*.test.*" --exclude="*.spec.*" --exclude-dir="__tests__")
fi

if [ "$NO_DTS" = true ]; then
    EXCLUDE_CMD+=(--exclude="*.d.ts")
fi

# Run the search
if [ ${#EXCLUDE_CMD[@]} -gt 0 ]; then
    MATCHES=$("${GREP_CMD[@]}" "${EXCLUDE_CMD[@]}" -E "$PATTERNS" "$TARGET" 2>/dev/null || true)
else
    MATCHES=$("${GREP_CMD[@]}" -E "$PATTERNS" "$TARGET" 2>/dev/null || true)
fi

if [ -z "$MATCHES" ]; then
    echo "No matches found."
    exit 0
fi

MATCH_COUNT=$(echo "$MATCHES" | wc -l)

if [ "$MATCH_COUNT" -gt "$MAX_MATCHES" ]; then
    echo "$MATCHES" | head -n "$MAX_MATCHES"
    echo ""
    echo "... and $((MATCH_COUNT - MAX_MATCHES)) more matches (total: $MATCH_COUNT)"
else
    echo "$MATCHES"
    echo ""
    echo "Total matches: $MATCH_COUNT"
fi
