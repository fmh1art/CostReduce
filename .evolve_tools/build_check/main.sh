#!/bin/bash
# build_check - Run build and verification commands in one step
# Usage: build_check <target_dir> [language]
#        build_check <target_dir> --build-only
#        build_check <target_dir> --vet-only
#        build_check <target_dir> --test-only
#        build_check <target_dir> --compile-only [--tags=TAGS]
#        build_check <target_dir> --ts [--filter=pattern]
#
# Runs build, vet, and optionally test on the specified package.
# Detects language from file extensions if not specified.
# Supports Go (go build/vet/test), TypeScript (npx tsc --noEmit), and Python (syntax check).
# --compile-only runs 'go test -c' to compile test files without executing tests.
# Saves steps by combining multiple verification commands into one action.
#
# Examples:
#   build_check ./pkg/tsdb/elasticsearch/client/    # Go: build + vet + test
#   build_check ./pkg/registry/apis/iam/...          # Go: build + vet + test
#   build_check ./pkg/tsdb/elasticsearch/client/ --build-only
#   build_check ./pkg/tsdb/elasticsearch/client/ --vet-only
#   build_check ./pkg/tsdb/elasticsearch/client/ --compile-only  # Go test -c (compile test files)
#   build_check ./pkg/tsdb/elasticsearch/client/ --compile-only --tags="kqueue,dev"  # With build tags
#   build_check lib/modules/manager/circleci/ --ts   # TS: npx tsc --noEmit
#   build_check lib/modules/manager/circleci/ --ts --filter=circleci  # Filter for specific errors
#   build_check file.py --python                              # Python syntax check
#   build_check src/ --python                                 # Check all .py files in dir

if [ $# -eq 0 ] || [ "$1" = "--help" ]; then
    echo "Usage: build_check <target_dir> [options]"
    echo ""
    echo "Options:"
    echo "  --build-only        Only run build (skip vet, test)"
    echo "  --vet-only          Only run vet (skip build, test)"
    echo "  --test-only         Only run tests (skip build, vet)"
    echo "  --compile-only      Only compile test files (go test -c), skip execution"
    echo "  --tags=TAGS         Go build tags (e.g., kqueue,dev,integration)"
    echo "  --goos=OS           Set GOOS for cross-compilation check"
    echo "  --goarch=ARCH       Set GOARCH for cross-compilation check"
    echo "  --ts                Run TypeScript compilation check (npx tsc --noEmit)"
    echo "  --filter=PATTERN    Filter tsc output for lines matching pattern"
    echo "  --python            Run Python syntax check (py_compile)"
    echo ""
    echo "Examples:"
    echo "  build_check ./pkg/tsdb/elasticsearch/client/"
    echo "  build_check ./pkg/registry/apis/iam/..."
    echo "  build_check ./pkg/tsdb/elasticsearch/client/ --build-only"
    echo "  build_check ./pkg/tsdb/elasticsearch/client/ --compile-only"
    echo "  build_check ./pkg/tsdb/elasticsearch/client/ --compile-only --tags=\"kqueue,dev\""
    echo "  build_check lib/modules/manager/circleci/ --ts"
    echo "  build_check lib/modules/manager/circleci/ --ts --filter=circleci"
    echo "  build_check file.py --python"
    echo "  build_check src/ --python"
    exit 0
fi

TARGET="$1"
shift

BUILD=true
VET=true
TEST=true
COMPILE_ONLY=false
GOOS=""
GOARCH=""
TS_MODE=false
TS_FILTER=""
PY_MODE=false
GO_TAGS=""

for arg in "$@"; do
    case "$arg" in
        --build-only) VET=false; TEST=false ;;
        --vet-only) BUILD=false; TEST=false ;;
        --test-only) BUILD=false; VET=false ;;
        --compile-only) BUILD=false; VET=false; TEST=false; COMPILE_ONLY=true ;;
        --goos=*) GOOS="${arg#*=}" ;;
        --goarch=*) GOARCH="${arg#*=}" ;;
        --tags=*) GO_TAGS="${arg#*=}" ;;
        --ts) TS_MODE=true ;;
        --filter=*) TS_FILTER="${arg#*=}" ;;
        --python) PY_MODE=true ;;
    esac
done

# Find repo root for proper cd
REPO_ROOT=""
if command -v git >/dev/null 2>&1; then
    REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || echo "")
fi

if [ -n "$REPO_ROOT" ]; then
    cd "$REPO_ROOT" || true
fi

# Handle TypeScript mode
if [ "$TS_MODE" = true ]; then
    echo "=== TypeScript Compilation Check ==="
    echo "Target: $TARGET"
    echo ""

    # Determine if target is a file or directory
    if [ -f "$TARGET" ]; then
        TS_FILE="$TARGET"
    elif [ -d "$TARGET" ]; then
        TS_FILE=""
    else
        # Try with .ts extension
        TS_FILE=""
    fi

    OVERALL_SUCCESS=true

    if [ -n "$TS_FILTER" ]; then
        echo "Filter: $TS_FILTER"
        echo ""
        if [ -n "$TS_FILE" ]; then
            OUTPUT=$(npx tsc --noEmit "$TS_FILE" 2>&1)
        else
            OUTPUT=$(npx tsc --noEmit 2>&1)
        fi
        RET=$?
        FILTERED=$(echo "$OUTPUT" | grep -i "$TS_FILTER" || true)
        if [ -n "$FILTERED" ]; then
            echo "TypeScript errors matching '$TS_FILTER':"
            echo "$FILTERED"
            OVERALL_SUCCESS=false
        else
            echo "No TypeScript errors matching '$TS_FILTER' found."
            if [ $RET -ne 0 ]; then
                echo "(Note: there were other compilation errors, but none matching '$TS_FILTER')"
            fi
        fi
    else
        if [ -n "$TS_FILE" ]; then
            OUTPUT=$(npx tsc --noEmit "$TS_FILE" 2>&1)
        else
            OUTPUT=$(npx tsc --noEmit 2>&1)
        fi
        RET=$?
        if [ $RET -eq 0 ]; then
            echo "TypeScript compilation OK"
        else
            echo "TypeScript compilation FAILED (exit code $RET)"
            echo "$OUTPUT" | head -50
            OVERALL_SUCCESS=false
        fi
    fi
    echo ""

    if [ "$OVERALL_SUCCESS" = true ]; then
        echo "All checks passed."
        exit 0
    else
        echo "Some checks FAILED. See above for details."
        exit 1
    fi
fi

# Handle Python mode
if [ "$PY_MODE" = true ]; then
    LANG="py"
elif echo "$TARGET" | grep -q '/\.\.\.$'; then
    LANG="go"
else
    # Check if directory has .go files
    if [ -d "$TARGET" ] && ls "$TARGET"/*.go 2>/dev/null | head -1 >/dev/null 2>&1; then
        LANG="go"
    elif [ -f "$TARGET" ] && echo "$TARGET" | grep -q '\.go$'; then
        LANG="go"
        TARGET_DIR="$(dirname "$TARGET")"
    else
        # Check for Python files
        if [ -d "$TARGET" ] && ls "$TARGET"/*.py 2>/dev/null | head -1 >/dev/null 2>&1; then
            LANG="py"
        elif [ -f "$TARGET" ] && echo "$TARGET" | grep -q '\.py$'; then
            LANG="py"
        # Check for TypeScript files
        elif [ -d "$TARGET" ] && ls "$TARGET"/*.ts 2>/dev/null | head -1 >/dev/null 2>&1; then
            LANG="ts"
        elif [ -f "$TARGET" ] && echo "$TARGET" | grep -q '\.ts$'; then
            LANG="ts"
        else
            echo "Error: Cannot detect language for $TARGET"
            echo "Supported languages: Go (.go), TypeScript (.ts), Python (.py)"
            echo "Use --ts to force TypeScript mode."
            exit 1
        fi
    fi
fi

OVERALL_SUCCESS=true

if [ "$LANG" = "go" ]; then
    # Set up GOWORK if needed
    GOWORK_OFF=""
    if [ -f "go.work" ]; then
        GOWORK_OFF="GOWORK=off"
    fi

    if [ "$COMPILE_ONLY" = true ]; then
        echo "=== go test -c $TARGET (compile only) ==="
        COMPILE_CMD="$GOWORK_OFF"
        [ -n "$GOOS" ] && COMPILE_CMD="$COMPILE_CMD GOOS=$GOOS"
        [ -n "$GOARCH" ] && COMPILE_CMD="$COMPILE_CMD GOARCH=$GOARCH"
        COMPILE_CMD="$COMPILE_CMD go test -c"
        [ -n "$GO_TAGS" ] && COMPILE_CMD="$COMPILE_CMD -tags \"$GO_TAGS\""
        COMPILE_CMD="$COMPILE_CMD ./$TARGET 2>&1"
        OUTPUT=$(eval "$COMPILE_CMD")
        RET=$?
        if [ $RET -eq 0 ]; then
            echo "TEST COMPILATION OK (all test files compile successfully)"
        else
            echo "TEST COMPILATION FAILED (exit code $RET)"
            echo "$OUTPUT"
            OVERALL_SUCCESS=false
        fi
        echo ""
    fi

    if [ "$BUILD" = true ]; then
        echo "=== go build $TARGET ==="
        BUILD_CMD="$GOWORK_OFF"
        [ -n "$GOOS" ] && BUILD_CMD="$BUILD_CMD GOOS=$GOOS"
        [ -n "$GOARCH" ] && BUILD_CMD="$BUILD_CMD GOARCH=$GOARCH"
        [ -n "$GO_TAGS" ] && BUILD_CMD="$BUILD_CMD -tags "$GO_TAGS""
        BUILD_CMD="$BUILD_CMD go build ./$TARGET 2>&1"
        OUTPUT=$(eval "$BUILD_CMD")
        RET=$?
        if [ $RET -eq 0 ]; then
            echo "BUILD OK"
        else
            echo "BUILD FAILED (exit code $RET)"
            echo "$OUTPUT"
            OVERALL_SUCCESS=false
        fi
        echo ""
    fi

    if [ "$VET" = true ]; then
        echo "=== go vet $TARGET ==="
        VET_CMD="$GOWORK_OFF go vet ./$TARGET 2>&1"
        OUTPUT=$(eval "$VET_CMD")
        RET=$?
        if [ $RET -eq 0 ]; then
            echo "VET OK"
        else
            echo "VET FAILED (exit code $RET)"
            echo "$OUTPUT"
            OVERALL_SUCCESS=false
        fi
        echo ""
    fi

    if [ "$TEST" = true ]; then
        echo "=== go test $TARGET ==="
        TEST_CMD="$GOWORK_OFF go test ./$TARGET -count=1 2>&1"
        OUTPUT=$(eval "$TEST_CMD")
        RET=$?
        if [ $RET -eq 0 ]; then
            echo "TEST OK"
        else
            echo "TEST FAILED (exit code $RET)"
            echo "$OUTPUT"
            OVERALL_SUCCESS=false
        fi
        echo ""
    fi
fi

if [ "$LANG" = "ts" ]; then
    echo "=== TypeScript Compilation Check ==="
    
    if [ -n "$TS_FILTER" ]; then
        echo "Filter: $TS_FILTER"
        echo ""
        if [ -f "$TARGET" ]; then
            OUTPUT=$(npx tsc --noEmit "$TARGET" 2>&1)
        else
            OUTPUT=$(npx tsc --noEmit 2>&1)
        fi
        RET=$?
        FILTERED=$(echo "$OUTPUT" | grep -i "$TS_FILTER" || true)
        if [ -n "$FILTERED" ]; then
            echo "TypeScript errors matching '$TS_FILTER':"
            echo "$FILTERED"
            OVERALL_SUCCESS=false
        else
            echo "No TypeScript errors matching '$TS_FILTER' found."
            if [ $RET -ne 0 ]; then
                echo "(Note: there were other compilation errors, but none matching '$TS_FILTER')"
            fi
        fi
    else
        if [ -f "$TARGET" ]; then
            OUTPUT=$(npx tsc --noEmit "$TARGET" 2>&1)
        else
            OUTPUT=$(npx tsc --noEmit 2>&1)
        fi
        RET=$?
        if [ $RET -eq 0 ]; then
            echo "TypeScript compilation OK"
        else
            echo "TypeScript compilation FAILED (exit code $RET)"
            echo "$OUTPUT" | head -50
            OVERALL_SUCCESS=false
        fi
    fi
    echo ""
fi

if [ "$LANG" = "py" ]; then
    echo "=== Python Syntax Check ==="
    echo "Target: $TARGET"
    echo ""
    
    OVERALL_SUCCESS=true
    
    if [ -d "$TARGET" ]; then
        # Find all .py files in directory
        PY_FILES=$(find "$TARGET" -name '*.py' -not -path '*/__pycache__/*' -not -path '*/.git/*' 2>/dev/null)
        if [ -z "$PY_FILES" ]; then
            echo "No .py files found in $TARGET"
        else
            ERROR_COUNT=0
            FILE_COUNT=0
            for pyfile in $PY_FILES; do
                FILE_COUNT=$((FILE_COUNT + 1))
                OUTPUT=$(python3 -m py_compile "$pyfile" 2>&1 || true)
                RET=$?
                if [ $RET -ne 0 ]; then
                    echo "SYNTAX ERROR: $pyfile"
                    echo "$OUTPUT" | head -5
                    ERROR_COUNT=$((ERROR_COUNT + 1))
                    OVERALL_SUCCESS=false
                fi
            done
            echo "Checked $FILE_COUNT Python file(s)"
            if [ $ERROR_COUNT -gt 0 ]; then
                echo "Found $ERROR_COUNT file(s) with syntax errors"
            fi
        fi
    elif [ -f "$TARGET" ]; then
        OUTPUT=$(python3 -m py_compile "$TARGET" 2>&1 || true)
        RET=$?
        if [ $RET -eq 0 ]; then
            echo "Python syntax OK"
        else
            echo "Python syntax ERROR:"
            echo "$OUTPUT"
            OVERALL_SUCCESS=false
        fi
    fi
    echo ""
fi

if [ "$OVERALL_SUCCESS" = true ]; then
    echo "All checks passed."
    exit 0
else
    echo "Some checks FAILED. See above for details."
    exit 1
fi
