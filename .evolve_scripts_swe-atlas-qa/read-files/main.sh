#!/bin/bash
# read-files - Batch read files or find+grep with line range, head, tail, brief, grep-with-context, line counting, line numbering, directory listing, find-by-name, and file-type filtering
# Usage: read-files [--dir=DIR] [--head=N] [--tail=N] [--lines=START-END]... [--brief=NUMS] [--grep=PATTERN] [--grep-context=N] [--count] [--number] [--ls] [--include=GLOB] [--exclude=PATTERN] [--find-name=PATTERN] [--find-type=f|d] [--find-depth=N] [--find-limit=N] [--find-exclude=PATTERN] [--find-ignore-case] [--names-only] [--exclude-dir=PATTERN] [--exclude-pattern=PATTERN] [--ignore-case] file1 [file2...]

set -euo pipefail

DIR=""
HEAD=""
TAIL=""
LINES_ARRAY=()
BRIEF=""
GREP_PATTERN=""
GREP_CONTEXT=""
COUNT=false
NUMBER=false
LIST_MODE=false
INCLUDE_GLOB=""
EXCLUDE_PATTERN=""
FILES=()

# Find options
FIND_NAMES=()
FIND_TYPE="f"
FIND_DEPTH=""
FIND_LIMIT="100"
FIND_EXCLUDE="*/node_modules/*"
FIND_IGNORE_CASE=false

# Grep options (from search-files merge)
NAMES_ONLY=false
IGNORE_CASE=false
EXCLUDE_DIRS=()
EXCLUDE_GREP_LINES=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dir=*)
            DIR="${1#*=}"
            shift
            ;;
        --head=*)
            HEAD="${1#*=}"
            shift
            ;;
        --tail=*)
            TAIL="${1#*=}"
            shift
            ;;
        --lines=*)
            LINES_ARRAY+=("${1#*=}")
            shift
            ;;
        --brief=*)
            BRIEF="${1#*=}"
            shift
            ;;
        --grep=*)
            GREP_PATTERN="${1#*=}"
            shift
            ;;
        --grep-context=*)
            GREP_CONTEXT="${1#*=}"
            shift
            ;;
        --count|-c)
            COUNT=true
            shift
            ;;
        --number|-n)
            NUMBER=true
            shift
            ;;
        --ls|--list)
            LIST_MODE=true
            shift
            ;;
        --include=*)
            INCLUDE_GLOB="${1#*=}"
            shift
            ;;
        --exclude=*)
            EXCLUDE_PATTERN="${1#*=}"
            shift
            ;;
        # Find options (merged from search-files)
        --find-name=*)
            FIND_NAMES+=("${1#*=}")
            shift
            ;;
        --find-type=*)
            FIND_TYPE="${1#*=}"
            shift
            ;;
        --find-depth=*|--max-depth=*)
            FIND_DEPTH="${1#*=}"
            shift
            ;;
        --find-limit=*|--limit=*)
            FIND_LIMIT="${1#*=}"
            shift
            ;;
        --find-exclude=*)
            FIND_EXCLUDE="$FIND_EXCLUDE|${1#*=}"
            shift
            ;;
        --find-ignore-case)
            FIND_IGNORE_CASE=true
            shift
            ;;
        # Grep options (merged from search-files)
        --names-only|-l)
            NAMES_ONLY=true
            shift
            ;;
        --ignore-case|-i)
            IGNORE_CASE=true
            shift
            ;;
        --exclude-dir=*)
            IFS=',' read -ra DIRS <<< "${1#*=}"
            for d in "${DIRS[@]}"; do
                EXCLUDE_DIRS+=(--exclude-dir="$d")
            done
            shift
            ;;
        --exclude-pattern=*)
            EXCLUDE_GREP_LINES="${1#*=}"
            shift
            ;;
        --*)
            echo "Unknown option: $1" >&2
            exit 1
            ;;
        *)
            FILES+=("$1")
            shift
            ;;
    esac
done

if [ -n "$DIR" ]; then
    cd "$DIR"
fi

# Build awk script for --brief option
build_brief_awk() {
    local brief="$1"
    local script=""
    IFS=',' read -ra PARTS <<< "$brief"
    for part in "${PARTS[@]}"; do
        if [[ "$part" == *-* ]]; then
            local start="${part%-*}"
            local end="${part#*-}"
            [ -n "$script" ] && script="$script ||"
            script="$script (NR >= $start && NR <= $end)"
        else
            [ -n "$script" ] && script="$script ||"
            script="$script NR == $part"
        fi
    done
    echo "$script"
}

# If --find-name is specified, resolve files via find first
if [ ${#FIND_NAMES[@]} -gt 0 ]; then
    RESOLVED_FILES=()
    local_find_depth="$FIND_DEPTH"
    local_find_limit="$FIND_LIMIT"
    
    FIND_ARGS=()
    [ -n "$local_find_depth" ] && FIND_ARGS+=(-maxdepth "$local_find_depth")
    
    # Determine search roots
    if [ ${#FILES[@]} -gt 0 ]; then
        SEARCH_ROOTS=("${FILES[@]}")
    else
        SEARCH_ROOTS=(".")
    fi
    
    for root in "${SEARCH_ROOTS[@]}"; do
        # Build find command
        cmd=(find "$root" -type "$FIND_TYPE")
        [ -n "$local_find_depth" ] && cmd+=(-maxdepth "$local_find_depth")
        
        # Build multi-name conditions with -o
        name_cond=()
        for idx in "${!FIND_NAMES[@]}"; do
            nm="${FIND_NAMES[$idx]}"
            if [ "$FIND_IGNORE_CASE" = true ]; then
                [ $idx -eq 0 ] && name_cond+=(-iname "$nm") || name_cond+=(-o -iname "$nm")
            else
                [ $idx -eq 0 ] && name_cond+=(-name "$nm") || name_cond+=(-o -name "$nm")
            fi
        done
        if [ ${#name_cond[@]} -gt 1 ]; then
            cmd+=(\( "${name_cond[@]}" \))
        else
            cmd+=("${name_cond[@]}")
        fi
        
        # Apply exclusions
        IFS='|' read -ra EXCL_ARR <<< "$FIND_EXCLUDE"
        for exc in "${EXCL_ARR[@]}"; do
            [ -n "$exc" ] && cmd+=(! -path "$exc")
        done
        
        while IFS= read -r -d '' f; do
            RESOLVED_FILES+=("$f")
        done < <("${cmd[@]}" -print0 2>/dev/null | head -z -n "$local_find_limit" || true)
    done
    
    if [ ${#RESOLVED_FILES[@]} -eq 0 ]; then
        echo "[NO MATCH] No files found matching name(s): ${FIND_NAMES[*]}" >&2
        exit 0
    fi
    
    # Override FILES with resolved files
    FILES=("${RESOLVED_FILES[@]}")
fi

if [ ${#FILES[@]} -eq 0 ]; then
    echo "Usage: $0 [--dir=DIR] [--head=N] [--tail=N] [--lines=START-END]... [--brief=NUMS] [--grep=PATTERN] [--grep-context=N] [--count] [--number] [--ls] [--include=GLOB] [--exclude=PATTERN] [--find-name=PATTERN] [--find-type=f|d] [--find-depth=N] [--find-limit=N] [--find-exclude=PATTERN] [--names-only] [--ignore-case] [--exclude-dir=PATTERN] [--exclude-pattern=PATTERN] file1 [file2...]" >&2
    exit 1
fi

for file in "${FILES[@]}"; do
    # --count mode: show line/word/char counts like wc
    if [ "$COUNT" = true ]; then
        if [ -f "$file" ]; then
            wc -l "$file"
        elif [ -d "$file" ]; then
            find "$file" -type f 2>/dev/null | head -500 | xargs wc -l 2>/dev/null | tail -1
        else
            echo "[SKIP] Not found: $file" >&2
        fi
        continue
    fi

    # --ls mode: list directory contents
    if [ "$LIST_MODE" = true ]; then
        if [ -d "$file" ]; then
            ls -la "$file"
        else
            echo "[SKIP] Not a directory: $file" >&2
        fi
        continue
    fi

    # --grep mode: support both files and directories
    if [ -n "$GREP_PATTERN" ]; then
        if [ ${#FILES[@]} -gt 1 ]; then
            echo "===== $file ====="
        fi
        if [ -d "$file" ]; then
            # Directory: search recursively
            GREP_ARGS=(-rn)
            [ "$NAMES_ONLY" = true ] && GREP_ARGS+=(-l)
            [ "$IGNORE_CASE" = true ] && GREP_ARGS+=(-i)
            [ -n "$GREP_CONTEXT" ] && GREP_ARGS+=(-C "$GREP_CONTEXT")
            [ -n "$INCLUDE_GLOB" ] && GREP_ARGS+=(--include="$INCLUDE_GLOB")
            [ ${#EXCLUDE_DIRS[@]} -gt 0 ] && GREP_ARGS+=("${EXCLUDE_DIRS[@]}")
            
            if [ -n "$EXCLUDE_GREP_LINES" ]; then
                grep "${GREP_ARGS[@]}" "$GREP_PATTERN" "$file" 2>/dev/null | grep -vE "$EXCLUDE_GREP_LINES" || echo "[NO MATCH] Pattern not found: $GREP_PATTERN in $file" >&2
            else
                grep "${GREP_ARGS[@]}" "$GREP_PATTERN" "$file" 2>/dev/null || echo "[NO MATCH] Pattern not found: $GREP_PATTERN in $file" >&2
            fi
        elif [ -f "$file" ]; then
            # Single file: search within it
            LOCAL_GREP_ARGS=(-n)
            [ "$NAMES_ONLY" = true ] && LOCAL_GREP_ARGS+=(-l)
            [ "$IGNORE_CASE" = true ] && LOCAL_GREP_ARGS+=(-i)
            [ -n "$GREP_CONTEXT" ] && LOCAL_GREP_ARGS+=(-C "$GREP_CONTEXT")
            
            if [ -n "$EXCLUDE_GREP_LINES" ]; then
                grep "${LOCAL_GREP_ARGS[@]}" -- "$GREP_PATTERN" "$file" 2>/dev/null | grep -vE "$EXCLUDE_GREP_LINES" || echo "[NO MATCH] Pattern not found: $GREP_PATTERN" >&2
            else
                grep "${LOCAL_GREP_ARGS[@]}" -- "$GREP_PATTERN" "$file" 2>/dev/null || echo "[NO MATCH] Pattern not found: $GREP_PATTERN" >&2
            fi
        else
            echo "[SKIP] Not found: $file" >&2
        fi
        continue
    fi

    # File not found: try listing parent directory as fallback
    if [ ! -f "$file" ] && [ -z "$GREP_PATTERN" ]; then
        parent_dir="$(dirname "$file" 2>/dev/null)"
        if [ -d "$parent_dir" ]; then
            echo "[NOT FOUND] $file" >&2
            echo "[FALLBACK] Listing: $parent_dir" >&2
            ls -la "$parent_dir"
        else
            echo "[SKIP] File not found: $file" >&2
        fi
        continue
    fi

    if [ ${#FILES[@]} -gt 1 ]; then
        echo "===== $file ====="
    fi

    if [ -n "$BRIEF" ]; then
        awk_condition="$(build_brief_awk "$BRIEF")"
        if [ "$NUMBER" = true ]; then
            awk "$awk_condition {printf \"%6d\\t%s\\n\", NR, \$0}" "$file"
        else
            awk "$awk_condition" "$file"
        fi
    elif [ -n "$HEAD" ]; then
        if [ "$NUMBER" = true ]; then
            head -n "$HEAD" "$file" | nl -ba
        else
            head -n "$HEAD" "$file"
        fi
    elif [ -n "$TAIL" ]; then
        if [ "$NUMBER" = true ]; then
            tail -n "$TAIL" "$file" | nl -ba
        else
            tail -n "$TAIL" "$file"
        fi
    elif [ ${#LINES_ARRAY[@]} -gt 0 ]; then
        # Support multiple --lines values (e.g., --lines=10-20 --lines=30-40)
        for range in "${LINES_ARRAY[@]}"; do
            if [ ${#LINES_ARRAY[@]} -gt 1 ]; then
                echo "[LINES $range]"
            fi
            START="${range%-*}"
            END="${range#*-}"
            if [ "$NUMBER" = true ]; then
                nl -ba "$file" | sed -n "${START},${END}p"
            else
                sed -n "${START},${END}p" "$file"
            fi
        done
    else
        if [ "$NUMBER" = true ]; then
            nl -ba "$file"
        else
            cat "$file"
        fi
    fi
done
