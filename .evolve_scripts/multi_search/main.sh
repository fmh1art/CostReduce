#!/bin/bash
# multi_search - Search across multiple patterns efficiently
# Usage: multi_search <directory_or_file> [options] <pattern1> [pattern2] ...
#        multi_search <directory> --include='*.ext' <pattern1> [pattern2] ...
#        multi_search <directory> --names-only <pattern1> [pattern2] ...
#        multi_search <file1.py> <file2.py> ... <pattern1> [pattern2] ...
#
# Searches for ALL patterns in ONE pass using grep -E with alternation,
# reducing filesystem scans from N passes to 1.
# Each pattern is treated as an ERE (extended regex) pattern.
# Patterns containing | (alternation) are handled correctly.
# By default searches common source file types (.py, .go, .js, .ts, .rs, .java, .sh, etc.)
# Use --names-only to search file names only (like find -name).
# Use --include='*.ext' to narrow to specific file types.
# Use -i or --ignore-case for case-insensitive search.
# Use -l or --files-with-matches to list only matching file names.
# Use -v or --exclude-pattern=PATTERN to filter OUT lines matching a pattern.
# Use multiple -v flags for multiple exclusion patterns.
# Saves steps by replacing multiple separate grep commands and combining
# the search into a single filesystem pass.
#
# If the first N positional arguments are existing files, they are used as file targets.
# Otherwise, the first argument is the search directory.
#
# Handles BusyBox grep (which lacks --include/--exclude-dir) by falling back
# to find ... -exec grep ... automatically.
#
# Examples:
#   multi_search . pattern1 pattern2 pattern3
#   multi_search . --include='*.py' def_class def_function
#   multi_search . --names-only test_* *_test.py
#   multi_search file1.py file2.py pattern1 pattern2   # search specific files
#   multi_search . -i PATTERN1 PATTERN2                      # case-insensitive
#   multi_search . -l pattern                           # list files only
#   multi_search . -v 'test' -v '\.git' 'pattern'           # exclude lines matching patterns

if [ $# -lt 2 ]; then
    echo "Usage: multi_search <directory_or_file> [options] <pattern1> [pattern2] ..."
    echo ""
    echo "Options:"
    echo "  --include='*.ext'       Search only specific file types"
    echo "  --names-only            Search file names instead of content"
    echo "  -i, --ignore-case       Case-insensitive search"
    echo "  -l, --files-with-matches List only matching file names"
  echo "  -v, --exclude-pattern=PATTERN  Exclude lines matching this pattern"
  echo "  -v, --exclude-pattern=PATTERN  Exclude lines matching this pattern"
    echo ""
    echo "Examples:"
    echo "  multi_search . pattern1 pattern2 pattern3"
    echo "  multi_search . --include='*.py' def_class def_function"
    echo "  multi_search . --names-only test_* *_test.py"
    echo "  multi_search file1.py file2.py pattern1 pattern2"
    echo "  multi_search . -i PATTERN1 PATTERN2"
    echo "  multi_search . -l pattern"
    exit 1
fi

# Collect positional args and options
POSITIONAL=()
NAMES_ONLY=false
USER_INCLUDES=()
EXCLUDE_PATTERNS=()
IGNORE_CASE=false
FILES_WITH_MATCHES=false

while [ $# -gt 0 ]; do
    case "$1" in
        --include=*)
            USER_INCLUDES+=("${1#*=}")
            shift
            ;;
        --names-only)
            NAMES_ONLY=true
            shift
            ;;
        -i|--ignore-case)
            IGNORE_CASE=true
            shift
            ;;
        -l|--files-with-matches)
            FILES_WITH_MATCHES=true
            shift
            ;;
        -v=*|--exclude-pattern=*)
            EXCLUDE_PATTERNS+=("${1#*=}")
            shift
            ;;
        -v|--exclude-pattern)
            shift
            if [ $# -gt 0 ]; then
                EXCLUDE_PATTERNS+=("$1")
                shift
            fi
            ;;
        --help)
            echo "Usage: multi_search <dir|file> [options] <pattern1> [pattern2] ..."
            echo "  --include='*.ext'  Filter by file extension"
            echo "  --names-only       Search file names"
            echo "  -i, --ignore-case  Case-insensitive search"
            echo "  -l, --files-with-matches  List file names only"
            echo "  -v, --exclude-pattern=PATTERN  Exclude lines matching pattern"
            exit 0
            ;;
        -*)
            echo "Unknown option: $1"
            exit 1
            ;;
        *)
            POSITIONAL+=("$1")
            shift
            ;;
    esac
done

set -- "${POSITIONAL[@]}"

if [ $# -lt 2 ]; then
    echo "Error: Need at least one search target and one pattern."
    exit 1
fi

# Determine if the leading positional args are files or a directory
FILE_TARGETS=()
DIR_TARGET=""
PATTERNS=()

# Check if first N args are all existing files
all_args=("$@")
num_args=$#
found_patterns=false
for ((i=0; i<num_args; i++)); do
    arg="${all_args[$i]}"
    if [ $i -eq 0 ] && [ -d "$arg" ]; then
        # First arg is a directory - this is a directory search
        DIR_TARGET="$arg"
        # Remaining args are patterns
        for ((j=i+1; j<num_args; j++)); do
            PATTERNS+=("${all_args[$j]}")
        done
        break
    elif [ -f "$arg" ]; then
        FILE_TARGETS+=("$arg")
    else
        # Not a file, and first arg wasn't a dir - remaining are patterns
        for ((j=i; j<num_args; j++)); do
            PATTERNS+=("${all_args[$j]}")
        done
        break
    fi
done

if [ ${#PATTERNS[@]} -eq 0 ] && [ -z "$DIR_TARGET" ]; then
    echo "Error: No search patterns provided. Usage: multi_search <target> <pattern1> [pattern2] ..."
    exit 1
fi

# Common exclusions for find-based searches
EXCLUDE_PATHS=(
    "*/node_modules/*"
    "*/.git/*"
    "*/__pycache__/*"
    "*/.venv/*"
    "*/venv/*"
    "*/env/*"
    "*/.tox/*"
    "*/build/*"
    "*/dist/*"
    "*/.egg-info/*"
    "*/.mypy_cache/*"
    "*/vendor/*"
    "*/target/*"
)

# Default file includes (common source file types)
DEFAULT_INCLUDES=(
    "*.py" "*.go" "*.js" "*.jsx" "*.ts" "*.tsx" "*.rs" "*.java"
    "*.c" "*.h" "*.cpp" "*.hpp" "*.cc" "*.hh" "*.cxx"
    "*.sh" "*.bash" "*.zsh" "*.fish"
    "*.rb" "*.php" "*.kt" "*.kts" "*.swift"
    "*.toml" "*.yaml" "*.yml" "*.json" "*.xml" "*.ini" "*.cfg"
    "*.md" "*.txt" "*.rst" "*.tex"
    "*.sql" "*.r" "*.m" "*.mm" "*.scala" "*.ex" "*.exs"
    "Makefile" "Dockerfile" "*.mk"
)

check_grep_include() {
    # Check if grep supports --include
    grep --help 2>&1 | grep -q "\-\-include" && return 0 || return 1
}

if [ "$NAMES_ONLY" = true ]; then
    # Search file names only (like find -name)
    echo "=== File Name Search ==="
    echo "Patterns: ${PATTERNS[*]}"
    echo ""

    for p in "${PATTERNS[@]}"; do
        echo "=== Pattern: '$p' ==="
        if [ -n "$DIR_TARGET" ]; then
            if [ "$IGNORE_CASE" = true ]; then
                find "$DIR_TARGET" -iname "$p" -type f 2>/dev/null | head -50
            else
                find "$DIR_TARGET" -name "$p" -type f 2>/dev/null | head -50
            fi
        elif [ ${#FILE_TARGETS[@]} -gt 0 ]; then
            for f in "${FILE_TARGETS[@]}"; do
                basename "$f" | grep -q "$p" && echo "$f"
            done
        fi
        echo ""
    done
    exit 0
fi

if [ ${#FILE_TARGETS[@]} -gt 0 ]; then
    # Search specific files
    ALT_PATTERN=""
    for p in "${PATTERNS[@]}"; do
        if [ -n "$ALT_PATTERN" ]; then
            ALT_PATTERN="$ALT_PATTERN|$p"
        else
            ALT_PATTERN="$p"
        fi
    done

    GREP_ARGS=(-n)
    if [ "$IGNORE_CASE" = true ]; then
        GREP_ARGS+=(-i)
    fi
    if [ "$FILES_WITH_MATCHES" = true ]; then
        GREP_ARGS+=(-l)
    fi
    GREP_ARGS+=(-E)

    if [ ${#PATTERNS[@]} -eq 1 ]; then
        echo "=== Pattern: '${PATTERNS[0]}' ==="
        RESULT=$(grep "${GREP_ARGS[@]}" "$ALT_PATTERN" "${FILE_TARGETS[@]}" 2>/dev/null)
        for exc in "${EXCLUDE_PATTERNS[@]}"; do
            RESULT=$(echo "$RESULT" | grep -v -E "$exc" 2>/dev/null || true)
        done
        echo "$RESULT" | head -50
    else
        TEMPFILE=$(mktemp)
        grep "${GREP_ARGS[@]}" "$ALT_PATTERN" "${FILE_TARGETS[@]}" 2>/dev/null > "$TEMPFILE"
        for exc in "${EXCLUDE_PATTERNS[@]}"; do
            TEMPFILE2=$(mktemp)
            grep -v -E "$exc" "$TEMPFILE" 2>/dev/null > "$TEMPFILE2" || true
            mv "$TEMPFILE2" "$TEMPFILE"
        done
        for p in "${PATTERNS[@]}"; do
            echo "=== Pattern: '$p' ==="
            if [ "$IGNORE_CASE" = true ]; then
                grep -i "$p" "$TEMPFILE" 2>/dev/null | head -50
            else
                grep "$p" "$TEMPFILE" 2>/dev/null | head -50
            fi
            echo ""
        done
        rm -f "$TEMPFILE"
    fi
else
    # Directory search
    INCLUDES=("${USER_INCLUDES[@]:-${DEFAULT_INCLUDES[@]}}")

    # Build the combined alternation pattern
    ALT_PATTERN=""
    for p in "${PATTERNS[@]}"; do
        if [ -n "$ALT_PATTERN" ]; then
            ALT_PATTERN="$ALT_PATTERN|$p"
        else
            ALT_PATTERN="$p"
        fi
    done

    GREP_ARGS=(-n)
    if [ "$IGNORE_CASE" = true ]; then
        GREP_ARGS+=(-i)
    fi
    if [ "$FILES_WITH_MATCHES" = true ]; then
        GREP_ARGS+=(-l)
    fi
    GREP_ARGS+=(-E)

    if check_grep_include; then
        # GNU grep - use --include and --exclude-dir
        GREP_INCLUDES=()
        for inc in "${INCLUDES[@]}"; do
            GREP_INCLUDES+=("--include=$inc")
        done
        GREP_EXCLUDES=()
        for exc in "${EXCLUDE_PATHS[@]}"; do
            dirname=$(echo "$exc" | sed 's|^\*/||; s|/\*$||')
            GREP_EXCLUDES+=("--exclude-dir=$dirname")
        done

        if [ ${#PATTERNS[@]} -eq 1 ]; then
            echo "=== Pattern: '${PATTERNS[0]}' ==="
            RESULT=$(grep -r "${GREP_ARGS[@]}" "$ALT_PATTERN" "$DIR_TARGET" \
                "${GREP_INCLUDES[@]}" "${GREP_EXCLUDES[@]}" 2>/dev/null)
            for exc in "${EXCLUDE_PATTERNS[@]}"; do
                RESULT=$(echo "$RESULT" | grep -v -E "$exc" 2>/dev/null || true)
            done
            echo "$RESULT" | head -50
            echo ""
        else
            echo "=== Patterns: ${PATTERNS[*]} ==="
            if [ "$FILES_WITH_MATCHES" = true ]; then
                RESULT=$(grep -r "${GREP_ARGS[@]}" "$ALT_PATTERN" "$DIR_TARGET" \
                    "${GREP_INCLUDES[@]}" "${GREP_EXCLUDES[@]}" 2>/dev/null)
                for exc in "${EXCLUDE_PATTERNS[@]}"; do
                    RESULT=$(echo "$RESULT" | grep -v -E "$exc" 2>/dev/null || true)
                done
                echo "$RESULT" | head -100
                echo ""
            else
                TEMPFILE=$(mktemp)
                grep -r "${GREP_ARGS[@]}" "$ALT_PATTERN" "$DIR_TARGET" \
                    "${GREP_INCLUDES[@]}" "${GREP_EXCLUDES[@]}" 2>/dev/null > "$TEMPFILE"
                for exc in "${EXCLUDE_PATTERNS[@]}"; do
                    TEMPFILE2=$(mktemp)
                    grep -v -E "$exc" "$TEMPFILE" 2>/dev/null > "$TEMPFILE2" || true
                    mv "$TEMPFILE2" "$TEMPFILE"
                done
                for p in "${PATTERNS[@]}"; do
                    echo "=== Pattern: '$p' ==="
                    if [ "$IGNORE_CASE" = true ]; then
                        grep -i "$p" "$TEMPFILE" 2>/dev/null | head -50
                    else
                        grep "$p" "$TEMPFILE" 2>/dev/null | head -50
                    fi
                    echo ""
                done
                rm -f "$TEMPFILE"
            fi
        fi
    else
        # BusyBox grep (no --include/--exclude-dir) - use find ... -exec grep
        echo "=== Using find -exec grep (grep --include not available) ==="

        FIND_CMD="find \"$DIR_TARGET\" -type f"

        if [ ${#INCLUDES[@]} -gt 0 ]; then
            FIND_CMD="$FIND_CMD \( -false"
            for inc in "${INCLUDES[@]}"; do
                FIND_CMD="$FIND_CMD -o -name \"$inc\""
            done
            FIND_CMD="$FIND_CMD \)"
        fi

        for e in "${EXCLUDE_PATHS[@]}"; do
            FIND_CMD="$FIND_CMD -not -path \"$e\""
        done

        GREP_FIND_ARGS=""
        if [ "$IGNORE_CASE" = true ]; then
            GREP_FIND_ARGS="$GREP_FIND_ARGS -i"
        fi
        if [ "$FILES_WITH_MATCHES" = true ]; then
            GREP_FIND_ARGS="$GREP_FIND_ARGS -l"
        fi
        GREP_FIND_ARGS="$GREP_FIND_ARGS -nE"

        if [ ${#PATTERNS[@]} -eq 1 ]; then
            echo "=== Pattern: '${PATTERNS[0]}' ==="
            eval "$FIND_CMD -exec grep $GREP_FIND_ARGS \"$ALT_PATTERN\" {} +" 2>/dev/null | head -50
            echo ""
        else
            echo "=== Patterns: ${PATTERNS[*]} ==="
            if [ "$FILES_WITH_MATCHES" = true ]; then
                eval "$FIND_CMD -exec grep $GREP_FIND_ARGS \"$ALT_PATTERN\" {} +" 2>/dev/null | head -100
                echo ""
            else
                TEMPFILE=$(mktemp)
                eval "$FIND_CMD -exec grep $GREP_FIND_ARGS \"$ALT_PATTERN\" {} +" 2>/dev/null > "$TEMPFILE"
                for p in "${PATTERNS[@]}"; do
                    echo "=== Pattern: '$p' ==="
                    if [ "$IGNORE_CASE" = true ]; then
                        grep -i "$p" "$TEMPFILE" 2>/dev/null | head -50
                    else
                        grep "$p" "$TEMPFILE" 2>/dev/null | head -50
                    fi
                    echo ""
                done
                rm -f "$TEMPFILE"
            fi
        fi
    fi
fi
