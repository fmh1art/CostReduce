#!/usr/bin/env bash
set -euo pipefail

# 加载公共变量和函数，用于读取 LLM 配置、结果目录和并发参数。
source "$(dirname "$0")/_bench_common.sh"

# 从 _config/deepseekv4_flash.yaml 解析模型名、API key、base URL 和温度。
load_llm_config

# 切换到项目根目录，确保后续相对路径都基于 CostReduce 仓库。
cd "$ROOT_DIR"

# 创建 DeepSWE 结果目录，避免 Pier 写入结果时目录不存在。
mkdir -p "$RESULTS_DIR/deep-swe"

# 生成传给 mini-swe-agent 容器/进程的 OpenAI-compatible 环境变量参数。
mapfile -t AGENT_ENV < <(agent_env_args)

# 使用 Pier 在 DeepSWE 全量任务集上运行 mini-swe-agent，并用 deepseek-v4-flash 作为 LLM。
"$UV_BIN" tool run --from datacurve-pier pier run \
  -p "$ROOT_DIR/benchmark/deep-swe/tasks" \
  -a mini-swe-agent \
  -m "$MODEL" \
  -e "$HARBOR_ENV" \
  -k "$N_ATTEMPTS" \
  -n "$N_CONCURRENT" \
  --n-tasks "$N_TASKS" \
  -o "$RESULTS_DIR/deep-swe" \
  --job-name "$RUN_ID" \
  --ak model_class=litellm \
  "${AGENT_ENV[@]}"
