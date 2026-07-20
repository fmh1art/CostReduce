#!/usr/bin/env bash

# ROOT_DIR 兜底：从本脚本位置（scripts/）推导仓库根，避免硬编码绝对路径
# （直接 bash scripts/run_*.sh 时父进程未 export ROOT_DIR 也能正确解析）。
# 经 run_exp.sh → run_evolve_experiment.sh 调用时 ROOT_DIR 已被 export，此处不触发。
ROOT_DIR="${ROOT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
LLM_CONFIG="${LLM_CONFIG:-${ROOT_DIR}/_config/deepseekv4_flash.yaml}"
RESULTS_DIR="${RESULTS_DIR:-${ROOT_DIR}/results}"
RUN_ID="${RUN_ID:-smoke-$(date +%m%d-%H%M%S)}"
N_CONCURRENT="${N_CONCURRENT:-8}"
N_ATTEMPTS="${N_ATTEMPTS:-1}"
N_TASKS="${N_TASKS:-1000}"
SWE_ATLAS_SPLITS="${SWE_ATLAS_SPLITS:-qa}"
HARBOR_AGENT_SETUP_TIMEOUT_MULTIPLIER="${HARBOR_AGENT_SETUP_TIMEOUT_MULTIPLIER:-4}"
PROXY_URL="${PROXY_URL:-http://sys-proxy-rd-relay.byted.org:8118}"
HARBOR_ENV="${HARBOR_ENV:-docker}"
UV_BIN="${UV_BIN:-uv}"
API_RETRY_PAUSE_SECONDS="${API_RETRY_PAUSE_SECONDS:-60}"
MSWEA_MODEL_RETRY_WAIT_SECONDS="${MSWEA_MODEL_RETRY_WAIT_SECONDS:-${API_RETRY_PAUSE_SECONDS}}"
API_RETRY_RUNTIME_HOST="${API_RETRY_RUNTIME_HOST:-${ROOT_DIR}/src/tools/api_retry_runtime}"
API_RETRY_RUNTIME_TARGET="${API_RETRY_RUNTIME_TARGET:-/opt/optiharness_api_retry}"

# 可选：要 bind mount 到容器 workspace 根目录的辅助 bash 脚本目录。
# 不设或为空时，沿用 Pier 默认行为（不附加任何额外挂载，使用默认的 code agent）。
EVOLVE_SCRIPTS_DIR="${EVOLVE_SCRIPTS_DIR:-}"
# 容器内 workspace 根目录下用于盛放 evolve 辅助 bash 脚本的子目录。
# 单独放进一个隐藏子目录，避免与任务自带的 monorepo 顶层条目混在一起、误导 agent。
EVOLVE_SCRIPTS_TARGET="${EVOLVE_SCRIPTS_TARGET:-/app/.preinstalled_scripts}"
# 是否以只读方式挂载 evolve 辅助 bash 脚本。默认只读，避免容器内污染 host 上的脚本目录。
EVOLVE_SCRIPTS_READONLY="${EVOLVE_SCRIPTS_READONLY:-1}"
# Native evolved tools execute in a disposable worker. Keep this much shorter
# than the task timeout so a failed helper becomes an LLM observation rather
# than consuming/killing the whole agent. Both values are passed to the agent
# container and may be overridden per experiment.
EVOLVE_TOOLS_V6_TIMEOUT_SECONDS="${EVOLVE_TOOLS_V6_TIMEOUT_SECONDS:-30}"
EVOLVE_TOOLS_V6_MEMORY_MB="${EVOLVE_TOOLS_V6_MEMORY_MB:-1024}"
EVOLVE_TOOLS_V6_OUTPUT_TOKENS="${EVOLVE_TOOLS_V6_OUTPUT_TOKENS:-1000}"
# 是否在生成的 mounts JSON 中显式带上 logs/{agent,verifier,artifacts} 三个默认 bind mount。
# - 1（默认，Pier）：显式带上，因为 Pier 显式传 --mounts-json 会覆盖默认 mount。
# - 0（Harbor 等）：跳过，由调度器（Trial 层）自行追加，否则会重复挂载。
EVOLVE_SCRIPTS_INCLUDE_DEFAULT_LOG_MOUNTS="${EVOLVE_SCRIPTS_INCLUDE_DEFAULT_LOG_MOUNTS:-1}"
# 可选：跳过执行的 case id 列表文件，每行一个 task name（支持 glob）。
# 设为 "auto"（默认）时：若 EVOLVE_SCRIPTS_DIR 非空且目录下存在
# evolve_used_case_id.txt 则使用之；否则不跳过任何 case。也可显式设置成具体路径
# 或空字符串以禁用。
EVOLVE_SKIP_FILE="${EVOLVE_SKIP_FILE-auto}"

load_llm_config() {
  # 使用 Python 解析简单 YAML 配置，并输出可被当前 shell eval 的 export 语句。
  # api_type=azure_chat/responses 时：
  #   MODEL=azure/<llm_name>，额外导出 AZURE_API_KEY/AZURE_API_BASE/AZURE_API_VERSION，
  #   前者走 chat-completions，后者走 responses。
  eval "$(python - "$LLM_CONFIG" <<'PY'
from pathlib import Path
import shlex
import sys

data = {}
for line in Path(sys.argv[1]).read_text().splitlines():
    if ':' in line and not line.startswith(' '):
        key, value = line.split(':', 1)
        data[key.strip()] = value.strip().strip('"\'')

api_type = data.get('api_type', '').strip().lower() or 'chat'
if api_type not in {'chat', 'azure_chat', 'responses'}:
    raise ValueError(f"unsupported api_type={api_type!r}")
api_key = data['key']
temperature = data.get('temperature', '')
thinking = data.get('thinking', '').strip().lower()
if thinking not in {'', 'enabled', 'disabled', 'auto'}:
    raise ValueError(f'unsupported thinking={thinking!r}')
exports = {
    'OPENAI_API_KEY': api_key,
    'MSWEA_API_KEY': api_key,
    'JUDGE_API_KEY': api_key,
    'JUDGE_MODEL': data['llm_name'],
    'LLM_API_TYPE': api_type,
    'JUDGE_API_TYPE': api_type,
    'JUDGE_TEMPERATURE': temperature,
    'TEMPERATURE': temperature,
    'LLM_THINKING': thinking,
}
if api_type in {'azure_chat', 'responses'}:
    azure_endpoint = data.get('azure_endpoint') or data.get('openai_base_url')
    if not azure_endpoint:
        raise ValueError(f'api_type={api_type} requires azure_endpoint')
    exports.update({
        'MODEL': 'azure/' + data['llm_name'],
        'AZURE_API_KEY': api_key,
        'AZURE_API_BASE': azure_endpoint,
        'AZURE_API_VERSION': data.get('api_version', '2024-03-01-preview'),
        # 兼容 harbor mini 适配器读取 OPENAI_BASE_URL 并转发；azure 路由实际用 AZURE_API_BASE。
        'OPENAI_BASE_URL': azure_endpoint,
        'OPENAI_API_BASE': azure_endpoint,
        'JUDGE_BASE_URL': azure_endpoint,
        'JUDGE_API_VERSION': data.get('api_version', '2024-03-01-preview'),
    })
else:
    base_url = data['openai_base_url']
    exports.update({
        'MODEL': 'openai/' + data['llm_name'],
        'OPENAI_BASE_URL': base_url,
        'OPENAI_API_BASE': base_url,
        'JUDGE_BASE_URL': base_url,
    })
for key, value in exports.items():
    print(f'export {key}={shlex.quote(value)}')
PY
)"
}

agent_env_args() {
  # 将 LLM API 相关环境变量转换成 Harbor/Pier 的 --ae 参数列表。
  # PYTHONPATH 只能注入一次。Pier/Harbor 对重复 --ae KEY=value 采用后值覆盖，
  # 因此在 evolved rollout 中必须把 API retry runtime 与 native-tools runtime
  # 合并后再传入，不能让后面的通用 agent env 覆盖前者。
  local agent_pythonpath="${API_RETRY_RUNTIME_TARGET}"
  if [[ -n "${EVOLVE_SCRIPTS_DIR:-}" ]]; then
    agent_pythonpath="${agent_pythonpath}:${EVOLVE_SCRIPTS_TARGET:-/app/.preinstalled_scripts}/.runtime"
  fi
  printf '%s\n' \
    --ae "OPENAI_API_KEY=${OPENAI_API_KEY}" \
    --ae "MSWEA_API_KEY=${MSWEA_API_KEY}" \
    --ae "OPENAI_BASE_URL=${OPENAI_BASE_URL}" \
    --ae "OPENAI_API_BASE=${OPENAI_API_BASE}" \
    --ae "MSWEA_COST_TRACKING=ignore_errors" \
    --ae "API_RETRY_PAUSE_SECONDS=${API_RETRY_PAUSE_SECONDS}" \
    --ae "MSWEA_MODEL_RETRY_WAIT_SECONDS=${MSWEA_MODEL_RETRY_WAIT_SECONDS}" \
    --ae "PYTHONPATH=${agent_pythonpath}"
  # 两种 Azure 协议都需要 AZURE_*；model_class 决定 chat 还是 responses。
  if [[ "${LLM_API_TYPE:-chat}" == "azure_chat" || "${LLM_API_TYPE:-chat}" == "responses" ]]; then
    printf '%s\n' \
      --ae "AZURE_API_KEY=${AZURE_API_KEY}" \
      --ae "AZURE_API_BASE=${AZURE_API_BASE}" \
      --ae "AZURE_API_VERSION=${AZURE_API_VERSION}"
  fi
}

mswea_llm_config_file() {
  # Merge the model controls from the single LLM_CONFIG into any mini-swe-agent
  # config used by prep, rollout, evolve, or final eval.  The generated file
  # intentionally excludes credentials; only temperature/thinking and an
  # optional protocol-specific model_class are copied.  $3 can override the
  # mini-swe-agent environment timeout for benchmarks (such as DAB) whose task
  # budget differs from the default.
  local src="${1:-}" model_class="${2:-}" environment_timeout="${3:-}" tmp
  tmp="$(mktemp -t mswea_llm_cfg.XXXXXX.yaml)"
  python - "$src" "$tmp" "$LLM_CONFIG" "$model_class" "$environment_timeout" <<'PY'
from pathlib import Path
import sys
import yaml

src, dst, llm_path, model_class, environment_timeout = sys.argv[1:]
base = {}
if src and Path(src).is_file():
    base = yaml.safe_load(Path(src).read_text(encoding="utf-8")) or {}
llm = yaml.safe_load(Path(llm_path).read_text(encoding="utf-8")) or {}
model = base.setdefault("model", {})
if model_class:
    model["model_class"] = model_class
kwargs = model.setdefault("model_kwargs", {})
temperature = llm.get("temperature")
if temperature not in (None, ""):
    kwargs["temperature"] = float(temperature)
thinking = str(llm.get("thinking") or "").strip().lower()
if thinking:
    if thinking not in {"enabled", "disabled", "auto"}:
        raise ValueError(f"unsupported thinking={thinking!r}")
    extra_body = kwargs.setdefault("extra_body", {})
    extra_body["thinking"] = {"type": thinking}
if not kwargs:
    model.pop("model_kwargs", None)
if environment_timeout:
    base.setdefault("environment", {})["timeout"] = int(environment_timeout)
Path(dst).write_text(
    # The experiment shell may resolve ``python`` to the repository's legacy
    # Python 3.7 environment, whose PyYAML predates the sort_keys argument.
    yaml.safe_dump(base, allow_unicode=True, default_flow_style=False),
    encoding="utf-8",
)
PY
  printf '%s\n' "$tmp"
}

proxy_env_args() {
  # 将代理环境变量转换成 Harbor/Pier 的 --ae 参数列表，用于容器内 apt/curl/pip 等联网步骤。
  local no_proxy="localhost,127.0.0.1,::1"
  if [[ "${OPENAI_BASE_URL:-}" == *"bytedance.net"* ]]; then
    no_proxy=".bytedance.net,bytedance.net,${no_proxy}"
  fi
  printf '%s\n' \
    --ae "HTTP_PROXY=${PROXY_URL}" \
    --ae "HTTPS_PROXY=${PROXY_URL}" \
    --ae "http_proxy=${PROXY_URL}" \
    --ae "https_proxy=${PROXY_URL}" \
    --ae "NO_PROXY=${no_proxy}" \
    --ae "no_proxy=${no_proxy}"
}

verifier_env_args() {
  # 将 SWE-Atlas LLM verifier 的独立配置转换成 Harbor 的 --ve 参数列表。
  # 这些 VERIFIER_* 只由 run_swe_atlas.sh 的 ATLAS_EVAL_CONFIG 设置，不从
  # LLM_CONFIG 派生；这是统一 LLM_CONFIG 参数链的唯一例外。
  # EVAL_API_TYPE=responses（aidp 网关）时追加 AZURE_*，让 evaluate_tests.py 用
  # AzureOpenAI + responses.create 路由到网关 Responses API；chat 路径维持原样。
  local args=(
    --ve "EVAL_API_KEY=${VERIFIER_API_KEY}"
    --ve "EVAL_BASE_URL=${VERIFIER_BASE_URL}"
    --ve "EVAL_MODEL=${VERIFIER_MODEL}"
    --ve "HTTP_PROXY=${PROXY_URL}"
    --ve "HTTPS_PROXY=${PROXY_URL}"
    --ve "http_proxy=${PROXY_URL}"
    --ve "https_proxy=${PROXY_URL}"
    --ve "NO_PROXY=localhost,127.0.0.1,::1"
    --ve "no_proxy=localhost,127.0.0.1,::1"
  )
  if [[ -n "${EVAL_API_TYPE:-}" ]]; then
    args+=(
      --ve "EVAL_API_TYPE=${EVAL_API_TYPE}"
      --ve "AZURE_API_KEY=${AZURE_API_KEY:-}"
      --ve "AZURE_API_BASE=${AZURE_API_BASE:-}"
      --ve "AZURE_API_VERSION=${AZURE_API_VERSION:-}"
      --ve "EVAL_API_VERSION=${EVAL_API_VERSION:-}"
    )
  fi
  printf '%s\n' "${args[@]}"
}

evolve_scripts_mounts_json() {
  # 根据 EVOLVE_SCRIPTS_DIR / EVOLVE_SCRIPTS_TARGET 生成 Pier --mounts-json 参数。
  #
  # 入参（来自环境变量）：
  #   EVOLVE_SCRIPTS_DIR       host 上要 bind mount 进容器的辅助 bash 脚本目录，可为空。
  #   EVOLVE_SCRIPTS_TARGET    容器内挂载根目录，默认 /app/.preinstalled_scripts。
  #   EVOLVE_SCRIPTS_READONLY  1=只读（默认），0=读写。
  #
  # 输出：
  #   stdout 打印一行 JSON 字符串。即使没有 evolved scripts，也会挂载 API retry runtime。
  #
  # 说明：因为显式传 --mounts-json 会覆盖 Pier 默认的 logs/agent、logs/verifier、
  # logs/artifacts 三个 bind mount，所以这里同时把这三个默认 mount 加回去，
  # 否则 agent/verifier 日志和 artifact 都会丢失。
  local scripts_dir="${EVOLVE_SCRIPTS_DIR:-}"
  if [[ -n "${scripts_dir}" && ! -d "${scripts_dir}" ]]; then
    echo "[evolve_scripts_mounts_json] EVOLVE_SCRIPTS_DIR='${scripts_dir}' is not a directory" >&2
    return 1
  fi
  [[ -d "${API_RETRY_RUNTIME_HOST}" ]] \
    || { echo "[evolve_scripts_mounts_json] API retry runtime missing: ${API_RETRY_RUNTIME_HOST}" >&2; return 1; }

  EVOLVE_SCRIPTS_DIR_ABS="${scripts_dir:+$(cd "${scripts_dir}" && pwd)}" \
  EVOLVE_SCRIPTS_TARGET="${EVOLVE_SCRIPTS_TARGET}" \
  EVOLVE_SCRIPTS_READONLY="${EVOLVE_SCRIPTS_READONLY}" \
  EVOLVE_SCRIPTS_INCLUDE_DEFAULT_LOG_MOUNTS="${EVOLVE_SCRIPTS_INCLUDE_DEFAULT_LOG_MOUNTS}" \
  API_RETRY_RUNTIME_HOST="$(cd "${API_RETRY_RUNTIME_HOST}" && pwd)" \
  API_RETRY_RUNTIME_TARGET="${API_RETRY_RUNTIME_TARGET}" \
  python - <<'PY'
import json
import os
from pathlib import Path

scripts_raw = os.environ.get("EVOLVE_SCRIPTS_DIR_ABS", "")
scripts_dir = Path(scripts_raw) if scripts_raw else None
target_root = os.environ.get("EVOLVE_SCRIPTS_TARGET", "/app/.preinstalled_scripts").rstrip("/") or "/"
retry_runtime = Path(os.environ["API_RETRY_RUNTIME_HOST"])
retry_target = os.environ.get("API_RETRY_RUNTIME_TARGET", "/opt/optiharness_api_retry")
read_only = os.environ.get("EVOLVE_SCRIPTS_READONLY", "1") not in ("0", "", "false", "False")
include_default_logs = os.environ.get("EVOLVE_SCRIPTS_INCLUDE_DEFAULT_LOG_MOUNTS", "1") not in ("0", "", "false", "False")

# 占位符，使用 Pier docker compose env 中 DockerEnvironmentEnvVars 注入的变量，
# 让 docker compose 在容器启动时把 logs 目录绑回宿主机。
# Harbor 的 Trial 层会自动在 user mounts 之前追加这三个默认 bind mount，
# 因此 Harbor 调用方应将 EVOLVE_SCRIPTS_INCLUDE_DEFAULT_LOG_MOUNTS=0，避免重复挂载。
mounts = []
if include_default_logs:
    mounts.extend([
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
    ])

mounts.append({
    "type": "bind",
    "source": str(retry_runtime.resolve()),
    "target": retry_target,
    "read_only": True,
})

if scripts_dir is not None:
    for entry in sorted(scripts_dir.iterdir()):
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

evolve_scripts_deploy() {
  # Deploy COAT's tools.json + executor.py as native function tools via the
  # stable evolve_tools_v6 runtime/config.
  # Idempotent; safe to call before every rollout. No-op if EVOLVE_SCRIPTS_DIR is empty.
  #
  # Sets a global for the caller:
  #   EVOLVE_TOOLS_CONFIG_HOST  host path of the config yaml → pass via `--ak config_file=`
  #
  # 使用配置中声明的协议；不能用 AZURE_API_KEY 推断，因为 azure_chat 同样使用该 key。
  # max_completion_tokens is read from MSWEA_MAXTOK_CONFIG when present (swebench path).
  local scripts_dir="${EVOLVE_SCRIPTS_DIR:-}"
  if [[ -z "${scripts_dir}" ]]; then
    return 0
  fi
  if [[ ! -d "${scripts_dir}" ]]; then
    echo "[evolve_scripts_deploy] EVOLVE_SCRIPTS_DIR='${scripts_dir}' is not a directory" >&2
    return 1
  fi
  local api_type="${LLM_API_TYPE:-chat}"
  local deploy_args=(--scripts-dir "${scripts_dir}" --api-type "${api_type}")
  if [[ -f "${MSWEA_MAXTOK_CONFIG:-}" ]]; then
    local mt
    mt="$(grep -E '^[[:space:]]*max_completion_tokens:' "${MSWEA_MAXTOK_CONFIG}" 2>/dev/null | head -1 | awk '{print $2}')"
    [[ -n "${mt}" ]] && deploy_args+=(--max-completion-tokens "${mt}")
  fi
  [[ -n "${TEMPERATURE:-}" ]] && deploy_args+=(--temperature "${TEMPERATURE}")
  [[ -n "${LLM_THINKING:-}" ]] && deploy_args+=(--thinking "${LLM_THINKING}")
  local json_out
  json_out="$(cd "$ROOT_DIR" && PYTHONPATH="$ROOT_DIR${PYTHONPATH:+:$PYTHONPATH}" python -m src.evolve.native_tools_v6 deploy "${deploy_args[@]}")" \
    || { echo "[evolve_scripts_deploy] deploy failed (framework=coat, api_type=${api_type})" >&2; return 1; }
  EVOLVE_TOOLS_CONFIG_HOST="$(python -c "import json,sys; print(json.load(sys.stdin)['config'])" <<<"${json_out}")"
  echo "[evolve_scripts_deploy] framework=coat api_type=${api_type} scripts=${scripts_dir} config=${EVOLVE_TOOLS_CONFIG_HOST}" >&2
}

evolve_scripts_native_tools_args() {
  # Emit pier/harbor args that register COAT's evolved native tools inside the
  # rollout container.
  # Pier's mini-swe-agent adapter defaults model_class=auto and maps openai/*
  # to litellm_response, which breaks OpenAI-compatible chat endpoints such as
  # DeepSeek and also overrides our native-tools config. An empty model_class
  # disables that auto override while preserving config_file's model_class.
  # No-op (prints nothing) when EVOLVE_TOOLS_CONFIG_HOST is unset (no scripts deployed →
  # the caller falls back to plain litellm / litellm_response).
  if [[ -z "${EVOLVE_TOOLS_CONFIG_HOST:-}" ]]; then
    return 0
  fi
  local target="${EVOLVE_SCRIPTS_TARGET:-/app/.preinstalled_scripts}"
  printf '%s\n' \
    --ak "model_class=" \
    --ak "config_file=${EVOLVE_TOOLS_CONFIG_HOST}" \
    --ae "EVOLVE_TOOLS_V6_REGISTRY=${target}/tools.json" \
    --ae "EVOLVE_TOOLS_V6_EXECUTOR=${target}/executor.py" \
    --ae "EVOLVE_TOOLS_V6_TIMEOUT_SECONDS=${EVOLVE_TOOLS_V6_TIMEOUT_SECONDS}" \
    --ae "EVOLVE_TOOLS_V6_MEMORY_MB=${EVOLVE_TOOLS_V6_MEMORY_MB}" \
    --ae "EVOLVE_TOOLS_V6_OUTPUT_TOKENS=${EVOLVE_TOOLS_V6_OUTPUT_TOKENS}"
}

evolve_scripts_prompt_template() {
  # Build a Jinja2 prompt template from EVOLVE_SCRIPTS_DIR/instruction.md only.
  #
  # Tool schemas come from tools.json, so the prompt carries only the
  # high-level guidance from instruction.md followed by {{ instruction }}.
  #
  # Pier/harbor's render_prompt_template requires the template to contain
  # {{ instruction }}; it is rendered then passed to mini-swe-agent --task=...
  #
  # 入参（环境变量）：
  #   EVOLVE_SCRIPTS_DIR  host 上 evolved scripts 目录；空则不生成。
  #
  # 输出：stdout 打印临时模板文件路径；为空或无 instruction.md 时打印空串。
  # 注意：调用方需在退出时清理该临时文件（trap 删除）。
  local scripts_dir="${EVOLVE_SCRIPTS_DIR:-}"
  if [[ -z "${scripts_dir}" ]]; then
    return 0
  fi
  local instr="${scripts_dir%/}/instruction.md"
  if [[ ! -f "${instr}" ]]; then
    return 0
  fi
  local tmp
  tmp="$(mktemp -t evolve_prompt.XXXXXX)"
  # instruction.md 可能含 {{ }} 字面量（Go template、shell $(( ))、JSON 等），
  # 直接交给 Jinja2 会被当表达式解析而报 TemplateSyntaxError。用 {% raw %}
  # 整体包起来按字面输出，只保留末尾真正的 {{ instruction }} 占位符。
  {
    printf '{%% raw %%}\n'
    cat "${instr}"
    printf '\n\n## Native-tool failure fallback\n'
    printf -- '- Evolved tools have a hard execution deadline. If one times out, runs out of memory, or returns a non-zero result, avoid repeating the same call unchanged.\n'
    printf -- '- Recommended response: narrow the evolved-tool path/query or otherwise reduce its scope; alternatively, fall back to an equivalent bash command.\n'
    printf '\n{%% endraw %%}\n\n---\n\n{{ instruction }}\n'
  } > "${tmp}"
  printf '%s\n' "${tmp}"
}

evolve_skip_exclude_args() {
  # 根据 EVOLVE_SKIP_FILE 输出一串可直接展开到 pier run 命令行的
  # "-x <task_name>" 参数（每行一个，便于 mapfile 读取）。
  #
  # 解析规则：
  #   - EVOLVE_SKIP_FILE="auto"（默认）：
  #       若 EVOLVE_SCRIPTS_DIR 非空且其下存在 evolve_used_case_id.txt，则使用之；
  #       否则不输出任何参数。
  #   - EVOLVE_SKIP_FILE=""：禁用。
  #   - EVOLVE_SKIP_FILE=<path>：强制使用该文件，若不存在则报错。
  #
  # 文件格式：每行一个 task name（Pier 的 -x 支持 glob）；
  # 忽略空行和以 # 开头的注释行；前后空白会被 strip。
  local skip_file="${EVOLVE_SKIP_FILE-auto}"
  if [[ "${skip_file}" == "auto" ]]; then
    if [[ -n "${EVOLVE_SCRIPTS_DIR:-}" ]] \
      && [[ -f "${EVOLVE_SCRIPTS_DIR%/}/evolve_used_case_id.txt" ]]; then
      skip_file="${EVOLVE_SCRIPTS_DIR%/}/evolve_used_case_id.txt"
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

evolve_skip_file_resolved() {
  # 与 evolve_skip_exclude_args 共用同一套解析规则，但只输出最终解析到的 skip 文件路径。
  # 适用于将 skip 文件直接传给非 pier/harbor 类（如 DataMind 的 --skip-case-id-txt）的入口。
  # 解析失败或禁用时不输出任何内容（成功返回）。
  local skip_file="${EVOLVE_SKIP_FILE-auto}"
  if [[ "${skip_file}" == "auto" ]]; then
    if [[ -n "${EVOLVE_SCRIPTS_DIR:-}" ]] \
      && [[ -f "${EVOLVE_SCRIPTS_DIR%/}/evolve_used_case_id.txt" ]]; then
      skip_file="${EVOLVE_SCRIPTS_DIR%/}/evolve_used_case_id.txt"
    else
      return 0
    fi
  fi
  if [[ -z "${skip_file}" ]]; then
    return 0
  fi
  if [[ ! -f "${skip_file}" ]]; then
    echo "[evolve_skip_file_resolved] EVOLVE_SKIP_FILE='${skip_file}' not found" >&2
    return 1
  fi
  printf '%s\n' "${skip_file}"
}

evolve_instruction_md_path() {
  # 输出 EVOLVE_SCRIPTS_DIR 下 instruction.md 的绝对路径；不存在则不输出。
  local scripts_dir="${EVOLVE_SCRIPTS_DIR:-}"
  if [[ -z "${scripts_dir}" ]]; then
    return 0
  fi
  local instr="${scripts_dir%/}/instruction.md"
  if [[ ! -f "${instr}" ]]; then
    return 0
  fi
  printf '%s\n' "${instr}"
}
