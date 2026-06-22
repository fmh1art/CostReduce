#!/bin/bash
# run_build - Build/compile a project. Auto-detects the build system.
# Usage: main.sh [directory] [target] [--output-limit=<lines>] [--tags=<build_tags>] [--env=<key=val,...>]
#   directory: project directory (default: .)
#   target: optional, specific package/module/target to build
#   --output-limit: optional, max lines of output to show (default: 200, use 0 for unlimited)
#   --tags: optional, Go build tags (e.g., "kqueue,dev" for `-tags kqueue,dev`)
#   --env: optional, comma-separated KEY=VAL environment variables (e.g., "CGO_ENABLED=0,DEBUG=1")
# Supports: Go, Node/TypeScript, Rust, Python, Java/Maven, Gradle, Cargo, Make
# Automatically adds timeout (default: 120s) to prevent hanging.

DIR=""
TARGET=""
TIMEOUT=120
OUTPUT_LIMIT=200
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
            elif [ -z "$TARGET" ]; then
                TARGET="$1"
            fi
            ;;
    esac
    shift
done

DIR="${DIR:-.}"

cd "$DIR" || { echo "ERROR: Cannot cd to $DIR"; exit 1; }

echo "=== Detecting build system in $(pwd) ==="
echo ""

# Build env prefix string from --env arg
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

# Run a command with optional timeout, env vars, and output limiting
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

# Go
if [ -f "go.mod" ]; then
    echo "Detected: Go project"
    tags_flag=""
    if [ -n "$BUILD_TAGS" ]; then
        tags_flag="-tags $BUILD_TAGS"
        echo "Build tags: $BUILD_TAGS"
    fi
    if [ -n "$TARGET" ]; then
        echo "Building: $TARGET"
        run_cmd go build $tags_flag "$TARGET"
    else
        echo "Building: all packages"
        run_cmd go build $tags_flag ./...
    fi
    exit $?
fi

# Node/TypeScript
if [ -f "package.json" ]; then
    echo "Detected: Node/TypeScript project"
    # Check for build script
    if grep -q '"build"' package.json 2>/dev/null; then
        echo "Running: npm run build"
        run_cmd npm run build
        exit $?
    fi
    # Check for tsc / TypeScript compiler
    if [ -f "tsconfig.json" ]; then
        echo "Running: npx tsc (TypeScript compiler)"
        run_cmd npx tsc --noEmit
        exit $?
    fi
    echo "No build script found in package.json"
    exit 0
fi

# Rust
if [ -f "Cargo.toml" ]; then
    echo "Detected: Rust project"
    if [ -n "$TARGET" ]; then
        run_cmd cargo build -p "$TARGET"
    else
        run_cmd cargo build
    fi
    exit $?
fi

# Python (check syntax)
if [ -f "pyproject.toml" ] || [ -f "setup.py" ] || [ -f "setup.cfg" ]; then
    echo "Detected: Python project"
    if [ -n "$TARGET" ]; then
        run_cmd python -m py_compile "$TARGET"
    else
        run_cmd python -m compileall .
    fi
    exit $?
fi

# Java/Maven
if [ -f "pom.xml" ]; then
    echo "Detected: Java/Maven project"
    run_cmd mvn compile
    exit $?
fi

# Gradle
if [ -f "build.gradle" ] || [ -f "build.gradle.kts" ]; then
    echo "Detected: Gradle project"
    run_cmd gradle build
    exit $?
fi

# Makefile
if [ -f "Makefile" ]; then
    echo "Detected: Makefile project"
    if [ -n "$TARGET" ]; then
        run_cmd make "$TARGET"
    else
        run_cmd make
    fi
    exit $?
fi

echo "No recognizable build system found."
exit 1
