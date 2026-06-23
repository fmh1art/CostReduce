#!/usr/bin/env bash
# Drive the full script-evolution pipeline:
#   1. Annotate trajectory step dependencies with an LLM.
#   2. Build positive/negative contrastive samples.
#   3. Evolve scripts + instruction.md via mini-swe-agent.
#
# All parameters can be overridden via environment variables.

set -euo pipefail

ROOT_DIR="${ROOT_DIR:-/home/fanmeihao/projects/CostReduce}"
LLM_CONFIG="${LLM_CONFIG:-${ROOT_DIR}/_config/deepseekv4_flash.yaml}"
SCRIPTS_DIR="${SCRIPTS_DIR:-${ROOT_DIR}/.evolve_scripts}"
MINI_SWE_AGENT_DIR="${MINI_SWE_AGENT_DIR:-${ROOT_DIR}/agent/mini-swe-agent}"

WORKERS="${WORKERS:-4}"
BATCH_SIZE="${BATCH_SIZE:-5}"
MAX_OBSERVATION_CHARS="${MAX_OBSERVATION_CHARS:-500}"
RETRY_FAILED="${RETRY_FAILED:-1}"

RESULT_DIR="${1:-${RESULT_DIR:-}}"
if [[ -z "${RESULT_DIR}" ]]; then
  echo "usage: $0 <result_dir> [extra args forwarded to python -m src.evolve run]" >&2
  exit 1
fi
shift || true

TASK_ARGS=()
if [[ -n "${TASK:-}" ]]; then
  TASK_ARGS=(--task "${TASK}")
fi

OUTPUT_ARGS=()
if [[ -n "${OUTPUT_DIR:-}" ]]; then
  OUTPUT_ARGS=(--output-dir "${OUTPUT_DIR}")
fi

LOG_ARGS=()
if [[ -n "${LOG_FILE:-}" ]]; then
  LOG_ARGS=(--log-file "${LOG_FILE}")
fi

cd "${ROOT_DIR}"

set -x
python -m src.evolve run "${RESULT_DIR}" \
  --config "${LLM_CONFIG}" \
  --scripts-dir "${SCRIPTS_DIR}" \
  --mini-swe-agent-dir "${MINI_SWE_AGENT_DIR}" \
  --workers "${WORKERS}" \
  --retry-failed "${RETRY_FAILED}" \
  --batch-size "${BATCH_SIZE}" \
  --max-observation-chars "${MAX_OBSERVATION_CHARS}" \
  "${TASK_ARGS[@]}" \
  "${OUTPUT_ARGS[@]}" \
  "${LOG_ARGS[@]}" \
  "$@"
