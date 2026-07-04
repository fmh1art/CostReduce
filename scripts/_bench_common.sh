#!/usr/bin/env bash

ROOT_DIR="${ROOT_DIR:-/home/fanmeihao/projects/CostReduce}"
LLM_CONFIG="${LLM_CONFIG:-${ROOT_DIR}/_config/deepseekv4_flash.yaml}"
RESULTS_DIR="${RESULTS_DIR:-${ROOT_DIR}/results}"
RUN_ID="${RUN_ID:-smoke-$(date +%m%d-%H%M%S)}"
N_CONCURRENT="${N_CONCURRENT:-8}"
N_ATTEMPTS="${N_ATTEMPTS:-1}"
N_TASKS="${N_TASKS:-1000}"
SWE_ATLAS_SPLITS="${SWE_ATLAS_SPLITS:-qa}"
HARBOR_AGENT_SETUP_TIMEOUT_MULTIPLIER="${HARBOR_AGENT_SETUP_TIMEOUT_MULTIPLIER:-4}"
PROXY_URL="${PROXY_URL:-http://sys-proxy-rd-relay.byted.org:8118}"
# verifier / LLM judge 统一用 deepseek-v4-flash（与 swe-atlas 的 VERIFIER_CONFIG 一致）。
# 默认从 _config/deepseekv4_flash.yaml 派生 VERIFIER_API_KEY/BASE_URL/MODEL；各 run 脚本
# 可用 VERIFIER_CONFIG 覆盖（如 responses 路由的 gpt53_codex.yaml）。swe-atlas 在自身脚本
# 里重复解析 VERIFIER_CONFIG 并会覆盖此处默认值；这里仅作 fallback 与非 swe-atlas 入口用。
VERIFIER_CONFIG="${VERIFIER_CONFIG:-${ROOT_DIR}/_config/deepseekv4_flash.yaml}"
if [[ -z "${VERIFIER_API_KEY:-}${VERIFIER_BASE_URL:-}${VERIFIER_MODEL:-}" ]]; then
  eval "$(python - "$VERIFIER_CONFIG" <<'PY'
from pathlib import Path
import shlex, sys
data = {}
for line in Path(sys.argv[1]).read_text().splitlines():
    if ':' in line and not line.startswith(' '):
        k, v = line.split(':', 1)
        data[k.strip()] = v.strip().strip("\"'")
for k, vk in [('VERIFIER_API_KEY','key'), ('VERIFIER_MODEL','llm_name'),
              ('VERIFIER_BASE_URL','openai_base_url')]:
    if vk in data:
        print(f'export {k}={shlex.quote(data[vk])}')
PY
)"
fi
HARBOR_ENV="${HARBOR_ENV:-docker}"
UV_BIN="${UV_BIN:-uv}"

# 可选：要 bind mount 到容器 workspace 根目录的辅助 bash 脚本目录。
# 不设或为空时，沿用 Pier 默认行为（不附加任何额外挂载，使用默认的 code agent）。
EVOLVE_SCRIPTS_DIR="${EVOLVE_SCRIPTS_DIR:-}"
# 容器内 workspace 根目录下用于盛放 evolve 辅助 bash 脚本的子目录。
# 单独放进一个隐藏子目录，避免与任务自带的 monorepo 顶层条目混在一起、误导 agent。
EVOLVE_SCRIPTS_TARGET="${EVOLVE_SCRIPTS_TARGET:-/app/.preinstalled_scripts}"
# 是否以只读方式挂载 evolve 辅助 bash 脚本。默认只读，避免容器内污染 host 上的脚本目录。
EVOLVE_SCRIPTS_READONLY="${EVOLVE_SCRIPTS_READONLY:-1}"
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
  # api_type=responses 时（bytedance aidp 网关只暴露 Responses API）：
  #   MODEL=azure/<llm_name>，额外导出 AZURE_API_KEY/AZURE_API_BASE/AZURE_API_VERSION，
  #   litellm.responses(azure/...) 据此路由到网关 responses 端点。
  eval "$(python - "$LLM_CONFIG" <<'PY'
from pathlib import Path
import shlex
import sys

data = {}
for line in Path(sys.argv[1]).read_text().splitlines():
    if ':' in line and not line.startswith(' '):
        key, value = line.split(':', 1)
        data[key.strip()] = value.strip().strip('"\'')

api_type = data.get('api_type', '').strip().lower()
api_key = data['key']
temperature = data.get('temperature', '0')
exports = {
    'OPENAI_API_KEY': api_key,
    'MSWEA_API_KEY': api_key,
    'JUDGE_API_KEY': api_key,
    'TEMPERATURE': temperature,
}
if api_type == 'responses':
    azure_endpoint = data['azure_endpoint']
    exports.update({
        'MODEL': 'azure/' + data['llm_name'],
        'AZURE_API_KEY': api_key,
        'AZURE_API_BASE': azure_endpoint,
        'AZURE_API_VERSION': data.get('api_version', '2024-03-01-preview'),
        # 兼容 harbor mini 适配器读取 OPENAI_BASE_URL 并转发；azure 路由实际用 AZURE_API_BASE。
        'OPENAI_BASE_URL': azure_endpoint,
        'OPENAI_API_BASE': azure_endpoint,
        'JUDGE_BASE_URL': azure_endpoint,
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
  printf '%s\n' \
    --ae "OPENAI_API_KEY=${OPENAI_API_KEY}" \
    --ae "MSWEA_API_KEY=${MSWEA_API_KEY}" \
    --ae "OPENAI_BASE_URL=${OPENAI_BASE_URL}" \
    --ae "OPENAI_API_BASE=${OPENAI_API_BASE}"
  # responses 配置额外注入 AZURE_*，让容器内 litellm.responses(azure/...) 路由到网关。
  if [[ -n "${AZURE_API_KEY:-}" ]]; then
    printf '%s\n' \
      --ae "AZURE_API_KEY=${AZURE_API_KEY}" \
      --ae "AZURE_API_BASE=${AZURE_API_BASE}" \
      --ae "AZURE_API_VERSION=${AZURE_API_VERSION}"
  fi
}

mswea_responses_config_file() {
  # 入参 $1: 原 mswea run_config yaml 路径。
  # 非 responses 配置（AZURE_API_KEY 未设）时原样返回原路径。
  # responses 配置时：复制该 yaml 并把 model.model_class 改成 litellm_response
  # （让 mini-swe-agent 走 litellm.responses 而非 chat-completions），打印临时文件路径。
  # 调用方负责在用完后删除返回的临时文件。
  local src="${1:-}"
  if [[ -z "${src}" ]]; then return 0; fi
  if [[ -z "${AZURE_API_KEY:-}" ]]; then printf '%s\n' "${src}"; return 0; fi
  local tmp
  tmp="$(mktemp -t mswea_resp_cfg.XXXXXX.yaml)"
  python - "$src" "$tmp" <<'PY'
from pathlib import Path
import sys

src, dst = Path(sys.argv[1]), Path(sys.argv[2])
lines = src.read_text(encoding="utf-8").splitlines()
out, replaced = [], False
for line in lines:
    stripped = line.lstrip()
    if stripped.startswith("model_class:") and not replaced:
        indent = line[: len(line) - len(stripped)]
        out.append(f"{indent}model_class: litellm_response")
        replaced = True
    else:
        out.append(line)
if not replaced:
    for i, line in enumerate(out):
        if line.rstrip() == "model:":
            out.insert(i + 1, "  model_class: litellm_response")
            replaced = True
            break
if not replaced:
    out = ["model:", "  model_class: litellm_response", ""] + out
dst.write_text("\n".join(out) + "\n", encoding="utf-8")
PY
  printf '%s\n' "${tmp}"
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
  # 将 SWE-Atlas LLM verifier 的配置转换成 Harbor 的 --ve 参数列表。
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
  #   EVOLVE_SCRIPTS_DIR       host 上要 bind mount 进容器的辅助 bash 脚本目录。空则不生成。
  #   EVOLVE_SCRIPTS_TARGET    容器内挂载根目录，默认 /app/.preinstalled_scripts。
  #   EVOLVE_SCRIPTS_READONLY  1=只读（默认），0=读写。
  #
  # 输出：
  #   stdout 打印一行 JSON 字符串。EVOLVE_SCRIPTS_DIR 为空时打印空串。
  #
  # 说明：因为显式传 --mounts-json 会覆盖 Pier 默认的 logs/agent、logs/verifier、
  # logs/artifacts 三个 bind mount，所以这里同时把这三个默认 mount 加回去，
  # 否则 agent/verifier 日志和 artifact 都会丢失。
  local scripts_dir="${EVOLVE_SCRIPTS_DIR:-}"
  if [[ -z "${scripts_dir}" ]]; then
    printf ''
    return 0
  fi
  if [[ ! -d "${scripts_dir}" ]]; then
    echo "[evolve_scripts_mounts_json] EVOLVE_SCRIPTS_DIR='${scripts_dir}' is not a directory" >&2
    return 1
  fi

  EVOLVE_SCRIPTS_DIR_ABS="$(cd "${scripts_dir}" && pwd)" \
  EVOLVE_SCRIPTS_TARGET="${EVOLVE_SCRIPTS_TARGET}" \
  EVOLVE_SCRIPTS_READONLY="${EVOLVE_SCRIPTS_READONLY}" \
  EVOLVE_SCRIPTS_INCLUDE_DEFAULT_LOG_MOUNTS="${EVOLVE_SCRIPTS_INCLUDE_DEFAULT_LOG_MOUNTS}" \
  python - <<'PY'
import json
import os
from pathlib import Path

scripts_dir = Path(os.environ["EVOLVE_SCRIPTS_DIR_ABS"])
target_root = os.environ.get("EVOLVE_SCRIPTS_TARGET", "/app/.preinstalled_scripts").rstrip("/") or "/"
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

evolve_scripts_tools_block() {
  # 扫 $EVOLVE_SCRIPTS_DIR/*/intro.json，拼成 markdown 工具清单（含绝对路径），
  # 用于注入下游 code agent 的 system prompt。
  #
  # 入参（环境变量）：
  #   EVOLVE_SCRIPTS_DIR          host 上 evolve 输出目录；空则不输出。
  #   EVOLVE_SCRIPTS_TARGET_ROOT  容器内挂载根目录，默认 /app/.preinstalled_scripts。
  #                               tools block 里 entrypoint 会拼成绝对路径：
  #                               ${EVOLVE_SCRIPTS_TARGET_ROOT}/<name>/<entrypoint>
  #
  # 输出（stdout）：
  #   - 没有任何 intro.json 时输出空串（软回退：测评脚本退回旧行为，只看 instruction.md）
  #   - 有 intro.json 时输出 markdown 工具清单
  #
  # 解析失败的 intro.json 会被跳过并在 stderr 打 warning，不会中断。
  local scripts_dir="${EVOLVE_SCRIPTS_DIR:-}"
  if [[ -z "${scripts_dir}" ]]; then
    return 0
  fi
  if [[ ! -d "${scripts_dir}" ]]; then
    return 0
  fi
  local target_root="${EVOLVE_SCRIPTS_TARGET_ROOT:-/app/.preinstalled_scripts}"
  EVOLVE_SCRIPTS_DIR_ABS="$(cd "${scripts_dir}" && pwd)" \
  EVOLVE_SCRIPTS_TARGET_ROOT="${target_root}" \
  python - <<'PY'
import json
import os
import sys
from pathlib import Path

# 下游 system prompt 拼装时只保留 agent 真正需要的最小字段集合：
#   - description:  一句话用途
#   - entrypoint:   绝对路径
#   - parameters:   名称/类型/必填/简短描述
#   - example:      至多 1 条（call + 一行 expected）
#   - cost_saving_rationale: 一句话
# 不输出 when_to_use（与 description 重复），不输出全部 examples（避免 prompt 膨胀）。
# 所有长字段都按下面 *_LIMIT 截断，确保 system prompt 不会因为单个工具爆 token。
DESC_LIMIT = 240
PARAM_DESC_LIMIT = 120
EXAMPLE_LIMIT = 1
EXPECTED_LIMIT = 160
RATIONALE_LIMIT = 200
# example 的 call（bash 全路径调用）是 agent 唯一能看到的脚本用法，给更长上限。
CALL_LIMIT = 320

def clip(text, limit):
    text = "" if text is None else str(text)
    text = " ".join(text.split())  # 折叠多余空白/换行
    return text if len(text) <= limit else text[:limit].rstrip() + "..."

scripts_dir = Path(os.environ["EVOLVE_SCRIPTS_DIR_ABS"])
target_root = os.environ.get("EVOLVE_SCRIPTS_TARGET_ROOT", "/app/.preinstalled_scripts").rstrip("/") or "/"

intros = []
for d in sorted(scripts_dir.iterdir()):
    if not d.is_dir():
        continue
    intro = d / "intro.json"
    if not intro.exists():
        continue
    try:
        data = json.loads(intro.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"[evolve_scripts_tools_block] skip {intro}: invalid JSON: {exc}", file=sys.stderr)
        continue
    if not isinstance(data, dict):
        print(f"[evolve_scripts_tools_block] skip {intro}: top-level is not a JSON object", file=sys.stderr)
        continue
    intros.append((d.name, data))

if not intros:
    sys.exit(0)

lines = [
    "## Available bash helper scripts",
    "",
    "Invoke each script with the `bash` tool using its full path, e.g.",
    "`bash /app/.preinstalled_scripts/<name>/main.sh <args>`. Do NOT call a script",
    "name directly as a tool — it is not one. Use the example line for the call form.",
    "",
]
for name, data in intros:
    entrypoint = data.get("entrypoint", "main.sh")
    abs_path = f"{target_root}/{name}/{entrypoint}" if target_root != "/" else f"/{name}/{entrypoint}"
    lines.append(f"### {name}")
    lines.append(f"- description: {clip(data.get('description', '(no description)'), DESC_LIMIT)}")
    lines.append(f"- entrypoint: {abs_path}")
    # 不输出 parameters schema —— 它会被下游 agent 误当成 native function tool 的参数
    # schema，从而用 <name>({param: ...}) 形式调用，但 evolved scripts 没注册成 native
    # tool，导致 'Unknown tool' FormatError → RepeatedFormatError → 0 步崩。只留 example
    # 的 bash 调用形式，让 agent 知道走 bash。
    examples = data.get("examples") or []
    if isinstance(examples, list) and examples:
        # 只取第一条 example，避免 prompt 膨胀
        ex = examples[0]
        if isinstance(ex, dict):
            call = clip(ex.get("call", ""), CALL_LIMIT)
            expected = clip(ex.get("expected", ""), EXPECTED_LIMIT)
            lines.append(f"- example: {call}  ->  {expected}")
    rationale = data.get("cost_saving_rationale")
    if rationale:
        lines.append(f"- cost_saving_rationale: {clip(rationale, RATIONALE_LIMIT)}")
    lines.append("")

print("\n".join(lines))
PY
}

evolve_scripts_prompt_template() {
  # 根据 EVOLVE_SCRIPTS_DIR 找到 instruction.md，并生成可供 Pier
  # --ak prompt_template_path=... 使用的 Jinja2 模板文件。
  #
  # 模板内容 = instruction.md 全文 + 分隔符 + tools_block（来自 intro.json）
  #           + 分隔符 + "{{ instruction }}"。
  # Pier 的 render_prompt_template 校验模板必须包含 {{ instruction }}，
  # 渲染后再传给底层 agent（如 mini-swe-agent --task=...）。
  #
  # 入参（环境变量）：
  #   EVOLVE_SCRIPTS_DIR          host 上辅助 bash 脚本目录；空则不生成。
  #   EVOLVE_SCRIPTS_TARGET_ROOT  容器内挂载根目录，用于 tools block 拼绝对路径。
  #
  # 输出：
  #   stdout 打印临时模板文件路径；EVOLVE_SCRIPTS_DIR 为空或不存在 instruction.md 时打印空串。
  #
  # 注意：调用方需要在退出时清理该临时文件（用 trap 删除）。
  local scripts_dir="${EVOLVE_SCRIPTS_DIR:-}"
  if [[ -z "${scripts_dir}" ]]; then
    return 0
  fi
  local instr="${scripts_dir%/}/instruction.md"
  if [[ ! -f "${instr}" ]]; then
    return 0
  fi
  local tools_block
  tools_block="$(evolve_scripts_tools_block)"
  local tmp
  tmp="$(mktemp -t evolve_prompt.XXXXXX)"
  # instruction.md 和 tools_block 可能含 {{ }} 字面量（如 Go template
  # '{{.GoFiles}}'、shell $(( ))、JSON 等），若直接交给 Jinja2 会被当成
  # 表达式解析而报 TemplateSyntaxError。用 {% raw %}...{% endraw %} 把这两
  # 段整体包起来按字面输出，只保留末尾真正的 {{ instruction }} 占位符。
  {
    printf '{%% raw %%}\n'
    cat "${instr}"
    if [[ -n "${tools_block}" ]]; then
      printf '\n\n---\n\n'
      printf '%s\n' "${tools_block}"
    fi
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
