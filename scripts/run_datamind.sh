#!/usr/bin/env bash
set -euo pipefail

# 加载公共变量和函数，用于读取 LLM 配置、结果目录和并发参数。
source "$(dirname "$0")/_bench_common.sh"

# 从 _config/deepseekv4_flash.yaml 解析模型名、API key、base URL 和温度。
load_llm_config

# 切换到 DataMind/LongDS 的 DSGym 目录，确保 examples/longds.py 能找到本地包和数据路径。
cd "$ROOT_DIR/benchmark/DataMind/longds/DSGym"

# 创建 LongDS 本次运行的结果目录，按 RUN_ID 隔离不同实验输出。
mkdir -p "$RESULTS_DIR/datamind-longds/$RUN_ID"

# 导出推理模型和 LLM-as-judge 所需的 OpenAI-compatible 环境变量。
export OPENAI_API_KEY OPENAI_BASE_URL OPENAI_API_BASE JUDGE_API_KEY JUDGE_BASE_URL

# 可选：通过 EVOLVE_SKIP_FILE 跳过指定 case id（默认从 EVOLVE_SCRIPTS_DIR
# 下的 evolve_used_case_id.txt 自动读取，可显式覆盖或置空禁用）。
EVOLVE_SKIP_FILE_RESOLVED="$(evolve_skip_file_resolved)"
SKIP_ARGS=()
if [[ -n "${EVOLVE_SKIP_FILE_RESOLVED}" ]]; then
  SKIP_ARGS=(--skip-case-id-txt "${EVOLVE_SKIP_FILE_RESOLVED}")
fi

# 可选：若设置了 EVOLVE_SCRIPTS_DIR，则把其下文件安装到容器内的
# /app/.preinstalled_scripts/，并把 instruction.md 拼到 system prompt 前面。
# DataMind 不走 harbor/pier，没有 --mounts 接口；agent 在每个任务首次 env.init
# 之后会通过 jupyter kernel 把脚本写入容器对应目录（multi_turn_react_ds_agent.py）。
# 此外，扫 EVOLVE_SCRIPTS_DIR/*/intro.json 拼出工具清单（含绝对路径），
# 追加到 system prompt 末尾，让 agent 知道有哪些 evolved scripts 可用。
EVOLVE_SCRIPTS_ARGS=()
EVOLVE_INSTRUCTIONS_ARGS=()
EVOLVE_TOOLS_BLOCK_FILE=""
EVOLVE_TOOLS_BLOCK_ARGS=()
if [[ -n "${EVOLVE_SCRIPTS_DIR:-}" ]]; then
  EVOLVE_SCRIPTS_ARGS=(--scripts-dir "${EVOLVE_SCRIPTS_DIR}")
  EVOLVE_INSTR_PATH="$(evolve_instruction_md_path)"
  if [[ -n "${EVOLVE_INSTR_PATH}" ]]; then
    EVOLVE_INSTRUCTIONS_ARGS=(--instructions-file "${EVOLVE_INSTR_PATH}")
  fi
  EVOLVE_TOOLS_BLOCK="$(evolve_scripts_tools_block)"
  if [[ -n "${EVOLVE_TOOLS_BLOCK}" ]]; then
    EVOLVE_TOOLS_BLOCK_FILE="$(mktemp -t evolve_tools.XXXXXX)"
    printf '%s\n' "${EVOLVE_TOOLS_BLOCK}" > "${EVOLVE_TOOLS_BLOCK_FILE}"
    EVOLVE_TOOLS_BLOCK_ARGS=(--tools-block-file "${EVOLVE_TOOLS_BLOCK_FILE}")
  fi
fi
# 退出时清理临时 tools block 文件。
if [[ -n "${EVOLVE_TOOLS_BLOCK_FILE}" ]]; then
  trap 'rm -f "${EVOLVE_TOOLS_BLOCK_FILE}"' EXIT
fi

# 使用 DSGym 的 LongDS 入口运行 DataMind/LongDS 评测，并调用 deepseek-v4-flash API。
"$UV_BIN" run python examples/longds.py \
  --dataset longds \
  --model "$MODEL" \
  --backend litellm \
  --output-dir "$RESULTS_DIR/datamind-longds/$RUN_ID" \
  --temperature "$TEMPERATURE" \
  --task-limit "$N_TASKS" \
  --judge-model "${JUDGE_MODEL:-deepseek-v4-pro}" \
  "${EVOLVE_SCRIPTS_ARGS[@]}" \
  "${EVOLVE_INSTRUCTIONS_ARGS[@]}" \
  "${EVOLVE_TOOLS_BLOCK_ARGS[@]}" \
  "${SKIP_ARGS[@]}"
