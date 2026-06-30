#!/usr/bin/env bash
set -euo pipefail

# 在 Harbor 上运行 SWE-Bench Verified benchmark 的封装脚本。
#
# 数据来源（二选一，由环境变量控制）：
#   SWEBENCH_TASK_PATH=<目录>   使用本地已生成的任务目录（传 -p）。
#       可用 adapter 生成：
#         cd "$ROOT_DIR/tmp/harbor/adapters/swebench" && uv run swebench --limit 10
#       产物默认写到 tmp/harbor/datasets/swebench-verified/，再把该目录赋给
#       SWEBENCH_TASK_PATH 即可。
#   否则                        使用 Harbor registry 数据集（传 -d），默认
#                              SWEBENCH_DATASET=swebench-verified（500 条 SWE-bench
#                              Verified；首次运行会从 github 拉 harbor-datasets 仓库，
#                              需要外网/代理，见下文 EXPORT_HOST_PROXY）。
#
# LLM 配置：复用 _config/*.yaml（默认 _config/deepseekv4_flash.yaml，可用 LLM_CONFIG 覆盖）。
#   - chat 类配置（如 deepseekv4_flash）：不传 config_file，mini-swe-agent 用内置
#     mini.yaml 走 chat-completions。
#   - responses 类配置（如 gpt53_codex，bytedance aidp 网关只暴露 Responses API）：
#     自动合成一份仅含 model.model_class=litellm_response 的临时 mswea 配置，让
#     mini-swe-agent 走 litellm.responses(azure/...) 路由到网关 Responses API；
#     凭据（AZURE_API_KEY/BASE/VERSION）由 agent_env_args 注入容器环境。
#     （mini-swe-agent 的 -c 是 recursive_merge，故最小配置只覆盖 model_class，
#      其余沿用 mini.yaml 默认。）
#   - 也可用 SWEBENCH_MSWEA_CONFIG=<yaml> 直接指定一份完整 mswea 配置，覆盖以上自动行为。
#
# 与 SWE-Atlas 的关键差异：SWE-Bench 的 verifier 是确定性 pytest + swebench parser
# （非 LLM judge），因此不注入 verifier LLM 配置；但 tests/test.sh 里 `uv run parser.py`
# 会按 PEP 723 拉取 swebench==4.0.3 等依赖，故仍需给 verifier 传代理（--ve）。
#
# 默认 N_TASKS=1 仅做 smoke（单任务）；全量评测设 N_TASKS=500（或更大的值表示不限）。
#
# 前置依赖：
#   - Docker（默认 HARBOR_ENV=docker），且 docker daemon 能拉取 swebench/sweb.eval.*
#     镜像（需在 docker daemon 层面配置代理；本脚本只能控制 harbor 进程与容器内代理）。
#   - 外网/代理：registry 数据集首次拉取、容器内 pip/uv 拉依赖、agent 调 LLM 网关均需要。
#     EXPORT_HOST_PROXY=1（默认）会把代理导出到 harbor 进程环境，覆盖 registry 的
#     git 拉取等 host 侧联网；容器内代理由 --ae/--ve 注入。
#   - 也可改用 HARBOR_ENV=modal/daytona 等云沙箱规避本地 docker 镜像拉取（需对应云凭证）。

# RUN_ID / N_TASKS 默认值需在 source _bench_common.sh 之前设置，否则会被其默认覆盖。
RUN_ID="${RUN_ID:-swebench-$(date +%m%d-%H%M%S)}"
# 默认 smoke：仅跑 1 个任务。全量请显式 N_TASKS=500（或更大表示不限）。
N_TASKS="${N_TASKS:-1}"

source "$(dirname "$0")/_bench_common.sh"

# SWE-Bench 专属配置（均可在 source 后由调用方覆盖）。
SWEBENCH_DATASET="${SWEBENCH_DATASET:-swebench-verified}"   # -d 数据集名（registry）
SWEBENCH_TASK_PATH="${SWEBENCH_TASK_PATH:-}"                # -p 本地任务目录（优先于 -d）
SWEBENCH_MSWEA_CONFIG="${SWEBENCH_MSWEA_CONFIG:-}"           # 显式 mswea 配置 yaml
SWEBENCH_RESULTS_SUBDIR="${SWEBENCH_RESULTS_SUBDIR:-swebench-verified}"
EXPORT_HOST_PROXY="${EXPORT_HOST_PROXY:-1}"

swebench_verifier_proxy_args() {
  # SWE-Bench verifier 不调 LLM，但 tests/test.sh 的 `uv run parser.py` 会按 PEP 723
  # 拉取 swebench==4.0.3 等依赖，需要代理。这里只输出代理（+ uv 超时）给 --ve。
  printf '%s\n' \
    --ve "HTTP_PROXY=${PROXY_URL}" \
    --ve "HTTPS_PROXY=${PROXY_URL}" \
    --ve "http_proxy=${PROXY_URL}" \
    --ve "https_proxy=${PROXY_URL}" \
    --ve "NO_PROXY=localhost,127.0.0.1,::1" \
    --ve "no_proxy=localhost,127.0.0.1,::1" \
    --ve "UV_HTTP_TIMEOUT=300"
}

swebench_responses_config_file() {
  # responses（bytedance aidp 网关）配置时，合成一份最小 mswea 配置 yaml：
  #   model:
  #     model_class: litellm_response
  # 让 mini-swe-agent 走 litellm.responses(azure/...) 路由到网关 Responses API。
  # 非 responses 配置（AZURE_API_KEY 未设）时不生成，返回空串。
  # 调用方负责在退出时删除返回的临时文件。
  if [[ -z "${AZURE_API_KEY:-}" ]]; then return 0; fi
  local tmp
  tmp="$(mktemp -t swebench_resp_cfg.XXXXXX.yaml)"
  printf 'model:\n  model_class: litellm_response\n' > "$tmp"
  printf '%s\n' "$tmp"
}

# 退出时清理本脚本合成的临时文件（responses 配置 + evolve prompt 模板）。
MSWEA_CFG_TMP=""
EVOLVE_PROMPT_TEMPLATE=""
cleanup() {
  # 用 if 而非 `[[ ]] && rm`：条件不成立时 if 语句返回 0，避免在无可清理临时文件时
  # 让 trap 的非零返回值覆盖脚本本来的退出码。
  if [[ -n "${MSWEA_CFG_TMP:-}" && -f "${MSWEA_CFG_TMP}" ]]; then rm -f "${MSWEA_CFG_TMP}"; fi
  if [[ -n "${EVOLVE_PROMPT_TEMPLATE:-}" && -f "${EVOLVE_PROMPT_TEMPLATE}" ]]; then rm -f "${EVOLVE_PROMPT_TEMPLATE}"; fi
}
trap cleanup EXIT

# 从 _config/*.yaml 解析模型名、API key、base URL（responses 时还导出 AZURE_*）。
load_llm_config

cd "$ROOT_DIR"
mkdir -p "$RESULTS_DIR/$SWEBENCH_RESULTS_SUBDIR"

# agent 容器/进程的 OpenAI-compatible 环境变量（LLM 凭据 + base_url + AZURE_*）。
mapfile -t AGENT_ENV < <(agent_env_args)
# agent setup/运行阶段的代理环境变量（容器内 apt/curl/pip/uv + 调 LLM 网关）。
mapfile -t PROXY_ENV < <(proxy_env_args)
# verifier 阶段的代理（仅代理 + uv 超时，无 LLM 配置）。
mapfile -t VERIFIER_PROXY_ENV < <(swebench_verifier_proxy_args)

# 可选：通过 EVOLVE_SKIP_FILE 跳过指定 case id（与 run_swe_atlas.sh 同语义）。
mapfile -t SKIP_ARGS < <(evolve_skip_exclude_args)

# 可选：若设置了 EVOLVE_SCRIPTS_DIR，把其下文件 bind mount 到容器辅助脚本目录，
# 并把 instruction.md + tools block 注入 prompt 模板。Harbor 的 Trial 层会自动追加
# /logs/{agent,verifier,artifacts} 三个默认 bind mount，故强制
# EVOLVE_SCRIPTS_INCLUDE_DEFAULT_LOG_MOUNTS=0 避免重复挂载触发冲突。
EVOLVE_MOUNTS_ARGS=()
EVOLVE_PROMPT_ARGS=()
if [[ -n "${EVOLVE_SCRIPTS_DIR:-}" ]]; then
  EVOLVE_MOUNTS_JSON="$(EVOLVE_SCRIPTS_INCLUDE_DEFAULT_LOG_MOUNTS=0 evolve_scripts_mounts_json)"
  if [[ -n "${EVOLVE_MOUNTS_JSON}" ]]; then
    EVOLVE_MOUNTS_ARGS=(--mounts "${EVOLVE_MOUNTS_JSON}")
  fi
  EVOLVE_PROMPT_TEMPLATE="$(evolve_scripts_prompt_template)"
  if [[ -n "${EVOLVE_PROMPT_TEMPLATE}" ]]; then
    EVOLVE_PROMPT_ARGS=(--ak "prompt_template_path=${EVOLVE_PROMPT_TEMPLATE}")
  fi
fi

# 选择 mini-swe-agent 的 config_file：
#   - SWEBENCH_MSWEA_CONFIG 显式指定 → 直接用。
#   - responses 配置 → 合成 litellm_response 临时配置。
#   - chat 配置 → 不传 config_file，沿用 mini.yaml 默认。
MSWEA_CFG_ARGS=()
if [[ -n "${SWEBENCH_MSWEA_CONFIG}" ]]; then
  MSWEA_CFG_ARGS=(--ak "config_file=${SWEBENCH_MSWEA_CONFIG}")
else
  MSWEA_CFG_TMP="$(swebench_responses_config_file)"
  if [[ -n "${MSWEA_CFG_TMP}" ]]; then
    MSWEA_CFG_ARGS=(--ak "config_file=${MSWEA_CFG_TMP}")
  fi
fi

# 数据来源参数：优先本地任务目录（-p），否则 registry 数据集（-d）。
DATA_ARGS=()
if [[ -n "${SWEBENCH_TASK_PATH}" ]]; then
  if [[ ! -d "${SWEBENCH_TASK_PATH}" ]]; then
    echo "[run_swe_bench] SWEBENCH_TASK_PATH='${SWEBENCH_TASK_PATH}' is not a directory" >&2
    exit 1
  fi
  DATA_ARGS=(-p "${SWEBENCH_TASK_PATH}")
else
  DATA_ARGS=(-d "${SWEBENCH_DATASET}")
fi

# 把代理导出到 harbor 进程环境，覆盖 registry 数据集的 git 拉取等 host 侧联网。
# （docker 镜像拉取由 docker daemon 自身代理配置控制，本脚本不覆盖。）
if [[ "${EXPORT_HOST_PROXY}" == "1" ]]; then
  export HTTP_PROXY="$PROXY_URL" HTTPS_PROXY="$PROXY_URL"
  export http_proxy="$PROXY_URL" https_proxy="$PROXY_URL"
  export NO_PROXY="localhost,127.0.0.1,::1" no_proxy="localhost,127.0.0.1,::1"
fi

# 使用 Harbor 在 SWE-Bench Verified 上运行 mini-swe-agent。
"$UV_BIN" run --directory "$ROOT_DIR/tmp/harbor" harbor run \
  "${DATA_ARGS[@]}" \
  -a mini-swe-agent \
  -m "$MODEL" \
  -e "$HARBOR_ENV" \
  -k "$N_ATTEMPTS" \
  -n "$N_CONCURRENT" \
  --agent-setup-timeout-multiplier "$HARBOR_AGENT_SETUP_TIMEOUT_MULTIPLIER" \
  --n-tasks "$N_TASKS" \
  -o "$RESULTS_DIR/$SWEBENCH_RESULTS_SUBDIR" \
  --job-name "$RUN_ID" \
  --yes \
  "${MSWEA_CFG_ARGS[@]}" \
  "${EVOLVE_MOUNTS_ARGS[@]}" \
  "${EVOLVE_PROMPT_ARGS[@]}" \
  "${SKIP_ARGS[@]}" \
  "${AGENT_ENV[@]}" \
  "${PROXY_ENV[@]}" \
  "${VERIFIER_PROXY_ENV[@]}"
