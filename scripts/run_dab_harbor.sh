#!/usr/bin/env bash
set -euo pipefail

# 在 Harbor 上运行 DataAgentBench (DAB) 的封装脚本。
#
# 数据与 adapter：
#   DAB 源仓库默认位于 benchmark/DBA-bench/DataAgentBench。
#   本脚本会调用 benchmark/DBA-bench/dab_harbor_adapter.py，把每个 DAB query
#   转成一个 Harbor task（task.toml + instruction.md + environment + tests）。
#   生成目录默认：
#     benchmark/DBA-bench/harbor/datasets/dab
#
# 常用参数：
#   DAB_TASK_PATH=<目录>       使用已有 Harbor task 目录，不自动生成。
#   DAB_REGENERATE_TASKS=1     重新生成 task 目录。
#   DAB_PREP_LIMIT=10          只生成前 10 个 task；0 表示全量。
#   DAB_DATASETS=bookreview    只生成/运行指定 dataset，逗号分隔。
#   DAB_USE_HINTS=1            把 db_description_withhint.txt 写入 instruction。
#   N_TASKS=1                  Harbor 实际运行 task 数；默认 smoke=1，全量可设 104。
#
# 运行时：
#   - SQLite/DuckDB 直接读文件。
#   - PostgreSQL/MongoDB 由每个 Harbor task 的 docker-compose 按需启动并初始化。
#   - agent 需要把最终答案写入 /app/answer.txt；verifier 调原始 DAB validate.py。

RUN_ID="${RUN_ID:-dab-$(date +%m%d-%H%M%S)}"
N_TASKS="${N_TASKS:-1}"
# Original DataAgentBench's ExecTool allows a single database/Python command to
# run for 600 seconds. Preserve that budget for both bash and evolved tools.
EVOLVE_TOOLS_V6_TIMEOUT_SECONDS="${EVOLVE_TOOLS_V6_TIMEOUT_SECONDS:-600}"

source "$(dirname "$0")/_bench_common.sh"

DAB_ROOT="${DAB_ROOT:-$ROOT_DIR/benchmark/DBA-bench/DataAgentBench}"
DAB_ADAPTER="${DAB_ADAPTER:-$ROOT_DIR/benchmark/DBA-bench/dab_harbor_adapter.py}"
DAB_TASK_PATH="${DAB_TASK_PATH:-$ROOT_DIR/benchmark/DBA-bench/harbor/datasets/dab}"
DAB_RESULTS_SUBDIR="${DAB_RESULTS_SUBDIR:-dab}"
DAB_REGENERATE_TASKS="${DAB_REGENERATE_TASKS:-0}"
DAB_PREP_LIMIT="${DAB_PREP_LIMIT:-0}"
DAB_DATASETS="${DAB_DATASETS:-}"
DAB_USE_HINTS="${DAB_USE_HINTS:-0}"
DAB_MSWEA_CONFIG="${DAB_MSWEA_CONFIG:-}"
EXPORT_HOST_PROXY="${EXPORT_HOST_PROXY:-1}"
MSWEA_MAXTOK_CONFIG="${MSWEA_MAXTOK_CONFIG:-$ROOT_DIR/_config/mswea_maxtok.yaml}"

dab_verifier_proxy_args() {
  printf '%s\n' \
    --ve "HTTP_PROXY=${PROXY_URL}" \
    --ve "HTTPS_PROXY=${PROXY_URL}" \
    --ve "http_proxy=${PROXY_URL}" \
    --ve "https_proxy=${PROXY_URL}" \
    --ve "NO_PROXY=postgres,mongo,localhost,127.0.0.1,::1" \
    --ve "no_proxy=postgres,mongo,localhost,127.0.0.1,::1" \
    --ve "UV_HTTP_TIMEOUT=300"
}

MSWEA_CFG_TMP=""
EVOLVE_PROMPT_TEMPLATE=""
cleanup() {
  if [[ -n "${MSWEA_CFG_TMP:-}" && -f "${MSWEA_CFG_TMP}" ]]; then rm -f "${MSWEA_CFG_TMP}"; fi
  if [[ -n "${EVOLVE_PROMPT_TEMPLATE:-}" && -f "${EVOLVE_PROMPT_TEMPLATE}" ]]; then rm -f "${EVOLVE_PROMPT_TEMPLATE}"; fi
}
trap cleanup EXIT

load_llm_config

cd "$ROOT_DIR"
mkdir -p "$RESULTS_DIR/$DAB_RESULTS_SUBDIR"

if [[ ! -d "$DAB_ROOT" ]]; then
  echo "[run_dab_harbor] DAB_ROOT 不存在：$DAB_ROOT" >&2
  echo "  请先在 benchmark/DBA-bench 下准备 DataAgentBench 并运行 download.sh。" >&2
  exit 1
fi

if [[ ! -f "$DAB_ADAPTER" ]]; then
  echo "[run_dab_harbor] DAB adapter 不存在：$DAB_ADAPTER" >&2
  exit 1
fi

if [[ "${EXPORT_HOST_PROXY}" == "1" ]]; then
  export HTTP_PROXY="$PROXY_URL" HTTPS_PROXY="$PROXY_URL"
  export http_proxy="$PROXY_URL" https_proxy="$PROXY_URL"
  export NO_PROXY="postgres,mongo,localhost,127.0.0.1,::1" no_proxy="postgres,mongo,localhost,127.0.0.1,::1"
fi

dab_task_count() {
  local root="$1" d count=0
  [[ -d "$root" ]] || { printf '0\n'; return 0; }
  # Exact experiment taskdirs contain symlinks to task directories and do not
  # necessarily have a manifest.json.  Test each child explicitly so symlinked
  # task.toml files count as existing tasks.
  for d in "$root"/*; do
    [[ -f "$d/task.toml" ]] && count=$((count + 1))
  done
  printf '%s\n' "$count"
}

dab_agent_answers_exposed() {
  local root="$1"
  [[ -d "$root" ]] || return 1
  find -L "$root" -type f \
    \( -path '*/environment/dab/query/ground_truth.csv' \
       -o -path '*/environment/dab/query/validate.py' \) \
    -print -quit 2>/dev/null | grep -q .
}

DAB_EXISTING_TASKS="$(dab_task_count "$DAB_TASK_PATH")"
DAB_LEGACY_LEAK=0
if dab_agent_answers_exposed "$DAB_TASK_PATH"; then
  DAB_LEGACY_LEAK=1
  if [[ -f "$DAB_TASK_PATH/manifest.json" ]]; then
    echo "[run_dab_harbor] detected legacy answer-leaking tasks; regenerating $DAB_TASK_PATH" >&2
  elif [[ "$DAB_REGENERATE_TASKS" != "1" ]]; then
    echo "[run_dab_harbor] refusing answer-leaking selected taskdir: $DAB_TASK_PATH" >&2
    echo "  Regenerate the canonical DAB tasks, then rebuild this selected taskdir." >&2
    exit 1
  fi
fi
if [[ "${DAB_REGENERATE_TASKS}" == "1" || "$DAB_LEGACY_LEAK" -eq 1 || ! -d "${DAB_TASK_PATH}" || "$DAB_EXISTING_TASKS" -eq 0 ]]; then
  echo "[run_dab_harbor] generating DAB Harbor tasks at ${DAB_TASK_PATH}"
  GEN_ARGS=(
    --dab-root "$DAB_ROOT"
    --output-dir "$DAB_TASK_PATH"
    --limit "$DAB_PREP_LIMIT"
    --overwrite
  )
  if [[ -n "$DAB_DATASETS" ]]; then
    GEN_ARGS+=(--datasets "$DAB_DATASETS")
  fi
  if [[ "$DAB_USE_HINTS" == "1" ]]; then
    GEN_ARGS+=(--use-hints)
  fi
  python "$DAB_ADAPTER" "${GEN_ARGS[@]}"
else
  echo "[run_dab_harbor] using existing DAB Harbor tasks at ${DAB_TASK_PATH} (${DAB_EXISTING_TASKS} tasks)"
fi

if [[ ! -d "$DAB_TASK_PATH" ]]; then
  echo "[run_dab_harbor] DAB_TASK_PATH 不是目录：$DAB_TASK_PATH" >&2
  exit 1
fi
if dab_agent_answers_exposed "$DAB_TASK_PATH"; then
  echo "[run_dab_harbor] generated task still exposes ground truth/validator; refusing to run" >&2
  exit 1
fi

mapfile -t AGENT_ENV < <(agent_env_args)
# Harbor 会在每个全新的 DAB agent 容器内从 astral.sh 安装 uv。
# 内网代理访问 Astral/Cloudflare 偶发 504，而宿主直连可用，因此仅对
# DAB agent 的这两个下载域名绕过代理；LLM API 和其他 benchmark 不受影响。
mapfile -t PROXY_ENV < <(proxy_env_args "astral.sh,releases.astral.sh")
mapfile -t VERIFIER_PROXY_ENV < <(dab_verifier_proxy_args)
mapfile -t SKIP_ARGS < <(evolve_skip_exclude_args)

EVOLVE_MOUNTS_ARGS=()
EVOLVE_PROMPT_ARGS=()
EVOLVE_NATIVE_ARGS=()
EVOLVE_MOUNTS_JSON="$(EVOLVE_SCRIPTS_INCLUDE_DEFAULT_LOG_MOUNTS=0 evolve_scripts_mounts_json)"
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
if [[ ${#EVOLVE_NATIVE_ARGS[@]} -eq 0 ]]; then
  MSWEA_CFG_BASE="${DAB_MSWEA_CONFIG:-${MSWEA_MAXTOK_CONFIG}}"
  MSWEA_MODEL_CLASS=""
  [[ "${LLM_API_TYPE:-chat}" == "responses" ]] && MSWEA_MODEL_CLASS="litellm_response"
  MSWEA_CFG_TMP="$(mswea_llm_config_file "$MSWEA_CFG_BASE" "$MSWEA_MODEL_CLASS" "$EVOLVE_TOOLS_V6_TIMEOUT_SECONDS")"
  MSWEA_CFG_ARGS=(--ak "config_file=${MSWEA_CFG_TMP}")
fi

"$UV_BIN" run --directory "$ROOT_DIR/tmp/harbor" harbor run \
  -p "$DAB_TASK_PATH" \
  -a mini-swe-agent \
  -m "$MODEL" \
  -e "$HARBOR_ENV" \
  -k "$N_ATTEMPTS" \
  -n "$N_CONCURRENT" \
  --agent-setup-timeout-multiplier "$HARBOR_AGENT_SETUP_TIMEOUT_MULTIPLIER" \
  --n-tasks "$N_TASKS" \
  -o "$RESULTS_DIR/$DAB_RESULTS_SUBDIR" \
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
