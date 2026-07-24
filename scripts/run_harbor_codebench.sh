#!/usr/bin/env bash
set -euo pipefail

# Shared Harbor runner for locally exported, deterministic code-agent datasets.
# The thin benchmark wrappers set HARBOR_CODEBENCH_* before entering here.

: "${HARBOR_CODEBENCH_NAME:?set HARBOR_CODEBENCH_NAME}"
: "${HARBOR_CODEBENCH_TASK_PATH:?set HARBOR_CODEBENCH_TASK_PATH}"
: "${HARBOR_CODEBENCH_RESULTS_SUBDIR:?set HARBOR_CODEBENCH_RESULTS_SUBDIR}"

RUN_ID="${RUN_ID:-${HARBOR_CODEBENCH_NAME}-$(date +%m%d-%H%M%S)}"
N_TASKS="${N_TASKS:-1}"

source "$(dirname "$0")/_bench_common.sh"

EXPORT_HOST_PROXY="${EXPORT_HOST_PROXY:-1}"
MSWEA_MAXTOK_CONFIG="${MSWEA_MAXTOK_CONFIG:-$ROOT_DIR/_config/mswea_maxtok.yaml}"

deterministic_verifier_proxy_args() {
  printf '%s\n' \
    --ve "TEST_DIR=/tests" \
    --ve "HTTP_PROXY=${PROXY_URL}" \
    --ve "HTTPS_PROXY=${PROXY_URL}" \
    --ve "http_proxy=${PROXY_URL}" \
    --ve "https_proxy=${PROXY_URL}" \
    --ve "NO_PROXY=localhost,127.0.0.1,::1" \
    --ve "no_proxy=localhost,127.0.0.1,::1" \
    --ve "UV_HTTP_TIMEOUT=300"
}

MSWEA_CFG_TMP=""
EVOLVE_PROMPT_TEMPLATE=""
cleanup() {
  if [[ -n "${MSWEA_CFG_TMP:-}" && -f "${MSWEA_CFG_TMP}" ]]; then
    rm -f "${MSWEA_CFG_TMP}"
  fi
  if [[ -n "${EVOLVE_PROMPT_TEMPLATE:-}" && -f "${EVOLVE_PROMPT_TEMPLATE}" ]]; then
    rm -f "${EVOLVE_PROMPT_TEMPLATE}"
  fi
}
trap cleanup EXIT

load_llm_config

if [[ ! -d "${HARBOR_CODEBENCH_TASK_PATH}" ]]; then
  echo "[run_harbor_codebench] task path is not a directory: ${HARBOR_CODEBENCH_TASK_PATH}" >&2
  exit 1
fi

cd "$ROOT_DIR"
mkdir -p "$RESULTS_DIR/$HARBOR_CODEBENCH_RESULTS_SUBDIR"

mapfile -t AGENT_ENV < <(agent_env_args)
mapfile -t PROXY_ENV < <(proxy_env_args)
mapfile -t VERIFIER_PROXY_ENV < <(deterministic_verifier_proxy_args)
mapfile -t SKIP_ARGS < <(evolve_skip_exclude_args)

# Always mount the retry runtime and shared uv cache. When an evolved harness is
# present, also deploy its native tools and append its instruction template.
EVOLVE_MOUNTS_ARGS=()
EVOLVE_PROMPT_ARGS=()
EVOLVE_NATIVE_ARGS=()
EVOLVE_MOUNTS_JSON="$(
  EVOLVE_SCRIPTS_INCLUDE_DEFAULT_LOG_MOUNTS=0 evolve_scripts_mounts_json
)"
if [[ -n "${EVOLVE_MOUNTS_JSON}" ]]; then
  EVOLVE_MOUNTS_ARGS=(--mounts "${EVOLVE_MOUNTS_JSON}")
fi
if [[ -n "${EVOLVE_SCRIPTS_DIR:-}" ]]; then
  EVOLVE_PROMPT_TEMPLATE="$(evolve_scripts_prompt_template)"
  if [[ -n "${EVOLVE_PROMPT_TEMPLATE}" ]]; then
    EVOLVE_PROMPT_ARGS=(--ak "prompt_template_path=${EVOLVE_PROMPT_TEMPLATE}")
  fi
  evolve_scripts_deploy || exit 1
  mapfile -t EVOLVE_NATIVE_ARGS < <(evolve_scripts_native_tools_args)
fi

MSWEA_CFG_ARGS=()
if [[ -z "${EVOLVE_TOOLS_CONFIG_HOST:-}" ]]; then
  MSWEA_MODEL_CLASS=""
  [[ "${LLM_API_TYPE:-chat}" == "responses" ]] \
    && MSWEA_MODEL_CLASS="litellm_response"
  MSWEA_CFG_TMP="$(
    mswea_llm_config_file "$MSWEA_MAXTOK_CONFIG" "$MSWEA_MODEL_CLASS"
  )"
  MSWEA_CFG_ARGS=(--ak "config_file=${MSWEA_CFG_TMP}")
fi

if [[ "${EXPORT_HOST_PROXY}" == "1" ]]; then
  export HTTP_PROXY="$PROXY_URL" HTTPS_PROXY="$PROXY_URL"
  export http_proxy="$PROXY_URL" https_proxy="$PROXY_URL"
  export NO_PROXY="localhost,127.0.0.1,::1"
  export no_proxy="$NO_PROXY"
fi

"$UV_BIN" run --directory "$ROOT_DIR/tmp/harbor" harbor run \
  -p "$HARBOR_CODEBENCH_TASK_PATH" \
  -a mini-swe-agent \
  -m "$MODEL" \
  -e "$HARBOR_ENV" \
  -k "$N_ATTEMPTS" \
  -n "$N_CONCURRENT" \
  --agent-setup-timeout-multiplier "$HARBOR_AGENT_SETUP_TIMEOUT_MULTIPLIER" \
  --n-tasks "$N_TASKS" \
  -o "$RESULTS_DIR/$HARBOR_CODEBENCH_RESULTS_SUBDIR" \
  --job-name "$RUN_ID" \
  --yes \
  "${MSWEA_CFG_ARGS[@]}" \
  "${EVOLVE_NATIVE_ARGS[@]}" \
  "${EVOLVE_MOUNTS_ARGS[@]}" \
  "${EVOLVE_PROMPT_ARGS[@]}" \
  "${SKIP_ARGS[@]}" \
  "${AGENT_ENV[@]}" \
  "${PROXY_ENV[@]}" \
  "${VERIFIER_PROXY_ENV[@]}"
