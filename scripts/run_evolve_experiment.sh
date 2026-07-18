#!/usr/bin/env bash
# =============================================================================
# run_evolve_experiment.sh
#
# 在「单个 benchmark」上跑完整的 v6.1 evolve 实验：
#   prep    采样 N 个 case（EVOLVE_CASE_COUNT，默认 16）+ 无脚本跑 code agent 得 T0
#           + v2 annotate（这是现有 v6.1 prep 流程的兼容依赖），存 results/prep/{runs,handles}/。
#   步骤 1  运行 focused-DAG v6.1 闭环，演化 tools.json、executor.py 和 instruction.md。
#   步骤 2  装载最终 harness 做独立 case 评测。
#           EVOLVE_SCRIPTS_DIR=<上一步 scripts> 调 scripts/run_<bench>.sh，
#           从未参与 evolve 的独立 case pool 精确选择 EVAL_N_TASKS 个 case，
#           测 code agent 装上 evolved scripts 后的效果。
#
# 设计要点：
#   * 本脚本只支持 v6.1。
#   * prep 保留既有 code-agent + v2 annotate 流程；v6.1 在步骤 1 构建 focused-DAG signals。
#   * prep 与 LLM 绑定（按 <bench>/<llm_name> 存），不同 LLM 不复用。
#   * evolve/eval case 显式互斥，split provenance 写入 WORK_DIR。
#
# 用法：
#   BENCHMARK=deep-swe bash scripts/run_evolve_experiment.sh
#   BENCHMARK=swe-atlas-tw LLM_CONFIG=_config/gpt53_codex.yaml N_CONCURRENT=4 \
#     bash scripts/run_evolve_experiment.sh
#   BENCHMARK=swebench SWEBENCH_TASK_PATH=tmp/harbor/datasets/swebench-verified \
#     bash scripts/run_evolve_experiment.sh
#   # swebench 也接受 HF 风格 parquet 目录（如 .../SWEBenchVerified），脚本会自动用
#   #   adapter 生成 flat 任务目录（一次性、用 HF 离线缓存 + 代理，幂等可复用）。
#   DRY_RUN=1 BENCHMARK=deep-swe bash scripts/run_evolve_experiment.sh   # 只打印命令
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
export ROOT_DIR

# ---------- 参数（环境变量，均有默认） ----------
BENCHMARK="${BENCHMARK:-}"
LLM_CONFIG="${LLM_CONFIG:-${ROOT_DIR}/_config/deepseekv4_flash.yaml}"
N_CONCURRENT="${N_CONCURRENT:-4}"
EVOLVE_CASE_COUNT="${EVOLVE_CASE_COUNT:-16}"   # 采样多少 case 做 evolve + 回验
EVOLVE_CASE_SELECTION="${EVOLVE_CASE_SELECTION:-diverse}" # diverse=按 codebase 轮转；sorted=旧字典序
EVAL_N_TASKS="${EVAL_N_TASKS:-64}"              # 步骤 2 最终评测跑多少 case（默认 64；swebench 上限 500）
SCRIPTS_DIR="${SCRIPTS_DIR:-}"                # 默认见下方带 TS 的兜底
MINI_SWE_AGENT_DIR="${MINI_SWE_AGENT_DIR:-${ROOT_DIR}/agent/mini-swe-agent}"  # evolve agent 用
# v6.1 每条 trajectory 最多 3 个 focused signal；batch-size 按 signal 计。
EVOLVE_CASES_PER_PROMPT="${EVOLVE_CASES_PER_PROMPT:-2}"
WORK_DIR="${WORK_DIR:-}"
SWEBENCH_TASK_PATH="${SWEBENCH_TASK_PATH:-}"  # swebench 必填
DAB_TASK_PATH="${DAB_TASK_PATH:-}"            # dab harbor flat task 目录（可自动生成）
SKIP_FINAL_EVAL="${SKIP_FINAL_EVAL:-0}"       # 1=只做步骤 1，跳过最终评测
DRY_RUN="${DRY_RUN:-0}"
CONDA_ENV="${CONDA_ENV-0622}"                 # 置空串则不激活 conda
# 保留变量用于既有日志、路径和外部调用兼容，但本分支固定为 v6.1。
EVOLVE_VERSION="${EVOLVE_VERSION:-v6.1}"

# ---------- phase 切换（prep / evolve / all / no_evolve）----------
# prep：每 benchmark 采样 16 + 无脚本跑 code agent 得 T0 + v2 标注（annotate+contrastive），
#       产物存到 $PREP_DIR（symlink 指向实际 run dir），所有 evolve 框架可复用。
# evolve：复用 prep 作 --baseline-dir，运行 v6.1 闭环。
# all ：prep 不就绪或 FORCE_PREP=1 才跑 prep，随后运行 v6.1 闭环。
PHASE="${PHASE:-all}"
EVOLVE_WORKERS="${EVOLVE_WORKERS:-8}"          # prep 的 v2 annotate LLM 并发
V61_ANNOTATE_EXECUTION="${V61_ANNOTATE_EXECUTION:-exact-global}"
V61_ANNOTATE_CHECKPOINT="${V61_ANNOTATE_CHECKPOINT:-1}"
V61_JUDGE_CONFIG="${V61_JUDGE_CONFIG:-$LLM_CONFIG}"
# prep 结果与被测 LLM 绑定（不同 LLM 的 trajectory/标注不可互用），故 PREP_DIR 按
# <bench>/<llm_name> 区分；LLM_NAME 从 $LLM_CONFIG 解析（失败回退到 config 文件名）。
LLM_NAME="${LLM_NAME:-$(
  python - "$LLM_CONFIG" <<'PY' 2>/dev/null || true
from pathlib import Path
import sys
for line in Path(sys.argv[1]).read_text().splitlines():
    if line.startswith("llm_name:"):
        print(line.split(":", 1)[1].strip().strip("\"'")); break
PY
)}"
[[ -n "$LLM_NAME" ]] || LLM_NAME="$(basename "$LLM_CONFIG" .yaml)"
RESULTS_ROOT="${RESULTS_ROOT:-${ROOT_DIR}/results}"
PREP_RUNS_ROOT="${PREP_RUNS_ROOT:-${RESULTS_ROOT}/prep/runs}"
PREP_HANDLES_ROOT="${PREP_HANDLES_ROOT:-${RESULTS_ROOT}/prep/handles}"
EVOLVE_RESULTS_ROOT="${EVOLVE_RESULTS_ROOT:-${RESULTS_ROOT}/evolve}"
EVAL_RESULTS_ROOT="${EVAL_RESULTS_ROOT:-${RESULTS_ROOT}/eval}"
NO_EVOLVE_RESULTS_ROOT="${NO_EVOLVE_RESULTS_ROOT:-${RESULTS_ROOT}/no_evolve}"
PREP_DIR="${PREP_DIR:-${PREP_HANDLES_ROOT}/${BENCHMARK}/${LLM_NAME}}"  # 稳定复用 handle（symlink）
FORCE_PREP="${FORCE_PREP:-0}"                  # 1=无视已有 prep 重跑
BASELINE_DIR="${BASELINE_DIR:-}"               # 显式覆盖 v6.1 --baseline-dir（默认用 $PREP_DIR）
FINAL_BASELINE_DIR="${FINAL_BASELINE_DIR:-}"   # final eval 对应的 no-evolve 64-case run

log()  { printf '\n\033[1;34m[evolve-exp]\033[0m %s\n' "$*" >&2; }
warn() { printf '\n\033[1;33m[evolve-exp] WARN:\033[0m %s\n' "$*" >&2; }
die()  { printf '\n\033[1;31m[evolve-exp] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

[[ -n "$BENCHMARK" ]] || die "请设置 BENCHMARK（deep-swe / swe-atlas-qa / swe-atlas-tw / swe-atlas-rf / swebench / datamind / dab）"

case "$EVOLVE_VERSION" in
  v6.1) EVOLVE_MOD="evolve_v6_1_cycle"; VERSION_TAG="v61cycle" ;;
  *) die "branchv6_1 只支持 EVOLVE_VERSION=v6.1（当前=$EVOLVE_VERSION）" ;;
esac
log "[$BENCHMARK] EVOLVE_VERSION=$EVOLVE_VERSION -> src.evolve.$EVOLVE_MOD"

# ---------- swebench：把 parquet / 非flat 目录转成 harbor flat task 目录 ----------
# harbor 的 -p 只接受「每实例一个子目录、含 task.toml（或 task.yaml）」的 flat 目录；parquet 不行。
# 本函数：若给定的 SWEBENCH_TASK_PATH 已是 flat 且实例数够 → 原样返回；
# 否则（parquet，或 flat 但实例不足）调 adapter 生成 / 补齐（HF 离线缓存 + 代理），
# 产物落 SWEBENCH_TASKS_GEN（默认 tmp/harbor/datasets/swebench-verified），幂等可复用。
# -H：跟随作为起始点的 symlink（让 symlink 共享的数据目录也能采样/计数），
#      但不跟随遍历中遇到的 symlink（与真实目录行为一致，不会多算软链条目）。
_swebench_count() { find -H "$1" -maxdepth 2 \( -name task.toml -o -name task.yaml \) 2>/dev/null | wc -l; }

prepare_swebench_tasks() {
  local given="$1"
  local need="${SWEBENCH_GEN_LIMIT:-${EVAL_N_TASKS}}"
  [[ "$need" =~ ^[0-9]+$ ]] || need=50
  # 期望 flat 目录路径：若 given 本身是 flat 就用它；否则用统一的 gen_dir
  local gen_dir="${SWEBENCH_TASKS_GEN:-${ROOT_DIR}/tmp/harbor/datasets/swebench-verified}"
  local target
  if [[ -d "$given" ]] && [[ "$(_swebench_count "$given")" -gt 0 ]]; then
    target="$given"
  else
    target="$gen_dir"
  fi
  # 已有且够数 → 直接用
  local have
  have="$(_swebench_count "$target")"
  if [[ "$have" -ge "$need" ]]; then
    log "[swebench] 使用已有 flat task 目录：$target（$have 个实例 ≥ 需求 $need）"
    printf '%s\n' "$target"; return 0
  fi
  local adapter_dir="${ROOT_DIR}/tmp/harbor/adapters/swebench"
  local proxy="${PROXY_URL:-http://sys-proxy-rd-relay.byted.org:8118}"
  mkdir -p "$target"
  log "[swebench] flat 任务不足（现有 $have < 需求 $need），用 adapter 补齐到 ≤${need} -> $target"
  log "[swebench]   一次性操作，约 $((need * 30 / 60)) 分钟（每实例 ~30s）；用 HF 离线缓存 + 代理 $proxy"
  if [[ "${DRY_RUN}" == "1" ]]; then
    warn "[DRY_RUN] 跳过 adapter 生成；假设 flat 目录已在 $target"
    printf '%s\n' "$target"; return 0
  fi
  # adapter 用 print() 输出进度到 stdout，会污染外层 $(...) 捕获 → 重定向到 stderr；
  # 函数返回值由末尾 printf '%s\n' "$target" 提供（干净路径）。
  ( cd "$adapter_dir" \
    && HTTP_PROXY="$proxy" HTTPS_PROXY="$proxy" http_proxy="$proxy" https_proxy="$proxy" \
       UV_HTTP_TIMEOUT=300 HF_DATASETS_OFFLINE=1 \
       uv run python src/swebench_adapter/main.py --all --limit "$need" \
         --task-dir "$target" --overwrite ) >&2 \
    || die "[swebench] adapter 生成 flat 任务目录失败（见上方输出）。可手动重试：
  cd '$adapter_dir' && HTTP_PROXY=$proxy HTTPS_PROXY=$proxy UV_HTTP_TIMEOUT=300 HF_DATASETS_OFFLINE=1 \
    uv run python src/swebench_adapter/main.py --all --limit $need --task-dir '$target' --overwrite"
  have="$(_swebench_count "$target")"
  [[ "$have" -ge 1 ]] || die "[swebench] adapter 跑完但未生成任何 task 目录（$target）"
  log "[swebench] flat 任务就绪：$target（$have 个实例）"
  printf '%s\n' "$target"
}

# ---------- datamind：用 longds_adapter 生成 harbor flat task 目录 ----------
# LongDS 不在 harbor registry，必须本地用 adapter 把每个 task 转成 harbor multi-step
# flat task 目录（每轮一个 [[steps]]，verifier 用 LLM judge）。本函数幂等：已有且够数则复用。
# 产物落 DATAMIND_TASKS_GEN（默认 tmp/harbor/datasets/longds），返回该路径。
# 与 prepare_swebench_tasks 同构，但 longds adapter 不需要 HF 离线缓存/代理（纯本地数据）。
_datamind_count() { find -H "$1" -maxdepth 2 -name task.toml 2>/dev/null | wc -l; }

prepare_datamind_tasks() {
  local need="${DATAMIND_GEN_LIMIT:-${EVAL_N_TASKS}}"
  [[ "$need" =~ ^[0-9]+$ ]] || need=68
  local target="${DATAMIND_TASKS_GEN:-${ROOT_DIR}/tmp/harbor/datasets/longds}"
  local task_root="${DATAMIND_TASK_ROOT:-${ROOT_DIR}/benchmark/DataMind/longds/DSGym/data/task/longds}"
  [[ -f "$task_root/task_list.json" ]] \
    || die "[datamind] task_list.json 不存在：$task_root/task_list.json"
  local have
  have="$(_datamind_count "$target")"
  if [[ "$have" -ge "$need" ]]; then
    log "[datamind] 使用已有 flat task 目录：$target（$have 个实例 ≥ 需求 $need）"
    printf '%s\n' "$target"; return 0
  fi
  local adapter_dir="${ROOT_DIR}/tmp/harbor/adapters/longds"
  log "[datamind] flat 任务不足（现有 $have < 需求 $need），用 adapter 生成 ≤${need} -> $target"
  log "[datamind]   一次性操作（纯本地数据，无需代理/HF）；每 task 含多轮 + 拷数据，约 $((need * 3 / 60)) 分钟"
  if [[ "${DRY_RUN}" == "1" ]]; then
    warn "[DRY_RUN] 跳过 adapter 生成；假设 flat 目录已在 $target"
    printf '%s\n' "$target"; return 0
  fi
  # adapter 用 print() 输出进度到 stdout，会污染外层 $(...) 捕获 → 重定向到 stderr。
  ( cd "$adapter_dir" \
    && uv run python src/longds_adapter/main.py --all --limit "$need" \
         --task-root "$task_root" --task-dir "$target" --overwrite ) >&2 \
    || die "[datamind] adapter 生成 flat 任务目录失败（见上方输出）。可手动重试：
  cd '$adapter_dir' && uv run python src/longds_adapter/main.py --all --limit $need \
    --task-root '$task_root' --task-dir '$target' --overwrite"
  have="$(_datamind_count "$target")"
  [[ "$have" -ge 1 ]] || die "[datamind] adapter 跑完但未生成任何 task 目录（$target）"
  log "[datamind] flat 任务就绪：$target（$have 个实例）"
  printf '%s\n' "$target"
}

# ---------- dab：用本项目 adapter 生成 harbor flat task 目录 ----------
_dab_count() { find -H "$1" -maxdepth 2 -name task.toml 2>/dev/null | wc -l; }

_dab_agent_answers_exposed() {
  local root="$1"
  [[ -d "$root" ]] || return 1
  find -L "$root" -type f \
    \( -path '*/environment/dab/query/ground_truth.csv' \
       -o -path '*/environment/dab/query/validate.py' \) \
    -print -quit 2>/dev/null | grep -q .
}

prepare_dab_tasks() {
  local need="${DAB_GEN_LIMIT:-${EVAL_N_TASKS}}"
  [[ "$need" =~ ^[0-9]+$ ]] || need=104
  local target="${DAB_TASK_PATH:-${ROOT_DIR}/benchmark/DBA-bench/harbor/datasets/dab}"
  local have legacy_blindness_bug=0
  have="$(_dab_count "$target")"
  if _dab_agent_answers_exposed "$target"; then
    legacy_blindness_bug=1
    warn "[dab] 检测到旧 Harbor task 将 ground truth/validator 暴露给 agent，强制重新生成：$target"
  fi
  if [[ "$have" -ge "$need" && "$legacy_blindness_bug" -eq 0 ]]; then
    log "[dab] 使用已有 flat task 目录：$target（$have 个实例 ≥ 需求 $need）"
    printf '%s\n' "$target"; return 0
  fi
  local adapter="${ROOT_DIR}/benchmark/DBA-bench/dab_harbor_adapter.py"
  local dab_root="${DAB_ROOT:-${ROOT_DIR}/benchmark/DBA-bench/DataAgentBench}"
  [[ -f "$adapter" ]] || die "[dab] adapter 不存在：$adapter"
  [[ -d "$dab_root" ]] || die "[dab] DAB_ROOT 不存在：$dab_root"
  log "[dab] flat 任务不足（现有 $have < 需求 $need），用 adapter 生成 ≤${need} -> $target"
  if [[ "${DRY_RUN}" == "1" ]]; then
    warn "[DRY_RUN] 跳过 DAB adapter 生成；假设 flat 目录已在 $target"
    printf '%s\n' "$target"; return 0
  fi
  local args=(python "$adapter" --dab-root "$dab_root" --output-dir "$target" --limit "$need" --overwrite)
  [[ -n "${DAB_DATASETS:-}" ]] && args+=(--datasets "$DAB_DATASETS")
  [[ "${DAB_USE_HINTS:-0}" == "1" ]] && args+=(--use-hints)
  ( cd "$ROOT_DIR" && "${args[@]}" ) >&2 \
    || die "[dab] adapter 生成 flat 任务目录失败（见上方输出）"
  have="$(_dab_count "$target")"
  [[ "$have" -ge 1 ]] || die "[dab] adapter 跑完但未生成任何 task 目录（$target）"
  ! _dab_agent_answers_exposed "$target" \
    || die "[dab] 新生成 task 仍向 agent 暴露 ground truth/validator，拒绝运行"
  log "[dab] flat 任务就绪：$target（$have 个实例）"
  printf '%s\n' "$target"
}

# ---------- benchmark 元信息（与 v6.1 BENCHMARKS 保持一致） ----------
case "$BENCHMARK" in
  deep-swe)
    RUN_SCRIPT="run_deep_swe.sh"; RESULTS_SUBDIR="deep-swe"; SPLIT=""
    SOURCE_TASK_DIR="${ROOT_DIR}/benchmark/deep-swe/tasks"
    ;;
  swe-atlas-qa|swe-atlas-tw|swe-atlas-rf)
    RUN_SCRIPT="run_swe_atlas.sh"; RESULTS_SUBDIR="$BENCHMARK"
    SPLIT="${BENCHMARK#swe-atlas-}"            # qa / tw / rf
    SOURCE_TASK_DIR="${ROOT_DIR}/benchmark/SWE-Atlas/data/${SPLIT}"
    ;;
  swebench)
    RUN_SCRIPT="run_swe_bench.sh"; RESULTS_SUBDIR="swebench-verified"; SPLIT=""
    # SWEBENCH_TASK_PATH 可指向：
    #   (a) 已生成的 harbor flat task 目录（每实例一个子目录、含 task.toml）—— 直接用；
    #   (b) HF 风格 parquet/数据根（如 .../SWEBenchVerified/data/*.parquet）——
    #       不能直接喂 harbor -p，需先用 adapter 生成 flat 目录（见 prepare_swebench_tasks）。
    SWEBENCH_TASK_PATH="${SWEBENCH_TASK_PATH:-${ROOT_DIR}/tmp/harbor/datasets/swebench-verified}"
    SOURCE_TASK_DIR="$(prepare_swebench_tasks "${SWEBENCH_TASK_PATH}")"
    ;;
  datamind)
    # DataMind/longds：走 harbor（与 swebench 同构），用 longds_adapter 生成 multi-step
    # flat task 目录，mini-swe-agent 跑，verifier 用 LLM judge。产物是 ATIF trajectory，
    # evolve 直接消费（无需 DSGym schema 适配）。
    # DATAMIND_TASK_PATH 可指向已有 harbor flat task 目录；否则用 adapter 生成。
    DATAMIND_TASK_PATH="${DATAMIND_TASK_PATH:-${ROOT_DIR}/tmp/harbor/datasets/longds}"
    RUN_SCRIPT="run_datamind_harbor.sh"; RESULTS_SUBDIR="datamind-longds"; SPLIT=""
    SOURCE_TASK_DIR="$(prepare_datamind_tasks)"
    ;;
  dab)
    DAB_TASK_PATH="${DAB_TASK_PATH:-${ROOT_DIR}/benchmark/DBA-bench/harbor/datasets/dab}"
    export EVOLVE_TOOLS_V6_TIMEOUT_SECONDS="${EVOLVE_TOOLS_V6_TIMEOUT_SECONDS:-600}"
    RUN_SCRIPT="run_dab_harbor.sh"; RESULTS_SUBDIR="dab"; SPLIT=""
    SOURCE_TASK_DIR="$(prepare_dab_tasks)"
    ;;
  *) die "未知 BENCHMARK=$BENCHMARK（支持：deep-swe / swe-atlas-qa / swe-atlas-tw / swe-atlas-rf / swebench / datamind / dab）";;
esac

# DRY_RUN 时 prepare_*_tasks 会跳过实际生成、假设目录已就绪，故跳过存在性校验。
if [[ "${DRY_RUN}" != "1" ]]; then
  [[ -d "$SOURCE_TASK_DIR" ]] || die "源任务目录不存在：$SOURCE_TASK_DIR"
fi

# ---------- conda 激活（可选） ----------
if [[ -n "$CONDA_ENV" ]] && command -v conda >/dev/null 2>&1; then
  # shellcheck disable=SC1091
  source "$(conda info --base)/etc/profile.d/conda.sh" 2>/dev/null || true
  if conda activate "$CONDA_ENV" 2>/dev/null; then
    log "已激活 conda 环境：$CONDA_ENV"
  else
    warn "conda activate $CONDA_ENV 失败，沿用当前环境。"
  fi
fi

# ---------- 输出目录（带时间戳 + 版本，可被环境变量覆盖以便 resume） ----------
TS="$(date +%m%d-%H%M%S)"
# 每类结果有独立根目录，避免 prep/evolve/eval/no-evolve 相互混杂。
if [[ -z "$WORK_DIR" ]]; then
  case "$PHASE" in
    prep)      WORK_DIR="${RESULTS_ROOT}/prep/work/${BENCHMARK}/${TS}" ;;
    no_evolve) WORK_DIR="${NO_EVOLVE_RESULTS_ROOT}/work/${BENCHMARK}/${TS}" ;;
    *)         WORK_DIR="${EVOLVE_RESULTS_ROOT}/${VERSION_TAG}/${BENCHMARK}/${TS}" ;;
  esac
fi
if [[ "${DRY_RUN}" == "1" ]]; then
  [[ -n "$SCRIPTS_DIR" ]] || SCRIPTS_DIR="${WORK_DIR}/scripts_dryrun"
else
  [[ -n "$SCRIPTS_DIR" ]] || SCRIPTS_DIR="${WORK_DIR}/scripts"
fi
EVAL_CASES_FILE="${WORK_DIR}/eval_cases.txt"
FINAL_EVAL_CASES_FILE="${WORK_DIR}/final_eval_cases.txt"
mkdir -p "$WORK_DIR" "$SCRIPTS_DIR"

# ---------- 步骤 0：锁定 final eval，并选择与之互斥的 evolve case ----------
# v6.1 正式比较必须直接复用 no-evolve run 实际完成的 64 个 case。先锁定
# final eval，再从剩余任务中按 codebase/dataset 分层轮转选择 16 个 evolve case。
case "$EVOLVE_CASE_SELECTION" in diverse|sorted) ;; *)
  die "EVOLVE_CASE_SELECTION 必须是 diverse/sorted（当前=$EVOLVE_CASE_SELECTION）" ;;
esac
LOCK_FINAL_TO_NO_EVOLVE=0
if [[ "$PHASE" != "prep" && "$PHASE" != "no_evolve" ]]; then
  LOCK_FINAL_TO_NO_EVOLVE=1
  if [[ -z "$FINAL_BASELINE_DIR" ]]; then
    FINAL_BASELINE_DIR="$(find "${NO_EVOLVE_RESULTS_ROOT}/${RESULTS_SUBDIR}" \
      -mindepth 1 -maxdepth 1 -type d -name "noevolve-${BENCHMARK}-*" \
      2>/dev/null | sort | tail -1)"
  fi
  [[ -n "$FINAL_BASELINE_DIR" && -d "$FINAL_BASELINE_DIR" ]] \
    || die "[$EVOLVE_VERSION] 找不到 no-evolve baseline：${NO_EVOLVE_RESULTS_ROOT}/${RESULTS_SUBDIR}/noevolve-${BENCHMARK}-*。可用 FINAL_BASELINE_DIR=<run-dir> 显式指定。"
  python "$SCRIPT_DIR/select_eval_cases_from_baseline.py" \
    --run-dir "$FINAL_BASELINE_DIR" --expected-count "$EVAL_N_TASKS" \
    --output "$FINAL_EVAL_CASES_FILE" \
    --manifest "$WORK_DIR/final_eval_case_selection.json" >/dev/null
  mapfile -t FINAL_EVAL_CASE_IDS < "$FINAL_EVAL_CASES_FILE"
  log "[$BENCHMARK/$EVOLVE_VERSION] final eval 锁定到 no-evolve baseline：$FINAL_BASELINE_DIR"
  mapfile -t CASE_IDS < <(python "$SCRIPT_DIR/select_evolve_cases.py" \
    --task-root "$SOURCE_TASK_DIR" --benchmark "$BENCHMARK" --policy "$EVOLVE_CASE_SELECTION" \
    --limit "$EVOLVE_CASE_COUNT" --exclude-file "$FINAL_EVAL_CASES_FILE" \
    --manifest "$WORK_DIR/case_selection.json")
else
  mapfile -t CASE_IDS < <(python "$SCRIPT_DIR/select_evolve_cases.py" \
    --task-root "$SOURCE_TASK_DIR" --benchmark "$BENCHMARK" --policy "$EVOLVE_CASE_SELECTION" \
    --limit "$EVOLVE_CASE_COUNT" --manifest "$WORK_DIR/case_selection.json")
fi
N=${#CASE_IDS[@]}
[[ "$N" -ge 1 ]] || die "源任务目录 $SOURCE_TASK_DIR 下未找到 case 子目录"
printf '%s\n' "${CASE_IDS[@]}" > "$EVAL_CASES_FILE"
log "[$BENCHMARK] 采样 $N 个 case（policy=$EVOLVE_CASE_SELECTION，evolve 来源 + 回验集）-> $EVAL_CASES_FILE"

if [[ "$LOCK_FINAL_TO_NO_EVOLVE" != "1" ]]; then
  # 仅 prep/no_evolve 时从剩余 pool 独立选择 final eval。
  mapfile -t FINAL_EVAL_CASE_IDS < <(python "$SCRIPT_DIR/select_evolve_cases.py" \
    --task-root "$SOURCE_TASK_DIR" --benchmark "$BENCHMARK" \
    --policy diverse --limit "$EVAL_N_TASKS" --exclude-file "$EVAL_CASES_FILE" \
    --manifest "$WORK_DIR/final_eval_case_selection.json")
  printf '%s\n' "${FINAL_EVAL_CASE_IDS[@]}" > "$FINAL_EVAL_CASES_FILE"
fi
FINAL_EVAL_N=${#FINAL_EVAL_CASE_IDS[@]}
[[ "$FINAL_EVAL_N" -eq "$EVAL_N_TASKS" ]] \
  || die "final eval case 数量错误：需要 $EVAL_N_TASKS，实际 $FINAL_EVAL_N"
if comm -12 <(sort "$EVAL_CASES_FILE") <(sort "$FINAL_EVAL_CASES_FILE") | grep -q .; then
  die "evolve/eval case selection 出现交集，拒绝继续"
fi
python - "$WORK_DIR/case_selection.json" "$WORK_DIR/final_eval_case_selection.json" \
  "$WORK_DIR/experiment_split_manifest.json" <<'PY'
import json, sys
from pathlib import Path
evolve = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
evaluate = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
Path(sys.argv[3]).write_text(json.dumps({
    "schema_version": "evolve.experiment-split.v1",
    "disjoint": not ({x["case_id"] for x in evolve["selected"]}
                     & {x["case_id"] for x in evaluate["selected"]}),
    "evolve": evolve,
    "evaluation": evaluate,
}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
log "[$BENCHMARK] 独立 final eval 集：$FINAL_EVAL_N 个，和 evolve 交集为 0 -> $FINAL_EVAL_CASES_FILE"

# ---------- prep：temp task dir（16-case 软链，镜像 v3 _build_temp_task_dir）----------
# prep 阶段把 16 个 case 软链进临时目录，用 -p 限定无脚本 run 只跑这 16 个。
_prep_task_meta() {
  case "$BENCHMARK" in
    deep-swe)    PREP_TASK_ENV_NAME="DEEP_SWE_TASKS_PATH"; PREP_TASK_LAYOUT="flat" ;;
    swe-atlas-*) PREP_TASK_ENV_NAME="SWE_ATLAS_DATA_DIR";   PREP_TASK_LAYOUT="split" ;;
    swebench)    PREP_TASK_ENV_NAME="SWEBENCH_TASK_PATH";    PREP_TASK_LAYOUT="flat" ;;
    datamind)    PREP_TASK_ENV_NAME="DATAMIND_TASK_PATH";    PREP_TASK_LAYOUT="flat" ;;
    dab)         PREP_TASK_ENV_NAME="DAB_TASK_PATH";          PREP_TASK_LAYOUT="flat" ;;
    *) die "prep 不支持 benchmark=$BENCHMARK" ;;
  esac
}

_build_prep_taskdir() {
  local base="$1" cid src n=0 target_dir
  _prep_task_meta
  rm -rf "$base"; mkdir -p "$base"
  case "$PREP_TASK_LAYOUT" in
    split)
      # swe-atlas：-p = ${SWE_ATLAS_DATA_DIR}/${split}，故软链落 base/<split>，env 指向 base
      target_dir="$base/$SPLIT"; mkdir -p "$target_dir"; PREP_TASK_ENV_VAL="$base"
      for cid in "${CASE_IDS[@]}"; do
        src="$SOURCE_TASK_DIR/$cid"
        [[ -d "$src" ]] || { warn "prep: case 任务目录缺失，跳过：$src"; continue; }
        ln -sfn "$src" "$target_dir/$cid" || warn "prep: 软链失败：$cid"
        n=$((n+1))
      done ;;
    flat|*)
      target_dir="$base"; PREP_TASK_ENV_VAL="$base"
      for cid in "${CASE_IDS[@]}"; do
        src="$SOURCE_TASK_DIR/$cid"
        [[ -d "$src" ]] || { warn "prep: case 任务目录缺失，跳过：$src"; continue; }
        ln -sfn "$src" "$target_dir/$cid" || warn "prep: 软链失败：$cid"
        n=$((n+1))
      done ;;
  esac
  log "[$BENCHMARK] prep taskdir：$n/$N 个 case -> $target_dir（$PREP_TASK_ENV_NAME=$PREP_TASK_ENV_VAL）"
}

_build_taskdir_from_case_file() {
  local case_file="$1" base="$2" cid src n=0 target_dir
  _prep_task_meta
  rm -rf "$base"; mkdir -p "$base"
  if [[ "$PREP_TASK_LAYOUT" == "split" ]]; then
    target_dir="$base/$SPLIT"; mkdir -p "$target_dir"; EXACT_TASK_ENV_VAL="$base"
  else
    target_dir="$base"; EXACT_TASK_ENV_VAL="$base"
  fi
  while IFS= read -r cid || [[ -n "$cid" ]]; do
    [[ -n "$cid" ]] || continue
    src="$SOURCE_TASK_DIR/$cid"
    [[ -d "$src" ]] || die "独立 eval case 任务目录缺失：$src"
    ln -sfn "$src" "$target_dir/$cid"
    n=$((n+1))
  done < "$case_file"
  [[ "$n" -eq "$EVAL_N_TASKS" ]] || die "独立 eval taskdir 数量错误：$n != $EVAL_N_TASKS"
  log "[$BENCHMARK] exact final eval taskdir：$n 个非 evolve case -> $target_dir"
}

_prep_ready() {
  [[ -e "$PREP_DIR" && -f "$PREP_DIR/.prep_done" && -f "$PREP_DIR/eval_cases.txt" ]] || return 1
  # DAB prep produced before dab-harbor.v2-blind may contain trajectories that
  # read ground_truth.csv or query-specific validate.py. Never reuse those as
  # evolution evidence, including for v6.1 focused-DAG construction.
  if [[ "$BENCHMARK" == "dab" && ! -f "$PREP_DIR/.dab_harbor_v2_blind" ]]; then
    warn "[dab] 旧 prep 缺少 blind-schema marker，拒绝复用：$PREP_DIR"
    return 1
  fi
  # A prep handle is reusable only for the exact selected set. This prevents a
  # legacy all-Astropy baseline from being paired with the new diverse canary.
  cmp -s "$EVAL_CASES_FILE" "$PREP_DIR/eval_cases.txt"
}

# ---------- prep 主体：无脚本跑 16 + v2 annotate/contrastive ----------
run_prep() {
  local run_id="prep-${BENCHMARK}-${LLM_NAME}-${TS}"
  local results_root="${PREP_RESULTS_DIR:-${PREP_RUNS_ROOT}}"
  local prep_run_dir="${results_root}/${RESULTS_SUBDIR}/${run_id}"
  local taskdir_base="${WORK_DIR}/prep_taskdir"

  _build_prep_taskdir "$taskdir_base"

  local prep_env=(
    EVOLVE_SCRIPTS_DIR=""                # 无脚本 baseline
    EVOLVE_SKIP_FILE=""                  # 不跳过
    RUN_ID="$run_id"
    N_TASKS="$EVOLVE_CASE_COUNT"         # 只跑这 16 个
    N_CONCURRENT="$N_CONCURRENT"
    LLM_CONFIG="$LLM_CONFIG"
    RESULTS_DIR="$results_root"
  )
  [[ -n "$SPLIT" ]] && prep_env+=(SWE_ATLAS_SPLITS="$SPLIT")
  prep_env+=("${PREP_TASK_ENV_NAME}=${PREP_TASK_ENV_VAL}")

  log "[$BENCHMARK] prep 步骤 1：无脚本跑 $N 个 case（RUN_ID=$run_id）-> $prep_run_dir"
  if [[ "$DRY_RUN" == "1" ]]; then
    warn "[DRY_RUN] env ${prep_env[*]} bash ${SCRIPT_DIR}/${RUN_SCRIPT}"
  else
    env "${prep_env[@]}" bash "${SCRIPT_DIR}/${RUN_SCRIPT}" \
      || warn "[$BENCHMARK] prep 运行退出非零（部分 case 可能失败），继续标注。"
    # 兜底解析实际输出目录（run 脚本可能不在我们算的精确路径落盘）
    if [[ ! -d "$prep_run_dir" ]]; then
      prep_run_dir="$(find "${results_root}/${RESULTS_SUBDIR}" -maxdepth 1 -type d -name "${run_id}" 2>/dev/null | head -1 || true)"
    fi
    [[ -n "$prep_run_dir" && -d "$prep_run_dir" ]] \
      || die "[$BENCHMARK] prep 跑完后找不到输出目录：${results_root}/${RESULTS_SUBDIR}/${run_id}"
    local actual_prep_cases
    actual_prep_cases="$(extract_eval_cases_used "$prep_run_dir")"
    if ! diff -u <(sort "$EVAL_CASES_FILE") <(sort "$actual_prep_cases"); then
      die "[$BENCHMARK] prep 实际 case 与预选 evolve set 不一致；拒绝标注和 evolve"
    fi
    log "[$BENCHMARK] prep 实际 case 已核验：$N 个，和预选 evolve set 完全一致"
  fi

  log "[$BENCHMARK] prep 步骤 2：v2 annotate（deps + LLM op_type + step_meta）"
  log "  （contrastive 不在 prep 跑——各版本 sample 格式不同，由步骤 1 按 EVOLVE_VERSION 自建）"
  local ann_cmd=(python -m src.evolve.evolve_v2_chunk annotate "$prep_run_dir"
    --config "$LLM_CONFIG" --workers "$EVOLVE_WORKERS")
  if [[ "$DRY_RUN" == "1" ]]; then
    warn "[DRY_RUN] $(printf '%q ' "${ann_cmd[@]}")"
    warn "[DRY_RUN] 假设 prep 产物落 $prep_run_dir，symlink $PREP_DIR -> $prep_run_dir"
  else
    ( cd "$ROOT_DIR" && "${ann_cmd[@]}" ) \
      || die "[$BENCHMARK] v2 annotate 失败（见上方输出）"
    # 收尾：让 prep 目录自包含 + 稳定 handle 供所有框架复用
    cp "$EVAL_CASES_FILE" "$prep_run_dir/eval_cases.txt"
    if [[ "$BENCHMARK" == "dab" ]]; then
      printf '%s\n' 'dab-harbor.v2-blind' > "$prep_run_dir/.dab_harbor_v2_blind"
    fi
    touch "$prep_run_dir/.prep_done"
    mkdir -p "$(dirname "$PREP_DIR")"
    ln -sfn "$prep_run_dir" "$PREP_DIR"
    log "[$BENCHMARK] prep 完成：$PREP_DIR -> $prep_run_dir"
    log "  （含 .prep_done + eval_cases.txt + 已标注 trajectory.json；contrastive 由步骤 1 自建）"
  fi
}

# ---------- 提取 evolve 装 scripts 评测实际跑的 case id ----------
# 从 results/eval/<subdir>/evolve-v2chunk-<bench>-*/ 的 trial 子目录里读 config.json 的
# task.path，取 basename 作 case id（比解析 trial 目录名 __suffix 更可靠，且不受 harbor
# task name 30 字符截断影响）。写到该评测目录下的 eval_cases_used.txt。
# 入参：$1=评测目录（evolve-v2chunk-<bench>-<ts>）。输出：该目录下 eval_cases_used.txt。
extract_eval_cases_used() {
  local eval_dir="$1"
  [[ -d "$eval_dir" ]] || die "[no_evolve] 评测目录不存在：$eval_dir"
  local out="$eval_dir/eval_cases_used.txt"
  python - "$eval_dir" "$out" <<'PY'
import json, sys
from pathlib import Path
eval_dir, out = Path(sys.argv[1]), Path(sys.argv[2])
case_ids = []
for sub in sorted(eval_dir.iterdir()):
    if not sub.is_dir():
        continue
    cfg = sub / "config.json"
    if not cfg.exists():
        continue
    try:
        c = json.loads(cfg.read_text(encoding="utf-8"))
    except Exception:
        continue
    task_path = (c.get("task") or {}).get("path") if isinstance(c.get("task"), dict) else None
    if not task_path:
        continue
    cid = Path(task_path).name
    if cid and cid not in case_ids:
        case_ids.append(cid)
out.write_text("\n".join(case_ids) + ("\n" if case_ids else ""), encoding="utf-8")
# 进度走 stderr，避免污染外层 $(...) 捕获（stdout 只留干净路径由 shell printf 提供）
print(f"[extract] {len(case_ids)} case ids -> {out}", file=sys.stderr)
PY
  printf '%s\n' "$out"
}

# ---------- phase 校验 + 分发 ----------
# PHASE 控制 prep / evolve / all / no_evolve。
case "$PHASE" in
  prep|evolve|all|no_evolve) ;;
  *) die "PHASE 必须是 prep / evolve / all / no_evolve（当前=$PHASE）";;
esac

if [[ "$PHASE" == "prep" ]]; then
  run_prep
  log "[$BENCHMARK] 完成（PHASE=prep）。prep 产物：$PREP_DIR"
  exit 0
fi

# ---------- no_evolve：复用 evolve 装 scripts 评测的 case 集合，不装 scripts 重跑 ----------
# 用途：对照 evolve 是否降本。提取 step2 装 scripts 评测实际跑的 case id（eval_cases_used.txt），
# 在同样这些 case 上不装 scripts（EVOLVE_SCRIPTS_DIR=""）跑一遍，与 step2 结果对照成本。
# 依赖：step2 评测产物（evolve-<version>-<bench>-<ts>）必须已存在（先跑 PHASE=all）。
if [[ "$PHASE" == "no_evolve" ]]; then
  # 找最新的 evolve-*-${BENCH}-* 评测目录（no_evolve 的 case 来源；跨版本匹配）
  eval_results_root="${EVAL_RESULTS_DIR:-${EVAL_RESULTS_ROOT}}"
  noevolve_results_root="${NO_EVOLVE_RESULTS_DIR:-${NO_EVOLVE_RESULTS_ROOT}}"
  eval_src_dir="${NO_EVOLVE_EVAL_DIR:-}"
  if [[ -z "$eval_src_dir" ]]; then
    eval_src_dir="$(find "${eval_results_root}/${RESULTS_SUBDIR}" -maxdepth 1 -type d \
      -name "evolve-*-${BENCHMARK}-*" 2>/dev/null | sort | tail -1)"
  fi
  [[ -n "$eval_src_dir" && -d "$eval_src_dir" ]] \
    || die "[no_evolve] 找不到 evolve 装 scripts 评测产物（results/${RESULTS_SUBDIR}/evolve-*-${BENCHMARK}-*）。请先跑 PHASE=all，或用 NO_EVOLVE_EVAL_DIR=<dir> 指定。"

  # 提取 case id（或复用已有 eval_cases_used.txt）
  cases_used_file="$eval_src_dir/eval_cases_used.txt"
  if [[ ! -f "$cases_used_file" ]]; then
    cases_used_file="$(extract_eval_cases_used "$eval_src_dir")"
  else
    log "[no_evolve] 复用已有 case 列表：$cases_used_file"
  fi
  [[ -s "$cases_used_file" ]] || die "[no_evolve] 提取的 case 列表为空：$cases_used_file"

  # 用提取的 case id 覆盖 CASE_IDS（复用 _build_prep_taskdir 软链机制）
  mapfile -t CASE_IDS < "$cases_used_file"
  N=${#CASE_IDS[@]}
  printf '%s\n' "${CASE_IDS[@]}" > "$EVAL_CASES_FILE"
  log "[no_evolve] 从 $eval_src_dir 提取 $N 个 case，不装 scripts 重跑（对照 evolve 评测）"

  # 软链这些 case 进临时目录，-p 指向它（精确控制 case 集合 = evolve 评测的那批）
  local_noevolve_taskdir="${WORK_DIR}/noevolve_taskdir"
  _build_prep_taskdir "$local_noevolve_taskdir"

  noevolve_run_id="noevolve-${BENCHMARK}-${TS}"
  noevolve_env=(
    EVOLVE_SCRIPTS_DIR=""                # 不装 scripts（对照基线）
    EVOLVE_SKIP_FILE=""                  # 不跳过
    RUN_ID="$noevolve_run_id"
    N_TASKS="$N"                         # 只跑提取的这些 case
    N_CONCURRENT="$N_CONCURRENT"
    LLM_CONFIG="$LLM_CONFIG"
    RESULTS_DIR="$noevolve_results_root"
  )
  [[ -n "$SPLIT" ]] && noevolve_env+=(SWE_ATLAS_SPLITS="$SPLIT")
  noevolve_env+=("${PREP_TASK_ENV_NAME}=${PREP_TASK_ENV_VAL}")

  log "[$BENCHMARK] no_evolve：不装 scripts 跑 $N 个 case（RUN_ID=$noevolve_run_id）"
  log "  对照 evolve 评测：$eval_src_dir"
  log "  结果目录 -> ${noevolve_results_root}/${RESULTS_SUBDIR}/${noevolve_run_id}"
  if [[ "$DRY_RUN" == "1" ]]; then
    warn "[DRY_RUN] env ${noevolve_env[*]} bash ${SCRIPT_DIR}/${RUN_SCRIPT}"
  else
    env "${noevolve_env[@]}" bash "${SCRIPT_DIR}/${RUN_SCRIPT}" \
      || warn "[$BENCHMARK] no_evolve 评测退出非零（部分 case 可能失败），结果仍保留。"
  fi
  log "[$BENCHMARK] 完成（PHASE=no_evolve）。对照基线结果：${noevolve_results_root}/${RESULTS_SUBDIR}/${noevolve_run_id}/"
  log "  evolve 评测（装 scripts）：$eval_src_dir"
  log "  no_evolve（不装 scripts）：${noevolve_results_root}/${RESULTS_SUBDIR}/${noevolve_run_id}/"
  exit 0
fi

# evolve / all：需要 prep 就绪（或显式 BASELINE_DIR）
if [[ "$PHASE" == "all" ]]; then
  if ! _prep_ready || [[ "$FORCE_PREP" == "1" ]]; then
    [[ "$FORCE_PREP" == "1" ]] && log "[$BENCHMARK] FORCE_PREP=1，重跑 prep"
    run_prep
  else
    log "[$BENCHMARK] 复用已有 prep（case selection 完全一致）：$PREP_DIR"
  fi
else  # PHASE == evolve
  if [[ -z "$BASELINE_DIR" ]] && ! _prep_ready; then
    die "[$BENCHMARK] PHASE=evolve 但 prep 未就绪（$PREP_DIR 无 .prep_done）。先跑 PHASE=prep，或用 BASELINE_DIR=<dir> 指定已有 baseline。"
  fi
fi
# evolve 输入 = prep 目录（已含无脚本 trajectory + v2 标注：step_meta/dependencies/
# brief_observations）。v6.1 在步骤 1 自建 focused-DAG contrastive samples。
EVOLVE_INPUT="${BASELINE_DIR:-$PREP_DIR}"

# ---------- 步骤 1：运行 v6.1 evolve ----------
# ---------- v6.1：focused DAG slices + complete harness evolution ----------
# v6.1 保持 v6 registry runtime，但使用独立完整 Python 实现：prefix-only 标注
# dependencies/op_type/op_state，优先生成局部 DAG 信号，无信号时按 bounded phase 兜底；
# evolve agent 同时优化 tools.json、executor.py 和 instruction.md。
if [[ "$EVOLVE_VERSION" == "v6.1" ]]; then
  case "$V61_ANNOTATE_CHECKPOINT" in
    1|true|TRUE|yes|YES) V61_ANNOTATE_CHECKPOINT_ENABLED=1 ;;
    0|false|FALSE|no|NO) V61_ANNOTATE_CHECKPOINT_ENABLED=0 ;;
    *) die "V61_ANNOTATE_CHECKPOINT 必须是 0/1/true/false（当前=$V61_ANNOTATE_CHECKPOINT）" ;;
  esac
  V61_CMD=(python -m src.evolve.evolve_v6_1_cycle run
    --benchmark "$BENCHMARK"
    --config "$LLM_CONFIG"
    --judge-config "$V61_JUDGE_CONFIG"
    --eval-cases-file "$EVAL_CASES_FILE"
    --baseline-dir "$EVOLVE_INPUT"
    --scripts-dir "$SCRIPTS_DIR"
    --work-dir "$WORK_DIR"
    --log-file "$WORK_DIR/v6_1_experiment.log"
    --mini-swe-agent-dir "$MINI_SWE_AGENT_DIR"
    --workers "$EVOLVE_WORKERS"
    --annotation-execution "$V61_ANNOTATE_EXECUTION"
    --batch-size "$EVOLVE_CASES_PER_PROMPT"
    --max-prompt-chars "${V61_MAX_PROMPT_CHARS:-50000}"
    --max-observation-chars "${V61_MAX_OBSERVATION_CHARS:-1000}"
    --n-tasks "$N"
    --n-concurrent "$N_CONCURRENT"
    --n-cycles "${V61_N_CYCLES:-4}")
  [[ "$V61_ANNOTATE_CHECKPOINT_ENABLED" == "0" ]] && V61_CMD+=(--no-annotation-checkpoint)
  [[ "$BENCHMARK" == "swebench" ]] && export SWEBENCH_TASK_PATH="$SOURCE_TASK_DIR"
  [[ "$BENCHMARK" == "dab" ]] && export DAB_TASK_PATH="$SOURCE_TASK_DIR"
  [[ "$DRY_RUN" == "1" ]] && V61_CMD+=(--dry-run)
  log "[$BENCHMARK] 步骤 1：v6.1 focused-DAG 闭环（baseline=$EVOLVE_INPUT，cycles=${V61_N_CYCLES:-4}）"
  log "  scripts -> $SCRIPTS_DIR  （完整 harness：tools.json + executor.py + instruction.md）"
  log "  work    -> $WORK_DIR（局部 DAG signals + phase fallback；prompt budget=${V61_MAX_PROMPT_CHARS:-50000} chars）"
  log "  annotate -> ${V61_ANNOTATE_EXECUTION}（checkpoint=${V61_ANNOTATE_CHECKPOINT_ENABLED}；逐 step prompt 强等价）"
  log "  gate     -> 每个 batch 的 harness diff 经 LLM-as-Judge；拒绝即回滚（config=$V61_JUDGE_CONFIG）"
  log "  layout  -> ${WORK_DIR}/cycle-N/{rollout,evolve_logs,harness_after}（prep 只读快照）"
  if [[ "$DRY_RUN" == "1" ]]; then
    warn "[DRY_RUN] $(printf '%q ' "${V61_CMD[@]}")"
  else
    ( cd "$ROOT_DIR" && "${V61_CMD[@]}" ) \
      || die "[$BENCHMARK] v6.1 cycle 失败（见 ${WORK_DIR}/v6_1_report.json）"
  fi
  log "[$BENCHMARK] 完成 v6.1 evolve 闭环，进入独立 $EVAL_N_TASKS-case final eval。"
  EVOLVE_DONE=1
fi

# ---------- 步骤 2：装 scripts 最终评测（与 evolve case 严格互斥） ----------
if [[ "$SKIP_FINAL_EVAL" == "1" ]]; then
  log "[$BENCHMARK] SKIP_FINAL_EVAL=1，跳过步骤 2（最终评测）"
  log "[$BENCHMARK] 完成。evolved scripts：$SCRIPTS_DIR"
  exit 0
fi

# 校验 evolve 确实产出了 scripts（至少有一个 <name>/intro.json 或 instruction.md）
if [[ "$DRY_RUN" != "1" ]]; then
  if ! ls "$SCRIPTS_DIR"/*/intro.json >/dev/null 2>&1 && [[ ! -f "$SCRIPTS_DIR/instruction.md" ]]; then
    warn "[$BENCHMARK] scripts_dir 未见 evolved scripts（$SCRIPTS_DIR），步骤 2 将以空脚本运行"
  fi
fi

EVAL_RUN_ID="evolve-${VERSION_TAG}-${BENCHMARK}-${TS}"
FINAL_EVAL_TASKDIR="${WORK_DIR}/final_eval_taskdir"
FINAL_EVAL_DIR="${EVAL_RESULTS_ROOT}/${RESULTS_SUBDIR}/${EVAL_RUN_ID}"
FINAL_EVAL_CASE_MATCH="not-run"
_build_taskdir_from_case_file "$FINAL_EVAL_CASES_FILE" "$FINAL_EVAL_TASKDIR"
EVAL_ENV=(
  EVOLVE_SCRIPTS_DIR="$SCRIPTS_DIR"
  EVOLVE_SKIP_FILE=""                # taskdir 已精确限定为独立 eval set
  RUN_ID="$EVAL_RUN_ID"
  N_TASKS="$EVAL_N_TASKS"
  N_CONCURRENT="$N_CONCURRENT"
  LLM_CONFIG="$LLM_CONFIG"
  RESULTS_DIR="$EVAL_RESULTS_ROOT"
)
EVAL_ENV+=(EVOLVE_TOOLS_MODE="registry")
[[ -n "$SPLIT" ]] && EVAL_ENV+=(SWE_ATLAS_SPLITS="$SPLIT")
EVAL_ENV+=("${PREP_TASK_ENV_NAME}=${EXACT_TASK_ENV_VAL}")

log "[$BENCHMARK] 步骤 2：装 scripts 最终评测（RUN_ID=$EVAL_RUN_ID，独立非-evolve case，N_TASKS=$EVAL_N_TASKS）"
log "  结果目录 -> ${EVAL_RESULTS_ROOT}/${RESULTS_SUBDIR}/${EVAL_RUN_ID}"
if [[ "$DRY_RUN" == "1" ]]; then
  warn "[DRY_RUN] env ${EVAL_ENV[*]} bash ${SCRIPT_DIR}/${RUN_SCRIPT}"
else
  env "${EVAL_ENV[@]}" bash "${SCRIPT_DIR}/${RUN_SCRIPT}" \
    || warn "[$BENCHMARK] 最终评测退出非零（部分 case 可能失败），结果仍保留。"
  if [[ -d "$FINAL_EVAL_DIR" ]]; then
    ACTUAL_EVAL_CASES_FILE="$(extract_eval_cases_used "$FINAL_EVAL_DIR")"
    if ! diff -u <(sort "$FINAL_EVAL_CASES_FILE") <(sort "$ACTUAL_EVAL_CASES_FILE"); then
      FINAL_EVAL_CASE_MATCH="false"
      warn "[$BENCHMARK] 实际 eval case 与预选独立集合不一致，结果不可用于正式比较"
    else
      FINAL_EVAL_CASE_MATCH="true"
      log "[$BENCHMARK] 实际 final eval case 已核验：$EVAL_N_TASKS 个，和 evolve set 交集为 0"
    fi
  else
    FINAL_EVAL_CASE_MATCH="missing-result-dir"
  fi
fi

python - "$WORK_DIR/experiment_result_manifest.json" "$WORK_DIR" "$PREP_DIR" \
  "$FINAL_EVAL_DIR" "$FINAL_EVAL_CASE_MATCH" "$N" "$EVAL_N_TASKS" "$SCRIPTS_DIR" <<'PY'
import json
import sys
from pathlib import Path

output, work, prep, final_eval, case_match, evolve_n, eval_n, scripts_dir = sys.argv[1:]
payload = {
    "schema_version": "v6.1-experiment-result.1",
    "version": "v6.1",
    "evolve_work_dir": work,
    "prep_handle": prep,
    "final_eval_dir": final_eval,
    "final_eval_dir_exists": Path(final_eval).is_dir(),
    "final_eval_cases_match_selection": case_match,
    "evolve_case_count": int(evolve_n),
    "final_eval_case_count": int(eval_n),
    "artifacts": {
        "run_manifest": str(Path(work) / "v6_1_run_manifest.json"),
        "cycle_report": str(Path(work) / "v6_1_report.json"),
        "output_layout": str(Path(work) / "output_layout.json"),
        "split_manifest": str(Path(work) / "experiment_split_manifest.json"),
        "final_scripts": scripts_dir,
    },
}
Path(output).write_text(
    json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
PY

log "[$BENCHMARK] 完成。"
log "  prep（可复用）：$PREP_DIR"
log "  evolved scripts：$SCRIPTS_DIR"
log "  evolve 日志：    ${WORK_DIR}/cycle-N/evolve_logs"
log "  最终评测结果：  ${EVAL_RESULTS_ROOT}/${RESULTS_SUBDIR}/${EVAL_RUN_ID}/"
