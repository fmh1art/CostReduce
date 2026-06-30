#!/usr/bin/env bash
set -euo pipefail

# build_check - Run build/vet/test for Go, TypeScript (tsc), Python (syntax check)
# Usage: build_check <target> [--build-only|--vet-only|--test-only|--compile-only]
#        build_check <target> --ts [--filter=PATTERN] [--ts-lib=LIB] [--ts-module=MODULE]
#                               [--ts-target=TARGET] [--ts-module-resolution=RES]
#                               [--ts-skipLibCheck] [--ts-esModuleInterop] [--ts-jsx=JSX]
#        build_check <file.py> --python

ACTION="all"
TAGS=""
GOOS=""
GOARCH=""
TS_MODE=false
FILTER=""
PYTHON_MODE=false
TARGET=""

# TypeScript compiler flags
TS_LIB=""
TS_MODULE=""
TS_TARGET=""
TS_MODULE_RESOLUTION=""
TS_SKIP_LIB_CHECK=false
TS_ES_MODULE_INTEROP=false
TS_JSX=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --build-only) ACTION="build"; shift ;;
        --vet-only) ACTION="vet"; shift ;;
        --test-only) ACTION="test"; shift ;;
        --compile-only) ACTION="compile"; shift ;;
        --tags=*) TAGS="${1#*=}"; shift ;;
        --goos=*) GOOS="${1#*=}"; shift ;;
        --goarch=*) GOARCH="${1#*=}"; shift ;;
        --ts) TS_MODE=true; shift ;;
        --filter=*) FILTER="${1#*=}"; shift ;;
        --python) PYTHON_MODE=true; shift ;;
        --ts-lib=*) TS_LIB="${1#*=}"; shift ;;
        --ts-module=*) TS_MODULE="${1#*=}"; shift ;;
        --ts-target=*) TS_TARGET="${1#*=}"; shift ;;
        --ts-module-resolution=*) TS_MODULE_RESOLUTION="${1#*=}"; shift ;;
        --ts-skipLibCheck) TS_SKIP_LIB_CHECK=true; shift ;;
        --ts-esModuleInterop) TS_ES_MODULE_INTEROP=true; shift ;;
        --ts-jsx=*) TS_JSX="${1#*=}"; shift ;;
        -*)
            echo "Unknown option: $1" >&2
            exit 1
            ;;
        *)
            TARGET="$1"
            shift
            ;;
    esac
done

if [[ -z "${TARGET:-}" ]]; then
    echo "Usage: $0 <target> [--build-only|--vet-only|--test-only|--compile-only]" >&2
    echo "       $0 <target> --ts [--filter=PATTERN] [options]" >&2
    echo "       $0 <file.py> --python" >&2
    exit 1
fi

if $TS_MODE; then
    # Build TypeScript compiler flags
    TS_FLAGS="--noEmit"
    [[ -n "$TS_LIB" ]] && TS_FLAGS="$TS_FLAGS --lib $TS_LIB"
    [[ -n "$TS_MODULE" ]] && TS_FLAGS="$TS_FLAGS --module $TS_MODULE"
    [[ -n "$TS_TARGET" ]] && TS_FLAGS="$TS_FLAGS --target $TS_TARGET"
    [[ -n "$TS_MODULE_RESOLUTION" ]] && TS_FLAGS="$TS_FLAGS --moduleResolution $TS_MODULE_RESOLUTION"
    [[ -n "$TS_JSX" ]] && TS_FLAGS="$TS_FLAGS --jsx $TS_JSX"
    $TS_SKIP_LIB_CHECK && TS_FLAGS="$TS_FLAGS --skipLibCheck"
    $TS_ES_MODULE_INTEROP && TS_FLAGS="$TS_FLAGS --esModuleInterop"

    if [[ -n "$FILTER" ]]; then
        exec npx tsc $TS_FLAGS --project "$FILTER"
    elif [[ -f "$TARGET" ]]; then
        exec npx tsc $TS_FLAGS "$TARGET"
    elif [[ -d "$TARGET" ]]; then
        # Try tsconfig.json in target dir
        if [[ -f "$TARGET/tsconfig.json" ]]; then
            exec npx tsc $TS_FLAGS --project "$TARGET/tsconfig.json"
        else
            exec npx tsc $TS_FLAGS "$TARGET"
        fi
    else
        exec npx tsc $TS_FLAGS "$TARGET"
    fi
fi

if $PYTHON_MODE; then
    # Python syntax check
    exec python3 -m py_compile "$TARGET"
fi

# Go mode (default for .go files or directories)
if [[ "$TARGET" == *.go ]] || [[ -d "$TARGET" ]]; then
    # Auto-set Go environment (PATH, GOPATH, CGO_ENABLED=0)
    if [[ -z "${GOPATH:-}" ]]; then
        export GOPATH=/root/go
    fi
    if [[ ":$PATH:" != *":/usr/local/go/bin:"* && ":$PATH:" != *":/root/go/bin:"* ]]; then
        export PATH="$PATH:/usr/local/go/bin:/root/go/bin"
    fi
    export CGO_ENABLED=0
    GO_FLAGS=""
    [[ -n "$TAGS" ]] && GO_FLAGS="$GO_FLAGS -tags $TAGS"
    [[ -n "$GOOS" ]] && export GOOS="$GOOS"
    [[ -n "$GOARCH" ]] && export GOARCH="$GOARCH"

    case "$ACTION" in
        all)
            echo "=== go build ==="
            go build $GO_FLAGS "$TARGET" 2>&1 || true
            echo "=== go vet ==="
            go vet $GO_FLAGS "$TARGET" 2>&1 || true
            ;;
        build)
            exec go build $GO_FLAGS "$TARGET"
            ;;
        vet)
            exec go vet $GO_FLAGS "$TARGET"
            ;;
        test)
            exec go test $GO_FLAGS "$TARGET"
            ;;
        compile)
            exec go build -o /dev/null $GO_FLAGS "$TARGET"
            ;;
    esac
fi
