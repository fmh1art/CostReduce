#!/bin/bash
# go-env-check: Check Go environment, download modules if needed, then run build/vet/test/list.
# Usage: go-env-check/main.sh [command] [target] [options]
# Commands: build (default), vet, test, list, run (build then execute)
# Options:
#   -C <dir>           Directory to run in (default: .)
#   --timeout <secs>   Timeout for build/vet/test (default: 30)
#   --quick, -q        Skip env check and go mod download, just run command immediately
#   -f <format>        Go list format string (only for list command, e.g. '{{.GoFiles}}')
#   -h, --help         Show usage

set -euo pipefail

command="build"
target=""
binary_args=()
timeout_secs=30
workdir="."
quick=false
list_format=""

while [ $# -gt 0 ]; do
  case "$1" in
    -C)
      [ $# -lt 2 ] && { echo "ERROR: -C requires a directory" >&2; exit 1; }
      workdir="$2"
      shift 2
      ;;
    --timeout)
      [ $# -lt 2 ] && { echo "ERROR: --timeout requires a value" >&2; exit 1; }
      timeout_secs="$2"
      shift 2
      ;;
    --quick|-q)
      quick=true
      shift
      ;;
    -f)
      [ $# -lt 2 ] && { echo "ERROR: -f requires a format string" >&2; exit 1; }
      list_format="$2"
      shift 2
      ;;
    -h|--help)
      echo "Usage: $0 [command] [target] [options]"
      echo "Commands: build (default), vet, test, list, run"
      echo "  -C <dir>         Directory to run in"
      echo "  --timeout <secs> Timeout for build/vet/test (default: 30)"
      echo "  --quick, -q      Skip env check and go mod download"
      echo "  -f <format>      Go list format string (for list command, e.g. '{{.GoFiles}}')"
      exit 0
      ;;
    build|vet|test|list|run)
      command="$1"
      shift
      ;;
    --)
      shift
      # Everything after -- is binary args (for run command)
      binary_args=("$@")
      break
      ;;
    *)
      target="$1"
      shift
      ;;
  esac
done

cd "$workdir"

# ---- list command: quick go list -e to check compilation ----
if [ "$command" = "list" ]; then
  target="${target:-./...}"
  set +e
  if [ -n "$list_format" ]; then
    output=$(timeout "$timeout_secs" go list -e -f "$list_format" "$target" 2>&1)
  else
    output=$(timeout "$timeout_secs" go list -e "$target" 2>&1)
  fi
  rc=$?
  set -e
  if [ $rc -eq 124 ]; then
    echo "list $target: TIMEOUT after ${timeout_secs}s"
    exit 124
  elif [ $rc -ne 0 ]; then
    echo "list $target: FAIL"
    echo "$output" | head -30
    exit $rc
  else
    echo "$output"
  fi
  exit 0
fi

if [ "$quick" = true ]; then
  # Quick mode: skip env check and module download, just run immediately
  target="${target:-./...}"
  set +e
  case "$command" in
    build)
      output=$(timeout "$timeout_secs" go build "$target" 2>&1)
      rc=$?
      ;;
    vet)
      output=$(timeout "$timeout_secs" go vet "$target" 2>&1)
      rc=$?
      ;;
    test)
      output=$(timeout "$timeout_secs" go test "$target" 2>&1)
      rc=$?
      ;;
    run)
      # Build and run binary
      binary_path="/tmp/go-run-binary"
      build_output=$(timeout "$timeout_secs" go build -o "$binary_path" "$target" 2>&1)
      rc=$?
      if [ $rc -eq 0 ]; then
        output=$("$binary_path" "${binary_args[@]}" 2>&1)
        rc=$?
        rm -f "$binary_path"
      else
        output="$build_output"
      fi
      ;;
  esac
  set -e
  if [ $rc -eq 124 ]; then
    echo "$command $target: TIMEOUT after ${timeout_secs}s"
    exit 124
  elif [ $rc -ne 0 ]; then
    echo "$command $target: FAIL"
    echo "$output" | head -30
    exit $rc
  else
    echo "$command $target: ok"
  fi
  exit 0
fi

# ---- Full mode: env check + module download + build/vet/test ----

# ---- Step 1: Check Go environment ----
go_path=$(go env GOPATH 2>/dev/null || echo "unknown")
go_modcache=$(go env GOMODCACHE 2>/dev/null || echo "unknown")
echo "GOPATH=$go_path GOMODCACHE=$go_modcache"

# ---- Step 2: Check module cache ----
if [ -d "$go_modcache/cache/download" ] && ls "$go_modcache/cache/download/"*/*/ 2>/dev/null | head -3 | grep -q .; then
  echo "mod cache: present"
else
  echo "mod cache: absent"
fi

# ---- Step 3: Check go.mod exists ----
if [ ! -f "go.mod" ]; then
  echo "ERROR: No go.mod found in $(pwd)" >&2
  exit 1
fi

# ---- Step 4: Run go mod download (always, idempotent) ----
if timeout "$timeout_secs" go mod download 2>&1; then
  echo "go mod download: ok"
else
  echo "go mod download: FAIL" >&2
  exit 1
fi

# ---- Step 5: Run build/vet/test ----
target="${target:-./...}"

set +e
case "$command" in
  build)
    output=$(timeout "$timeout_secs" go build "$target" 2>&1)
    rc=$?
    ;;
  vet)
    output=$(timeout "$timeout_secs" go vet "$target" 2>&1)
    rc=$?
    ;;
  test)
    output=$(timeout "$timeout_secs" go test "$target" 2>&1)
    rc=$?
    ;;
  run)
    # Build and run binary
    binary_path="/tmp/go-run-binary"
    build_output=$(timeout "$timeout_secs" go build -o "$binary_path" "$target" 2>&1)
    rc=$?
    if [ $rc -eq 0 ]; then
      output=$("$binary_path" "${binary_args[@]}" 2>&1)
      rc=$?
      rm -f "$binary_path"
    else
      output="$build_output"
    fi
    ;;
esac
set -e

if [ $rc -eq 124 ]; then
  echo "$command $target: TIMEOUT after ${timeout_secs}s"
  exit 124
elif [ $rc -ne 0 ]; then
  echo "$command $target: FAIL"
  echo "$output" | head -30
  exit $rc
else
  echo "$command $target: ok"
fi
