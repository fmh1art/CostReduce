#!/usr/bin/env bash
set -euo pipefail

# Run the same full benchmark experiment set as total_benchmark_exp.sh,
# excluding cases that were already used to evolve tools and pre-installing the
# evolved tools from ./.evolve_tools into each benchmark workspace.

ROOT_DIR="/home/fanmeihao/projects/CostReduce"
cd "${ROOT_DIR}"

# Fixed run parameters.
LLM_CONFIG="_config/deepseekv4_flash.yaml"
N_CONCURRENT=4
N_ATTEMPTS=1
RUN_ID="full-rerun-with-evolve-tools-n4-$(date +%m%d-%H%M%S)"
RESULTS_DIR="${ROOT_DIR}/results"
SKIP_CASE_ID_TXT="${ROOT_DIR}/results/evolve_used_case_id.txt"
EVOLVE_TOOLS_DIR="${ROOT_DIR}/.evolve_tools"

echo "[total_benchmark_exp_with_evolve_tools] root=${ROOT_DIR}"
echo "[total_benchmark_exp_with_evolve_tools] llm_config=${LLM_CONFIG}"
echo "[total_benchmark_exp_with_evolve_tools] run_id=${RUN_ID}"
echo "[total_benchmark_exp_with_evolve_tools] concurrency=${N_CONCURRENT}"
echo "[total_benchmark_exp_with_evolve_tools] results_dir=${RESULTS_DIR}"
echo "[total_benchmark_exp_with_evolve_tools] skip_case_id_txt=${SKIP_CASE_ID_TXT}"
echo "[total_benchmark_exp_with_evolve_tools] pre_install_tools=${EVOLVE_TOOLS_DIR}"

if [[ ! -f "${SKIP_CASE_ID_TXT}" ]]; then
  echo "[total_benchmark_exp_with_evolve_tools] ERROR: skip case id txt not found: ${SKIP_CASE_ID_TXT}" >&2
  exit 1
fi

if [[ ! -d "${EVOLVE_TOOLS_DIR}" ]]; then
  echo "[total_benchmark_exp_with_evolve_tools] ERROR: evolved tools dir not found: ${EVOLVE_TOOLS_DIR}" >&2
  exit 1
fi

mkdir -p "${RESULTS_DIR}/logs"

run_and_log() {
  local name="$1"
  shift
  local log_file="${RESULTS_DIR}/logs/${RUN_ID}_${name}.log"

  echo
  echo "========== [${name}] START $(date '+%F %T') ==========" | tee "${log_file}"
  echo "+ $*" | tee -a "${log_file}"
  "$@" 2>&1 | tee -a "${log_file}"
  local status=${PIPESTATUS[0]}
  echo "========== [${name}] END status=${status} $(date '+%F %T') ==========" | tee -a "${log_file}"
  return "${status}"
}

# 1. DeepSWE: all tasks except cases listed in SKIP_CASE_ID_TXT, with evolved tools.
run_and_log "deep-swe" \
  python example/benchmark_code_agent.py \
    --benchmark deep-swe \
    --llm-config "${LLM_CONFIG}" \
    --jobs-dir "${RESULTS_DIR}" \
    --skip_case_id_txt "${SKIP_CASE_ID_TXT}" \
    --pre_install_tools "${EVOLVE_TOOLS_DIR}" \
    --run-id "${RUN_ID}" \
    -n "${N_CONCURRENT}" \
    -k "${N_ATTEMPTS}"

# 2. SWE-Atlas: all qa/tw/rf tasks except cases listed in SKIP_CASE_ID_TXT, with evolved tools.
run_and_log "swe-atlas" \
  python example/benchmark_code_agent.py \
    --benchmark swe-atlas \
    --swe-atlas-splits qa,tw,rf \
    --llm-config "${LLM_CONFIG}" \
    --jobs-dir "${RESULTS_DIR}" \
    --skip_case_id_txt "${SKIP_CASE_ID_TXT}" \
    --pre_install_tools "${EVOLVE_TOOLS_DIR}" \
    --run-id "${RUN_ID}" \
    -n "${N_CONCURRENT}" \
    -k "${N_ATTEMPTS}"

# 3. DataMind/LongDS: full data benchmark set except cases listed in SKIP_CASE_ID_TXT, with evolved tools.
#    DataMind Python/SQL use --bs as their batch/concurrency knob.
run_and_log "datamind-longds" \
  python example/benchmark_data_agent.py \
    --suite all \
    --llm-config "${LLM_CONFIG}" \
    --output-dir "${RESULTS_DIR}" \
    --run-id "${RUN_ID}" \
    --skip_case_id_txt "${SKIP_CASE_ID_TXT}" \
    --pre_install_tools "${EVOLVE_TOOLS_DIR}" \
    --bs "${N_CONCURRENT}"

echo
echo "[total_benchmark_exp_with_evolve_tools] All benchmark commands completed."
echo "[total_benchmark_exp_with_evolve_tools] Logs: ${RESULTS_DIR}/logs/${RUN_ID}_*.log"
