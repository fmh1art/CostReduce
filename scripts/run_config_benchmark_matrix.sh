#!/usr/bin/env bash
# Run one LLM configuration across the requested benchmark matrix.  Launch one
# instance of this wrapper per config to keep model-level concurrency isolated.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

LLM_CONFIG="${1:-}"
N_CONCURRENT="${2:-}"
[[ -n "$LLM_CONFIG" && -n "$N_CONCURRENT" ]] || {
  echo "usage: $0 <llm-config.yaml> <concurrency>" >&2
  exit 2
}
[[ "$LLM_CONFIG" == /* ]] || LLM_CONFIG="${ROOT_DIR}/${LLM_CONFIG#./}"
[[ -f "$LLM_CONFIG" ]] || { echo "missing LLM config: $LLM_CONFIG" >&2; exit 2; }
[[ "$N_CONCURRENT" =~ ^[1-9][0-9]*$ ]] \
  || { echo "concurrency must be a positive integer: $N_CONCURRENT" >&2; exit 2; }

RESULTS_ROOT="${RESULTS_ROOT:-${ROOT_DIR}/results}"
EVOLVE_CASE_COUNT="${EVOLVE_CASE_COUNT:-16}"
COAT_N_CYCLES="${COAT_N_CYCLES:-2}"
MATRIX_BENCHMARKS="${MATRIX_BENCHMARKS:-swebench deep-swe dab}"
EXPERIMENT_ATTEMPTS="${EXPERIMENT_ATTEMPTS:-3}"
API_RETRY_PAUSE_SECONDS="${API_RETRY_PAUSE_SECONDS:-60}"
RUN_NO_EVOLVE_AFTER="${RUN_NO_EVOLVE_AFTER:-1}"
CONDA_ENV="${CONDA_ENV:-0622}"

config_name="$(basename "$LLM_CONFIG")"
config_name="${config_name%.*}"
config_name="${config_name//[^[:alnum:]._-]/_}"
namespace="${RESULTS_ROOT}/${config_name}/evolve${EVOLVE_CASE_COUNT}_evalall"

resume_work_dir() {
  case "$1" in
    swebench) printf '%s' "${RESUME_SWEBENCH_WORK_DIR:-}" ;;
    deep-swe) printf '%s' "${RESUME_DEEPSWE_WORK_DIR:-}" ;;
    dab) printf '%s' "${RESUME_DAB_WORK_DIR:-}" ;;
    *) return 1 ;;
  esac
}

declare -a failed=()
for benchmark in $MATRIX_BENCHMARKS; do
  work_dir="$(resume_work_dir "$benchmark")" || {
    echo "unsupported benchmark in MATRIX_BENCHMARKS: $benchmark" >&2
    failed+=("$benchmark")
    continue
  }
  if [[ -z "$work_dir" ]]; then
    work_dir="${namespace}/evolve/coat/${benchmark}/$(date +%m%d-%H%M%S)"
  fi
  mkdir -p "$work_dir"
  echo "[matrix] config=$config_name benchmark=$benchmark concurrency=$N_CONCURRENT work_dir=$work_dir"

  benchmark_ok=0
  for ((attempt=1; attempt<=EXPERIMENT_ATTEMPTS; attempt++)); do
    echo "[matrix] benchmark=$benchmark attempt=$attempt/$EXPERIMENT_ATTEMPTS"
    if env \
      BENCHMARKS="$benchmark" \
      LLM_CONFIG="$LLM_CONFIG" \
      RESULTS_ROOT="$RESULTS_ROOT" \
      WORK_DIR="$work_dir" \
      N_CONCURRENT="$N_CONCURRENT" \
      EVOLVE_WORKERS="$N_CONCURRENT" \
      EVOLVE_CASE_COUNT="$EVOLVE_CASE_COUNT" \
      EVAL_ALL_CASES=1 \
      COAT_N_CYCLES="$COAT_N_CYCLES" \
      RUN_NO_EVOLVE_AFTER="$RUN_NO_EVOLVE_AFTER" \
      API_RETRY_PAUSE_SECONDS="$API_RETRY_PAUSE_SECONDS" \
      MSWEA_MODEL_RETRY_WAIT_SECONDS="$API_RETRY_PAUSE_SECONDS" \
      CONDA_ENV="$CONDA_ENV" \
      bash "${SCRIPT_DIR}/run_exp.sh"; then
      benchmark_ok=1
      break
    fi
    if (( attempt < EXPERIMENT_ATTEMPTS )); then
      echo "[matrix] benchmark=$benchmark failed; sleep ${API_RETRY_PAUSE_SECONDS}s before resume"
      sleep "$API_RETRY_PAUSE_SECONDS"
    fi
  done
  if [[ "$benchmark_ok" != "1" ]]; then
    failed+=("$benchmark")
  fi
done

if [[ ${#failed[@]} -gt 0 ]]; then
  echo "[matrix] config=$config_name unfinished benchmarks: ${failed[*]}" >&2
  exit 1
fi
echo "[matrix] config=$config_name all benchmarks and paired no-evolve runs complete"
