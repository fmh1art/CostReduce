#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${ROOT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
export HARBOR_CODEBENCH_NAME="deveval"
export HARBOR_CODEBENCH_TASK_PATH="${DEVEVAL_TASK_PATH:-$ROOT_DIR/tmp/harbor/datasets/deveval}"
export HARBOR_CODEBENCH_RESULTS_SUBDIR="${DEVEVAL_RESULTS_SUBDIR:-deveval}"

exec bash "$(dirname "$0")/run_harbor_codebench.sh"
