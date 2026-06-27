#!/usr/bin/env bash
# grep-search: Search for patterns in files or directories with line numbers, context, and filters.
# Supports multiple patterns (union of matches).
# Usage: grep-search [options] <pattern1> [pattern2...] <file_or_dir>
#        grep-search [options] <pattern> <file_or_dir>

set -euo pipefail

show_help() {
    cat << 'HELP_EOF'
Usage: grep-search [options] <pattern1> [pattern2...] <file_or_dir>

Options:
  --cd=DIR, -C DIR      Change to directory before searching (replaces cd + grep pattern)
  -r, --recursive        Recursively search directories (uses find + xargs grep)
  -i, --ignore-case      Case-insensitive search
  --include=GLOB         Only search files matching glob (e.g., *.go, *.ts)
  --exclude=GLOB         Exclude files/dirs matching glob (e.g. */node_modules/*)
  --max-count=N          Stop after N matching lines per file
  -C, --context=N        Show N lines of context around matches
  --files-only, -l       List only filenames with matches
  --names-only           Alias for --files-only
  -v, --exclude-pattern=PATTERN  Exclude lines matching this pattern (repeatable)
  -d, --max-depth=N      Max directory depth for recursive search (default: unlimited)
  --help, -h             Show this help

Examples:
  grep-search --cd=/app -r -i --include=*.go FindOptions .
  grep-search --cd=libs/core -r pattern .
  grep-search -r --exclude=*/node_modules/* pattern .
  grep-search -v debugger file.go
  grep-search --files-only -r pattern .
  grep-search -r --include=*.ts -d 3 "export const" src/
  grep-search -r "class1" "class2" .
HELP_EOF
    exit 0
}

CD_DIR=""
RECURSIVE=""
IGNORE_CASE=""
INCLUDE=""
EXCLUDE=""
MAX_COUNT=""
CONTEXT=""
FILES_ONLY=""
EXCLUDE_PATTERNS=()
MAX_DEPTH=""
ARGS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --help|-h) show_help ;;
        --cd=*) CD_DIR="${1#*=}" ;;
        -C|--cd)
            shift
            [[ $# -lt 1 ]] && { echo "Error: --cd needs a directory" >&2; exit 1; }
            CD_DIR="$1"
            ;;
        -r|--recursive) RECURSIVE="1" ;;
        -i|--ignore-case) IGNORE_CASE="-i" ;;
        --include=*) INCLUDE="${1#*=}" ;;
        --exclude=*) EXCLUDE="${1#*=}" ;;
        --max-count=*) MAX_COUNT="${1#*=}" ;;
        -C=*|--context=*) CONTEXT="${1#*=}" ;;
        -C|--context)
            shift
            CONTEXT="$1"
            ;;
        --files-only|-l) FILES_ONLY="1" ;;
        --names-only) FILES_ONLY="1" ;;
        -v=*|--exclude-pattern=*) EXCLUDE_PATTERNS+=("${1#*=}") ;;
        -v|--exclude-pattern)
            shift
            EXCLUDE_PATTERNS+=("$1")
            ;;
        -d=*|--max-depth=*) MAX_DEPTH="${1#*=}" ;;
        -d|--max-depth)
            shift
            MAX_DEPTH="$1"
            ;;
        *) ARGS+=("$1") ;;
    esac
    shift
done

[[ ${#ARGS[@]} -lt 2 ]] && { echo "Error: Need at least <pattern> and <file_or_dir>" >&2; show_help; }

# Last arg is the target, all before are patterns
TARGET="${ARGS[${#ARGS[@]}-1]}"
PATTERNS=("${ARGS[@]:0:${#ARGS[@]}-1}")

# Change directory if --cd was specified
if [[ -n "$CD_DIR" ]]; then
    cd "$CD_DIR" || { echo "Error: Cannot cd to $CD_DIR" >&2; exit 1; }
fi

if [[ ! -e "$TARGET" ]]; then
    echo "Error: Target not found: $TARGET" >&2
    exit 1
fi

# Build grep command base
build_grep_cmd() {
    local pattern_idx=$1
    local pattern="${PATTERNS[$pattern_idx]}"
    local grep_cmd=(grep -n)
    [[ -n "$IGNORE_CASE" ]] && grep_cmd+=("$IGNORE_CASE")
    [[ -n "$MAX_COUNT" ]] && grep_cmd+=(-m "$MAX_COUNT")
    [[ -n "$CONTEXT" ]] && grep_cmd+=(-C "$CONTEXT")
    [[ -n "$FILES_ONLY" ]] && grep_cmd+=(-l)
    # Add exclude patterns
    for ep in "${EXCLUDE_PATTERNS[@]}"; do
        grep_cmd+=(-v -e "$ep")
    done
    grep_cmd+=(-e "$pattern")
    echo "${grep_cmd[@]}"
}

# If recursive mode with directory target
if [[ -n "$RECURSIVE" ]] && [[ -d "$TARGET" ]]; then
    # Build find command
    FIND_CMD=(find "$TARGET" -type f)
    [[ -n "$MAX_DEPTH" ]] && FIND_CMD+=(-maxdepth "$MAX_DEPTH")
    if [[ -n "$INCLUDE" ]]; then
        FIND_CMD+=(-name "$INCLUDE")
    fi
    if [[ -n "$EXCLUDE" ]]; then
        FIND_CMD+=(-not -path "$EXCLUDE")
    fi
    if [[ -z "$EXCLUDE" ]]; then
        FIND_CMD+=(-not -path "*/node_modules/*" -not -path "*/.git/*")
    fi

    FILES=$("${FIND_CMD[@]}" 2>/dev/null || true)
    [[ -z "$FILES" ]] && exit 0

    # Run grep for each pattern, sort -u for union
    if [[ ${#PATTERNS[@]} -eq 1 ]]; then
        # Single pattern - just run once
        GREP_CMD=($(build_grep_cmd 0))
        echo "$FILES" | xargs -r "${GREP_CMD[@]}" 2>/dev/null || true
    else
        # Multiple patterns - run each and union the results
        ALL_RESULTS=""
        for i in "${!PATTERNS[@]}"; do
            GREP_CMD=($(build_grep_cmd "$i"))
            RESULT=$(echo "$FILES" | xargs -r "${GREP_CMD[@]}" 2>/dev/null || true)
            if [[ -n "$RESULT" ]]; then
                ALL_RESULTS+="$RESULT"$'\n'
            fi
        done
        if [[ -n "$ALL_RESULTS" ]]; then
            # Deduplicate and sort
            echo "$ALL_RESULTS" | sort -u -t: -k1,2
        fi
    fi
else
    # Non-recursive: use direct grep
    if [[ ${#PATTERNS[@]} -eq 1 ]]; then
        GREP_CMD=(grep -n)
        [[ -n "$IGNORE_CASE" ]] && GREP_CMD+=("$IGNORE_CASE")
        [[ -n "$RECURSIVE" ]] && GREP_CMD+=(-r)
        [[ -n "$INCLUDE" ]] && GREP_CMD+=(--include="$INCLUDE")
        [[ -n "$EXCLUDE" ]] && GREP_CMD+=(--exclude="$EXCLUDE")
        [[ -n "$MAX_COUNT" ]] && GREP_CMD+=(-m "$MAX_COUNT")
        [[ -n "$CONTEXT" ]] && GREP_CMD+=(-C "$CONTEXT")
        [[ -n "$FILES_ONLY" ]] && GREP_CMD+=(-l)
        for ep in "${EXCLUDE_PATTERNS[@]}"; do
            GREP_CMD+=(-v -e "$ep")
        done
        GREP_CMD+=(-e "${PATTERNS[0]}" "$TARGET")
        "${GREP_CMD[@]}" 2>&1 || true
    else
        # Multiple patterns - run each and union
        ALL_RESULTS=""
        for pattern in "${PATTERNS[@]}"; do
            GREP_CMD=(grep -n)
            [[ -n "$IGNORE_CASE" ]] && GREP_CMD+=("$IGNORE_CASE")
            [[ -n "$RECURSIVE" ]] && GREP_CMD+=(-r)
            [[ -n "$INCLUDE" ]] && GREP_CMD+=(--include="$INCLUDE")
            [[ -n "$EXCLUDE" ]] && GREP_CMD+=(--exclude="$EXCLUDE")
            [[ -n "$MAX_COUNT" ]] && GREP_CMD+=(-m "$MAX_COUNT")
            [[ -n "$CONTEXT" ]] && GREP_CMD+=(-C "$CONTEXT")
            [[ -n "$FILES_ONLY" ]] && GREP_CMD+=(-l)
            for ep in "${EXCLUDE_PATTERNS[@]}"; do
                GREP_CMD+=(-v -e "$ep")
            done
            GREP_CMD+=(-e "$pattern" "$TARGET")
            RESULT=$("${GREP_CMD[@]}" 2>&1 || true)
            if [[ -n "$RESULT" ]]; then
                ALL_RESULTS+="$RESULT"$'\n'
            fi
        done
        if [[ -n "$ALL_RESULTS" ]]; then
            echo "$ALL_RESULTS" | sort -u -t: -k1,2
        fi
    fi
fi
