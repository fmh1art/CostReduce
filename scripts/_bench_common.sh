#!/usr/bin/env bash

ROOT_DIR="${ROOT_DIR:-/home/fanmeihao/projects/CostReduce}"
LLM_CONFIG="${LLM_CONFIG:-${ROOT_DIR}/_config/deepseekv4_flash.yaml}"
RESULTS_DIR="${RESULTS_DIR:-${ROOT_DIR}/results}"
RUN_ID="${RUN_ID:-smoke-$(date +%m%d-%H%M%S)}"
N_CONCURRENT="${N_CONCURRENT:-1}"
N_ATTEMPTS="${N_ATTEMPTS:-1}"
N_TASKS="${N_TASKS:-1}"
SWE_ATLAS_SPLITS="${SWE_ATLAS_SPLITS:-qa}"
HARBOR_AGENT_SETUP_TIMEOUT_MULTIPLIER="${HARBOR_AGENT_SETUP_TIMEOUT_MULTIPLIER:-4}"
PROXY_URL="${PROXY_URL:-http://sys-proxy-rd-relay.byted.org:8118}"
VERIFIER_API_KEY="${VERIFIER_API_KEY:-sk-BzMx97xHrUYmbBLBcotVpNXsIY2rbw74pD6Xv4mmsISQwcTz}"
VERIFIER_BASE_URL="${VERIFIER_BASE_URL:-https://api.whatai.cc/v1}"
VERIFIER_MODEL="${VERIFIER_MODEL:-claude-opus-4-5-20251101}"
HARBOR_ENV="${HARBOR_ENV:-docker}"
UV_BIN="${UV_BIN:-uv}"

load_llm_config() {
  # 使用 Python 解析简单 YAML 配置，并输出可被当前 shell eval 的 export 语句。
  eval "$(python - "$LLM_CONFIG" <<'PY'
from pathlib import Path
import shlex
import sys

data = {}
for line in Path(sys.argv[1]).read_text().splitlines():
    if ':' in line and not line.startswith(' '):
        key, value = line.split(':', 1)
        data[key.strip()] = value.strip().strip('"\'')

model = 'openai/' + data['llm_name']
base_url = data['openai_base_url']
api_key = data['key']
temperature = data.get('temperature', '0')
for key, value in {
    'MODEL': model,
    'OPENAI_API_KEY': api_key,
    'MSWEA_API_KEY': api_key,
    'OPENAI_BASE_URL': base_url,
    'OPENAI_API_BASE': base_url,
    'JUDGE_API_KEY': api_key,
    'JUDGE_BASE_URL': base_url,
    'TEMPERATURE': temperature,
}.items():
    print(f'export {key}={shlex.quote(value)}')
PY
)"
}

agent_env_args() {
  # 将 LLM API 相关环境变量转换成 Harbor/Pier 的 --ae 参数列表。
  printf '%s\n' \
    --ae "OPENAI_API_KEY=${OPENAI_API_KEY}" \
    --ae "MSWEA_API_KEY=${MSWEA_API_KEY}" \
    --ae "OPENAI_BASE_URL=${OPENAI_BASE_URL}" \
    --ae "OPENAI_API_BASE=${OPENAI_API_BASE}"
}

proxy_env_args() {
  # 将代理环境变量转换成 Harbor/Pier 的 --ae 参数列表，用于容器内 apt/curl/pip 等联网步骤。
  printf '%s\n' \
    --ae "HTTP_PROXY=${PROXY_URL}" \
    --ae "HTTPS_PROXY=${PROXY_URL}" \
    --ae "http_proxy=${PROXY_URL}" \
    --ae "https_proxy=${PROXY_URL}" \
    --ae "NO_PROXY=localhost,127.0.0.1,::1" \
    --ae "no_proxy=localhost,127.0.0.1,::1"
}

verifier_env_args() {
  # 将 SWE-Atlas LLM verifier 的 OpenAI-compatible 配置转换成 Harbor 的 --ve 参数列表。
  printf '%s\n' \
    --ve "EVAL_API_KEY=${VERIFIER_API_KEY}" \
    --ve "EVAL_BASE_URL=${VERIFIER_BASE_URL}" \
    --ve "EVAL_MODEL=${VERIFIER_MODEL}" \
    --ve "HTTP_PROXY=${PROXY_URL}" \
    --ve "HTTPS_PROXY=${PROXY_URL}" \
    --ve "http_proxy=${PROXY_URL}" \
    --ve "https_proxy=${PROXY_URL}" \
    --ve "NO_PROXY=localhost,127.0.0.1,::1" \
    --ve "no_proxy=localhost,127.0.0.1,::1"
}
