#!/usr/bin/env bash
set -euo pipefail

# find_in_files - Search multiple patterns in one grep pass with filtering
# Usage: find_in_files [options] <directory_or_file> <pattern1> [pattern2 ...]
#   All patterns are combined into a single grep -e call (one pass, not per-pattern).
#   --include=*.ext       Filter by file extension/glob (repeatable)
#   --names-only          Search file names only (like find -name)
#   --name-regex=PATTERN  Filter file paths by extended regex (replaces find | grep -E pipelines)
#   --ls                  List directory contents (like ls -la)
#   -i, --ignore-case     Case-insensitive search
#   -l, --files-with-matches  List filenames only
#   -v, --exclude-pattern=PATTERN  Exclude matching lines (repeatable)
#   --exclude-path=PATTERN  Exclude matching file paths (repeatable)
#   --path=PATTERN        Include only files whose path matches pattern (uses find -path; works in content search and --names-only modes) (repeatable)
#   --max-depth=N         Max directory depth
#   --max-results=N       Max output lines (default: 50 for content search, 100 for --names-only)
#   -t, --type=TYPE       File type: f (file) or d (dir) (for --names-only)
#   --no-exclude-defaults Don't exclude .git/node_modules

DIR_OR_FILE=""
PATTERNS=()
INCLUDES=()
NAMES_ONLY=false
NAME_REGEX=""
LS_MODE=false
IGNORE_CASE=false
FILES_WITH_MATCHES=false
EXCLUDE_PATTERNS=()
EXCLUDE_PATHS=()
INCLUDE_PATHS=()
MAX_DEPTH=""
MAX_RESULTS=""
FILE_TYPE=""
PATH_FILTER=""
NO_EXCLUDE_DEFAULTS=false
LS_DIRS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --include=*)
            INCLUDES+=("${1#*=}")
            shift
            ;;
        --names-only)
            NAMES_ONLY=true
            shift
            ;;
        --name-regex=*)
            NAME_REGEX="${1#*=}"
            shift
            ;;
        --name-regex)
            NAME_REGEX="$2"
            shift 2
            ;;
        --ls)
            LS_MODE=true
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
        -v)
            EXCLUDE_PATTERNS+=("$2")
            shift 2
            ;;
        --exclude-pattern=*)
            EXCLUDE_PATTERNS+=("${1#*=}")
            shift
            ;;
        --exclude-path=*)
            EXCLUDE_PATHS+=("${1#*=}")
            shift
            ;;
        --path=*|--include-path=*)
            INCLUDE_PATHS+=("${1#*=}")
            shift
            ;;
        --path|--include-path)
            INCLUDE_PATHS+=("$2")
            shift 2
            ;;
        --max-depth=*)
            MAX_DEPTH="${1#*=}"
            shift
            ;;
        --max-results=*)
            MAX_RESULTS="${1#*=}"
            shift
            ;;
        -t|--type)
            FILE_TYPE="$2"
            shift 2
            ;;
        --type=*)
            FILE_TYPE="${1#*=}"
            shift
            ;;
        --no-exclude-defaults)
            NO_EXCLUDE_DEFAULTS=true
            shift
            ;;
        -*)
            echo "Unknown option: $1" >&2
            exit 1
            ;;
        *)
            if $LS_MODE; then
                LS_DIRS+=("$1")
            elif [[ -z "$DIR_OR_FILE" ]]; then
                DIR_OR_FILE="$1"
            else
                PATTERNS+=("$1")
            fi
            shift
            ;;
    esac
done

# ============================================================
# --ls mode: list directory contents (like ls -la)
# ============================================================
if $LS_MODE; then
    if [[ ${#LS_DIRS[@]} -eq 0 ]]; then
        LS_DIRS=(".")
    fi
    for target_dir in "${LS_DIRS[@]}"; do
        if [[ ! -d "$target_dir" ]]; then
            echo "Error: directory not found: $target_dir" >&2
            continue
        fi
        echo "=== $target_dir ==="
        python3 -c "
import os, sys, stat
root = sys.argv[1]
try:
    entries = sorted(os.listdir(root), key=lambda x: x.lower())
except PermissionError:
    print(f'Permission denied: {root}')
    sys.exit(1)

total = 0
for name in entries:
    path = os.path.join(root, name)
    try:
        st = os.stat(path)
        total += 1
        is_dir = stat.S_ISDIR(st.st_mode)
        is_link = stat.S_ISLNK(st.st_mode)
        prefix = 'd' if is_dir else ('l' if is_link else '-')
        sz = st.st_size
        if sz < 1024:
            sz_str = f'{sz:>4}B'
        elif sz < 1024*1024:
            sz_str = f'{sz/1024:>5.0f}K'
        else:
            sz_str = f'{sz/(1024*1024):>5.1f}M'
        display = name + ('/' if is_dir else '')
        print(f'{prefix} {sz_str} {display}')
    except OSError:
        print(f'?      ? {name}')
print(f'{total} entries')
" "$target_dir"
        echo
    done
    exit 0
fi

# ============================================================
# --names-only mode: find files by name glob
# ============================================================
if $NAMES_ONLY; then
    if [[ -z "$DIR_OR_FILE" ]]; then
        echo "Usage: $0 --names-only <directory> <name_glob1> [name_glob2 ...]" >&2
        exit 1
    fi
    if [[ ! -d "$DIR_OR_FILE" ]]; then
        echo "Error: directory not found: $DIR_OR_FILE" >&2
        exit 1
    fi

    # Set default limit
    if [[ -z "$MAX_RESULTS" ]]; then
        MAX_RESULTS=100
    fi

    # Build find command
    FIND_CMD=(find "$DIR_OR_FILE")
    [[ -n "$MAX_DEPTH" ]] && FIND_CMD+=(-maxdepth "$MAX_DEPTH")
    [[ -n "$FILE_TYPE" ]] && FIND_CMD+=(-type "$FILE_TYPE")

    # If --name-regex is specified, use find to discover files and grep for regex
    if [[ -n "$NAME_REGEX" ]]; then
        # Build base find with include/exclude filters but no name globs
        # --path/--include-path: use find's -path natively
        for p in "${INCLUDE_PATHS[@]}"; do
            FIND_CMD+=(-path "$p")
        done

        # Add --include filters (file extension/name glob)
        if [[ ${#INCLUDES[@]} -gt 0 ]]; then
            FIND_CMD+=( \( )
            for i in "${!INCLUDES[@]}"; do
                if [[ $i -gt 0 ]]; then
                    FIND_CMD+=(-o)
                fi
                FIND_CMD+=(-name "${INCLUDES[$i]}")
            done
            FIND_CMD+=( \) )
        fi

        # Exclude .git and node_modules by default
        if ! $NO_EXCLUDE_DEFAULTS; then
            FIND_CMD+=(! -path '*/.git/*' ! -path '*/node_modules/*' ! -path '*/venv/*' ! -path '*__pycache__*')
        fi

        # Run find and pipe through grep with the regex
        _grep_case_flag=""
        $IGNORE_CASE && _grep_case_flag="-i"
        "${FIND_CMD[@]}" 2>/dev/null | grep $_grep_case_flag -E "$NAME_REGEX" | head -n "$MAX_RESULTS" || true
        exit 0
    fi

    # --path/--include-path: use find's -path natively (AND with name patterns)
    for p in "${INCLUDE_PATHS[@]}"; do
        FIND_CMD+=(-path "$p")
    done

    # Build name matching
    if [[ ${#PATTERNS[@]} -eq 0 ]]; then
        :  # No patterns: show everything
    elif [[ ${#PATTERNS[@]} -eq 1 ]]; then
        if $IGNORE_CASE; then
            FIND_CMD+=(-iname "${PATTERNS[0]}")
        else
            FIND_CMD+=(-name "${PATTERNS[0]}")
        fi
    else
        FIND_CMD+=( \( )
        for i in "${!PATTERNS[@]}"; do
            if [[ $i -gt 0 ]]; then
                FIND_CMD+=(-o)
            fi
            if $IGNORE_CASE; then
                FIND_CMD+=(-iname "${PATTERNS[$i]}")
            else
                FIND_CMD+=(-name "${PATTERNS[$i]}")
            fi
        done
        FIND_CMD+=( \) )
    fi

    # Exclude .git and node_modules by default
    if ! $NO_EXCLUDE_DEFAULTS; then
        FIND_CMD+=(! -path '*/.git/*' ! -path '*/node_modules/*' ! -path '*/venv/*' ! -path '*__pycache__*')
    fi

    # Add --include filters as additional -name constraints (AND with existing)
    if [[ ${#INCLUDES[@]} -gt 0 ]]; then
        FIND_CMD+=( \( )
        for i in "${!INCLUDES[@]}"; do
            if [[ $i -gt 0 ]]; then
                FIND_CMD+=(-o)
            fi
            FIND_CMD+=(-name "${INCLUDES[$i]}")
        done
        FIND_CMD+=( \) )
    fi

    # Execute find
    "${FIND_CMD[@]}" 2>/dev/null | head -n "$MAX_RESULTS" || true
    exit 0
fi

# ============================================================
# Content search mode (default)
# ============================================================
if [[ -z "$DIR_OR_FILE" ]] || [[ ${#PATTERNS[@]} -eq 0 ]]; then
    echo "Usage: $0 [options] <directory_or_file> <pattern1> [pattern2 ...]" >&2
    echo "       $0 [options] --names-only <directory> <name_glob1> [name_glob2 ...]" >&2
    echo "       $0 --ls [directory ...]" >&2
    exit 1
fi

# Set default max-results for content search if not set
if [[ -z "$MAX_RESULTS" ]]; then
    MAX_RESULTS=50
fi

# Build grep flags
GREP_FLAGS="-n"
$IGNORE_CASE && GREP_FLAGS="$GREP_FLAGS -i"
$FILES_WITH_MATCHES && GREP_FLAGS="$GREP_FLAGS -l"

# Build exclude args
EXCLUDE_ARGS=""
for excl in "${EXCLUDE_PATTERNS[@]}"; do
    EXCLUDE_ARGS="$EXCLUDE_ARGS | grep -v -e $(printf '%q' "$excl")"
done

run_search() {
    # Build combined -e pattern arguments for grep
    local grep_pattern_args=""
    for pattern in "${PATTERNS[@]}"; do
        local escaped=$(printf '%q' "$pattern")
        grep_pattern_args="$grep_pattern_args -e $escaped"
    done
    local cmd=""

    # Use find pipeline if any filtering is needed (includes, exclude-paths, max-depth, include-paths, name-regex)
    if [[ ${#INCLUDES[@]} -gt 0 ]] || [[ ${#EXCLUDE_PATHS[@]} -gt 0 ]] || [[ -n "$MAX_DEPTH" ]] || [[ ${#INCLUDE_PATHS[@]} -gt 0 ]] || [[ -n "$NAME_REGEX" ]]; then
        local find_args=""
        find_args="find $(printf '%q' "$DIR_OR_FILE")"
        [[ -n "$MAX_DEPTH" ]] && find_args="$find_args -maxdepth $MAX_DEPTH"
        
        # Add --path/--include-path filters (positive path matching)
        for ipath in "${INCLUDE_PATHS[@]}"; do
            find_args="$find_args -path $(printf '%q' "$ipath")"
        done
        
        # Add --exclude-path filters
        for xpath in "${EXCLUDE_PATHS[@]}"; do
            find_args="$find_args ! -path $(printf '%q' "$xpath")"
        done
        
        # Add --include filters (file extension/name glob)
        if [[ ${#INCLUDES[@]} -gt 0 ]]; then
            find_args="$find_args \\("
            local first=true
            for inc in "${INCLUDES[@]}"; do
                if [ "$first" = true ]; then
                    find_args="$find_args -name $(printf '%q' "$inc")"
                    first=false
                else
                    find_args="$find_args -o -name $(printf '%q' "$inc")"
                fi
            done
            find_args="$find_args \\)"
        fi
        find_args="$find_args 2>/dev/null"
        
        # Add --name-regex filtering (pipe find output through grep -E)
        local cmd_prefix="$find_args"
        if [[ -n "$NAME_REGEX" ]]; then
            local grep_case_flag=""
            $IGNORE_CASE && grep_case_flag="-i"
            cmd_prefix="$cmd_prefix | grep $grep_case_flag -E $(printf '%q' "$NAME_REGEX")"
        fi
        cmd="$cmd_prefix | xargs grep $GREP_FLAGS $grep_pattern_args 2>/dev/null"
    else
        local dir_escaped=$(printf '%q' "$DIR_OR_FILE")
        if [[ -f "$DIR_OR_FILE" ]]; then
            cmd="grep $GREP_FLAGS $grep_pattern_args $dir_escaped 2>/dev/null"
        else
            cmd="grep $GREP_FLAGS -r $grep_pattern_args $dir_escaped 2>/dev/null"
        fi
    fi

    cmd="$cmd $EXCLUDE_ARGS"
    cmd="$cmd | head -n $MAX_RESULTS"

    eval "$cmd" || true
}

run_search
