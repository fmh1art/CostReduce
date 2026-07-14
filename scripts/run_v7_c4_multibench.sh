#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
RUN_TAG="${RUN_TAG:-v7_c4_multibench_$(date +%m%d-%H%M%S)}"
LOG_FILE="${LOG_FILE:-$ROOT_DIR/logs/${RUN_TAG}.log}"
UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/optiharness-uv-cache}"
XDG_CACHE_HOME="${XDG_CACHE_HOME:-/tmp/optiharness-xdg-cache}"

export ROOT_DIR UV_CACHE_DIR XDG_CACHE_HOME
mkdir -p "$(dirname "$LOG_FILE")" "$UV_CACHE_DIR" "$XDG_CACHE_HOME"
exec >>"$LOG_FILE" 2>&1

cd "$ROOT_DIR"
source /home/fanmeihao/anaconda3/etc/profile.d/conda.sh
conda activate 0622
trap 'rc=$?; echo "[runner] FAILED rc=$rc at $(date)"; exit "$rc"' EXIT

echo "[runner] start: $(date)"
echo "[runner] framework=V7 cycles=4 evolve_cases=16 eval_cases=64 concurrency=16"
echo "[runner] benchmarks=deep-swe swebench dab"
echo "[runner] result_root=$ROOT_DIR/results/evolve/v7cycle"

for bench in deep-swe swebench dab; do
  ts="$(date +%m%d-%H%M%S)"
  work_dir="$ROOT_DIR/results/evolve/v7cycle/$bench/$ts"
  scripts_dir="$work_dir/scripts"
  eval_run_id="evolve-v7cycle-$bench-$ts-eval64"
  mkdir -p "$work_dir" "$scripts_dir"

  echo "[runner] === $bench evolve start: $(date) ==="
  echo "[runner] timeflag=$ts"
  echo "[runner] work_dir=$work_dir"

  common_env=(
    "BENCHMARKS=$bench"
    "USE_EVOLVE_V7=1"
    "EVOLVE_VERSION=v7"
    "V7_N_CYCLES=4"
    "EVOLVE_CASE_COUNT=16"
    "EVAL_N_TASKS=64"
    "N_CONCURRENT=16"
    "EVOLVE_WORKERS=16"
    "V7_EVOLVE_CASES_PER_PROMPT=4"
    "V7_MAX_STEPS_PER_SAMPLE=8"
    "V7_MAX_PROMPT_CHARS=32000"
    "PHASE=all"
    "FORCE_PREP=0"
    "SKIP_FINAL_EVAL=1"
    "WORK_DIR=$work_dir"
    "SCRIPTS_DIR=$scripts_dir"
    "LLM_CONFIG=$ROOT_DIR/_config/deepseekv4_flash.yaml"
  )
  if [[ "$bench" == "swebench" ]]; then
    common_env+=("SWEBENCH_TASK_PATH=$ROOT_DIR/tmp/harbor/datasets/swebench-verified")
  elif [[ "$bench" == "dab" ]]; then
    common_env+=("DAB_TASK_PATH=$ROOT_DIR/benchmark/DBA-bench/harbor/datasets/dab")
  fi
  env "${common_env[@]}" bash scripts/run_exp.sh

  test -s "$scripts_dir/tools.json"
  test -s "$scripts_dir/executor.py"
  test -s "$scripts_dir/instruction.md"
  python -c 'import json, sys; d=json.load(open(sys.argv[1])); cs=d.get("cycles", []); assert d.get("n_cycles")==4 and len(cs)==4 and all(c.get("annotated") and c.get("provenance_samples_built") and c.get("evolved") for c in cs), d' "$work_dir/v7_report.json"
  echo "[runner] === $bench evolve complete: $(date) ==="
  echo "[runner] === $bench eval64 start: RUN_ID=$eval_run_id $(date) ==="

  eval_env=(
    "EVOLVE_TOOLS_MODE=registry"
    "EVOLVE_SCRIPTS_DIR=$scripts_dir"
    "EVOLVE_SKIP_FILE="
    "RUN_ID=$eval_run_id"
    "N_TASKS=64"
    "N_CONCURRENT=16"
    "LLM_CONFIG=$ROOT_DIR/_config/deepseekv4_flash.yaml"
    "RESULTS_DIR=$ROOT_DIR/results/eval"
  )
  case "$bench" in
    deep-swe)
      env "${eval_env[@]}" bash scripts/run_deep_swe.sh
      ;;
    swebench)
      env "${eval_env[@]}" \
        SWEBENCH_TASK_PATH="$ROOT_DIR/tmp/harbor/datasets/swebench-verified" \
        bash scripts/run_swe_bench.sh
      ;;
    dab)
      env "${eval_env[@]}" \
        DAB_TASK_PATH="$ROOT_DIR/benchmark/DBA-bench/harbor/datasets/dab" \
        bash scripts/run_dab_harbor.sh
      ;;
  esac
  echo "[runner] === $bench eval64 complete: $(date) ==="
done

trap - EXIT
echo "[runner] ALL BENCHMARKS COMPLETE: $(date)"
