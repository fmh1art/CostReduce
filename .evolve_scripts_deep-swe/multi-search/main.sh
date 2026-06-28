#!/usr/bin/env bash
# multi-search: Search for multiple patterns in files or directories in one pass.
# Groups results by pattern with per-pattern head limits.
# Usage: multi-search [options] pattern1[:N] [pattern2[:N]...] <file_or_dir>

set -euo pipefail

show_help() {
    cat << 'HELP_EOF'
Usage: multi-search [options] <pattern1[:N]> [<pattern2[:N]>...] <file_or_dir>

Search for multiple patterns in one pass, showing results grouped by pattern
with per-pattern head limits.

Options:
  --cd=DIR, -C DIR      Change to directory before searching
  -i, --ignore-case      Case-insensitive search
  -l, --files-with-matches  List only filenames (union of all patterns)
  -v, --exclude-pattern=PATTERN  Exclude lines matching pattern (repeatable)
  -C, --context=N        Show N lines of context around matches
  --include=GLOB         Only search files matching glob
  --exclude=GLOB         Exclude files/dirs matching glob
  -d, --max-depth=N      Max directory depth
  --max-count=N         Stop after N matching lines per file (passes -m to grep)
  --per-pattern-head=N  Default per-pattern head limit (override per pattern via pattern:N)
  --help, -h             Show this help

Pattern syntax:
  pattern         - Search for pattern (no limit)
  pattern:N       - Show at most N matching lines for this pattern

Examples:
  multi-search -i 'closed.Load:10' 'closed.*=.*atomic:5' file.go
  multi-search --cd=/app -r --include=*.go 'func.*Options:20' 'type.*Config:10' .
  multi-search -l -r 'error' 'warning' 'panic' .
HELP_EOF
    exit 0
}

CD_DIR=""
IGNORE_CASE=""
FILES_ONLY=""
EXCLUDE_PATTERNS=()
CONTEXT=""
INCLUDE=""
EXCLUDE=""
MAX_DEPTH=""
HEAD=""
PER_PATTERN_HEAD=""
MAX_COUNT=""
RECURSIVE=""
ARGS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --help|-h) show_help ;;
        --cd=*) CD_DIR="${1#*=}" ;;
        -C|--cd)
            shift; [[ $# -lt 1 ]] && { echo "Error: --cd needs a directory" >&2; exit 1; }
            CD_DIR="$1" ;;
        -i|--ignore-case) IGNORE_CASE="-i" ;;
        -l|--files-with-matches|--names-only) FILES_ONLY="1" ;;
        -v=*|--exclude-pattern=*) EXCLUDE_PATTERNS+=("${1#*=}") ;;
        -v|--exclude-pattern)
            shift; EXCLUDE_PATTERNS+=("$1") ;;
        -C=*|--context=*) CONTEXT="${1#*=}" ;;
        -C|--context)
            shift; CONTEXT="$1" ;;
        --include=*) INCLUDE="${1#*=}" ;;
        --exclude=*) EXCLUDE="${1#*=}" ;;
        -d=*|--max-depth=*) MAX_DEPTH="${1#*=}" ;;
        -d|--max-depth)
            shift; MAX_DEPTH="$1" ;;
        --head=*) HEAD="${1#*=}" ;;
        --per-pattern-head=*) PER_PATTERN_HEAD="${1#*=}" ;;
        --max-count=*) MAX_COUNT="${1#*=}" ;;
        -r|--recursive) RECURSIVE="1" ;;
        *) ARGS+=("$1") ;;
    esac
    shift
done

[[ ${#ARGS[@]} -lt 2 ]] && { echo "Error: Need at least <pattern> and <file_or_dir>" >&2; show_help; }

# Last arg is the target, all before are patterns
TARGET="${ARGS[${#ARGS[@]}-1]}"
PATTERN_SPECS=("${ARGS[@]:0:${#ARGS[@]}-1}")

# Change directory
if [[ -n "$CD_DIR" ]]; then
    cd "$CD_DIR" || { echo "Error: Cannot cd to $CD_DIR" >&2; exit 1; }
fi

[[ ! -e "$TARGET" ]] && { echo "Error: Target not found: $TARGET" >&2; exit 1; }

# Build grep base flags
build_grep_flags() {
    local flags=(-n)
    [[ -n "$IGNORE_CASE" ]] && flags+=("$IGNORE_CASE")
    [[ -n "$CONTEXT" ]] && flags+=(-C "$CONTEXT")
    [[ -n "$MAX_COUNT" ]] && flags+=(-m "$MAX_COUNT")
    for ep in "${EXCLUDE_PATTERNS[@]}"; do
        flags+=(-v -e "$ep")
    done
    echo "${flags[@]}"
}

# Parse pattern specs: pattern:N or just pattern
parse_pattern() {
    local spec="$1"
    if [[ "$spec" == *:* ]]; then
        echo "${spec%:*}"
    else
        echo "$spec"
    fi
}

parse_limit() {
    local spec="$1"
    if [[ "$spec" == *:* ]]; then
        local limit="${spec#*:}"
        if [[ "$limit" =~ ^[0-9]+$ ]]; then
            echo "$limit"
        else
            echo "$PER_PATTERN_HEAD"
        fi
    else
        echo "$PER_PATTERN_HEAD"
    fi
}

# Run grep for a single pattern with limit
run_grep_for_pattern() {
    local pattern="$1"
    local limit="$2"
    local is_last="$3"  # "1" if last pattern
    
    local grep_flags=($(build_grep_flags))
    
    if [[ -n "$FILES_ONLY" ]]; then
        grep_flags+=(-l)
    fi
    
    local result
    if [[ -d "$TARGET" ]] || [[ -n "$RECURSIVE" ]]; then
        # Directory target - use find
        local FIND_CMD=(find "$TARGET" -type f)
        [[ -n "$MAX_DEPTH" ]] && FIND_CMD+=(-maxdepth "$MAX_DEPTH")
        if [[ -n "$INCLUDE" ]]; then
            FIND_CMD+=(-name "$INCLUDE")
        fi
        if [[ -n "$EXCLUDE" ]]; then
            FIND_CMD+=(-not -path "$EXCLUDE")
        else
            FIND_CMD+=(-not -path "*/node_modules/*" -not -path "*/.git/*")
        fi
        
        local files
        files=$("${FIND_CMD[@]}" 2>/dev/null || true)
        [[ -z "$files" ]] && return 0
        
        if [[ -n "$limit" ]]; then
            result=$(echo "$files" | xargs -r grep "${grep_flags[@]}" -e "$pattern" 2>/dev/null | head -n "$limit" || true)
        else
            result=$(echo "$files" | xargs -r grep "${grep_flags[@]}" -e "$pattern" 2>/dev/null || true)
        fi
    else
        # File target - direct grep
        if [[ -n "$limit" ]]; then
            if [[ -n "$FILES_ONLY" ]]; then
                result=$(grep "${grep_flags[@]}" -e "$pattern" "$TARGET" 2>/dev/null | head -n "$limit" || true)
            else
                result=$(grep "${grep_flags[@]}" -e "$pattern" "$TARGET" 2>/dev/null | head -n "$limit" || true)
            fi
        else
            result=$(grep "${grep_flags[@]}" -e "$pattern" "$TARGET" 2>/dev/null || true)
        fi
    fi
    
    if [[ -n "$result" ]]; then
        if [[ -z "$FILES_ONLY" ]]; then
            echo "=== Pattern: $pattern ==="
        fi
        echo "$result"
        if [[ -z "$is_last" ]] || [[ "$is_last" != "1" ]]; then
            if [[ -z "$FILES_ONLY" ]]; then
                echo ""
            fi
        fi
    fi
}

TOTAL_COUNT="${#PATTERN_SPECS[@]}"
ALL_OUTPUT=""
SEPARATOR=""

for i in "${!PATTERN_SPECS[@]}"; do
    spec="${PATTERN_SPECS[$i]}"
    pattern=$(parse_pattern "$spec")
    limit=$(parse_limit "$spec")
    is_last=0
    [[ $i -eq $((TOTAL_COUNT - 1)) ]] && is_last=1
    
    output=$(run_grep_for_pattern "$pattern" "$limit" "$is_last")
    if [[ -n "$output" ]]; then
        ALL_OUTPUT+="${SEPARATOR}${output}"
        SEPARATOR=$'\n'
    fi
done

# Apply global head limit if set
if [[ -n "$ALL_OUTPUT" ]]; then
    if [[ -n "$FILES_ONLY" ]]; then
        # Deduplicate files-only output
        echo "$ALL_OUTPUT" | sort -u
        exit 0
    elif [[ -n "$HEAD" ]]; then
        echo "$ALL_OUTPUT" | head -n "$HEAD"
    else
        echo "$ALL_OUTPUT"
    fi
fi
