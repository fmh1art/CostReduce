#!/bin/bash
# Script: rust_build
# Description: Build a Rust project with cargo. Runs 'cargo build' with optional release mode, then reports build status.
# Replaces multiple commands: cd to dir, cargo build, checking output.
# Usage: main.sh <project_root> [mode=debug|release] [package=default]

PROJECT_ROOT="${1:-/app}"
MODE="${2:-debug}"
PACKAGE="${3:-}"

if [ ! -d "$PROJECT_ROOT" ]; then
  echo "ERROR: Project root not found: $PROJECT_ROOT"
  exit 1
fi

# Determine if this is a Rust project
if [ ! -f "$PROJECT_ROOT/Cargo.toml" ]; then
  echo "WARNING: No Cargo.toml found at $PROJECT_ROOT. May not be a Rust project."
fi

echo "=== Rust Build ==="
echo "Project: $PROJECT_ROOT"
echo "Mode: $MODE"
[ -n "$PACKAGE" ] && echo "Package: $PACKAGE"
echo ""

cd "$PROJECT_ROOT" || exit 1

# Check Rust version
RUSTC_VERSION=$(rustc --version 2>/dev/null)
if [ $? -ne 0 ]; then
  echo "ERROR: Rust is not installed or not in PATH"
  exit 1
fi
echo "Rust version: $RUSTC_VERSION"
CARGO_VERSION=$(cargo --version 2>/dev/null)
echo "Cargo version: $CARGO_VERSION"
echo ""

# Build
BUILD_CMD="cargo build"
if [ "$MODE" = "release" ]; then
  BUILD_CMD="$BUILD_CMD --release"
fi
if [ -n "$PACKAGE" ]; then
  BUILD_CMD="$BUILD_CMD -p $PACKAGE"
fi

echo "Running: $BUILD_CMD"
echo "--- Build Output ---"
eval "$BUILD_CMD" 2>&1
BUILD_STATUS=$?

echo ""
if [ $BUILD_STATUS -eq 0 ]; then
  echo "=== Build SUCCESS ==="
  # Find the built binary
  TARGET_DIR="$PROJECT_ROOT/target"
  if [ "$MODE" = "release" ]; then
    BUILD_DIR="release"
  else
    BUILD_DIR="debug"
  fi
  # Try to find binaries in target
  BINARIES=$(find "$TARGET_DIR/$BUILD_DIR" -maxdepth 1 -type f -executable 2>/dev/null | head -5)
  if [ -n "$BINARIES" ]; then
    echo ""
    echo "Built binaries:"
    for b in $BINARIES; do
      ls -la "$b" 2>/dev/null
      file "$b" 2>/dev/null
    done
  fi
else
  echo "=== Build FAILED (exit code: $BUILD_STATUS) ==="
fi

exit $BUILD_STATUS
