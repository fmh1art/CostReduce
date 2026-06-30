#!/usr/bin/env bash
set -euo pipefail

# batch_read - Read multiple files in one call with optional working directory, line ranges
# (including inline file:start-end syntax, open-ended START-, comma-separated multi-ranges),
# head/tail, line/word/char counts, line numbers, show-all (cat -A), and skip-empty.
# Supports MULTIPLE --grep patterns (repeatable) that each get their own section.
# Usage: batch_read [--dir=DIR] [--head=N|--tail=N] [--lines=START-END[,START2-END2...]]
#                   [--start-from=N] [--count|--wc] [--number|-n] [--show-all|-A] [--skip-empty]
#                   [--grep=PATTERN]... [--ignore-case|-i] [--after-context=N|-A=N]
#                   [--before-context=N|-B=N] [--context=N|-C=N]
#                   file1[:START-END] [file2[:START-END]...]

WORKDIR=""
HEAD_LINES=""
TAIL_LINES=""
GLOBAL_LINE_RANGES=()
SHOW_NUMBERS=false
SHOW_COUNT=false
SKIP_EMPTY=false
SHOW_ALL=false
GREP_PATTERNS=()
GREP_ICASE=""
GREP_AFTER=""
GREP_BEFORE=""
GREP_CONTEXT=""
FROM_MARKER=""
TO_MARKER=""
FUNC_NAME=""
FILES=()
# Associative arrays not portable in bash 3; use parallel arrays
INLINE_FILES=()
INLINE_RANGES=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dir=*)
            WORKDIR="${1#*=}"
            shift
            ;;
        --dir)
            WORKDIR="$2"
            shift 2
            ;;
        --head=*)
            HEAD_LINES="${1#*=}"
            shift
            ;;
        --head)
            HEAD_LINES="$2"
            shift 2
            ;;
        --tail=*)
            TAIL_LINES="${1#*=}"
            shift
            ;;
        --tail)
            TAIL_LINES="$2"
            shift 2
            ;;
        --start-from=*)
            # Convert --start-from=N to --lines=N- (open-ended range from N)
            GLOBAL_LINE_RANGES+=("${1#*=}-")
            shift
            ;;
        --start-from)
            GLOBAL_LINE_RANGES+=("$2-")
            shift 2
            ;;
        --lines=*)
            # Support comma-separated ranges: --lines=1-200,300-400,500-600
            IFS=',' read -ra RANGES <<< "${1#*=}"
            for r in "${RANGES[@]}"; do
                GLOBAL_LINE_RANGES+=("$r")
            done
            shift
            ;;
        --lines)
            shift
            # Support comma-separated ranges: --lines 1-200,300-400
            IFS=',' read -ra RANGES <<< "$1"
            for r in "${RANGES[@]}"; do
                GLOBAL_LINE_RANGES+=("$r")
            done
            shift
            ;;
        --number|-n)
            SHOW_NUMBERS=true
            shift
            ;;
        --count|--wc)
            SHOW_COUNT=true
            shift
            ;;
        --skip-empty)
            SKIP_EMPTY=true
            shift
            ;;
        --show-all|-A)
            SHOW_ALL=true
            shift
            ;;
        --grep=*)
            GREP_PATTERNS+=("${1#*=}")
            shift
            ;;
        --grep)
            GREP_PATTERNS+=("$2")
            shift 2
            ;;
        --ignore-case|-i)
            GREP_ICASE="-i"
            shift
            ;;
        --after-context=*|-A=*)
            GREP_AFTER="${1#*=}"
            shift
            ;;
        --after-context|-A)
            GREP_AFTER="$2"
            shift 2
            ;;
        --before-context=*|-B=*)
            GREP_BEFORE="${1#*=}"
            shift
            ;;
        --before-context|-B)
            GREP_BEFORE="$2"
            shift 2
            ;;
        --context=*|-C=*)
            GREP_CONTEXT="${1#*=}"
            shift
            ;;
        --context|-C)
            GREP_CONTEXT="$2"
            shift 2
            ;;
        --from-marker=*)
            FROM_MARKER="${1#*=}"
            shift
            ;;
        --from-marker)
            FROM_MARKER="$2"
            shift 2
            ;;
        --to-marker=*)
            TO_MARKER="${1#*=}"
            shift
            ;;
        --to-marker)
            TO_MARKER="$2"
            shift 2
            ;;
        --func=*)
            FUNC_NAME="${1#*=}"
            shift
            ;;
        --func)
            FUNC_NAME="$2"
            shift 2
            ;;
        -*)
            echo "Unknown option: $1" >&2
            exit 1
            ;;
        *)
            # Check for inline range syntax: file:START-END or file:START- or file:START-END,START2-END2
            if [[ "$1" =~ ^[^:]+:[0-9]+-([0-9]*)?(,[0-9]+-([0-9]*)?)*$ ]]; then
                filepath="${1%%:*}"
                rangespec="${1#*:}"
                INLINE_FILES+=("$filepath")
                INLINE_RANGES+=("$rangespec")
            else
                FILES+=("$1")
            fi
            shift
            ;;
    esac
done
# Change to working directory if specified
if [[ -n "$WORKDIR" ]]; then
    cd "$WORKDIR" || { echo "Error: Cannot cd to $WORKDIR" >&2; exit 1; }
fi

if [[ ${#FILES[@]} -eq 0 && ${#INLINE_FILES[@]} -eq 0 ]]; then
    echo "Usage: batch_read [--dir=DIR] [--head=N|--tail=N] [--lines=START-END[,START2-END2...]] [--count|--wc] [--number] [--show-all|-A] [--skip-empty] [--grep=PATTERN]... [--ignore-case|-i] [--after-context=N|-A=N] [--before-context=N|-B=N]
#                   [--start-from=N] [--context=N|-C=N] file1[:START-END] [file2[:START-END]...]" >&2
    exit 1
fi

# Build context args for grep
CONTEXT_ARGS=()
if [[ -n "$GREP_AFTER" ]]; then
    CONTEXT_ARGS+=(-A "$GREP_AFTER")
fi
if [[ -n "$GREP_BEFORE" ]]; then
    CONTEXT_ARGS+=(-B "$GREP_BEFORE")
fi
if [[ -n "$GREP_CONTEXT" ]]; then
    CONTEXT_ARGS+=(-C "$GREP_CONTEXT")
fi

# Helper: print lines for a file with optional range(s), head, or tail
print_file_lines() {
    local filename="$1"
    shift
    local ranges=("$@")

    if [[ ! -f "$filename" ]]; then
        if [[ "$SKIP_EMPTY" == false ]]; then
            echo "Error: File not found: $filename" >&2
        fi
        return
    fi

    # Check if file is empty
    if [[ ! -s "$filename" ]]; then
        if [[ "$SKIP_EMPTY" == false ]]; then
            echo "Warning: $filename is empty" >&2
        fi
        return
    fi

    # Multiple line ranges mode (from inline spec or --lines)
    if [[ ${#ranges[@]} -gt 0 ]]; then
        for range in "${ranges[@]}"; do
            start="${range%%-*}"
            end="${range#*-}"
            if [[ -z "$end" ]]; then
                # Open-ended range: from start to end of file
                if [[ "$SHOW_ALL" == true ]]; then
                    if [[ "$SHOW_NUMBERS" == true ]]; then
                        cat -An "$filename" | sed -n "${start},\$p"
                    else
                        cat -A "$filename" | sed -n "${start},\$p"
                    fi
                elif [[ "$SHOW_NUMBERS" == true ]]; then
                    cat -n "$filename" | sed -n "${start},\$p"
                else
                    sed -n "${start},\$p" "$filename"
                fi
            else
                if [[ "$SHOW_ALL" == true ]]; then
                    if [[ "$SHOW_NUMBERS" == true ]]; then
                        cat -An "$filename" | sed -n "${start},${end}p"
                    else
                        cat -A "$filename" | sed -n "${start},${end}p"
                    fi
                elif [[ "$SHOW_NUMBERS" == true ]]; then
                    cat -n "$filename" | sed -n "${start},${end}p"
                else
                    sed -n "${start},${end}p" "$filename"
                fi
            fi
        done
        return
    fi

    # Full file, head, or tail modes
    if [[ -n "$HEAD_LINES" ]]; then
        if [[ "$SHOW_ALL" == true ]]; then
            if [[ "$SHOW_NUMBERS" == true ]]; then
                head -n "$HEAD_LINES" "$filename" | cat -An | head -n "$HEAD_LINES"
            else
                head -n "$HEAD_LINES" "$filename" | cat -A | head -n "$HEAD_LINES"
            fi
        elif [[ "$SHOW_NUMBERS" == true ]]; then
            cat -n "$filename" | head -n "$HEAD_LINES"
        else
            head -n "$HEAD_LINES" "$filename"
        fi
    elif [[ -n "$TAIL_LINES" ]]; then
        if [[ "$SHOW_ALL" == true ]]; then
            if [[ "$SHOW_NUMBERS" == true ]]; then
                tail -n "$TAIL_LINES" "$filename" | cat -An | tail -n "$TAIL_LINES"
            else
                tail -n "$TAIL_LINES" "$filename" | cat -A | tail -n "$TAIL_LINES"
            fi
        elif [[ "$SHOW_NUMBERS" == true ]]; then
            cat -n "$filename" | tail -n "$TAIL_LINES"
        else
            tail -n "$TAIL_LINES" "$filename"
        fi
    else
        if [[ "$SHOW_ALL" == true ]]; then
            if [[ "$SHOW_NUMBERS" == true ]]; then
                cat -An "$filename"
            else
                cat -A "$filename"
            fi
        elif [[ "$SHOW_NUMBERS" == true ]]; then
            cat -n "$filename"
        else
            cat "$filename"
        fi
    fi
}

# Helper: grep with optional context and icase, returning up to head/tail limit
do_grep() {
    local pattern="$1"
    local filename="$2"
    local grep_args=()

    if [[ -n "$GREP_ICASE" ]]; then
        grep_args+=("$GREP_ICASE")
    fi
    if [[ ${#CONTEXT_ARGS[@]} -gt 0 ]]; then
        grep_args+=("${CONTEXT_ARGS[@]}")
    fi
    if [[ "$SHOW_NUMBERS" == true ]]; then
        grep -n "${grep_args[@]}" "$pattern" "$filename" 2>/dev/null || true
    else
        grep "${grep_args[@]}" "$pattern" "$filename" 2>/dev/null || true
    fi
}

# Helper: grep within a specific line range, showing absolute line numbers
grep_in_range() {
    local pattern="$1"
    local filename="$2"
    local start="$3"
    local end="$4"
    local grep_args=()

    if [[ -n "$GREP_ICASE" ]]; then
        grep_args+=("$GREP_ICASE")
    fi
    if [[ ${#CONTEXT_ARGS[@]} -gt 0 ]]; then
        grep_args+=("${CONTEXT_ARGS[@]}")
    fi

    if [[ "$SHOW_NUMBERS" == true ]]; then
        cat -n "$filename" | sed -n "${start},${end}p" | grep "${grep_args[@]}" "$pattern" 2>/dev/null || true
    else
        sed -n "${start},${end}p" "$filename" | grep "${grep_args[@]}" "$pattern" 2>/dev/null || true
    fi
}

# Helper: apply head/tail limit to output
apply_limit() {
    local input
    input=$(cat)
    if [[ -n "$HEAD_LINES" ]]; then
        echo "$input" | head -n "$HEAD_LINES"
    elif [[ -n "$TAIL_LINES" ]]; then
        echo "$input" | tail -n "$TAIL_LINES"
    else
        echo "$input"
    fi
}

# Helper: print lines from a marker regex to end of file or until another marker
print_between_markers() {
    local filename="$1"
    local from_regex="$2"
    local to_regex="$3"

    if [[ ! -f "$filename" ]]; then
        echo "Error: File not found: $filename" >&2
        return
    fi

    # Find the from-marker line number (first match)
    local from_line
    from_line=$(grep -nE "$from_regex" "$filename" | head -1 | cut -d: -f1 || true)
    if [[ -z "$from_line" ]]; then
        echo "Warning: from-marker '$from_regex' not found in $filename" >&2
        return
    fi

    if [[ -z "$to_regex" ]]; then
        # Show from marker to end of file
        if [[ "$SHOW_NUMBERS" == true ]]; then
            cat -n "$filename" | sed -n "${from_line},\$p"
        else
            sed -n "${from_line},\$p" "$filename"
        fi
        return
    fi

    # Find the to-marker line number (first match AFTER from_line, skipping the from_line itself)
    local to_line
    to_line=$(tail -n +$((from_line + 1)) "$filename" | grep -nE "$to_regex" | head -1 | cut -d: -f1 || true)
    if [[ -z "$to_line" ]]; then
        # No to-marker found after from-marker; show to end
        if [[ "$SHOW_NUMBERS" == true ]]; then
            cat -n "$filename" | sed -n "${from_line},\$p"
        else
            sed -n "${from_line},\$p" "$filename"
        fi
        return
    fi

    # to_line is relative offset from from_line (1-indexed)
    local abs_to_line=$(( from_line + to_line - 1 ))
    # Show lines from from_line to (abs_to_line - 1), i.e. exclude the to-marker line
    local end_line=$(( abs_to_line - 1 ))
    if [[ $end_line -lt $from_line ]]; then
        end_line=$from_line
    fi

    if [[ "$SHOW_NUMBERS" == true ]]; then
        cat -n "$filename" | sed -n "${from_line},${end_line}p"
    else
        sed -n "${from_line},${end_line}p" "$filename"
    fi
}

# Resolve --func=NAME to --from-marker and --to-marker
if [[ -n "$FUNC_NAME" ]]; then
    FROM_MARKER="^func ${FUNC_NAME}\("
    TO_MARKER="^func "
fi

# Show line/word/char count mode
if [[ "$SHOW_COUNT" == true ]]; then
    ALL_FILES=("${FILES[@]}" "${INLINE_FILES[@]}")
    if [[ ${#ALL_FILES[@]} -eq 1 ]]; then
        wc "${ALL_FILES[0]}" 2>/dev/null || echo "Error: File not found: ${ALL_FILES[0]}" >&2
    else
        wc "${ALL_FILES[@]}" 2>/dev/null || true
    fi
    exit 0
fi

# Process files with global --lines ranges
for filename in "${FILES[@]}"; do
    file_header_printed=false

    if [[ ${#GREP_PATTERNS[@]} -gt 0 ]]; then
        # Grep mode with multiple patterns
        for pat_idx in "${!GREP_PATTERNS[@]}"; do
            pattern="${GREP_PATTERNS[$pat_idx]}"
            if [[ ${#GREP_PATTERNS[@]} -gt 1 ]]; then
                if [[ "$file_header_printed" == false && (${#FILES[@]} -gt 1 || ${#INLINE_FILES[@]} -gt 0) ]]; then
                    echo "===== $filename ====="
                    file_header_printed=true
                fi
                echo "--- grep #$((pat_idx+1)): $pattern ---"
            fi
            if [[ ${#GLOBAL_LINE_RANGES[@]} -gt 0 ]]; then
                # Grep within each specified line range
                for range in "${GLOBAL_LINE_RANGES[@]}"; do
                    start="${range%%-*}"
                    end="${range#*-}"
                    if [[ -z "$end" ]]; then
                        grep_in_range "$pattern" "$filename" "$start" "\$"
                    else
                        grep_in_range "$pattern" "$filename" "$start" "$end"
                    fi
                done
            elif [[ -n "$HEAD_LINES" ]]; then
                do_grep "$pattern" "$filename" | head -n "$HEAD_LINES"
            elif [[ -n "$TAIL_LINES" ]]; then
                do_grep "$pattern" "$filename" | tail -n "$TAIL_LINES"
            else
                do_grep "$pattern" "$filename"
            fi
        done
    elif [[ -n "$FROM_MARKER" ]]; then
        # Marker-based reading
        if [[ ${#FILES[@]} -gt 1 || ${#INLINE_FILES[@]} -gt 0 ]]; then
            echo "===== $filename ====="
        fi
        if [[ -n "$HEAD_LINES" || -n "$TAIL_LINES" ]]; then
            print_between_markers "$filename" "$FROM_MARKER" "$TO_MARKER" | apply_limit
        else
            print_between_markers "$filename" "$FROM_MARKER" "$TO_MARKER"
        fi
    else
        # No grep - just read lines
        if [[ ${#FILES[@]} -gt 1 || ${#INLINE_FILES[@]} -gt 0 ]]; then
            echo "===== $filename ====="
        fi
        if [[ ${#GLOBAL_LINE_RANGES[@]} -gt 0 ]]; then
            # If head/tail is combined with lines, apply it
            if [[ -n "$HEAD_LINES" || -n "$TAIL_LINES" ]]; then
                print_file_lines "$filename" "${GLOBAL_LINE_RANGES[@]+${GLOBAL_LINE_RANGES[@]}}" | apply_limit
            else
                print_file_lines "$filename" "${GLOBAL_LINE_RANGES[@]+${GLOBAL_LINE_RANGES[@]}}"
            fi
        else
            print_file_lines "$filename"
        fi
    fi
    if [[ ${#FILES[@]} -gt 1 || ${#INLINE_FILES[@]} -gt 0 ]]; then
        echo ""
    fi
done

# Process files with inline ranges
for idx in "${!INLINE_FILES[@]}"; do
    filename="${INLINE_FILES[$idx]}"
    rangespec="${INLINE_RANGES[$idx]}"

    if [[ ! -f "$filename" ]]; then
        if [[ "$SKIP_EMPTY" == false ]]; then
            echo "Error: File not found: $filename" >&2
        fi
        continue
    fi

    if [[ ${#GREP_PATTERNS[@]} -gt 0 ]]; then
        # Grep mode scoped to inline ranges
        for pat_idx in "${!GREP_PATTERNS[@]}"; do
            pattern="${GREP_PATTERNS[$pat_idx]}"
            if [[ ${#GREP_PATTERNS[@]} -gt 1 ]]; then
                echo "--- $filename grep #$((pat_idx+1)): $pattern ---"
            elif [[ ${#INLINE_FILES[@]} -gt 1 ]]; then
                echo "===== $filename ====="
            fi
            # Parse inline ranges (comma-separated)
            IFS=',' read -ra PARSED_RANGES <<< "$rangespec"
            for range in "${PARSED_RANGES[@]}"; do
                start="${range%%-*}"
                end="${range#*-}"
                if [[ -z "$end" ]]; then
                    grep_in_range "$pattern" "$filename" "$start" "\$"
                else
                    grep_in_range "$pattern" "$filename" "$start" "$end"
                fi
            done
        done
        if [[ $idx -lt $(( ${#INLINE_FILES[@]} - 1 )) ]]; then
            echo ""
        fi
        continue
    fi

    # Parse inline ranges (comma-separated)
    IFS=',' read -ra PARSED_RANGES <<< "$rangespec"

    for range in "${PARSED_RANGES[@]}"; do
        start="${range%%-*}"
        end="${range#*-}"
        if [[ ${#PARSED_RANGES[@]} -gt 1 ]]; then
            if [[ -z "$end" ]]; then
                echo "===== $filename (lines $start+) ====="
            else
                echo "===== $filename (lines $start-$end) ====="
            fi
        fi
        if [[ -z "$end" ]]; then
            # Open-ended range: from start to end of file
            if [[ "$SHOW_ALL" == true ]]; then
                if [[ "$SHOW_NUMBERS" == true ]]; then
                    cat -An "$filename" | sed -n "${start},\$p"
                else
                    cat -A "$filename" | sed -n "${start},\$p"
                fi
            elif [[ "$SHOW_NUMBERS" == true ]]; then
                cat -n "$filename" | sed -n "${start},\$p"
            else
                sed -n "${start},\$p" "$filename"
            fi
        else
            if [[ "$SHOW_ALL" == true ]]; then
                if [[ "$SHOW_NUMBERS" == true ]]; then
                    cat -An "$filename" | sed -n "${start},${end}p"
                else
                    cat -A "$filename" | sed -n "${start},${end}p"
                fi
            elif [[ "$SHOW_NUMBERS" == true ]]; then
                cat -n "$filename" | sed -n "${start},${end}p"
            else
                sed -n "${start},${end}p" "$filename"
            fi
        fi
    done

    if [[ $idx -lt $(( ${#INLINE_FILES[@]} - 1 )) ]]; then
        echo ""
    fi
done
