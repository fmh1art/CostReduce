#!/usr/bin/env bash
set -euo pipefail

BASELINES_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() {
  cat <<'EOF'
Usage:
  ./run_swebench16.sh {agentdiet|zipact|eet|all} [runner options]

Defaults:
  --n-tasks 16
  --n-concurrent 8
  --llm-config ../_config/doubao_seed2_lite.yaml
  --output-root ../results/baselines

Examples:
  ./run_swebench16.sh all
  ./run_swebench16.sh zipact --run-id zipact-swebench16-rerun
  ./run_swebench16.sh eet --dry-run

With "all", methods run sequentially so global concurrency remains 8.
EOF
}

if [[ $# -lt 1 ]]; then
  usage >&2
  exit 2
fi

selection="$1"
shift

run_one() {
  local baseline="$1"
  shift
  "$BASELINES_ROOT/envs/$baseline/bin/python" \
    "$BASELINES_ROOT/run_swebench16.py" "$baseline" "$@"
}

case "$selection" in
  agentdiet|zipact|eet)
    run_one "$selection" "$@"
    ;;
  all)
    run_one agentdiet "$@"
    run_one zipact "$@"
    run_one eet "$@"
    ;;
  -h|--help)
    usage
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
