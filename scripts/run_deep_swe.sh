#!/usr/bin/env bash
set -euo pipefail

# 加载公共变量和函数，用于读取 LLM 配置、结果目录和并发参数。
source "$(dirname "$0")/_bench_common.sh"

# 从 _config/*.yaml 解析模型名、API key、base URL（responses 时还导出 AZURE_*）。
load_llm_config

# 切换到项目根目录，确保后续相对路径都基于 CostReduce 仓库。
cd "$ROOT_DIR"

# responses（bytedance aidp 网关，如 _config/gpt53_codex.yaml）配置时，AZURE_API_KEY 已被
# load_llm_config 导出；此时 mini-swe-agent 必须走 litellm.responses(azure/...) 而非
# chat-completions——网关对 GPT 模型只暴露 Responses API，chat 路径会被网关以
# "Missing required parameter: 'input'" 拒绝。合成一份最小 mswea 配置 yaml
# （model.model_class=litellm_response），通过 --ak config_file= 注入；mini-swe-agent
# 的 -c 是 recursive_merge，仅覆盖 model_class，其余沿用 mini.yaml 默认。
# 非 responses 配置（AZURE_API_KEY 未设）时回退到 --ak model_class=litellm（原行为）。
MSWEA_CFG_TMP=""
EVOLVE_PROMPT_TEMPLATE=""
deep_swe_responses_config_file() {
  if [[ -z "${AZURE_API_KEY:-}" ]]; then return 0; fi
  local tmp
  tmp="$(mktemp -t deepswe_resp_cfg.XXXXXX.yaml)"
  printf 'model:\n  model_class: litellm_response\n' > "$tmp"
  printf '%s\n' "$tmp"
}
cleanup() {
  # 用 if 而非 `[[ ]] && rm`：条件不成立时 if 返回 0，避免无可清理临时文件时
  # trap 的非零返回值覆盖脚本本来的退出码。
  if [[ -n "${MSWEA_CFG_TMP:-}" && -f "${MSWEA_CFG_TMP}" ]]; then rm -f "${MSWEA_CFG_TMP}"; fi
  if [[ -n "${EVOLVE_PROMPT_TEMPLATE:-}" && -f "${EVOLVE_PROMPT_TEMPLATE}" ]]; then rm -f "${EVOLVE_PROMPT_TEMPLATE}"; fi
}
trap cleanup EXIT

# DeepSWE 任务目录。默认仓库内 tasks 目录；V3 闭环（src/evolve/evolve_v3_cycle.py）
# 会把 16 个 evolve case 软链到一个临时目录并把该目录赋给 DEEP_SWE_TASKS_PATH，
# 从而只在 16 个 case 上跑一轮验证。未设置时沿用默认全量 tasks 目录，行为不变。
DEEP_SWE_TASKS_PATH="${DEEP_SWE_TASKS_PATH:-$ROOT_DIR/benchmark/deep-swe/tasks}"

# 创建 DeepSWE 结果目录，避免 Pier 写入结果时目录不存在。
mkdir -p "$RESULTS_DIR/deep-swe"

# 生成传给 mini-swe-agent 容器/进程的 OpenAI-compatible 环境变量参数。
mapfile -t AGENT_ENV < <(agent_env_args)

# 可选：通过 EVOLVE_SKIP_FILE 跳过指定 case id（默认从 EVOLVE_SCRIPTS_DIR
# 下的 evolve_used_case_id.txt 自动读取，可显式覆盖或置空禁用）。
mapfile -t SKIP_ARGS < <(evolve_skip_exclude_args)

# 可选：若设置了 EVOLVE_SCRIPTS_DIR，则把其下所有文件 bind mount 到容器
# workspace 的辅助脚本目录（默认 /app/.preinstalled_scripts）。未设置时保持空，
# 沿用 Pier 默认 mounts 与默认 code agent。
EVOLVE_MOUNTS_ARGS=()
EVOLVE_PROMPT_ARGS=()
if [[ -n "${EVOLVE_SCRIPTS_DIR:-}" ]]; then
  EVOLVE_MOUNTS_JSON="$(evolve_scripts_mounts_json)"
  if [[ -n "${EVOLVE_MOUNTS_JSON}" ]]; then
    EVOLVE_MOUNTS_ARGS=(--mounts-json "${EVOLVE_MOUNTS_JSON}")
  fi
  EVOLVE_PROMPT_TEMPLATE="$(evolve_scripts_prompt_template)"
  if [[ -n "${EVOLVE_PROMPT_TEMPLATE}" ]]; then
    EVOLVE_PROMPT_ARGS=(--ak "prompt_template_path=${EVOLVE_PROMPT_TEMPLATE}")
  fi
fi

# model_class 注入：responses 配置用 litellm_response 临时配置文件，否则用 litellm。
MSWEA_MODEL_CLASS_ARGS=()
MSWEA_CFG_TMP="$(deep_swe_responses_config_file)"
if [[ -n "${MSWEA_CFG_TMP}" ]]; then
  MSWEA_MODEL_CLASS_ARGS=(--ak "config_file=${MSWEA_CFG_TMP}")
else
  MSWEA_MODEL_CLASS_ARGS=(--ak "model_class=litellm")
fi

# 使用 Pier 在 DeepSWE 全量任务集上运行 mini-swe-agent。
"$UV_BIN" tool run --from datacurve-pier pier run \
  -p "$DEEP_SWE_TASKS_PATH" \
  -a mini-swe-agent \
  -m "$MODEL" \
  -e "$HARBOR_ENV" \
  -k "$N_ATTEMPTS" \
  -n "$N_CONCURRENT" \
  --n-tasks "$N_TASKS" \
  -o "$RESULTS_DIR/deep-swe" \
  --job-name "$RUN_ID" \
  "${MSWEA_MODEL_CLASS_ARGS[@]}" \
  "${EVOLVE_MOUNTS_ARGS[@]}" \
  "${EVOLVE_PROMPT_ARGS[@]}" \
  "${SKIP_ARGS[@]}" \
  "${AGENT_ENV[@]}"
