#!/usr/bin/env bash
set -euo pipefail

# batch_read - Read multiple files or line ranges in one native tool call
# Usage: batch_read [--cd=DIR] [--head=N] [--tail=N] [--lines=start-end] [--number] [--count-lines] [--grep=PATTERN] [--max-results=N] [--context=N|-C N] [-A N] [-B N] [--find=GLOB] [--brief] file1 [file2...]
#   file          - read entire file
#   file:start-end - read lines start to end (1-indexed)
#   file:start-   - read from start to end of file (open-ended)
#   file:-end     - read from line 1 to end
#   --cd=DIR      - change to DIR before reading files
#   --head=N      - show first N lines
#   --tail=N      - show last N lines
#   --tail=+N     - show all lines starting from line N (like tail -n +N)
#   --lines=M-N   - show lines M to N for subsequent files
#   --lines=M-    - show from line M to end for subsequent files
#   --number, -n  - show line numbers
#   --count-lines, -c - show line count for each file (like wc -l) before content
#   --grep=PATTERN - show only lines matching extended regex
#   --max-results=N - limit grep output to N matching lines (like grep PATTERN file | head -N)
#   --context=N, -C N - show N lines of context before and after each grep match (like grep -C)
#   -A N          - show N lines of context AFTER each grep match (like grep -A)
#   -B N          - show N lines of context BEFORE each grep match (like grep -B)
#   --show-all, -A (deprecated: use --cat-A) - show non-printable characters (like cat -A)
#   --cat-A       - show non-printable characters (like cat -A)
#   --find=GLOB   - Find files by name glob pattern before reading (replaces find ... -exec cat {} \;)
#   --brief       - Suppress file headers and separators for compact output
#   Supports glob patterns (e.g., "src/*.ts") in file arguments (via shell expansion).

CD_DIR=""
HEAD_LINES=""
TAIL_LINES=""
LINE_RANGE=""
SHOW_NUMBERS=false
SHOW_ALL=false
COUNT_LINES=false
GREP_PATTERN=""
GREP_AFTER=""
GREP_BEFORE=""
GREP_CONTEXT=""
FIND_GLOB=""
MAX_RESULTS=""
BRIEF=false
FILES=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --cd=*)
            CD_DIR="${1#*=}"
            shift
            ;;
        --head=*)
            HEAD_LINES="${1#*=}"
            shift
            ;;
        --tail=*)
            TAIL_LINES="${1#*=}"
            shift
            ;;
        --lines=*)
            LINE_RANGE="${1#*=}"
            shift
            ;;
        --number|-n)
            SHOW_NUMBERS=true
            shift
            ;;
        --count-lines|-c)
            COUNT_LINES=true
            shift
            ;;
        --show-all|--cat-A)
            SHOW_ALL=true
            shift
            ;;
        --brief)
            BRIEF=true
            shift
            ;;
        --grep=*)
            GREP_PATTERN="${1#*=}"
            shift
            ;;
        --max-results=*)
            MAX_RESULTS="${1#*=}"
            shift
            ;;
        --context=*|-C)
            if [[ "$1" == --context=* ]]; then
                GREP_CONTEXT="${1#*=}"
                shift
            else
                GREP_CONTEXT="$2"
                shift 2
            fi
            ;;
        -A)
            GREP_AFTER="$2"
            shift 2
            ;;
        -B)
            GREP_BEFORE="$2"
            shift 2
            ;;
        --find=*)
            FIND_GLOB="${1#*=}"
            shift
            ;;
        --find)
            FIND_GLOB="$2"
            shift 2
            ;;
        -*)
            echo "Unknown option: $1" >&2
            exit 1
            ;;
        *)
            FILES+=("$1")
            shift
            ;;
    esac
done

# Change to CD_DIR if specified
if [[ -n "$CD_DIR" ]]; then
    cd "$CD_DIR"
fi

# If --find=GLOB specified, find files matching the glob pattern
if [[ -n "$FIND_GLOB" ]]; then
    _find_name_flag="-name"
    if [[ "$FIND_GLOB" == *"/"* ]]; then
        _find_name_flag="-path"
    fi
    mapfile -t found_files < <(find . "$_find_name_flag" "$FIND_GLOB" -type f 2>/dev/null | head -100 || true)
    if [[ ${#found_files[@]} -eq 0 ]]; then
        echo "Error: no files found matching glob: $FIND_GLOB" >&2
        exit 1
    fi
    for f in "${found_files[@]}"; do
        # Strip leading ./ for cleaner display
        f="${f#./}"
        FILES+=("$f")
    done
fi

# Re-check that we have files to read
if [[ ${#FILES[@]} -eq 0 ]]; then
    echo "Usage: $0 [--cd=DIR] [--head=N] [--tail=N] [--lines=start-end] [--number] [--count-lines] [--grep=PATTERN] [--max-results=N] [--context=N|-C N] [-A N] [-B N] [--find=GLOB] [--brief] file1 [file2...]" >&2
    exit 1
fi

# Build grep context flags
GREP_CONTEXT_FLAGS=""
if [[ -n "$GREP_CONTEXT" ]]; then
    GREP_CONTEXT_FLAGS="-C $GREP_CONTEXT"
elif [[ -n "$GREP_AFTER" ]] && [[ -n "$GREP_BEFORE" ]]; then
    GREP_CONTEXT_FLAGS="-A $GREP_AFTER -B $GREP_BEFORE"
elif [[ -n "$GREP_AFTER" ]]; then
    GREP_CONTEXT_FLAGS="-A $GREP_AFTER"
elif [[ -n "$GREP_BEFORE" ]]; then
    GREP_CONTEXT_FLAGS="-B $GREP_BEFORE"
fi

for file_spec in "${FILES[@]}"; do
    # Parse file:line-range syntax
    file="${file_spec%%:*}"
    range=""
    if [[ "$file_spec" == *:* ]]; then
        range="${file_spec#*:}"
    fi

    # If file doesn't exist, try glob expansion using bash
    if [[ ! -f "$file" ]] && [[ ! -e "$file" ]]; then
        # Attempt glob expansion directly
        shopt -s nullglob
        matches=( $file )
        shopt -u nullglob
        if [[ ${#matches[@]} -gt 0 ]]; then
            # Recurse with expanded matches
            for match in "${matches[@]}"; do
                if [[ -n "$range" ]]; then
                    FILES+=("${match}:${range}")
                else
                    FILES+=("$match")
                fi
            done
            continue
        fi
    fi

    if [[ ! -f "$file" ]]; then
        echo "=== $file_spec (file not found) ===" >&2
        continue
    fi

    if $COUNT_LINES; then
        line_count=$(wc -l < "$file")
        echo "--- $file_spec: $line_count lines ---"
    fi

    if ! $BRIEF; then
        echo "=== $file_spec ==="
    fi

    # Determine the active start/end for this file
    ACTIVE_START=""
    ACTIVE_END=""
    RANGE_OPEN_END=false
    RANGE_OPEN_START=false
    if [[ -n "$range" ]]; then
        if [[ "$range" =~ ^[0-9]+-$ ]]; then
            ACTIVE_START="${range%-}"
            RANGE_OPEN_END=true
        elif [[ "$range" =~ ^-[0-9]+$ ]]; then
            ACTIVE_END="${range#-}"
            RANGE_OPEN_START=true
        else
            ACTIVE_START="${range%%-*}"
            ACTIVE_END="${range#*-}"
        fi
    elif [[ -n "$LINE_RANGE" ]]; then
        if [[ "$LINE_RANGE" =~ ^[0-9]+-$ ]]; then
            ACTIVE_START="${LINE_RANGE%-}"
            RANGE_OPEN_END=true
        elif [[ "$LINE_RANGE" =~ ^-[0-9]+$ ]]; then
            ACTIVE_END="${LINE_RANGE#-}"
            RANGE_OPEN_START=true
        else
            ACTIVE_START="${LINE_RANGE%%-*}"
            ACTIVE_END="${LINE_RANGE#*-}"
        fi
    fi

    # Build pipeline
    CMD=""
    if [[ -n "$ACTIVE_START" && "$RANGE_OPEN_END" == true ]]; then
        # Open-ended: from ACTIVE_START to end
        if $SHOW_NUMBERS; then
            CMD="awk -v start=$ACTIVE_START 'NR >= start {print NR, \$0}' \"$file\""
        else
            CMD="sed -n '${ACTIVE_START},\$p' \"$file\""
        fi
    elif [[ "$RANGE_OPEN_START" == true && -n "$ACTIVE_END" ]]; then
        # From beginning to ACTIVE_END
        if $SHOW_NUMBERS; then
            CMD="awk -v end=$ACTIVE_END 'NR <= end {print NR, \$0}' \"$file\""
        else
            CMD="sed -n '1,${ACTIVE_END}p' \"$file\""
        fi
    elif [[ -n "$ACTIVE_START" && -n "$ACTIVE_END" ]]; then
        # Closed range: ACTIVE_START to ACTIVE_END
        if $SHOW_NUMBERS; then
            CMD="awk -v start=$ACTIVE_START -v end=$ACTIVE_END 'NR >= start && NR <= end {print NR, \$0}' \"$file\""
        else
            CMD="sed -n '${ACTIVE_START},${ACTIVE_END}p' \"$file\""
        fi
    elif [[ -n "$HEAD_LINES" ]]; then
        if [[ "$HEAD_LINES" =~ ^\+ ]]; then
            NUM="${HEAD_LINES#+}"
            if $SHOW_NUMBERS; then
                CMD="awk -v start=$NUM 'NR >= start {print NR, \$0}' \"$file\""
            else
                CMD="sed -n '${NUM},\$p' \"$file\""
            fi
        else
            if $SHOW_NUMBERS; then
                CMD="awk -v n=$HEAD_LINES 'NR <= n {print NR, \$0}' \"$file\""
            else
                CMD="head -n \"$HEAD_LINES\" \"$file\""
            fi
        fi
    elif [[ -n "$TAIL_LINES" ]]; then
        if [[ "$TAIL_LINES" =~ ^\+ ]]; then
            NUM="${TAIL_LINES#+}"
            if $SHOW_NUMBERS; then
                CMD="awk -v start=$NUM 'NR >= start {print NR, \$0}' \"$file\""
            else
                CMD="tail -n +${NUM} \"$file\""
            fi
        else
            if $SHOW_NUMBERS; then
                CMD="awk '{print NR, \$0}' \"$file\" | tail -n \"$TAIL_LINES\""
            else
                CMD="tail -n \"$TAIL_LINES\" \"$file\""
            fi
        fi
    else
        if $SHOW_NUMBERS; then
            CMD="awk '{print NR, \$0}' \"$file\""
        else
            CMD="cat \"$file\""
        fi
    fi

    # Apply grep filtering on top
    if [[ -n "$GREP_PATTERN" ]]; then
        if [[ -n "$GREP_CONTEXT_FLAGS" ]]; then
            # With context: use grep -C/-A/-B so matching lines show context
            if [[ -n "$MAX_RESULTS" ]]; then
                if $SHOW_ALL; then
                    eval "$CMD" | grep -E $GREP_CONTEXT_FLAGS "$GREP_PATTERN" | head -n "$MAX_RESULTS" | cat -A || true
                else
                    eval "$CMD" | grep -E $GREP_CONTEXT_FLAGS "$GREP_PATTERN" | head -n "$MAX_RESULTS" || true
                fi
            else
                if $SHOW_ALL; then
                    eval "$CMD" | grep -E $GREP_CONTEXT_FLAGS "$GREP_PATTERN" | cat -A || true
                else
                    eval "$CMD" | grep -E $GREP_CONTEXT_FLAGS "$GREP_PATTERN" || true
                fi
            fi
        else
            if [[ -n "$MAX_RESULTS" ]]; then
                if $SHOW_ALL; then
                    eval "$CMD" | grep -E "$GREP_PATTERN" | head -n "$MAX_RESULTS" | cat -A || true
                else
                    eval "$CMD" | grep -E "$GREP_PATTERN" | head -n "$MAX_RESULTS" || true
                fi
            else
                if $SHOW_ALL; then
                    eval "$CMD" | grep -E "$GREP_PATTERN" | cat -A || true
                else
                    eval "$CMD" | grep -E "$GREP_PATTERN" || true
                fi
            fi
        fi
    else
        if $SHOW_ALL; then
            eval "$CMD" | cat -A || true
        else
            eval "$CMD" || true
        fi
    fi
    if ! $BRIEF; then
        echo
    fi
done
