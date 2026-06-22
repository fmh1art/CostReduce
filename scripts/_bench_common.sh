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

# 可选：要 bind mount 到容器 workspace 根目录的工具目录。
# 不设或为空时，沿用 Pier 默认行为（不附加任何额外挂载，使用默认的 code agent）。
EVOLVE_TOOLS_DIR="${EVOLVE_TOOLS_DIR:-}"
# 容器内 workspace 根目录下用于盛放 evolve tools 的子目录。
# 单独放进一个隐藏子目录，避免与任务自带的 monorepo 顶层条目混在一起、误导 agent。
EVOLVE_TOOLS_TARGET="${EVOLVE_TOOLS_TARGET:-/app/.preinstalled_tools}"
# 是否以只读方式挂载 evolve tools。默认只读，避免容器内污染 host 上的工具目录。
EVOLVE_TOOLS_READONLY="${EVOLVE_TOOLS_READONLY:-1}"
# 可选：跳过执行的 case id 列表文件，每行一个 task name（支持 glob）。
# 设为 "auto"（默认）时：若 EVOLVE_TOOLS_DIR 非空且目录下存在
# evolve_used_case_id.txt 则使用之；否则不跳过任何 case。也可显式设置成具体路径
# 或空字符串以禁用。
EVOLVE_SKIP_FILE="${EVOLVE_SKIP_FILE-auto}"

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

evolve_tools_mounts_json() {
  # 根据 EVOLVE_TOOLS_DIR / EVOLVE_TOOLS_TARGET 生成 Pier --mounts-json 参数。
  #
  # 入参（来自环境变量）：
  #   EVOLVE_TOOLS_DIR       host 上要 bind mount 进容器的工具目录。空则不生成。
  #   EVOLVE_TOOLS_TARGET    容器内挂载根目录，默认 /app。
  #   EVOLVE_TOOLS_READONLY  1=只读（默认），0=读写。
  #
  # 输出：
  #   stdout 打印一行 JSON 字符串。EVOLVE_TOOLS_DIR 为空时打印空串。
  #
  # 说明：因为显式传 --mounts-json 会覆盖 Pier 默认的 logs/agent、logs/verifier、
  # logs/artifacts 三个 bind mount，所以这里同时把这三个默认 mount 加回去，
  # 否则 agent/verifier 日志和 artifact 都会丢失。
  local tools_dir="${EVOLVE_TOOLS_DIR:-}"
  if [[ -z "${tools_dir}" ]]; then
    printf ''
    return 0
  fi
  if [[ ! -d "${tools_dir}" ]]; then
    echo "[evolve_tools_mounts_json] EVOLVE_TOOLS_DIR='${tools_dir}' is not a directory" >&2
    return 1
  fi

  EVOLVE_TOOLS_DIR_ABS="$(cd "${tools_dir}" && pwd)" \
  EVOLVE_TOOLS_TARGET="${EVOLVE_TOOLS_TARGET}" \
  EVOLVE_TOOLS_READONLY="${EVOLVE_TOOLS_READONLY}" \
  python - <<'PY'
import json
import os
from pathlib import Path

tools_dir = Path(os.environ["EVOLVE_TOOLS_DIR_ABS"])
target_root = os.environ.get("EVOLVE_TOOLS_TARGET", "/app").rstrip("/") or "/"
read_only = os.environ.get("EVOLVE_TOOLS_READONLY", "1") not in ("0", "", "false", "False")

# 占位符，使用 Pier docker compose env 中 DockerEnvironmentEnvVars 注入的变量，
# 让 docker compose 在容器启动时把 logs 目录绑回宿主机。
mounts = [
    {
        "type": "bind",
        "source": "${HOST_VERIFIER_LOGS_PATH}",
        "target": "${ENV_VERIFIER_LOGS_PATH}",
    },
    {
        "type": "bind",
        "source": "${HOST_AGENT_LOGS_PATH}",
        "target": "${ENV_AGENT_LOGS_PATH}",
    },
    {
        "type": "bind",
        "source": "${HOST_ARTIFACTS_PATH}",
        "target": "${ENV_ARTIFACTS_PATH}",
    },
]

for entry in sorted(tools_dir.iterdir()):
    target = f"{target_root}/{entry.name}" if target_root != "/" else f"/{entry.name}"
    mount = {
        "type": "bind",
        "source": str(entry.resolve()),
        "target": target,
    }
    if read_only:
        mount["read_only"] = True
    mounts.append(mount)

print(json.dumps(mounts))
PY
}

evolve_tools_prompt_template() {
  # 根据 EVOLVE_TOOLS_DIR 找到 instruction.md，并生成可供 Pier
  # --ak prompt_template_path=... 使用的 Jinja2 模板文件。
  #
  # 模板内容 = instruction.md 全文 + 分隔符 + "{{ instruction }}"。
  # Pier 的 render_prompt_template 校验模板必须包含 {{ instruction }}，
  # 渲染后再传给底层 agent（如 mini-swe-agent --task=...）。
  #
  # 入参（环境变量）：
  #   EVOLVE_TOOLS_DIR  host 上工具目录；空则不生成。
  #
  # 输出：
  #   stdout 打印临时模板文件路径；EVOLVE_TOOLS_DIR 为空或不存在 instruction.md 时打印空串。
  #
  # 注意：调用方需要在退出时清理该临时文件（用 trap 删除）。
  local tools_dir="${EVOLVE_TOOLS_DIR:-}"
  if [[ -z "${tools_dir}" ]]; then
    return 0
  fi
  local instr="${tools_dir%/}/instruction.md"
  if [[ ! -f "${instr}" ]]; then
    return 0
  fi
  local tmp
  tmp="$(mktemp -t evolve_prompt.XXXXXX)"
  {
    cat "${instr}"
    printf '\n\n---\n\n{{ instruction }}\n'
  } > "${tmp}"
  printf '%s\n' "${tmp}"
}

evolve_skip_exclude_args() {
  # 根据 EVOLVE_SKIP_FILE 输出一串可直接展开到 pier run 命令行的
  # "-x <task_name>" 参数（每行一个，便于 mapfile 读取）。
  #
  # 解析规则：
  #   - EVOLVE_SKIP_FILE="auto"（默认）：
  #       若 EVOLVE_TOOLS_DIR 非空且其下存在 evolve_used_case_id.txt，则使用之；
  #       否则不输出任何参数。
  #   - EVOLVE_SKIP_FILE=""：禁用。
  #   - EVOLVE_SKIP_FILE=<path>：强制使用该文件，若不存在则报错。
  #
  # 文件格式：每行一个 task name（Pier 的 -x 支持 glob）；
  # 忽略空行和以 # 开头的注释行；前后空白会被 strip。
  local skip_file="${EVOLVE_SKIP_FILE-auto}"
  if [[ "${skip_file}" == "auto" ]]; then
    if [[ -n "${EVOLVE_TOOLS_DIR:-}" ]] \
      && [[ -f "${EVOLVE_TOOLS_DIR%/}/evolve_used_case_id.txt" ]]; then
      skip_file="${EVOLVE_TOOLS_DIR%/}/evolve_used_case_id.txt"
    else
      return 0
    fi
  fi
  if [[ -z "${skip_file}" ]]; then
    return 0
  fi
  if [[ ! -f "${skip_file}" ]]; then
    echo "[evolve_skip_exclude_args] EVOLVE_SKIP_FILE='${skip_file}' not found" >&2
    return 1
  fi
  # 逐行输出 -x 与 task name，忽略空行/注释。
  while IFS= read -r line || [[ -n "${line}" ]]; do
    # strip 首尾空白
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    [[ -z "${line}" ]] && continue
    [[ "${line}" == \#* ]] && continue
    printf '%s\n%s\n' '-x' "${line}"
  done < "${skip_file}"
}
