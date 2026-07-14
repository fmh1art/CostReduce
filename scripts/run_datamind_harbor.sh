#!/usr/bin/env bash
set -euo pipefail

# 在 Harbor 上运行 DataMind/LongDS benchmark 的封装脚本。
#
# 与 DSGym 原生跑法（run_datamind.sh → examples/longds.py + jupyter-kernel agent）不同，
# 本脚本走 Harbor：用 longds_adapter 把每个 LongDS task 转成 harbor multi-step flat task
# 目录（每轮一个 [[steps]]，verifier 用 LLM judge），再用 mini-swe-agent 跑。
# 这样产出的 trajectory 是 ATIF 格式，evolve 框架直接消费（无需 DSGym schema 适配）。
#
# 数据来源：
#   DATAMIND_TASK_PATH=<目录>   使用本地已生成的 harbor flat task 目录（传 -p）。
#       用 adapter 生成：
#         cd "$ROOT_DIR/tmp/harbor/adapters/longds" && uv run longds --limit 10
#       产物默认写到 tmp/harbor/datasets/longds/，再把该目录赋给 DATAMIND_TASK_PATH。
#   否则                        报错退出（LongDS 不在 harbor registry，必须本地生成）。
#
# LLM 配置：复用 _config/*.yaml（默认 deepseekv4_flash.yaml）。同 run_swe_bench.sh，
# responses 配置时自动合成 litellm_response 临时 mswea 配置。
#
# Verifier：LongDS 用 LLM judge（搬 DSGym 的 JUDGE_PROMPT，解析 <score>0|1</score>），
# 默认走 deepseek-v4-flash（由 _bench_common.sh 的 VERIFIER_* 派生，可用 VERIFIER_CONFIG
# 覆盖）。judge 凭据 JUDGE_API_KEY/BASE_URL/MODEL + 代理通过 --ve 注入 verifier 容器。
#
# evolved scripts 注入：与 run_swe_bench.sh 完全一致（bind mount + Jinja2 prompt 模板），
# 复用 _bench_common.sh 的 evolve_scripts_mounts_json / evolve_scripts_prompt_template。
#
# 默认 N_TASKS=1 仅做 smoke；全量评测设 N_TASKS=68（LongDS 共 68 个 task）。

# RUN_ID / N_TASKS 默认值需在 source _bench_common.sh 之前设置。
RUN_ID="${RUN_ID:-datamind-longds-$(date +%m%d-%H%M%S)}"
N_TASKS="${N_TASKS:-1}"

source "$(dirname "$0")/_bench_common.sh"

# LongDS 专属配置（均可在 source 后由调用方覆盖）。
DATAMIND_TASK_PATH="${DATAMIND_TASK_PATH:-}"                 # -p 本地 harbor flat task 目录（必填）
DATAMIND_MSWEA_CONFIG="${DATAMIND_MSWEA_CONFIG:-}"            # 显式 mswea 配置 yaml
DATAMIND_RESULTS_SUBDIR="${DATAMIND_RESULTS_SUBDIR:-datamind-longds}"
EXPORT_HOST_PROXY="${EXPORT_HOST_PROXY:-1}"

datamind_verifier_args() {
  # LongDS verifier 是 LLM judge：注入 judge 凭据 + 代理。
  # JUDGE_API_KEY/BASE_URL/MODEL 由 _bench_common.sh 从 VERIFIER_CONFIG（默认 flash）派生，
  # 复用 VERIFIER_API_KEY/BASE_URL/MODEL（语义一致：judge = verifier LLM）。
  printf '%s\n' \
    --ve "JUDGE_API_KEY=${VERIFIER_API_KEY}" \
    --ve "JUDGE_BASE_URL=${VERIFIER_BASE_URL}" \
    --ve "JUDGE_MODEL=${VERIFIER_MODEL}" \
    --ve "HTTP_PROXY=${PROXY_URL}" \
    --ve "HTTPS_PROXY=${PROXY_URL}" \
    --ve "http_proxy=${PROXY_URL}" \
    --ve "https_proxy=${PROXY_URL}" \
    --ve "NO_PROXY=localhost,127.0.0.1,::1" \
    --ve "no_proxy=localhost,127.0.0.1,::1"
}

datamind_responses_config_file() {
  # responses（bytedance aidp 网关）配置时，合成 litellm_response 临时 mswea 配置。
  # 与 run_swe_bench.sh 同逻辑。非 responses 配置时不生成，返回空串。
  if [[ -z "${AZURE_API_KEY:-}" ]]; then return 0; fi
  local tmp
  tmp="$(mktemp -t datamind_resp_cfg.XXXXXX.yaml)"
  printf 'model:\n  model_class: litellm_response\n' > "$tmp"
  printf '%s\n' "$tmp"
}

# 退出时清理临时文件。
MSWEA_CFG_TMP=""
EVOLVE_PROMPT_TEMPLATE=""
cleanup() {
  if [[ -n "${MSWEA_CFG_TMP:-}" && -f "${MSWEA_CFG_TMP}" ]]; then rm -f "${MSWEA_CFG_TMP}"; fi
  if [[ -n "${EVOLVE_PROMPT_TEMPLATE:-}" && -f "${EVOLVE_PROMPT_TEMPLATE}" ]]; then rm -f "${EVOLVE_PROMPT_TEMPLATE}"; fi
}
trap cleanup EXIT

# 从 _config/*.yaml 解析模型名、API key、base URL（responses 时还导出 AZURE_*）。
load_llm_config

cd "$ROOT_DIR"
mkdir -p "$RESULTS_DIR/$DATAMIND_RESULTS_SUBDIR"

# 校验数据目录（LongDS 不在 registry，必须本地生成 flat task 目录）。
if [[ -z "${DATAMIND_TASK_PATH}" || ! -d "${DATAMIND_TASK_PATH}" ]]; then
  echo "[run_datamind_harbor] DATAMIND_TASK_PATH 未设置或不是目录：${DATAMIND_TASK_PATH}" >&2
  echo "  请先用 adapter 生成 harbor flat task 目录：" >&2
  echo "    cd '$ROOT_DIR/tmp/harbor/adapters/longds' && uv run longds --task-dir '$ROOT_DIR/tmp/harbor/datasets/longds' --all --overwrite" >&2
  exit 1
fi

# agent 容器/进程的 OpenAI-compatible 环境变量（LLM 凭据 + base_url + AZURE_*）。
mapfile -t AGENT_ENV < <(agent_env_args)

# agent setup/运行阶段的代理环境变量。bytedance 内网 LLM（如 doubao/ark）需特殊处理：
# ark 是内网域名，经外网代理会被 403；但 agent setup（装 uv/mini-swe-agent）又需代理。
# 故保留代理 + 注入 NO_PROXY=.bytedance.net 让 ark 请求 bypass 代理直连（httpx 后缀匹配）。
# verifier（judge）调外网 LLM 仍走代理，由 --ve 单独注入。
AGENT_PROXY_ENV=()
if [[ "${OPENAI_BASE_URL:-}" == *"bytedance.net"* ]]; then
  echo "[run_datamind_harbor] 检测到 bytedance 内网 LLM ($OPENAI_BASE_URL)，agent 注入 NO_PROXY=.bytedance.net（ark bypass 代理直连）"
  AGENT_PROXY_ENV=(
    --ae "HTTP_PROXY=${PROXY_URL}"
    --ae "HTTPS_PROXY=${PROXY_URL}"
    --ae "http_proxy=${PROXY_URL}"
    --ae "https_proxy=${PROXY_URL}"
    --ae "NO_PROXY=.bytedance.net,bytedance.net,localhost,127.0.0.1,::1"
    --ae "no_proxy=.bytedance.net,bytedance.net,localhost,127.0.0.1,::1"
  )
else
  mapfile -t AGENT_PROXY_ENV < <(proxy_env_args)
fi

# verifier 阶段的 judge 凭据 + 代理。
mapfile -t VERIFIER_JUDGE_ENV < <(datamind_verifier_args)

# 可选：通过 EVOLVE_SKIP_FILE 跳过指定 case id。
mapfile -t SKIP_ARGS < <(evolve_skip_exclude_args)

# 可选：若设置了 EVOLVE_SCRIPTS_DIR，把其下文件 bind mount 到容器辅助脚本目录，
# 并把 evolved scripts 注册成 native function tools（model/agent 子类 + manifest）。
# Harbor 的 Trial 层会自动追加 /logs/{agent,verifier,artifacts} 三个默认 bind mount，
# 故强制 EVOLVE_SCRIPTS_INCLUDE_DEFAULT_LOG_MOUNTS=0 避免重复挂载触发冲突。
EVOLVE_MOUNTS_ARGS=()
EVOLVE_PROMPT_ARGS=()
EVOLVE_NATIVE_ARGS=()
if [[ -n "${EVOLVE_SCRIPTS_DIR:-}" ]]; then
  EVOLVE_MOUNTS_JSON="$(EVOLVE_SCRIPTS_INCLUDE_DEFAULT_LOG_MOUNTS=0 evolve_scripts_mounts_json)"
  if [[ -n "${EVOLVE_MOUNTS_JSON}" ]]; then
    EVOLVE_MOUNTS_ARGS=(--mounts "${EVOLVE_MOUNTS_JSON}")
  fi
  EVOLVE_PROMPT_TEMPLATE="$(evolve_scripts_prompt_template)"
  if [[ -n "${EVOLVE_PROMPT_TEMPLATE}" ]]; then
    EVOLVE_PROMPT_ARGS=(--ak "prompt_template_path=${EVOLVE_PROMPT_TEMPLATE}")
  fi
  evolve_scripts_deploy || exit 1
  mapfile -t EVOLVE_NATIVE_ARGS < <(evolve_scripts_native_tools_args)
fi

# 选择 mini-swe-agent 的 config_file（仅在没有 evolved native tools 时生效）：
#   - DATAMIND_MSWEA_CONFIG 显式指定 → 直接用。
#   - responses 配置 → 合成 litellm_response 临时配置。
#   - chat 配置 → 不传 config_file，沿用 mini.yaml 默认。
# evolve 模式下 model_class+agent_class 由 EVOLVE_NATIVE_ARGS 的 config_file 设定。
MSWEA_CFG_ARGS=()
if [[ -z "${EVOLVE_TOOLS_CONFIG_HOST:-}" ]]; then
  if [[ -n "${DATAMIND_MSWEA_CONFIG}" ]]; then
    MSWEA_CFG_ARGS=(--ak "config_file=${DATAMIND_MSWEA_CONFIG}")
  else
    MSWEA_CFG_TMP="$(datamind_responses_config_file)"
    if [[ -n "${MSWEA_CFG_TMP}" ]]; then
      MSWEA_CFG_ARGS=(--ak "config_file=${MSWEA_CFG_TMP}")
    fi
  fi
fi

# 把代理导出到 harbor 进程环境（覆盖 host 侧联网；docker 镜像拉取由 daemon 代理控制）。
if [[ "${EXPORT_HOST_PROXY}" == "1" ]]; then
  export HTTP_PROXY="$PROXY_URL" HTTPS_PROXY="$PROXY_URL"
  export http_proxy="$PROXY_URL" https_proxy="$PROXY_URL"
  export NO_PROXY="localhost,127.0.0.1,::1" no_proxy="localhost,127.0.0.1,::1"
fi

# 使用 Harbor 在 LongDS 上运行 mini-swe-agent（multi-step task，每轮 LLM judge 打分）。
"$UV_BIN" run --directory "$ROOT_DIR/tmp/harbor" harbor run \
  -p "${DATAMIND_TASK_PATH}" \
  -a mini-swe-agent \
  -m "$MODEL" \
  -e "$HARBOR_ENV" \
  -k "$N_ATTEMPTS" \
  -n "$N_CONCURRENT" \
  --agent-setup-timeout-multiplier "$HARBOR_AGENT_SETUP_TIMEOUT_MULTIPLIER" \
  --n-tasks "$N_TASKS" \
  -o "$RESULTS_DIR/$DATAMIND_RESULTS_SUBDIR" \
  --job-name "$RUN_ID" \
  --yes \
  "${MSWEA_CFG_ARGS[@]}" \
  "${EVOLVE_NATIVE_ARGS[@]}" \
  "${EVOLVE_MOUNTS_ARGS[@]}" \
  "${EVOLVE_PROMPT_ARGS[@]}" \
  "${SKIP_ARGS[@]}" \
  "${AGENT_ENV[@]}" \
  "${AGENT_PROXY_ENV[@]}" \
  "${VERIFIER_JUDGE_ENV[@]}"
