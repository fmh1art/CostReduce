#!/usr/bin/env bash
# batch-read: Read multiple files or line ranges in one step, or list code structure.
# Usage: batch-read [--head=N|--tail=N|--lines=start-end|--number|--count|--grep=PATTERN|-C=N|--code-only|--structure|--dir=PATH --include=GLOB] file1 [file2:start-end ...]

set -euo pipefail

HEAD=""
TAIL=""
LINES=""
NUMBER=""
COUNT=""
GREP=""
CONTEXT=""
CODE_ONLY=""
STRUCTURE=""
SUMMARY=""
CD_DIR=""
DIR=""
INCLUDE="*"
SED_RANGE=""
SED_MULTI="" # space-separated list of sed-style ranges for a single file
FILES=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --help|-h)
            echo "Usage: batch-read [options] file1 [file2:start-end ...]"
            echo "Options:"
            echo "  --head=N         Show first N lines"
            echo "  --tail=N         Show last N lines"
            echo "  --lines=start-end Show line range (e.g., 10-30)"
            echo "  --number, -n     Show line numbers"
            echo "  --count          Show line/word/char count (like wc)"
            echo "  --grep=PATTERN   Show only lines matching pattern (grep -E)"
            echo "  -C=N, --context=N Show N lines context around grep matches"
            echo "  --code-only      Strip package/import boilerplate from source files"
            echo "  --structure, -s  List functions/classes/structs in source files"
            echo "  --summary        Compact one-line summary with --structure"
            echo "    --sed=RANGE       Accept sed-style range (e.g., '28,80p') - converts to :start-end internally
  --sed-multi=RANGES Space-separated list of sed-style ranges for same file (e.g., '28,80p 100,150p')
--dir=PATH       Read all files in directory (use with --include)"
            echo "  --include=GLOB   File glob filter for --dir (default: *)"
            echo "  --cd=DIR     Change to DIR before reading files"
            echo "File format: file:start-end (e.g., file.py:10-30) or just file"
            exit 0
            ;;
        --head=*) HEAD="${1#*=}" ;;
        --tail=*) TAIL="${1#*=}" ;;
        --lines=*) LINES="${1#*=}" ;;
        --number|-n) NUMBER="1" ;;
        --count) COUNT="1" ;;
        --grep=*) GREP="${1#*=}" ;;
        -C=*|--context=*) CONTEXT="${1#*=}" ;;
        -C|--context)
            shift
            CONTEXT="$1"
            ;;
        --code-only) CODE_ONLY="1" ;;
        --structure|-s) STRUCTURE="1" ;;
        --summary) SUMMARY="1" ;;
        --cd=*) CD_DIR="${1#*=}" ;;
        --cd)
            shift
            CD_DIR="$1"
            ;;

                --sed=*|--sed-range=*) SED_RANGE="${1#*=}" ;;
        --sed-multi=*) SED_MULTI="${1#*=}" ;;
--dir=*) DIR="${1#*=}" ;;
        --include=*) INCLUDE="${1#*=}" ;;
        *) FILES+=("$1") ;;
    esac
    shift
done

# If --dir is specified, find files and add them
if [[ -n "$DIR" ]]; then
    if [[ -d "$DIR" ]]; then
        while IFS= read -r -d $'\0' f; do
            FILES+=("$f")
        done < <(find "$DIR" -type f -name "$INCLUDE" -print0 2>/dev/null | sort -z)
    else
        echo "Error: Directory not found: $DIR" >&2
        exit 1
    fi
fi


# Resolve script directory for helper (before any cd)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Change directory if --cd was specified
if [[ -n "$CD_DIR" ]]; then
    if [[ -d "$CD_DIR" ]]; then
        cd "$CD_DIR"
    else
        echo "Error: Directory not found: $CD_DIR" >&2
        exit 1
    fi
fi

[[ ${#FILES[@]} -eq 0 ]] && { echo "Error: No files specified" >&2; exit 1; }


# Handle --sed flag: converts sed-style '28,80p' to :28-80 format on the first file
if [[ -n "$SED_RANGE" ]]; then
    # Strip trailing 'p' if present (sed -n '28,80p')
    RANGE_CLEAN="${SED_RANGE%p}"
    # Replace comma with dash
    RANGE_CLEAN="${RANGE_CLEAN//,/-}"
    if [[ ${#FILES[@]} -gt 0 ]]; then
        FILES[0]="${FILES[0]}:${RANGE_CLEAN}"
    fi
fi

# Handle --sed-multi flag: applies multiple sed-style ranges to the first file
# e.g., --sed-multi='28,80p 100,150p 200,250p' converts to multiple FILE_SPEC entries
if [[ -n "$SED_MULTI" ]]; then
    FIRST_FILE="${FILES[0]:-}"
    if [[ -n "$FIRST_FILE" ]]; then
        NEW_FILES=()
        for RANGE_SPEC in $SED_MULTI; do
            RANGE_CLEAN="${RANGE_SPEC%p}"
            RANGE_CLEAN="${RANGE_CLEAN//,/-}"
            NEW_FILES+=("${FIRST_FILE}:${RANGE_CLEAN}")
        done
        FILES=("${NEW_FILES[@]}")
    fi
fi


for FILE_SPEC in "${FILES[@]}"; do
    # Parse file:range format
    if [[ "$FILE_SPEC" == *:* ]]; then
        FILE="${FILE_SPEC%%:*}"
        RANGE="${FILE_SPEC#*:}"
    else
        FILE="$FILE_SPEC"
        RANGE=""
    fi

    [[ ! -f "$FILE" ]] && { echo "Error: File not found: $FILE" >&2; continue; }

    # Print filename separator if multiple files
    [[ ${#FILES[@]} -gt 1 ]] && echo "--- $FILE ---"

    if [[ -n "$STRUCTURE" ]]; then
        # Code structure mode: list functions/classes/structs/etc
        EXT="${FILE##*.}"
        case "$EXT" in
            go)
                if [[ -n "$SUMMARY" ]]; then
                    grep -nE '^func |^type [A-Za-z_][A-Za-z0-9_]* (struct|interface)' "$FILE" 2>/dev/null | sed -E 's/^[0-9]+://' || true
                else
                    echo "=== Functions ==="
                    grep -n '^func ' "$FILE" 2>/dev/null || true
                    echo "=== Types ==="
                    grep -n '^type ' "$FILE" 2>/dev/null || true
                fi
                ;;
            py)
                if [[ -n "$SUMMARY" ]]; then
                    grep -nE '^[[:space:]]*(class |def |async def )' "$FILE" 2>/dev/null | sed -E 's/^[[:space:]]*[0-9]+:[[:space:]]*//' || true
                else
                    echo "=== Classes ==="
                    grep -nE '^[[:space:]]*class ' "$FILE" 2>/dev/null || true
                    echo "=== Functions ==="
                    grep -nE '^[[:space:]]*def |^[[:space:]]*async def ' "$FILE" 2>/dev/null || true
                fi
                ;;
            rs)
                if [[ -n "$SUMMARY" ]]; then
                    grep -nE '^fn |^pub fn |^struct |^enum |^trait |^impl ' "$FILE" 2>/dev/null | sed -E 's/^[0-9]+://' || true
                else
                    echo "=== Functions ==="
                    grep -nE '^fn |^pub fn ' "$FILE" 2>/dev/null || true
                    echo "=== Types ==="
                    grep -nE '^struct |^enum |^trait |^impl ' "$FILE" 2>/dev/null || true
                fi
                ;;
            ts|tsx|js|jsx)
                if [[ -n "$SUMMARY" ]]; then
                    grep -nE '^[[:space:]]*(export |)(default |)(class|function|interface|type|enum|const) ' "$FILE" 2>/dev/null | sed -E 's/^[0-9]+://' || true
                else
                    echo "=== Definitions ==="
                    grep -nE '^[[:space:]]*(export |)(default |)(class|function|interface|type|enum|const) ' "$FILE" 2>/dev/null || true
                fi
                ;;
            java|kt|scala)
                if [[ -n "$SUMMARY" ]]; then
                    grep -nE '^[[:space:]]*public[[:space:]]+(abstract |final |static |)(class|interface|enum) ' "$FILE" 2>/dev/null | sed -E 's/^[0-9]+://' || true
                else
                    grep -nE '^[[:space:]]*public ' "$FILE" 2>/dev/null || true
                fi
                ;;
            *)
                if [[ -n "$SUMMARY" ]]; then
                    grep -nE '^[[:space:]]*(class |def |fn |function |struct |interface |enum |type |trait |impl )' "$FILE" 2>/dev/null | sed -E 's/^[[:space:]]*[0-9]+:[[:space:]]*//' || true
                else
                    grep -nE '^[[:space:]]*(class |def |fn |function |struct |interface |enum |type |trait |impl )' "$FILE" 2>/dev/null || true
                fi
                ;;
        esac
    elif [[ -n "$CODE_ONLY" ]]; then
        if [[ -n "$LINES" ]]; then
            START="${LINES%-*}"; END="${LINES#*-}"
            python3 "$SCRIPT_DIR/strip_boilerplate.py" "$FILE" | sed -n "${START},${END}p"
        elif [[ -n "$RANGE" ]]; then
            START="${RANGE%-*}"; END="${RANGE#*-}"
            python3 "$SCRIPT_DIR/strip_boilerplate.py" "$FILE" | sed -n "${START},${END}p"
        else
            if [[ -n "$NUMBER" ]]; then
                python3 "$SCRIPT_DIR/strip_boilerplate.py" "$FILE" | nl -ba
            else
                python3 "$SCRIPT_DIR/strip_boilerplate.py" "$FILE"
            fi
        fi
    elif [[ -n "$GREP" ]]; then
        if [[ -n "$CONTEXT" ]]; then
            if [[ -n "$NUMBER" ]]; then
                grep -nE -C "$CONTEXT" "$GREP" "$FILE" || true
            else
                grep -E -C "$CONTEXT" "$GREP" "$FILE" || true
            fi
        else
            if [[ -n "$NUMBER" ]]; then
                grep -nE "$GREP" "$FILE" || true
            else
                grep -E "$GREP" "$FILE" || true
            fi
        fi
    elif [[ -n "$COUNT" ]]; then
        wc -l "$FILE" | awk '{print $1}'
    elif [[ -n "$HEAD" ]]; then
        if [[ -n "$NUMBER" ]]; then nl -ba "$FILE" | head -n "$HEAD"; else head -n "$HEAD" "$FILE"; fi
    elif [[ -n "$TAIL" ]]; then
        if [[ -n "$NUMBER" ]]; then nl -ba "$FILE" | tail -n "$TAIL"; else tail -n "$TAIL" "$FILE"; fi
    elif [[ -n "$LINES" ]]; then
        START="${LINES%-*}"; END="${LINES#*-}"
        if [[ -n "$NUMBER" ]]; then nl -ba "$FILE" | sed -n "${START},${END}p"; else sed -n "${START},${END}p" "$FILE"; fi
    elif [[ -n "$RANGE" ]]; then
        START="${RANGE%-*}"; END="${RANGE#*-}"
        if [[ -n "$NUMBER" ]]; then nl -ba "$FILE" | sed -n "${START},${END}p"; else sed -n "${START},${END}p" "$FILE"; fi
    else
        if [[ -n "$NUMBER" ]]; then nl -ba "$FILE"; else cat "$FILE"; fi
    fi
done
