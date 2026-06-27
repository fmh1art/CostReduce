#!/bin/bash
# Script: explore_project
# Description: Quickly explore a project's structure - find source files, list key directories, count files
# Usage: main.sh <project_root> [lang=auto|py|go|c|rs|ts|js] [max_files=50]

PROJECT_ROOT="${1:-/app}"
LANG="${2:-auto}"
MAX_FILES="${3:-50}"

# Auto-detect language
if [ "$LANG" = "auto" ]; then
  PY_COUNT=$(find "$PROJECT_ROOT" -type f -name "*.py" 2>/dev/null | grep -v venv | grep -v __pycache__ | grep -v .venv | wc -l)
  GO_COUNT=$(find "$PROJECT_ROOT" -type f -name "*.go" 2>/dev/null | grep -v vendor | wc -l)
  C_COUNT=$(find "$PROJECT_ROOT" -type f -name "*.c" 2>/dev/null | wc -l)
  H_COUNT=$(find "$PROJECT_ROOT" -type f -name "*.h" 2>/dev/null | wc -l)
  RS_COUNT=$(find "$PROJECT_ROOT" -type f -name "*.rs" 2>/dev/null | grep -v target | wc -l)
  TS_COUNT=$(find "$PROJECT_ROOT" -type f -name "*.ts" 2>/dev/null | grep -v node_modules | wc -l)
  JS_COUNT=$(find "$PROJECT_ROOT" -type f -name "*.js" 2>/dev/null | grep -v node_modules | wc -l)
  if [ "$RS_COUNT" -gt "$PY_COUNT" ] && [ "$RS_COUNT" -gt "$GO_COUNT" ] && [ "$RS_COUNT" -gt "$C_COUNT" ] && [ "$RS_COUNT" -gt "$TS_COUNT" ] && [ "$RS_COUNT" -gt "$JS_COUNT" ]; then
    LANG="rs"
  elif [ "$TS_COUNT" -gt "$PY_COUNT" ] && [ "$TS_COUNT" -gt "$GO_COUNT" ] && [ "$TS_COUNT" -gt "$C_COUNT" ] && [ "$TS_COUNT" -gt "$RS_COUNT" ] && [ "$TS_COUNT" -gt "$JS_COUNT" ]; then
    LANG="ts"
  elif [ "$JS_COUNT" -gt "$PY_COUNT" ] && [ "$JS_COUNT" -gt "$GO_COUNT" ] && [ "$JS_COUNT" -gt "$C_COUNT" ] && [ "$JS_COUNT" -gt "$RS_COUNT" ] && [ "$JS_COUNT" -gt "$TS_COUNT" ]; then
    LANG="js"
  elif [ "$C_COUNT" -gt "$PY_COUNT" ] && [ "$C_COUNT" -gt "$GO_COUNT" ]; then
    LANG="c"
  elif [ "$GO_COUNT" -gt "$PY_COUNT" ]; then
    LANG="go"
  else
    LANG="py"
  fi
fi

echo "=== Project Structure Overview ==="
echo "Project root: $PROJECT_ROOT"
echo "Language: $LANG"
echo ""

# Find source files (limited)
case "$LANG" in
  py)
    echo "--- Source Files (*.py, up to $MAX_FILES) ---"
    find "$PROJECT_ROOT" -type f -name "*.py" | grep -v venv | grep -v __pycache__ | grep -v .venv | head -"$MAX_FILES"
    TOTAL=$(find "$PROJECT_ROOT" -type f -name "*.py" | grep -v venv | grep -v __pycache__ | grep -v .venv | wc -l)
    echo ""
    echo "Total *.py files: $TOTAL"
    ;;
  go)
    echo "--- Source Files (*.go, up to $MAX_FILES) ---"
    find "$PROJECT_ROOT" -type f -name "*.go" | grep -v vendor | head -"$MAX_FILES"
    TOTAL=$(find "$PROJECT_ROOT" -type f -name "*.go" | grep -v vendor | wc -l)
    echo ""
    echo "Total *.go files: $TOTAL"
    ;;
  c)
    echo "--- Source Files (*.c, *.h, up to $MAX_FILES) ---"
    find "$PROJECT_ROOT" -type f \( -name "*.c" -o -name "*.h" \) | grep -v 3rdparty | head -"$MAX_FILES"
    C_TOTAL=$(find "$PROJECT_ROOT" -type f -name "*.c" | grep -v 3rdparty | wc -l)
    H_TOTAL=$(find "$PROJECT_ROOT" -type f -name "*.h" | grep -v 3rdparty | wc -l)
    echo ""
    echo "Total *.c files: $C_TOTAL"
    echo "Total *.h files: $H_TOTAL"
    TOTAL=$((C_TOTAL + H_TOTAL))
    echo "Combined: $TOTAL"
    ;;
  rs)
    echo "--- Source Files (*.rs, up to $MAX_FILES) ---"
    find "$PROJECT_ROOT" -type f -name "*.rs" | grep -v target | head -"$MAX_FILES"
    TOTAL=$(find "$PROJECT_ROOT" -type f -name "*.rs" | grep -v target | wc -l)
    echo ""
    echo "Total *.rs files: $TOTAL"
    echo ""
    echo "--- Cargo Config ---"
    if [ -f "$PROJECT_ROOT/Cargo.toml" ]; then
      echo "Found: Cargo.toml ($(wc -l < "$PROJECT_ROOT/Cargo.toml") lines)"
    fi
    if [ -f "$PROJECT_ROOT/Cargo.lock" ]; then
      echo "Found: Cargo.lock ($(wc -l < "$PROJECT_ROOT/Cargo.lock") lines)"
    fi
    if [ -d "$PROJECT_ROOT/src" ]; then
      echo "Has src/ directory"
    fi
    ;;
  ts)
    echo "--- Source Files (*.ts, *.tsx, up to $MAX_FILES) ---"
    find "$PROJECT_ROOT" -type f \( -name "*.ts" -o -name "*.tsx" \) | grep -v node_modules | head -"$MAX_FILES"
    TS_TOTAL=$(find "$PROJECT_ROOT" -type f -name "*.ts" | grep -v node_modules | wc -l)
    TSX_TOTAL=$(find "$PROJECT_ROOT" -type f -name "*.tsx" | grep -v node_modules | wc -l)
    echo ""
    echo "Total *.ts files: $TS_TOTAL"
    echo "Total *.tsx files: $TSX_TOTAL"
    TOTAL=$((TS_TOTAL + TSX_TOTAL))
    echo "Combined: $TOTAL"
    echo ""
    echo "--- Package Config ---"
    for f in "$PROJECT_ROOT/package.json" "$PROJECT_ROOT/tsconfig.json" "$PROJECT_ROOT/pnpm-lock.yaml" "$PROJECT_ROOT/yarn.lock" "$PROJECT_ROOT/package-lock.json"; do
      if [ -f "$f" ]; then
        echo "Found: $f ($(wc -l < "$f") lines)"
      fi
    done
    ;;
  js)
    echo "--- Source Files (*.js, *.jsx, up to $MAX_FILES) ---"
    find "$PROJECT_ROOT" -type f \( -name "*.js" -o -name "*.jsx" \) | grep -v node_modules | head -"$MAX_FILES"
    JS_TOTAL=$(find "$PROJECT_ROOT" -type f -name "*.js" | grep -v node_modules | wc -l)
    JSX_TOTAL=$(find "$PROJECT_ROOT" -type f -name "*.jsx" | grep -v node_modules | wc -l)
    echo ""
    echo "Total *.js files: $JS_TOTAL"
    echo "Total *.jsx files: $JSX_TOTAL"
    TOTAL=$((JS_TOTAL + JSX_TOTAL))
    echo "Combined: $TOTAL"
    echo ""
    echo "--- Package Config ---"
    for f in "$PROJECT_ROOT/package.json" "$PROJECT_ROOT/.eslintrc.js" "$PROJECT_ROOT/.eslintrc.json" "$PROJECT_ROOT/.prettierrc" "$PROJECT_ROOT/pnpm-lock.yaml" "$PROJECT_ROOT/yarn.lock" "$PROJECT_ROOT/package-lock.json"; do
      if [ -f "$f" ]; then
        echo "Found: $f ($(wc -l < "$f") lines)"
      fi
    done
    ;;
esac
echo ""

# Count files by type for top-level overview
echo "--- File Type Counts (up to depth 3) ---"
for ext in py go c h sh yaml yml json toml mod sum rs ts tsx js jsx; do
  count=$(find "$PROJECT_ROOT" -maxdepth 3 -type f -name "*.$ext" 2>/dev/null | grep -v venv | grep -v __pycache__ | grep -v .venv | grep -v node_modules | grep -v target | wc -l)
  if [ "$count" -gt 0 ]; then
    printf "  *.%s: %d\n" "$ext" "$count"
  fi
done
echo ""

# List top-level directories
echo "--- Top-Level Directory Listing ---"
ls -la "$PROJECT_ROOT/" 2>/dev/null
echo ""

# Show key config files
echo "--- Config/Env Files ---"
case "$LANG" in
  py)
    for f in "$PROJECT_ROOT/example.env" "$PROJECT_ROOT/.env" "$PROJECT_ROOT/.flaskenv" "$PROJECT_ROOT/setup.cfg" "$PROJECT_ROOT/pyproject.toml" "$PROJECT_ROOT/requirements.txt" "$PROJECT_ROOT/Makefile"; do
      if [ -f "$f" ]; then
        echo "Found: $f ($(wc -l < "$f") lines)"
      fi
    done
    ;;
  go)
    for f in "$PROJECT_ROOT/go.mod" "$PROJECT_ROOT/go.sum" "$PROJECT_ROOT/Makefile" "$PROJECT_ROOT/Dockerfile" "$PROJECT_ROOT/.golangci.yml" "$PROJECT_ROOT/pyproject.toml"; do
      if [ -f "$f" ]; then
        echo "Found: $f ($(wc -l < "$f") lines)"
      fi
    done
    ;;
  c)
    for f in "$PROJECT_ROOT/Makefile" "$PROJECT_ROOT/CMakeLists.txt" "$PROJECT_ROOT/configure" "$PROJECT_ROOT/configure.ac" "$PROJECT_ROOT/Dockerfile" "$PROJECT_ROOT/.editorconfig" "$PROJECT_ROOT/pyproject.toml" "$PROJECT_ROOT/Makefile.am"; do
      if [ -f "$f" ]; then
        echo "Found: $f ($(wc -l < "$f") lines)"
      fi
    done
    ;;
  rs)
    for f in "$PROJECT_ROOT/Cargo.toml" "$PROJECT_ROOT/Cargo.lock" "$PROJECT_ROOT/rust-toolchain" "$PROJECT_ROOT/rust-toolchain.toml" "$PROJECT_ROOT/Makefile" "$PROJECT_ROOT/Dockerfile" "$PROJECT_ROOT/.cargo/config.toml"; do
      if [ -f "$f" ]; then
        echo "Found: $f ($(wc -l < "$f") lines)"
      fi
    done
    ;;
  ts)
    for f in "$PROJECT_ROOT/package.json" "$PROJECT_ROOT/tsconfig.json" "$PROJECT_ROOT/.eslintrc.js" "$PROJECT_ROOT/.eslintrc.json" "$PROJECT_ROOT/.prettierrc" "$PROJECT_ROOT/vite.config.ts" "$PROJECT_ROOT/jest.config.ts" "$PROJECT_ROOT/Makefile" "$PROJECT_ROOT/Dockerfile"; do
      if [ -f "$f" ]; then
        echo "Found: $f ($(wc -l < "$f") lines)"
      fi
    done
    ;;
  js)
    for f in "$PROJECT_ROOT/package.json" "$PROJECT_ROOT/.eslintrc.js" "$PROJECT_ROOT/.eslintrc.json" "$PROJECT_ROOT/.prettierrc" "$PROJECT_ROOT/Makefile" "$PROJECT_ROOT/Dockerfile" "$PROJECT_ROOT/.editorconfig"; do
      if [ -f "$f" ]; then
        echo "Found: $f ($(wc -l < "$f") lines)"
      fi
    done
    ;;
esac
echo ""

# Show key app directories
echo "--- App/Service Directories ---"
for d in "$PROJECT_ROOT/app" "$PROJECT_ROOT/src" "$PROJECT_ROOT/pkg" "$PROJECT_ROOT/cmd" "$PROJECT_ROOT/internal" "$PROJECT_ROOT/kitty" "$PROJECT_ROOT/tools" "$PROJECT_ROOT/crates" "$PROJECT_ROOT/core"; do
  if [ -d "$d" ]; then
    echo "Contents of $d/:"
    ls -la "$d/" 2>/dev/null | head -20
    echo ""
  fi
done
