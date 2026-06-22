#!/bin/bash
# project_structure - Explore a project's structure efficiently
# Usage: main.sh [directory] [extension_filter]
#   directory: path to explore (default: .)
#   extension_filter: optional, only show files with this extension (e.g., "rs", "ts", "py")
# Combines ls -la and find to give a compact overview.
# Also detects if the directory is a git repo (.git presence).

DIR="${1:-.}"
EXT_FILTER="${2:-}"

# Resolve to absolute path
ABS_DIR=$(cd "$DIR" 2>/dev/null && pwd) || { echo "ERROR: Cannot access $DIR"; exit 1; }

echo "=== Project Structure: $ABS_DIR ==="

# Detect git repo
if [ -d "$ABS_DIR/.git" ]; then
    echo ">>> Git repository detected <<<"
elif git -C "$ABS_DIR" rev-parse --git-dir > /dev/null 2>&1; then
    echo ">>> Git repository detected (worktree) <<<"
fi
echo ""

# Show top-level contents
echo "--- Top-level files/dirs ---"
ls -la "$DIR" 2>/dev/null | head -30

echo ""

# Function to find files by extension, excluding noise dirs
find_files() {
    local ext="$1"
    local dir="$2"
    find "$dir" -name "*.$ext" \
        -not -path '*/node_modules/*' \
        -not -path '*/.git/*' \
        -not -path '*/vendor/*' \
        -not -path '*/dist/*' \
        -not -path '*/.next/*' \
        -not -path '*/.venv/*' \
        -not -path '*/__pycache__/*' \
        -not -path '*/target/*' \
        -not -path '*/build/*' \
        -not -path '*/bin/*' \
        -not -path '*/obj/*' \
        2>/dev/null | sort
}

# If extension filter is given, only show that type
if [ -n "$EXT_FILTER" ]; then
    EXT="${EXT_FILTER#.}"  # Remove leading dot if present
    echo "--- Files (*.$EXT) ---"
    FILES=$(find_files "$EXT" "$DIR")
    if [ -n "$FILES" ]; then
        echo "$FILES" | head -80
        COUNT=$(echo "$FILES" | wc -l)
        echo ""
        echo "--- Total *.$EXT files: $COUNT ---"
    else
        echo "(No *.$EXT files found)"
    fi
    exit 0
fi

# Auto-detect and show source files by common extensions
echo "--- Source files ---"

# Go files
GO_FILES=$(find_files "go" "$DIR")
if [ -n "$GO_FILES" ]; then
    GO_COUNT=$(echo "$GO_FILES" | wc -l)
    echo "Go files ($GO_COUNT):"
    echo "$GO_FILES" | head -40
    echo ""
fi

# TypeScript/JavaScript files
TSJS_FILES=$(find_files "ts" "$DIR"; find_files "tsx" "$DIR"; find_files "js" "$DIR"; find_files "jsx" "$DIR")
if [ -n "$TSJS_FILES" ]; then
    TS_COUNT=$(echo "$TSJS_FILES" | wc -l)
    echo "TS/JS files ($TS_COUNT):"
    echo "$TSJS_FILES" | head -40
    echo ""
fi

# Python files
PY_FILES=$(find_files "py" "$DIR")
if [ -n "$PY_FILES" ]; then
    PY_COUNT=$(echo "$PY_FILES" | wc -l)
    echo "Python files ($PY_COUNT):"
    echo "$PY_FILES" | head -40
    echo ""
fi

# Rust files
RS_FILES=$(find_files "rs" "$DIR")
if [ -n "$RS_FILES" ]; then
    RS_COUNT=$(echo "$RS_FILES" | wc -l)
    echo "Rust files ($RS_COUNT):"
    echo "$RS_FILES" | head -40
    echo ""
fi

# Java/Kotlin files
JAVA_FILES=$(find_files "java" "$DIR"; find_files "kt" "$DIR")
if [ -n "$JAVA_FILES" ]; then
    JAVA_COUNT=$(echo "$JAVA_FILES" | wc -l)
    echo "Java/Kotlin files ($JAVA_COUNT):"
    echo "$JAVA_FILES" | head -20
    echo ""
fi

# C/C++ files
CPP_FILES=$(find_files "c" "$DIR"; find_files "h" "$DIR"; find_files "cpp" "$DIR"; find_files "hpp" "$DIR")
if [ -n "$CPP_FILES" ]; then
    CPP_COUNT=$(echo "$CPP_FILES" | wc -l)
    echo "C/C++ files ($CPP_COUNT):"
    echo "$CPP_FILES" | head -20
    echo ""
fi

# Ruby files
RB_FILES=$(find_files "rb" "$DIR")
if [ -n "$RB_FILES" ]; then
    RB_COUNT=$(echo "$RB_FILES" | wc -l)
    echo "Ruby files ($RB_COUNT):"
    echo "$RB_FILES" | head -20
    echo ""
fi

# Swift files
SWIFT_FILES=$(find_files "swift" "$DIR")
if [ -n "$SWIFT_FILES" ]; then
    SWIFT_COUNT=$(echo "$SWIFT_FILES" | wc -l)
    echo "Swift files ($SWIFT_COUNT):"
    echo "$SWIFT_FILES" | head -20
    echo ""
fi

# PHP files
PHP_FILES=$(find_files "php" "$DIR")
if [ -n "$PHP_FILES" ]; then
    PHP_COUNT=$(echo "$PHP_FILES" | wc -l)
    echo "PHP files ($PHP_COUNT):"
    echo "$PHP_FILES" | head -20
    echo ""
fi

# Package/config files
echo "--- Config files ---"
for f in "Cargo.toml" "package.json" "go.mod" "pyproject.toml" "setup.py" "Makefile" "CMakeLists.txt" "Gemfile" "build.gradle" "pom.xml"; do
    if [ -f "$DIR/$f" ]; then
        echo "Found: $f"
    fi
done

# Show directory structure (depth 2) for concise overview
echo ""
echo "--- Directory tree (depth 2) ---"
find "$DIR" -maxdepth 2 -type d \
    -not -path '*/node_modules/*' \
    -not -path '*/.git/*' \
    -not -path '*/vendor/*' \
    -not -path '*/target/*' \
    -not -path '*/dist/*' \
    -not -path '*/.next/*' \
    -not -path '*/__pycache__/*' \
    2>/dev/null | sort | head -40
