#!/usr/bin/env bash
set -euo pipefail

BASELINES_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$BASELINES_ROOT/.." && pwd)"
PYTHON="$BASELINES_ROOT/envs/agentdiet/bin/python"
RUNNER="$BASELINES_ROOT/run_benchmark_matrix.py"

usage() {
  cat <<'EOF'
Usage:
  ./run_benchmark_matrix.sh all [matrix-id]
  ./run_benchmark_matrix.sh one <llm-config.yaml> <concurrency> <matrix-id> [benchmark-order] [methods]
  ./run_benchmark_matrix.sh prepare <matrix-id>
  ./run_benchmark_matrix.sh status <matrix-id>

The "all" command starts the four model backbones in parallel. Within each
backbone, Harbor jobs run sequentially at that model's concurrency ceiling:
  deepseekv4_flash=8, deepseekv4_pro=8, doubao_seed2_lite=6, gpt5_5=4.

AgentDiet and EET run up to 64 fixed cases per benchmark (all 63 for DevEval).
ZipAct runs the first 16 cases from each matching full-sample manifest.
EOF
}

[[ $# -ge 1 ]] || {
  usage >&2
  exit 2
}

action="$1"
shift

case "$action" in
  prepare)
    [[ $# -eq 1 ]] || { usage >&2; exit 2; }
    exec "$PYTHON" "$RUNNER" prepare --matrix-id "$1"
    ;;
  status)
    [[ $# -eq 1 ]] || { usage >&2; exit 2; }
    exec "$PYTHON" "$RUNNER" status --matrix-id "$1"
    ;;
  one)
    [[ $# -ge 3 && $# -le 5 ]] || { usage >&2; exit 2; }
    llm_config="$1"
    concurrency="$2"
    matrix_id="$3"
    benchmark_order="${4:-swe-bench,deep-swe,dab,terminal-bench-2.1,deveval}"
    methods="${5:-agentdiet,eet,zipact}"
    [[ "$llm_config" == /* ]] || llm_config="$PROJECT_ROOT/${llm_config#./}"
    exec "$PYTHON" "$RUNNER" run \
      --matrix-id "$matrix_id" \
      --llm-config "$llm_config" \
      --n-concurrent "$concurrency" \
      --benchmark-order "$benchmark_order" \
      --methods "$methods" \
      --resume
    ;;
  all)
    [[ $# -le 1 ]] || { usage >&2; exit 2; }
    matrix_id="${1:-baseline-matrix64x16-$(date +%Y%m%d-%H%M%S)}"
    "$PYTHON" "$RUNNER" prepare --matrix-id "$matrix_id"
    state_root="$PROJECT_ROOT/results/baselines/_matrix/$matrix_id"
    logs_root="$state_root/logs"
    pids_root="$state_root/pids"
    mkdir -p "$logs_root" "$pids_root"

    declare -a names=(
      deepseekv4_flash
      deepseekv4_pro
      doubao_seed2_lite
      gpt5_5
    )
    declare -A concurrency=(
      [deepseekv4_flash]=8
      [deepseekv4_pro]=8
      [doubao_seed2_lite]=6
      [gpt5_5]=4
    )
    # Offset benchmark order to avoid all four backbones building the same
    # benchmark images at once while keeping every model fully utilized.
    declare -A order=(
      [deepseekv4_flash]="swe-bench,dab,terminal-bench-2.1,deveval,deep-swe"
      [deepseekv4_pro]="dab,deep-swe,deveval,swe-bench,terminal-bench-2.1"
      [doubao_seed2_lite]="deep-swe,terminal-bench-2.1,swe-bench,deveval,dab"
      [gpt5_5]="swe-bench,deveval,deep-swe,dab,terminal-bench-2.1"
    )

    declare -a pids=()
    for name in "${names[@]}"; do
      "$PYTHON" "$RUNNER" run \
        --matrix-id "$matrix_id" \
        --llm-config "$PROJECT_ROOT/_config/$name.yaml" \
        --n-concurrent "${concurrency[$name]}" \
        --benchmark-order "${order[$name]}" \
        --resume \
        >"$logs_root/$name.log" 2>&1 &
      pid="$!"
      pids+=("$pid")
      printf '%s\n' "$pid" >"$pids_root/$name.pid"
      printf '[matrix] started %s pid=%s concurrency=%s log=%s\n' \
        "$name" "$pid" "${concurrency[$name]}" "$logs_root/$name.log"
    done

    failed=0
    for index in "${!pids[@]}"; do
      if wait "${pids[$index]}"; then
        printf '[matrix] completed %s\n' "${names[$index]}"
      else
        rc="$?"
        failed=1
        printf '[matrix] failed %s rc=%s\n' "${names[$index]}" "$rc" >&2
      fi
    done
    "$PYTHON" "$RUNNER" status --matrix-id "$matrix_id"
    exit "$failed"
    ;;
  -h|--help)
    usage
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
