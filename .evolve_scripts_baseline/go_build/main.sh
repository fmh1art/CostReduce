#!/bin/bash
# Script: go_build
# Description: Build a Go project, optionally with cross-compilation (CGO_ENABLED=0). Reports build status and binary info.
# Replaces multiple commands: cd to dir, go build with flags, file/ls to verify binary.
# Usage: main.sh <project_root> [output_path] [build_pkg=.] [cgo_disabled=true|false]

PROJECT_ROOT="${1:-/app}"
OUTPUT_PATH="${2:-/tmp/build_output}"
BUILD_PKG="${3:-.}"
CGO_DISABLED="${4:-true}"

if [ ! -d "$PROJECT_ROOT" ]; then
  echo "ERROR: Project root not found: $PROJECT_ROOT"
  exit 1
fi

# Determine if this is a Go project
if [ ! -f "$PROJECT_ROOT/go.mod" ] && [ ! -f "$PROJECT_ROOT/go.sum" ]; then
  echo "WARNING: No go.mod or go.sum found at $PROJECT_ROOT. May not be a Go project."
fi

echo "=== Go Build ==="
echo "Project: $PROJECT_ROOT"
echo "Package: $BUILD_PKG"
echo "Output: $OUTPUT_PATH"
echo "CGO_ENABLED: $CGO_DISABLED"
echo ""

cd "$PROJECT_ROOT" || exit 1

# Check Go version
GO_VERSION=$(go version 2>/dev/null)
if [ $? -ne 0 ]; then
  echo "ERROR: Go is not installed or not in PATH"
  exit 1
fi
echo "Go version: $GO_VERSION"
echo ""

# Build
BUILD_CMD="go build"
[ "$CGO_DISABLED" = "true" ] && BUILD_CMD="CGO_ENABLED=0 $BUILD_CMD"
BUILD_CMD="$BUILD_CMD -o $OUTPUT_PATH $BUILD_PKG"

echo "Running: $BUILD_CMD"
echo "--- Build Output ---"
eval "$BUILD_CMD" 2>&1
BUILD_STATUS=$?

echo ""
if [ $BUILD_STATUS -eq 0 ]; then
  echo "=== Build SUCCESS ==="
  if [ -f "$OUTPUT_PATH" ]; then
    echo "Binary info:"
    file "$OUTPUT_PATH" 2>/dev/null
    ls -la "$OUTPUT_PATH" 2>/dev/null
    # Try to get version from binary if it's an executable
    if [ -x "$OUTPUT_PATH" ]; then
      VERSION_OUTPUT=$("$OUTPUT_PATH" version 2>/dev/null)
      if [ $? -eq 0 ]; then
        echo ""
        echo "Binary version output:"
        echo "$VERSION_OUTPUT"
      fi
    fi
  fi
else
  echo "=== Build FAILED (exit code: $BUILD_STATUS) ==="
fi

exit $BUILD_STATUS
