#!/usr/bin/env bash
set -euo pipefail

# 加载公共变量和函数，用于读取 LLM 配置、结果目录和并发参数。
source "$(dirname "$0")/_bench_common.sh"

# 从 _config/*.yaml 解析模型名、API key、base URL（responses 时还导出 AZURE_*）。
load_llm_config

# 切换到项目根目录，确保后续相对路径都基于 CostReduce 仓库。
cd "$ROOT_DIR"

# 为普通 agent 合成无凭据的临时 mswea 配置：responses 使用
# litellm_response，其余协议使用 litellm，并统一注入 LLM_CONFIG 中的
# temperature/thinking。evolved native-tools 模式由 deploy 生成等价配置。
MSWEA_CFG_TMP=""
EVOLVE_PROMPT_TEMPLATE=""
cleanup() {
  # 用 if 而非 `[[ ]] && rm`：条件不成立时 if 返回 0，避免无可清理临时文件时
  # trap 的非零返回值覆盖脚本本来的退出码。
  if [[ -n "${MSWEA_CFG_TMP:-}" && -f "${MSWEA_CFG_TMP}" ]]; then rm -f "${MSWEA_CFG_TMP}"; fi
  if [[ -n "${EVOLVE_PROMPT_TEMPLATE:-}" && -f "${EVOLVE_PROMPT_TEMPLATE}" ]]; then rm -f "${EVOLVE_PROMPT_TEMPLATE}"; fi
}
trap cleanup EXIT

# DeepSWE 任务目录。默认仓库内 tasks；COAT 实验入口会把选中的 evolve case
# 软链到临时目录并通过 DEEP_SWE_TASKS_PATH 传入。未设置时沿用全量目录。
DEEP_SWE_TASKS_PATH="${DEEP_SWE_TASKS_PATH:-$ROOT_DIR/benchmark/deep-swe/tasks}"

# 创建 DeepSWE 结果目录，避免 Pier 写入结果时目录不存在。
mkdir -p "$RESULTS_DIR/deep-swe"

# 生成传给 mini-swe-agent 容器/进程的 OpenAI-compatible 环境变量参数。
mapfile -t AGENT_ENV < <(agent_env_args)

# 可选：通过 EVOLVE_SKIP_FILE 跳过指定 case id（默认从 EVOLVE_SCRIPTS_DIR
# 下的 evolve_used_case_id.txt 自动读取，可显式覆盖或置空禁用）。
mapfile -t SKIP_ARGS < <(evolve_skip_exclude_args)

# 可选：若设置了 EVOLVE_SCRIPTS_DIR，则把其下所有文件 bind mount 到容器
# workspace 的辅助脚本目录（默认 /app/.preinstalled_scripts），并把 evolved
# scripts 注册成 native function tools（model/agent 子类 + manifest）。未设置
# 时保持空，沿用 Pier 默认 mounts 与默认 code agent。
EVOLVE_MOUNTS_ARGS=()
EVOLVE_PROMPT_ARGS=()
EVOLVE_NATIVE_ARGS=()
EVOLVE_MOUNTS_JSON="$(evolve_scripts_mounts_json)"
if [[ -n "${EVOLVE_MOUNTS_JSON}" ]]; then
  EVOLVE_MOUNTS_ARGS=(--mounts-json "${EVOLVE_MOUNTS_JSON}")
fi
if [[ -n "${EVOLVE_SCRIPTS_DIR:-}" ]]; then
  EVOLVE_PROMPT_TEMPLATE="$(evolve_scripts_prompt_template)"
  if [[ -n "${EVOLVE_PROMPT_TEMPLATE}" ]]; then
    EVOLVE_PROMPT_ARGS=(--ak "prompt_template_path=${EVOLVE_PROMPT_TEMPLATE}")
  fi
  # 把 evolved scripts 注册成 native function tools：生成 manifest/runtime/config，
  # 输出 --ak config_file=（设 model_class+agent_class）+ --ae EVOLVE_TOOLS_*。
  evolve_scripts_deploy || exit 1
  mapfile -t EVOLVE_NATIVE_ARGS < <(evolve_scripts_native_tools_args)
fi

# model_class 注入：仅在没有 evolved native tools 时走原 litellm/litellm_response
# 路径。有 evolved tools 时 model_class+agent_class 由 EVOLVE_NATIVE_ARGS 里的
# config_file 设定（evolve_tools.model.* / evolve_tools.agent.EvolveToolsAgent）。
# 这里以 EVOLVE_NATIVE_ARGS 是否为空为准，避免 EVOLVE_TOOLS_CONFIG_HOST 在某些调用路径
# 下未保留到当前 shell 时，错误地再追加一层 litellm_response/litellm，覆盖 native tools
# 的模型路由配置并把 deepseek chat 请求发到 Responses API。
MSWEA_MODEL_CLASS_ARGS=()
if [[ ${#EVOLVE_NATIVE_ARGS[@]} -eq 0 ]]; then
  MSWEA_MODEL_CLASS="litellm"
  [[ "${LLM_API_TYPE:-chat}" == "responses" ]] && MSWEA_MODEL_CLASS="litellm_response"
  MSWEA_CFG_TMP="$(mswea_llm_config_file "" "$MSWEA_MODEL_CLASS")"
  # Pier 在 model_class=auto 时会把所有 openai/* 模型强制改成
  # litellm_response，并把该覆盖追加在 config_file 之后。显式传参才能让
  # DeepSeek/Doubao/Kimi 使用 chat-completions，同时保留 GPT-5.5 responses。
  MSWEA_MODEL_CLASS_ARGS=(
    --ak "model_class=${MSWEA_MODEL_CLASS}"
    --ak "config_file=${MSWEA_CFG_TMP}"
  )
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
  "${EVOLVE_NATIVE_ARGS[@]}" \
  "${EVOLVE_MOUNTS_ARGS[@]}" \
  "${EVOLVE_PROMPT_ARGS[@]}" \
  "${SKIP_ARGS[@]}" \
  "${AGENT_ENV[@]}"
