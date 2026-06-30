#!/usr/bin/env bash
set -euo pipefail

# build_check - Run build/vet/test for Go, Rust/Cargo, TypeScript (tsc), Python (syntax check on multiple files).
# Usage: build_check [--dir=DIR] [options] <target(s)>
# Options:
#   --build-only       Only run build
#   --vet-only         Only run vet
#   --test-only        Only run test
#   --compile-only     Only compile (Go)
#   --tags=TAGS        Go build tags
#   --goos=OS / --goarch=ARCH  Cross-compilation
#   --ts [--filter=PATTERN]    TypeScript type-check (noEmit)
#   --tsc [--build=CONFIG] [--typecheck=CONFIG] [--clean=DIR]
#                            TypeScript compile (emit JS) and optionally type-check
#   --python           Python syntax check on one or more files
#   --rust             Rust/Cargo check (e.g., cargo check/test/build/run)
#   --go               Force Go mode for the given targets
#   --build-and-run    Build a Go binary then run it (first target = main package, rest = args to binary)
#   --clean-testcache  Run `go clean -testcache` before tests (Go only)
#   --head=N           Show only first N lines of output
#   --tail=N           Show only last N lines of output

# Helper: truncate output based on --head/--tail
truncate_output() {
    local input
    input=$(cat)
    if [[ -n "$HEAD_LINES" ]]; then
        echo "$input" | head -n "$HEAD_LINES"
    elif [[ -n "$TAIL_LINES" ]]; then
        echo "$input" | tail -n "$TAIL_LINES"
    else
        echo "$input"
    fi
}

WORKDIR=""
BANDIT_MODE=false
BANDIT_FORMAT="custom"
BANDIT_ARGS=""
TARGET=""
TARGETS=()
MODE="all"
TAGS=""
GOOS=""
GOARCH=""
TS_MODE=false
TS_FILTER=""
PY_MODE=false
RUST_MODE=false
GO_MODE=false
BUILD_AND_RUN=false
CLEAN_TESTCACHE=false
TSC_MODE=false
TSC_BUILD=""
TSC_TYPECHECK=""
TSC_CLEAN=""
HEAD_LINES=""
TAIL_LINES=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dir=*) WORKDIR="${1#*=}"; shift ;;
        --dir) WORKDIR="$2"; shift 2 ;;
        --build-only) MODE="build"; shift ;;
        --vet-only) MODE="vet"; shift ;;
        --test-only) MODE="test"; shift ;;
        --compile-only) MODE="compile"; shift ;;
        --run) MODE="run"; shift ;;
        --check-tests) MODE="check-tests"; shift ;;
        --test-lib) MODE="test-lib"; shift ;;
        --tags=*) TAGS="${1#*=}"; shift ;;
        --goos=*) GOOS="${1#*=}"; shift ;;
        --goarch=*) GOARCH="${1#*=}"; shift ;;
        --ts) TS_MODE=true; shift ;;
        --filter=*) TS_FILTER="${1#*=}"; shift ;;
        --tsc) TSC_MODE=true; shift ;;
        --build=*) TSC_BUILD="${1#*=}"; shift ;;
        --typecheck=*) TSC_TYPECHECK="${1#*=}"; shift ;;
        --clean=*) TSC_CLEAN="${1#*=}"; shift ;;
        --python) PY_MODE=true; shift ;;
        --bandit) BANDIT_MODE=true; shift ;;
        --bandit-format=*) BANDIT_FORMAT="${1#*=}"; shift ;;
        --bandit-output-format=*) BANDIT_FORMAT="${1#*=}"; shift ;;
        --bandit-args=*) BANDIT_ARGS="${1#*=}"; shift ;;
        --bandit-args) BANDIT_ARGS="$2"; shift 2 ;;
        --rust) RUST_MODE=true; shift ;;
        --go) GO_MODE=true; shift ;;
        --build-and-run) BUILD_AND_RUN=true; shift ;;
        --clean-testcache) CLEAN_TESTCACHE=true; shift ;;
        --head=*) HEAD_LINES="${1#*=}"; shift ;;
        --head) HEAD_LINES="$2"; shift 2 ;;
        --tail=*) TAIL_LINES="${1#*=}"; shift ;;
        --tail) TAIL_LINES="$2"; shift 2 ;;
        -*)
            echo "Unknown option: $1" >&2
            exit 1
            ;;
        *)
            TARGET="$1"
            TARGETS+=("$1")
            shift
            ;;
    esac
done

# Change to working directory if specified
if [[ -n "$WORKDIR" ]]; then
    cd "$WORKDIR" || { echo "Error: Cannot cd to $WORKDIR" >&2; exit 1; }
fi

# TypeScript compile mode: compile (emit JS) and optionally type-check
if [[ "$TSC_MODE" == true ]]; then
    # Clean build output if requested
    if [[ -n "$TSC_CLEAN" ]]; then
        rm -rf "$TSC_CLEAN"
    fi

    # Build (compile) step: emit JS
    if [[ -n "$TSC_BUILD" ]]; then
        echo "=== tsc build: $TSC_BUILD ==="
        npx tsc -p "$TSC_BUILD" 2>&1 | truncate_output || true
    elif [[ -n "$TARGET" ]]; then
        echo "=== tsc build: $TARGET ==="
        npx tsc -p "$TARGET" 2>&1 | truncate_output || true
    else
        echo "=== tsc build (tsconfig.json) ==="
        npx tsc -p tsconfig.json 2>&1 | truncate_output || true
    fi

    # Type-check step (noEmit)
    if [[ -n "$TSC_TYPECHECK" ]]; then
        echo "=== tsc type-check: $TSC_TYPECHECK ==="
        npx tsc --noEmit --pretty -p "$TSC_TYPECHECK" 2>&1 | truncate_output || true
    fi

    exit 0
fi

if [[ -z "$TARGET" && "$TS_MODE" == false && "$PY_MODE" == false && "$RUST_MODE" == false && "$BANDIT_MODE" == false && "$GO_MODE" == false ]]; then
    echo "Usage: build_check [--dir=DIR] [options] <target>" >&2
    exit 1
fi

if [[ "$PY_MODE" == true ]]; then
    if [[ ${#TARGETS[@]} -eq 0 ]]; then
        echo "Usage: build_check --python <file1.py> [file2.py ...]" >&2
        exit 1
    fi
    errors=0
    for f in "${TARGETS[@]}"; do
        if python3 -m py_compile "$f" 2>&1; then
            echo "$f: OK"
        else
            echo "$f: FAILED" >&2
            errors=$((errors + 1))
        fi
    done | truncate_output
    if [[ $errors -gt 0 ]]; then
        exit 1
    fi
    exit 0
fi

if [[ "$TS_MODE" == true ]]; then
    TSC_ARGS=("--noEmit" "--pretty")
    if [[ -n "$TS_FILTER" ]]; then
        TSC_ARGS+=("--project" "$TS_FILTER")
    fi
    if [[ -n "$TARGET" ]]; then
        echo "=== tsc --noEmit: $TARGET ==="
        npx tsc "${TSC_ARGS[@]}" --project "$TARGET" 2>&1 | truncate_output || true
    else
        echo "=== tsc --noEmit ==="
        npx tsc "${TSC_ARGS[@]}" 2>&1 | truncate_output || true
    fi
    exit 0
fi

if [[ "$RUST_MODE" == true ]]; then
    CARGO_ARGS=()
    if [[ -n "$TAGS" ]]; then
        CARGO_ARGS+=("--features=$TAGS")
    fi
    case "$MODE" in
        build)
            cargo build "${CARGO_ARGS[@]}" --lib -p "$TARGET" 2>&1 | grep -v "^\(⟳ Checking\|    Checking\) " | truncate_output || true
            ;;
        test)
            cargo test "${CARGO_ARGS[@]}" --lib -p "$TARGET" 2>&1 | grep -v "^\(⟳ Checking\|    Checking\) " | truncate_output || true
            ;;
        compile)
            cargo check "${CARGO_ARGS[@]}" --lib -p "$TARGET" 2>&1 | grep -v "^\(⟳ Checking\|    Checking\) " | truncate_output || true
            ;;
        vet)
            cargo clippy "${CARGO_ARGS[@]}" --lib -p "$TARGET" 2>&1 | grep -v "^\(⟳ Checking\|    Checking\) " | truncate_output || true
            ;;
        run)
            cargo run "${CARGO_ARGS[@]}" -p "$TARGET" 2>&1 | grep -v "^\(⟳ Checking\|    Checking\) " | truncate_output || true
            ;;
        check-tests)
            cargo check --tests -p "$TARGET" 2>&1 | grep -v "^\(⟳ Checking\|    Checking\) " | truncate_output || true
            ;;
        test-lib)
            cargo test --lib -p "$TARGET" 2>&1 | grep -v "^\(⟳ Checking\|    Checking\) " | truncate_output || true
            ;;
        all)
            echo "=== cargo check ==="
            cargo check "${CARGO_ARGS[@]}" --lib -p "$TARGET" 2>&1 | grep -v "^\(⟳ Checking\|    Checking\) " | truncate_output || true
            echo "=== cargo test ==="
            cargo test "${CARGO_ARGS[@]}" --lib -p "$TARGET" 2>&1 | grep -v "^\(⟳ Checking\|    Checking\) " | truncate_output || true
            ;;
    esac
    exit 0
fi

# Bandit mode (Python security linter) - supports multiple targets and extra bandit args
if [[ "$BANDIT_MODE" == true ]]; then
    if [[ ${#TARGETS[@]} -eq 0 ]]; then
        echo "Usage: build_check [options] <target1> [target2 ...] --bandit [--bandit-args=\"...\"]" >&2
        echo "Error: --bandit requires at least one target file or directory." >&2
        exit 1
    fi

    # Split --bandit-args into an array for safe passing
    BANDIT_EXTRA_ARGS=()
    if [[ -n "$BANDIT_ARGS" ]]; then
        # shellcheck disable=SC2206
        BANDIT_EXTRA_ARGS=($BANDIT_ARGS)
    fi

    for single_target in "${TARGETS[@]}"; do
        if [[ ${#TARGETS[@]} -gt 1 ]]; then
            echo "===== $single_target ====="
        fi
        case "$BANDIT_FORMAT" in
            custom)
                result=$(bandit -f json "${BANDIT_EXTRA_ARGS[@]}" "$single_target" 2>/dev/null | python3 -c "
import json,sys
try:
    data = json.load(sys.stdin)
    results = data.get('results', [])
    print('Test results:')
    if not results:
        print('No issues found.')
    for r in results:
        tid = r.get('test_id','')
        tname = r.get('test_name','')
        if tname:
            tid_full = f'{tid}:{tname}'
        else:
            tid_full = tid
        loc = r.get('filename','') + ':' + str(r.get('line_number',''))
        print(f'>> Issue: [{tid_full}] {r.get(\"issue_text\",\"\")}')
        print(f'   Location: {loc}')
        print()
    metrics = data.get('metrics', {})
    total_nosec = 0
    total_skipped = 0
    for fname, m in metrics.items():
        if fname != '_totals':
            total_nosec += m.get('nosec', 0)
            total_skipped += m.get('skipped_tests', 0)
    if total_nosec > 0 or total_skipped > 0:
        print(f'Total lines skipped (#nosec): {total_nosec}')
        if total_skipped > 0:
            print(f'Total tests skipped: {total_skipped}')
    print(f'Total issues: {len(results)}')
except:
    pass
" 2>/dev/null) || true
                    if [[ -z "$result" ]]; then
                        bandit "${BANDIT_EXTRA_ARGS[@]}" "$single_target" 2>&1 | grep -E "^(Test results:|>> Issue:|   Location:|Total lines skipped|Total issues)" || echo "No issues found by bandit for $single_target."
                    else
                        echo "$result"
                    fi
                ;;
            json|txt)
                bandit -f "$BANDIT_FORMAT" "${BANDIT_EXTRA_ARGS[@]}" "$single_target" 2>&1 | truncate_output || true
                ;;
            *)
                bandit -f "$BANDIT_FORMAT" "${BANDIT_EXTRA_ARGS[@]}" "$single_target" 2>&1 | truncate_output || true
                ;;
        esac
    done | truncate_output
    exit 0
fi

# Go mode (default when targets are given without --rust/--python/--ts)
BUILD_ARGS=()
[[ -n "$TAGS" ]] && BUILD_ARGS+=("--tags=$TAGS")
[[ -n "$GOOS" ]] && BUILD_ARGS+=("--goos=$GOOS")
[[ -n "$GOARCH" ]] && BUILD_ARGS+=("--goarch=$GOARCH")

# --build-and-run: build a Go binary from first target, then run it with remaining targets as args
if [[ "$BUILD_AND_RUN" == true ]]; then
    if [[ ${#TARGETS[@]} -lt 2 ]]; then
        echo "Usage: build_check --build-and-run <main_package> <arg1> [arg2 ...]" >&2
        echo "       First target = Go main package, rest = arguments to pass to the binary" >&2
        exit 1
    fi
    MAIN_PKG="${TARGETS[0]}"
    BINARY_ARGS=("${TARGETS[@]:1}")
    BINARY_PATH="/tmp/build_check_bin_$$"
    echo "=== go build $MAIN_PKG ==="
    go build "${BUILD_ARGS[@]}" -o "$BINARY_PATH" ./"$MAIN_PKG" 2>&1 | truncate_output || { echo "Build failed" >&2; exit 1; }
    echo "=== run binary ==="
    "$BINARY_PATH" "${BINARY_ARGS[@]}" 2>&1 | truncate_output || true
    rm -f "$BINARY_PATH"
    exit 0
fi

# Build the Go target paths
GO_TARGETS=()
for t in "${TARGETS[@]}"; do
    GO_TARGETS+=("./$t")
done

# If --clean-testcache, run go clean -testcache first
if [[ "$CLEAN_TESTCACHE" == true ]]; then
    echo "=== go clean -testcache ==="
    go clean -testcache 2>&1 | truncate_output || true
fi

case "$MODE" in
    build)
        go build "${BUILD_ARGS[@]}" "${GO_TARGETS[@]}" 2>&1 | truncate_output || true
        ;;
    vet)
        go vet "${BUILD_ARGS[@]}" "${GO_TARGETS[@]}" 2>&1 | truncate_output || true
        ;;
    test)
        go test "${BUILD_ARGS[@]}" "${GO_TARGETS[@]}" 2>&1 | truncate_output || true
        ;;
    compile)
        go build -n "${BUILD_ARGS[@]}" "${GO_TARGETS[@]}" 2>&1 | truncate_output || true
        ;;
    all)
        echo "=== go build ==="
        go build "${BUILD_ARGS[@]}" "${GO_TARGETS[@]}" 2>&1 | truncate_output || true
        echo "=== go vet ==="
        go vet "${BUILD_ARGS[@]}" "${GO_TARGETS[@]}" 2>&1 | truncate_output || true
        echo "=== go test ==="
        go test "${BUILD_ARGS[@]}" "${GO_TARGETS[@]}" 2>&1 | truncate_output || true
        ;;
esac
