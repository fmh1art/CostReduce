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

# SWE-Atlas verifier(evaluator) 改用 _config/deepseekv4_pro.yaml 作为 LLM judge。
# 解析该 yaml 的 llm_name / key / openai_base_url 覆盖默认 VERIFIER_* 配置。
eval "$(python - "$ROOT_DIR/_config/deepseekv4_pro.yaml" <<'PY'
from pathlib import Path
import shlex
import sys

data = {}
for line in Path(sys.argv[1]).read_text().splitlines():
    if ':' in line and not line.startswith(' '):
        key, value = line.split(':', 1)
        data[key.strip()] = value.strip().strip('"\'')

for key, value in {
    'VERIFIER_API_KEY': data['key'],
    'VERIFIER_BASE_URL': data['openai_base_url'],
    'VERIFIER_MODEL': data['llm_name'],
}.items():
    print(f'export {key}={shlex.quote(value)}')
PY
)"

# 生成传给 SWE-Atlas verifier 的 LLM judge 配置（已切换为 deepseekv4_pro）。
mapfile -t VERIFIER_ENV < <(verifier_env_args)

# 可选：通过 EVOLVE_SKIP_FILE 跳过指定 case id（默认从 EVOLVE_SCRIPTS_DIR
# 下的 evolve_used_case_id.txt 自动读取，可显式覆盖或置空禁用）。
mapfile -t SKIP_ARGS < <(evolve_skip_exclude_args)

# 可选：若设置了 EVOLVE_SCRIPTS_DIR，则把其下所有文件 bind mount 到容器
# workspace 的辅助脚本目录（默认 /app/.preinstalled_scripts）。Harbor 的 Trial 层
# 会自动追加 /logs/{agent,verifier,artifacts} 三个默认 bind mount，因此这里强制
# EVOLVE_SCRIPTS_INCLUDE_DEFAULT_LOG_MOUNTS=0，避免重复挂载触发冲突。
EVOLVE_MOUNTS_ARGS=()
EVOLVE_PROMPT_ARGS=()
EVOLVE_PROMPT_TEMPLATE=""
if [[ -n "${EVOLVE_SCRIPTS_DIR:-}" ]]; then
  EVOLVE_MOUNTS_JSON="$(EVOLVE_SCRIPTS_INCLUDE_DEFAULT_LOG_MOUNTS=0 evolve_scripts_mounts_json)"
  if [[ -n "${EVOLVE_MOUNTS_JSON}" ]]; then
    EVOLVE_MOUNTS_ARGS=(--mounts "${EVOLVE_MOUNTS_JSON}")
  fi
  EVOLVE_PROMPT_TEMPLATE="$(evolve_scripts_prompt_template)"
  if [[ -n "${EVOLVE_PROMPT_TEMPLATE}" ]]; then
    # 退出时清理临时模板文件。
    trap '[[ -n "${EVOLVE_PROMPT_TEMPLATE:-}" && -f "${EVOLVE_PROMPT_TEMPLATE}" ]] && rm -f "${EVOLVE_PROMPT_TEMPLATE}"' EXIT
    EVOLVE_PROMPT_ARGS=(--ak "prompt_template_path=${EVOLVE_PROMPT_TEMPLATE}")
  fi
fi

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
    "${EVOLVE_MOUNTS_ARGS[@]}" \
    "${EVOLVE_PROMPT_ARGS[@]}" \
    "${SKIP_ARGS[@]}" \
    "${AGENT_ENV[@]}" \
    "${PROXY_ENV[@]}" \
    "${VERIFIER_ENV[@]}"
done
