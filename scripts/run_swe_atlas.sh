#!/usr/bin/env bash
set -euo pipefail

# 加载公共变量和函数，用于读取 LLM 配置、结果目录和并发参数。
source "$(dirname "$0")/_bench_common.sh"

# 从 _config/deepseekv4_flash.yaml 解析模型名、API key、base URL 和温度。
load_llm_config

# 切换到 SWE-Atlas 目录，便于复用该 benchmark 自带的运行配置。
cd "$ROOT_DIR/benchmark/SWE-Atlas"

# 创建结果根目录，后续 qa/tw/rf 三个 split 会分别写入子目录。
mkdir -p "$RESULTS_DIR"

# 生成传给 mini-swe-agent 容器/进程的 OpenAI-compatible 环境变量参数。
mapfile -t AGENT_ENV < <(agent_env_args)

# 生成传给 Harbor agent setup/运行阶段的代理环境变量参数，帮助容器内 apt/curl/pip 访问外网。
mapfile -t PROXY_ENV < <(proxy_env_args)

# 生成传给 SWE-Atlas verifier 的 LLM judge 配置，使用 OpenAI-compatible Claude 接口评分。
mapfile -t VERIFIER_ENV < <(verifier_env_args)

# 依次评测 SWE_ATLAS_SPLITS 指定的 split；默认只跑 qa，便于先做 1 个 case 的 smoke test。
for split in ${SWE_ATLAS_SPLITS//,/ }; do
  # 使用 Harbor 在当前 split 上运行 mini-swe-agent，并加载该 split 专用的 mswea 配置。
  "$UV_BIN" run --directory "$ROOT_DIR/tmp/harbor" harbor run \
    -p "$ROOT_DIR/benchmark/SWE-Atlas/data/${split}" \
    -a mini-swe-agent \
    -m "$MODEL" \
    -e "$HARBOR_ENV" \
    -k "$N_ATTEMPTS" \
    -n "$N_CONCURRENT" \
    --agent-setup-timeout-multiplier "$HARBOR_AGENT_SETUP_TIMEOUT_MULTIPLIER" \
    --n-tasks "$N_TASKS" \
    -o "$RESULTS_DIR/swe-atlas-${split}" \
    --job-name "$RUN_ID" \
    --ak "config_file=$ROOT_DIR/benchmark/SWE-Atlas/run_config/${split}/mswea_${split}_config.yaml" \
    --yes \
    "${AGENT_ENV[@]}" \
    "${PROXY_ENV[@]}" \
    "${VERIFIER_ENV[@]}"
done
