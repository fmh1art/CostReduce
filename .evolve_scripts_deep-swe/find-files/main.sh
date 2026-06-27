#!/usr/bin/env bash
# find-files: Find files by name pattern with filtering, sorting, and limiting.
# Usage: find-files [directory] [options]

set -euo pipefail

show_help() {
    cat <<'HELP_EOF'
Usage: find-files [directory] [options]

Options:
  -n, --name=PATTERN    Name glob pattern (repeatable, e.g. *.go *.ts)
  -t, --type=TYPE       File type: f (file) or d (dir) (default: f)
  -d, --max-depth=N     Max directory depth (default: unlimited)
  -l, --limit=N         Max results (default: 100, 0 = unlimited)
  --head=N              Show first N results (same as --limit)
  --tail=N              Show last N results
  -p, --path=PATH       Path glob filter
  -x, --exclude=PATTERN Exclude path pattern
  -i, --case-insensitive Case-insensitive name matching
  --no-exclude-defaults Don't auto-exclude .git/node_modules
  --sort                Sort output alphabetically
  --help, -h            Show this help

Examples:
  find-files . -n "*.go"
  find-files /project -n "*.py" -d 3 -l 20
  find-files . -n "*test*" -i --sort
  find-files . --tail=20
HELP_EOF
    exit 0
}

# Defaults
CD_DIR=""
DIR="."
NAME_PATTERNS=()
FILETYPE="f"
MAX_DEPTH=""
LIMIT=100
HEAD=""
TAIL=""
PATH_GLOB=""
EXCLUDE_PATTERNS=()
CASE_INSENSITIVE=""
NO_EXCLUDE_DEFAULTS=""
SORT=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --help|-h) show_help ;;
        --cd=*) CD_DIR="${1#*=}" ;;
        -C|--cd)
            shift
            [[ $# -lt 1 ]] && { echo "Error: --cd needs a directory" >&2; exit 1; }
            CD_DIR="$1"
            ;;
        -n|--name) shift; NAME_PATTERNS+=("$1") ;;
        --name=*) NAME_PATTERNS+=("${1#*=}") ;;
        -t|--type) shift; FILETYPE="$1" ;;
        --type=*) FILETYPE="${1#*=}" ;;
        -d|--max-depth) shift; MAX_DEPTH="$1" ;;
        --max-depth=*) MAX_DEPTH="${1#*=}" ;;
        -l|--limit) shift; LIMIT="$1" ;;
        --limit=*) LIMIT="${1#*=}" ;;
        --head=*) HEAD="${1#*=}" ;;
        --tail=*) TAIL="${1#*=}" ;;
        -p|--path) shift; PATH_GLOB="$1" ;;
        --path=*) PATH_GLOB="${1#*=}" ;;
        -x|--exclude) shift; EXCLUDE_PATTERNS+=("-not" "-path" "$1") ;;
        --exclude=*) EXCLUDE_PATTERNS+=("-not" "-path" "${1#*=}") ;;
        -i|--case-insensitive) CASE_INSENSITIVE="1" ;;
        --no-exclude-defaults) NO_EXCLUDE_DEFAULTS="1" ;;
        --sort) SORT="1" ;;
        *) DIR="$1" ;;
    esac
    shift
done

# Change directory if --cd was specified
if [[ -n "$CD_DIR" ]]; then
    cd "$CD_DIR" || { echo "Error: Cannot cd to $CD_DIR" >&2; exit 1; }
fi

# Build find command
FIND_CMD=(find "$DIR" -type "$FILETYPE")

# Add max-depth if specified
[[ -n "$MAX_DEPTH" ]] && FIND_CMD+=(-maxdepth "$MAX_DEPTH")

# Add name patterns (use OR between multiple patterns)
if [[ ${#NAME_PATTERNS[@]} -gt 0 ]]; then
    FIND_CMD+=("(")
    FIRST=1
    for PAT in "${NAME_PATTERNS[@]}"; do
        if [[ $FIRST -eq 0 ]]; then
            FIND_CMD+=(-o)
        fi
        if [[ -n "$CASE_INSENSITIVE" ]]; then
            FIND_CMD+=(-iname "$PAT")
        else
            FIND_CMD+=(-name "$PAT")
        fi
        FIRST=0
    done
    FIND_CMD+=(")")
fi

# Add path glob filter
[[ -n "$PATH_GLOB" ]] && FIND_CMD+=(-path "$PATH_GLOB")

# Add exclude patterns
[[ ${#EXCLUDE_PATTERNS[@]} -gt 0 ]] && FIND_CMD+=("${EXCLUDE_PATTERNS[@]}")

# Add default excludes
if [[ -z "$NO_EXCLUDE_DEFAULTS" ]]; then
    FIND_CMD+=(-not -path "*/.git/*" -not -path "*/node_modules/*")
fi

# Determine effective limit and output mode
USE_TAIL="$TAIL"
USE_HEAD="$HEAD"
[[ -z "$USE_HEAD" ]] && USE_HEAD="$LIMIT"

# Execute
if [[ -n "$SORT" ]]; then
    if [[ -n "$USE_TAIL" ]]; then
        "${FIND_CMD[@]}" 2>/dev/null | sort | tail -n "$USE_TAIL"
    else
        "${FIND_CMD[@]}" 2>/dev/null | sort | head -n "$USE_HEAD"
    fi
else
    if [[ -n "$USE_TAIL" ]]; then
        "${FIND_CMD[@]}" 2>/dev/null | tail -n "$USE_TAIL"
    else
        "${FIND_CMD[@]}" 2>/dev/null | head -n "$USE_HEAD"
    fi
fi
