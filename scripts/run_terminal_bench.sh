#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${ROOT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
export HARBOR_CODEBENCH_NAME="terminal-bench-2-1"
export HARBOR_CODEBENCH_TASK_PATH="${TERMINAL_BENCH_TASK_PATH:-$ROOT_DIR/tmp/harbor/datasets/terminal-bench-2-1}"
export HARBOR_CODEBENCH_RESULTS_SUBDIR="${TERMINAL_BENCH_RESULTS_SUBDIR:-terminal-bench-2-1}"

exec bash "$(dirname "$0")/run_harbor_codebench.sh"
