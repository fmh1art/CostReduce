#!/usr/bin/env bash
set -uo pipefail

ROOT_DIR="/home/fanmeihao/projects/OptiHarnessForCost"
cd "$ROOT_DIR" || exit 1

source /home/fanmeihao/anaconda3/etc/profile.d/conda.sh
conda activate 0622

RUN_TAG="${RUN_TAG:-atlas_v6_c2_continue_$(date +%m%d-%H%M%S)}"
LOG="${LOG:-$ROOT_DIR/logs/${RUN_TAG}.log}"
mkdir -p "$(dirname "$LOG")"

{
  echo "[runner] continue atlas v6 c2: $(date)"
  echo "[runner] RUN_TAG=$RUN_TAG"

  # This continuation intentionally reuses the archived QA V6 tools.
  qa_scripts="$ROOT_DIR/_his/results_20260714-102857/.evolve_results/evolve_v6cycle/swe-atlas-qa/0713-170159/scripts"
  echo "[runner] swe-atlas-qa resume eval64 using scripts=$qa_scripts: $(date)"
  EVOLVE_TOOLS_MODE=registry \
  EVOLVE_SCRIPTS_DIR="$qa_scripts" \
  EVOLVE_SKIP_FILE="" \
  RUN_ID="evolve-v6cycle-swe-atlas-qa-0713-170159-eval64" \
  N_TASKS=64 \
  N_CONCURRENT=16 \
  LLM_CONFIG="$ROOT_DIR/_config/deepseekv4_flash.yaml" \
  SWE_ATLAS_SPLITS="qa" \
    bash scripts/run_swe_atlas.sh \
    || echo "[runner] WARN swe-atlas-qa eval64 exited nonzero: $(date)"
  echo "[runner] swe-atlas-qa eval64 stage complete: $(date)"

  for split in tw rf; do
    bench="swe-atlas-$split"
    ts="$(date +%m%d-%H%M%S)"
    work_dir="$ROOT_DIR/results/evolve/v6cycle/$bench/$ts"
    scripts_dir="$work_dir/scripts"
    eval_run_id="evolve-v6cycle-$bench-$ts-eval64"
    mkdir -p "$work_dir" "$scripts_dir"

    echo "[runner] === $bench start: $(date) ==="
    echo "[runner] work_dir=$work_dir"
    echo "[runner] scripts_dir=$scripts_dir"
    if BENCHMARKS="$bench" \
       EVOLVE_VERSION=v6 \
       V6_N_CYCLES=2 \
       EVOLVE_CASE_COUNT=16 \
       EVAL_N_TASKS=64 \
       N_CONCURRENT=16 \
       EVOLVE_WORKERS=16 \
       WORK_DIR="$work_dir" \
       SCRIPTS_DIR="$scripts_dir" \
         bash scripts/run_exp.sh; then
      echo "[runner] $bench v6 cycle complete: $(date)"
      echo "[runner] $bench start eval64 RUN_ID=$eval_run_id: $(date)"
      EVOLVE_TOOLS_MODE=registry \
      EVOLVE_SCRIPTS_DIR="$scripts_dir" \
      EVOLVE_SKIP_FILE="" \
      RUN_ID="$eval_run_id" \
      N_TASKS=64 \
      N_CONCURRENT=16 \
      LLM_CONFIG="$ROOT_DIR/_config/deepseekv4_flash.yaml" \
      SWE_ATLAS_SPLITS="$split" \
        bash scripts/run_swe_atlas.sh \
        || echo "[runner] WARN $bench eval64 exited nonzero: $(date)"
      echo "[runner] $bench eval64 stage complete: $(date)"
    else
      echo "[runner] ERROR $bench v6 cycle failed: $(date)"
      exit 1
    fi
  done

  echo "[runner] atlas continuation all done: $(date)"
} 2>&1 | tee -a "$LOG"
