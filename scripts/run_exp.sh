#!/usr/bin/env bash
# =============================================================================
# run_exp.sh
#
# 一键启动 5 个 benchmark 的完整 evolve 实验（deepseek-flash 等）：
#   deep-swe / swe-atlas-qa / swe-atlas-tw / swe-atlas-rf / swebench
#
# 为每个 benchmark 开一个 tmux 窗口（同一 session `evolve`），窗口内：
#   cd /home/fanmeihao/projects/CostReduce
#   conda activate 0622
#   BENCHMARK=<bench> N_CONCURRENT=<n> LLM_CONFIG=<cfg> bash scripts/run_evolve_experiment.sh
#
# 每个 benchmark 的实验流程（run_evolve_experiment.sh）：
#   步骤 1  v3 闭环生成 evolve scripts（无 baseline-dir：第 1 轮空脚本=baseline，
#           其 trajectory 作 evolve 来源；后续轮装 scripts 回验、LLM-judge、再演化
#           直至收敛或达 MAX_ROUNDS）。
#   步骤 2  EVOLVE_SCRIPTS_DIR=<上一步 scripts> 调 run_<bench>.sh，EVOLVE_SKIP_FILE=""
#           （不跳过 case）跑全量，测 code agent 装上 evolved scripts 的效果。
#
# 用法：
#   bash scripts/run_exp.sh                        # 默认 N_CONCURRENT=4，逐个创建窗口
#   N_CONCURRENT=3 bash scripts/run_exp.sh         # 并行度调 3（见下方并行度告警）
#   LLM_CONFIG=_config/gpt53_codex.yaml bash scripts/run_exp.sh
#   DRY_RUN=1 bash scripts/run_exp.sh              # 窗口内只打印命令，不执行
#   SESSION=evolve bash scripts/run_exp.sh         # 自定义 tmux session 名
#
# ⚠️ 并行度告警：deepseek-flash API 并行上限约 16。5 窗口 × N_CONCURRENT 默认 4 = 20 > 16，
#   会触发限流/排队甚至失败。建议 N_CONCURRENT=3（5×3=15 ≤ 16），或确认配额已上调到 ≥20。
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

# ---------- 参数（环境变量，均有默认） ----------
SESSION="${SESSION:-evolve}"
N_CONCURRENT="${N_CONCURRENT:-4}"
LLM_CONFIG="${LLM_CONFIG:-_config/deepseekv4_flash.yaml}"
CONDA_ENV="${CONDA_ENV:-0622}"
MAX_ROUNDS="${MAX_ROUNDS:-5}"
EVAL_N_TASKS="${EVAL_N_TASKS:-1000}"
EVOLVE_CASE_COUNT="${EVOLVE_CASE_COUNT:-16}"
DRY_RUN="${DRY_RUN:-0}"
FORCE="${FORCE:-0}"   # session 已含全部窗口时，FORCE=1 强行追加重复窗口（逃逸口）
# swebench 专属：数据源路径。默认指向本地 SWE-bench Verified parquet 目录（HF 风格）；
#   也可以是已生成的 harbor flat task 目录。run_evolve_experiment.sh 的 prepare_swebench_tasks
#   会自动判别：parquet → 用 adapter 生成 flat 目录（一次性、HF 离线缓存+代理）；flat → 直接用。
SWEBENCH_TASK_PATH="${SWEBENCH_TASK_PATH:-/home/fanmeihao/projects/_AutpPrep3_out/_data/SWEBenchVerified}"
# 生成多少个 flat 任务（够 v3 采样 16 + 最终评测用）。默认与 EVAL_N_TASKS 一致。
SWEBENCH_GEN_LIMIT="${SWEBENCH_GEN_LIMIT:-${EVAL_N_TASKS}}"

log()  { printf '\n\033[1;34m[run-exp]\033[0m %s\n' "$*" >&2; }
warn() { printf '\n\033[1;33m[run-exp] WARN:\033[0m %s\n' "$*" >&2; }
die()  { printf '\n\033[1;31m[run-exp] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

# ---------- 前置检查 ----------
command -v tmux >/dev/null 2>&1 || die "未找到 tmux，请先安装。"
[[ -f "${SCRIPT_DIR}/run_evolve_experiment.sh" ]] \
  || die "缺少 scripts/run_evolve_experiment.sh（应与本脚本同目录）。"

# 总并发 = 窗口数 × N_CONCURRENT。> 16 时告警但不阻断（用户可能已上调配额）。
TOTAL_CONC=$((5 * N_CONCURRENT))
if [[ "${TOTAL_CONC}" -gt 16 ]]; then
  warn "5 窗口 × N_CONCURRENT=${N_CONCURRENT} = ${TOTAL_CONC} > 16，可能触发 API 限流。"
  warn "  建议 N_CONCURRENT=3（5×3=15 ≤ 16），或确认配额已上调到 ≥${TOTAL_CONC}。"
fi

# ---------- swebench 任务目录预生成（启动窗口前确保就绪） ----------
# 若 SWEBENCH_TASK_PATH 是 parquet / 非 flat / 实例不足，在此一次性生成 / 补齐 flat 任务目录
# （幂等可复用），避免每个 tmux 窗口重复生成 / 互相竞争。
# 直接复用 run_evolve_experiment.sh 里的 _swebench_count + prepare_swebench_tasks（含数量校验）。
prepare_swebench() {
  if [[ "${DRY_RUN}" == "1" ]]; then
    warn "[DRY_RUN] 跳过 swebench flat 任务生成（实际运行时会自动生成）"
    return 0
  fi
  local p="${SWEBENCH_TASK_PATH}"
  log "swebench：检查/生成 flat 任务目录（数据源 ${p}，需求 ${SWEBENCH_GEN_LIMIT}）..."
  BENCHMARK=swebench EVAL_N_TASKS="${SWEBENCH_GEN_LIMIT}" SWEBENCH_GEN_LIMIT="${SWEBENCH_GEN_LIMIT}" \
    DRY_RUN=0 SWEBENCH_TASK_PATH="${p}" \
    bash -c '
      set -euo pipefail
      ROOT_DIR="'"${ROOT_DIR}"'"
      cd "$ROOT_DIR"
      export ROOT_DIR DRY_RUN=0 EVAL_N_TASKS SWEBENCH_GEN_LIMIT \
             SWEBENCH_TASK_PATH PROXY_URL="${PROXY_URL:-http://sys-proxy-rd-relay.byted.org:8118}"
      eval "$(sed -n "/^log()  {/,/^die()  {.*exit 1; }/p" scripts/run_evolve_experiment.sh)"
      eval "$(sed -n "/^_swebench_count()/,/^}$/p" scripts/run_evolve_experiment.sh)"
      eval "$(sed -n "/^prepare_swebench_tasks()/,/^}$/p" scripts/run_evolve_experiment.sh)"
      prepare_swebench_tasks "'"${p}"'" >/dev/null
    ' && log "swebench flat 任务目录已就绪" \
      || die "swebench flat 任务生成失败。可手动重试：
  BENCHMARK=swebench SWEBENCH_TASK_PATH='${p}' bash scripts/run_evolve_experiment.sh"
}

prepare_swebench

# ---------- 启动单个 benchmark 的 tmux 窗口 ----------
# 每个窗口：cd 到项目根 → 激活 conda 0622 → 带参跑 run_evolve_experiment.sh；
# 脚本退出后 exec bash 留住窗口，便于查看日志与排错。
start_window() {
  local bench="$1" win_name="$2" extra_env="$3"
  local cmd="cd ${ROOT_DIR} && bash -lc '"
  cmd+="conda activate ${CONDA_ENV} 2>/dev/null || true; "
  cmd+="BENCHMARK=${bench} N_CONCURRENT=${N_CONCURRENT} LLM_CONFIG=${LLM_CONFIG} "
  cmd+="MAX_ROUNDS=${MAX_ROUNDS} EVAL_N_TASKS=${EVAL_N_TASKS} "
  cmd+="EVOLVE_CASE_COUNT=${EVOLVE_CASE_COUNT} ${extra_env}"
  # swebench：让子脚本知道生成上限（与 EVAL_N_TASKS 一致，够 v3 采样+最终评测）
  [[ "$bench" == "swebench" ]] && cmd+=" SWEBENCH_GEN_LIMIT=${SWEBENCH_GEN_LIMIT}"
  if [[ "${DRY_RUN}" == "1" ]]; then
    cmd+=" DRY_RUN=1"
  fi
  cmd+=" bash scripts/run_evolve_experiment.sh'; exec bash"

  # 会话不存在 → new-session -s 创建（注意用 -s 而非 -t：-t 会让 tmux 把不存在的
  # session 当作 target 而报 "command or window name given with target"）；
  # 已存在 → 后续窗口用 new-window -t 追加。
  if ! tmux has-session -t "${SESSION}" 2>/dev/null; then
    tmux new-session -d -s "${SESSION}" -n "${win_name}" "${cmd}"
  else
    tmux new-window -t "${SESSION}" -n "${win_name}" "${cmd}"
  fi
  log "已启动窗口 ${win_name}（benchmark=${bench}）"
}

# ---------- 5 个 benchmark ----------
# 期望的窗口名（用 4 字母短名避免 tmux 把窗口名截断成 16 字符后多出 "-" 尾巴）。
# benchmark 实参仍是完整名，通过环境变量 BENCHMARK= 传给 run_evolve_experiment.sh。
WINDOWS=(
  "deep-swe|deep-swe|"
  "sweqa|swe-atlas-qa|"
  "swetw|swe-atlas-tw|"
  "swerf|swe-atlas-rf|"
  "swebench|swebench|SWEBENCH_TASK_PATH=${SWEBENCH_TASK_PATH} "
)

# 重新运行保护：若 session 已存在且 5 个窗口都在，说明本实验已在跑 / 已跑过，
# 直接追加会复制 5 个窗口。给提示让用户决定（FORCE=1 可强行追加）。
if tmux has-session -t "${SESSION}" 2>/dev/null; then
  EXISTING="$(tmux list-windows -t "${SESSION}" -F '#{window_name}' 2>/dev/null || true)"
  HAVE=0
  for entry in "${WINDOWS[@]}"; do
    wname="${entry%%|*}"
    if grep -qx "${wname}" <<<"${EXISTING}" 2>/dev/null; then HAVE=$((HAVE+1)); fi
  done
  if [[ "${HAVE}" -eq ${#WINDOWS[@]} ]] && [[ "${FORCE}" != "1" ]]; then
    die "session '${SESSION}' 已含全部 ${#WINDOWS[@]} 个窗口（可能实验已在跑）。" \
        "如需强行追加重复窗口：FORCE=1 bash $0；或换 SESSION=xxx bash $0；或先 tmux kill-session -t ${SESSION}。"
  fi
fi

for entry in "${WINDOWS[@]}"; do
  IFS='|' read -r wname bench extra <<<"${entry}"
  start_window "${bench}" "${wname}" "${extra}"
done

echo ""
log "5 个窗口已创建（session=${SESSION}）。"
log "查看 / 切换窗口：tmux attach -t ${SESSION}（Ctrl+B 再按数字键切窗口）"
log "各窗口日志：results/v3_cycle/<bench>/<TS>/v3_cycle.log"
log "最终评测：results/<bench>/evolve-v3-<bench>-<TS>/"
