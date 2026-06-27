#!/bin/bash
# explore_project - Quickly explore a project's structure
# Usage: explore_project <project_root> [lang] [max_files] [--subdir <path>]

PROJECT_ROOT="${1:-.}"
LANG="${2:-auto}"
MAX_FILES="${3:-50}"
SUBDIR=""

# Parse optional flags
shift 3 2>/dev/null || true
while [ $# -gt 0 ]; do
    case "$1" in
        --subdir)
            SUBDIR="$2"
            shift 2
            ;;
        *)
            shift
            ;;
    esac
done

if [ ! -d "$PROJECT_ROOT" ]; then
    echo "Error: Directory '$PROJECT_ROOT' does not exist"
    exit 1
fi

cd "$PROJECT_ROOT" || exit 1

# Determine the search root
if [ -n "$SUBDIR" ]; then
    SEARCH_ROOT="$SUBDIR"
    if [ ! -d "$SEARCH_ROOT" ]; then
        echo "Error: Subdirectory '$SEARCH_ROOT' does not exist"
        exit 1
    fi
else
    SEARCH_ROOT="."
fi

echo "=== Project: $(basename "$(pwd)") ===="
echo "Root: $(pwd)"
if [ -n "$SUBDIR" ]; then
    echo "Exploring subdirectory: $SUBDIR"
fi
echo ""

# Build directories to exclude (common build artifact dirs)
EXCLUDE_DIRS=(
    -not -path '*/target/*'
    -not -path '*/build/*'
    -not -path '*/dist/*'
    -not -path '*/.git/*'
    -not -path '*/node_modules/*'
    -not -path '*/.venv/*'
    -not -path '*/.pnpm/*'
    -not -path '*/__pycache__/*'
    -not -path '*/vendor/*'
)

# Detect language if auto
if [ "$LANG" = "auto" ]; then
    py_count=$(find "$SEARCH_ROOT" -maxdepth 4 -name '*.py' "${EXCLUDE_DIRS[@]}" 2>/dev/null | wc -l)
    go_count=$(find "$SEARCH_ROOT" -maxdepth 4 -name '*.go' "${EXCLUDE_DIRS[@]}" 2>/dev/null | wc -l)
    c_count=$(find "$SEARCH_ROOT" -maxdepth 4 -name '*.c' "${EXCLUDE_DIRS[@]}" 2>/dev/null | wc -l)
    h_count=$(find "$SEARCH_ROOT" -maxdepth 4 -name '*.h' "${EXCLUDE_DIRS[@]}" 2>/dev/null | wc -l)
    rs_count=$(find "$SEARCH_ROOT" -maxdepth 4 -name '*.rs' "${EXCLUDE_DIRS[@]}" 2>/dev/null | wc -l)
    ts_count=$(find "$SEARCH_ROOT" -maxdepth 4 \( -name '*.ts' -o -name '*.tsx' \) "${EXCLUDE_DIRS[@]}" 2>/dev/null | wc -l)
    
    if [ "$py_count" -gt 0 ] && [ "$py_count" -ge "$go_count" ] && [ "$py_count" -ge "$rs_count" ] && [ "$py_count" -ge "$ts_count" ]; then
        LANG="py"
    elif [ "$go_count" -gt 0 ] && [ "$go_count" -ge "$rs_count" ] && [ "$go_count" -ge "$ts_count" ]; then
        LANG="go"
    elif [ "$rs_count" -gt 0 ]; then
        LANG="rs"
    elif [ "$ts_count" -gt 0 ]; then
        LANG="ts"
    elif [ "$c_count" -gt 0 ] || [ "$h_count" -gt 0 ]; then
        LANG="c"
    else
        LANG="py"
    fi
fi

echo "Detected Language: $LANG"
echo ""

# Show subdirectory structure if not exploring root
if [ -n "$SUBDIR" ]; then
    echo "=== Directory structure of $SUBDIR ==="
    find "$SEARCH_ROOT" -maxdepth 2 -type d "${EXCLUDE_DIRS[@]}" 2>/dev/null | sort | head -30
    echo ""
fi

# Top-level directories (only show if exploring root)
if [ -z "$SUBDIR" ]; then
    echo "=== Top-level directories ==="
    for d in */; do
        dname="${d%/}"
        case "$dname" in
            target|build|dist|node_modules|.git|.venv|__pycache__|.pnpm|vendor)
                ;;
            *)
                echo "  $d"
                ;;
        esac
    done | head -20
    echo ""
fi

# File counts
echo "=== File counts by type ==="
case "$LANG" in
    py)
        src_count=$(find "$SEARCH_ROOT" -name '*.py' "${EXCLUDE_DIRS[@]}" 2>/dev/null | wc -l)
        echo "  Python (.py): $src_count"
        ;;
    go)
        src_count=$(find "$SEARCH_ROOT" -name '*.go' "${EXCLUDE_DIRS[@]}" 2>/dev/null | wc -l)
        echo "  Go (.go): $src_count"
        ;;
    c)
        c_src=$(find "$SEARCH_ROOT" -name '*.c' "${EXCLUDE_DIRS[@]}" 2>/dev/null | wc -l)
        h_src=$(find "$SEARCH_ROOT" -name '*.h' "${EXCLUDE_DIRS[@]}" 2>/dev/null | wc -l)
        echo "  C (.c): $c_src"
        echo "  Header (.h): $h_src"
        ;;
    rs)
        rs_src=$(find "$SEARCH_ROOT" -name '*.rs' "${EXCLUDE_DIRS[@]}" 2>/dev/null | wc -l)
        echo "  Rust (.rs): $rs_src"
        ;;
    ts)
        ts_src=$(find "$SEARCH_ROOT" \( -name '*.ts' -o -name '*.tsx' \) "${EXCLUDE_DIRS[@]}" 2>/dev/null | wc -l)
        echo "  TypeScript (.ts/.tsx): $ts_src"
        ;;
esac

# Source files
echo ""
echo "=== Source files (up to $MAX_FILES) ==="
case "$LANG" in
    py) find "$SEARCH_ROOT" -name '*.py' "${EXCLUDE_DIRS[@]}" -not -path '*/__pycache__/*' 2>/dev/null | sort | head -"$MAX_FILES" ;;
    go) find "$SEARCH_ROOT" -name '*.go' "${EXCLUDE_DIRS[@]}" 2>/dev/null | sort | head -"$MAX_FILES" ;;
    c)  find "$SEARCH_ROOT" \( -name '*.c' -o -name '*.h' \) "${EXCLUDE_DIRS[@]}" 2>/dev/null | sort | head -"$MAX_FILES" ;;
    rs) find "$SEARCH_ROOT" -name '*.rs' "${EXCLUDE_DIRS[@]}" 2>/dev/null | sort | head -"$MAX_FILES" ;;
    ts) find "$SEARCH_ROOT" \( -name '*.ts' -o -name '*.tsx' \) "${EXCLUDE_DIRS[@]}" 2>/dev/null | sort | head -"$MAX_FILES" ;;
esac
echo ""

# Config files (only if exploring root)
if [ -z "$SUBDIR" ]; then
    echo "=== Config files ==="
    case "$LANG" in
        py)
            for f in setup.py setup.cfg pyproject.toml requirements.txt Pipfile Makefile; do
                [ -f "$f" ] && echo "  $f"
            done
            ;;
        go)
            for f in go.mod go.sum Makefile; do
                [ -f "$f" ] && echo "  $f"
            done
            ;;
        c)
            for f in Makefile CMakeLists.txt configure; do
                [ -f "$f" ] && echo "  $f"
            done
            ;;
        rs)
            for f in Cargo.toml Cargo.lock Makefile; do
                [ -f "$f" ] && echo "  $f"
            done
            if [ -f "Cargo.toml" ]; then
                echo ""
                echo "  Cargo.toml name: $(grep '^name' Cargo.toml 2>/dev/null | head -1)"
                echo "  Cargo.toml edition: $(grep '^edition' Cargo.toml 2>/dev/null | head -1)"
                if grep -q '\[workspace\]' Cargo.toml 2>/dev/null; then
                    echo "  Workspace members:"
                    grep 'members' Cargo.toml -A 10 2>/dev/null | grep -E '^\s*"' | sed 's/^/    /'
                fi
            fi
            ;;
        ts)
            for f in package.json tsconfig.json .eslintrc jest.config.js Makefile; do
                [ -f "$f" ] && echo "  $f"
            done
            if [ -f "package.json" ]; then
                echo ""
                echo "  package.json name: $(grep '"name"' package.json 2>/dev/null | head -1)"
            fi
            ;;
    esac
fi

# Key subdirectories (only if exploring root)
if [ -z "$SUBDIR" ]; then
    echo ""
    echo "=== Key subdirectories ==="
    case "$LANG" in
        py)
            for d in src lib app tests test; do
                [ -d "$d" ] && echo "  $d/ ($(find "$d" -type f 2>/dev/null | wc -l) files)"
            done
            ;;
        go)
            for d in cmd pkg internal api tests test; do
                [ -d "$d" ] && echo "  $d/ ($(find "$d" -type f -name '*.go' 2>/dev/null | wc -l) files)"
            done
            ;;
        c)
            for d in src lib include tests test; do
                [ -d "$d" ] && echo "  $d/ ($(find "$d" -type f 2>/dev/null | wc -l) files)"
            done
            ;;
        rs)
            for d in src tests test examples core cli engine parser ast; do
                [ -d "$d" ] && echo "  $d/ ($(find "$d" -type f -name '*.rs' "${EXCLUDE_DIRS[@]}" 2>/dev/null | wc -l) files)"
            done
            ;;
        ts)
            for d in src lib app tests test components pages; do
                [ -d "$d" ] && echo "  $d/ ($(find "$d" -type f 2>/dev/null | wc -l) files)"
            done
            ;;
    esac
fi
