#!/bin/bash
# run_test - Detect and run tests for a project
# Usage: main.sh [directory] [test_pattern_or_file] [--timeout=<seconds>] [--output-limit=<lines>] [--tags=<build_tags>] [--env=<key=val,...>]
#   directory: project directory (default: .)
#   test_pattern_or_file: optional, test name pattern, specific test file path, or test function name
#   --timeout: optional, timeout in seconds (default: 120, use 0 for no timeout)
#   --output-limit: optional, max lines of output to show (default: 150, use 0 for unlimited)
#   --tags: optional, Go build tags (e.g., "kqueue,dev" for `-tags kqueue,dev`)
#   --env: optional, comma-separated KEY=VAL environment variables (e.g., "CGO_ENABLED=0,DEBUG=1")
# Auto-detects the test framework and runs tests.
# Supports: Go, Node/TypeScript (jest, vitest, mocha), Python (pytest, unittest),
#           Rust (cargo), Java (Maven, Gradle)

DIR=""
TEST_SPEC=""
TIMEOUT=120
OUTPUT_LIMIT=150
BUILD_TAGS=""
ENV_VARS=""

# Parse arguments
while [ $# -gt 0 ]; do
    case "$1" in
        --timeout=*)
            TIMEOUT="${1#*=}"
            ;;
        --output-limit=*)
            OUTPUT_LIMIT="${1#*=}"
            ;;
        --tags=*)
            BUILD_TAGS="${1#*=}"
            ;;
        --env=*)
            ENV_VARS="${1#*=}"
            ;;
        *)
            if [ -z "$DIR" ]; then
                DIR="$1"
            elif [ -z "$TEST_SPEC" ]; then
                TEST_SPEC="$1"
            fi
            ;;
    esac
    shift
done

DIR="${DIR:-.}"

cd "$DIR" || { echo "ERROR: Cannot cd to $DIR"; exit 1; }

echo "=== Detecting test framework in $(pwd) ==="
echo ""

# Output limiting helper
output_with_limit() {
    if [ "$OUTPUT_LIMIT" = "0" ]; then
        cat
    else
        tmpfile=$(mktemp)
        cat > "$tmpfile"
        total=$(wc -l < "$tmpfile")
        head -"$OUTPUT_LIMIT" "$tmpfile"
        if [ "$total" -gt "$OUTPUT_LIMIT" ]; then
            echo "... (output truncated, $total lines total)"
        fi
        rm -f "$tmpfile"
    fi
}

# Build env prefix string. Returns something like: "CGO_ENABLED=0 DEBUG=1"
build_env_prefix() {
    local prefix=""
    if [ -n "$ENV_VARS" ]; then
        local saved_ifs="$IFS"
        IFS=','
        for var in $ENV_VARS; do
            prefix="$prefix $var"
        done
        IFS="$saved_ifs"
    fi
    echo "$prefix"
}

# Run a command with optional timeout and env vars
run_cmd() {
    env_prefix=$(build_env_prefix)
    if [ "$TIMEOUT" != "0" ]; then
        if [ -n "$env_prefix" ]; then
            eval "$env_prefix timeout $TIMEOUT \"\$@\"" 2>&1 | output_with_limit
        else
            timeout "$TIMEOUT" "$@" 2>&1 | output_with_limit
        fi
    else
        if [ -n "$env_prefix" ]; then
            eval "$env_prefix \"\$@\"" 2>&1 | output_with_limit
        else
            "$@" 2>&1 | output_with_limit
        fi
    fi
}

# Build go test flags
go_flags() {
    local flags=""
    if [ -n "$BUILD_TAGS" ]; then
        flags="-tags $BUILD_TAGS"
    fi
    if [ "$TIMEOUT" != "0" ]; then
        flags="$flags -timeout ${TIMEOUT}s"
    fi
    echo "$flags"
}

# If TEST_SPEC looks like a file path (contains / or ends with test extension), run it directly
if [ -n "$TEST_SPEC" ] && [ -f "$TEST_SPEC" ]; then
    echo "Running specific test file: $TEST_SPEC"
    case "$TEST_SPEC" in
        *.ts|*.tsx)
            if [ -f "vitest.config.ts" ] || [ -f "vitest.config.js" ] || [ -f "vitest.config.mjs" ]; then
                run_cmd npx vitest run "$TEST_SPEC" --reporter verbose
            else
                run_cmd npx jest "$TEST_SPEC" --no-coverage --no-cache
            fi
            exit $?
            ;;
        *.js|*.jsx)
            if [ -f "vitest.config.ts" ] || [ -f "vitest.config.js" ]; then
                run_cmd npx vitest run "$TEST_SPEC" --reporter verbose
            else
                run_cmd npx jest "$TEST_SPEC" --no-coverage --no-cache
            fi
            exit $?
            ;;
        *.py)
            run_cmd python -m pytest "$TEST_SPEC" -v
            exit $?
            ;;
        *_test.go)
            GOFILE="$TEST_SPEC"
            # Derive package directory from the file path
            PKG_DIR=$(dirname "$GOFILE")
            PKG_PATH="./$PKG_DIR"
            # Extract all test function names from the file
            TEST_NAMES=$(grep -oE '^func (Test|Benchmark)[A-Za-z0-9_]+' "$GOFILE" | sed 's/^func //' | tr '\n' '|' | sed 's/|$//')
            GOTAGS=$(go_flags)
            if [ -n "$TEST_NAMES" ]; then
                echo "Running tests from $GOFILE: $TEST_NAMES"
                echo "Running: go test -v -run \"$TEST_NAMES\" $GOTAGS $PKG_PATH"
                run_cmd go test -v -run "$TEST_NAMES" $GOTAGS "$PKG_PATH"
            else
                echo "Running: go test -v $GOTAGS $PKG_PATH"
                run_cmd go test -v $GOTAGS "$PKG_PATH"
            fi
            exit $?
            ;;
        *.rs)
            run_cmd cargo test
            exit $?
            ;;
    esac
fi

# Go tests
if [ -f "go.mod" ]; then
    echo "Detected: Go project"
    tags_flag=""
    if [ -n "$BUILD_TAGS" ]; then
        tags_flag="-tags $BUILD_TAGS"
        echo "Build tags: $BUILD_TAGS"
    fi
    timeout_flag=""
    if [ "$TIMEOUT" != "0" ]; then
        timeout_flag="-timeout ${TIMEOUT}s"
    fi
    if [ -n "$TEST_SPEC" ]; then
        if [ "$TEST_SPEC" = "./..." ] || [ "${TEST_SPEC#./}" != "$TEST_SPEC" ]; then
            # TEST_SPEC is a package path (starts with ./)
            echo "Running: go test $tags_flag $timeout_flag -v $TEST_SPEC"
            run_cmd go test $tags_flag $timeout_flag -v "$TEST_SPEC"
        else
            echo "Running: go test $tags_flag $timeout_flag -v -run \"$TEST_SPEC\" ./..."
            run_cmd go test $tags_flag $timeout_flag -v -run "$TEST_SPEC" ./...
        fi
    else
        echo "Running: go test $tags_flag $timeout_flag ./..."
        run_cmd go test $tags_flag $timeout_flag ./...
    fi
    exit $?
fi

# Node/TypeScript tests
if [ -f "package.json" ]; then
    echo "Detected: Node/TypeScript project"

    # Check for vitest config files
    if [ -f "vitest.config.ts" ] || [ -f "vitest.config.js" ] || [ -f "vitest.config.mjs" ]; then
        echo "Test framework: vitest"
        run_cmd npx vitest run --reporter verbose
        exit $?
    fi

    # Check for jest config or dependencies
    if grep -q '"jest"' package.json 2>/dev/null || [ -f "jest.config.js" ] || [ -f "jest.config.ts" ]; then
        echo "Test framework: jest"
        if [ -n "$TEST_SPEC" ]; then
            run_cmd npx jest "$TEST_SPEC" --no-coverage --no-cache
        else
            run_cmd npx jest --no-coverage --no-cache
        fi
        exit $?
    fi

    # Check package.json for test scripts
    if grep -q '"test"' package.json 2>/dev/null; then
        echo "Running: npm test"
        run_cmd npm test
        exit $?
    fi

    echo "No test framework detected in package.json"
    exit 0
fi

# Python tests
if [ -f "pyproject.toml" ] || [ -f "setup.py" ] || [ -f "setup.cfg" ] || [ -f "requirements.txt" ]; then
    echo "Detected: Python project"
    if [ -n "$TEST_SPEC" ]; then
        run_cmd python -m pytest "$TEST_SPEC" -v
    else
        run_cmd python -m pytest -v
    fi
    exit $?
fi

# Rust tests
if [ -f "Cargo.toml" ]; then
    echo "Detected: Rust project"
    if [ -n "$TEST_SPEC" ]; then
        run_cmd cargo test "$TEST_SPEC"
    else
        run_cmd cargo test
    fi
    exit $?
fi

# Java/Maven tests
if [ -f "pom.xml" ]; then
    echo "Detected: Java/Maven project"
    run_cmd mvn test
    exit $?
fi

# Gradle tests
if [ -f "build.gradle" ] || [ -f "build.gradle.kts" ]; then
    echo "Detected: Gradle project"
    run_cmd gradle test
    exit $?
fi

echo "No recognizable test framework found."
exit 1
