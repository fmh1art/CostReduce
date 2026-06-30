#!/usr/bin/env bash
set -euo pipefail

# 加载公共变量和函数，用于读取 LLM 配置、结果目录和并发参数。
source "$(dirname "$0")/_bench_common.sh"

# 从 _config/deepseekv4_flash.yaml 解析模型名、API key、base URL 和温度。
load_llm_config

# 切换到项目根目录，确保后续相对路径都基于 CostReduce 仓库。
cd "$ROOT_DIR"

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
EVOLVE_PROMPT_TEMPLATE=""
if [[ -n "${EVOLVE_SCRIPTS_DIR:-}" ]]; then
  EVOLVE_MOUNTS_JSON="$(evolve_scripts_mounts_json)"
  if [[ -n "${EVOLVE_MOUNTS_JSON}" ]]; then
    EVOLVE_MOUNTS_ARGS=(--mounts-json "${EVOLVE_MOUNTS_JSON}")
  fi
  EVOLVE_PROMPT_TEMPLATE="$(evolve_scripts_prompt_template)"
  if [[ -n "${EVOLVE_PROMPT_TEMPLATE}" ]]; then
    # 退出时清理临时模板文件。
    trap '[[ -n "${EVOLVE_PROMPT_TEMPLATE:-}" && -f "${EVOLVE_PROMPT_TEMPLATE}" ]] && rm -f "${EVOLVE_PROMPT_TEMPLATE}"' EXIT
    EVOLVE_PROMPT_ARGS=(--ak "prompt_template_path=${EVOLVE_PROMPT_TEMPLATE}")
  fi
fi

# 使用 Pier 在 DeepSWE 全量任务集上运行 mini-swe-agent，并用 deepseek-v4-flash 作为 LLM。
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
  --ak model_class=litellm \
  "${EVOLVE_MOUNTS_ARGS[@]}" \
  "${EVOLVE_PROMPT_ARGS[@]}" \
  "${SKIP_ARGS[@]}" \
  "${AGENT_ENV[@]}"
