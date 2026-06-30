#!/usr/bin/env bash
# =============================================================================
# run_evolve_experiment.sh
#
# 在「单个 benchmark」上跑完整的 evolve 实验（deepseek-flash 等）：
#   步骤 1  v3 闭环生成 evolve scripts
#           python -m src.evolve.evolve_v3_cycle run ...
#           不传 --baseline-dir：第 1 轮以「空脚本」在采样到的 N 个 case 上跑
#           baseline（无 evolve scripts），得到 trajectory 作 evolve 来源；随后轮
#           装上演化出的 scripts 回验、LLM-judge、再演化，直至收敛或达上限轮。
#           产物：--scripts-dir 下的最终 evolved scripts（instruction.md + 工具脚本）。
#   步骤 2  装脚本最终评测
#           EVOLVE_SCRIPTS_DIR=<上一步 scripts> 调 scripts/run_<bench>.sh，
#           EVOLVE_SKIP_FILE=""（不跳过任何 case），跑全量 case（EVAL_N_TASKS），
#           测 code agent 装上 evolved scripts 后的效果。
#
# 设计要点（对应需求）：
#   * 不传入结果目录 → v3 闭环第 1 轮（空脚本）即 baseline，作 evolve 来源。
#   * 不需要跑「没有用 scripts 的数据」做对照 → 步骤 2 只跑装了 scripts 的一轮。
#   * 不跳过 case → 步骤 2 EVOLVE_SKIP_FILE="" 强制不跳过。
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
EVAL_N_TASKS="${EVAL_N_TASKS:-1000}"           # 步骤 2 最终评测跑多少 case（1000=全部）
MAX_ROUNDS="${MAX_ROUNDS:-5}"                 # v3 闭环最大轮数
SCRIPTS_DIR="${SCRIPTS_DIR:-}"                # 默认见下方带 TS 的兜底
WORK_DIR="${WORK_DIR:-}"
SWEBENCH_TASK_PATH="${SWEBENCH_TASK_PATH:-}"  # swebench 必填
SKIP_FINAL_EVAL="${SKIP_FINAL_EVAL:-0}"       # 1=只做步骤 1，跳过最终评测
DRY_RUN="${DRY_RUN:-0}"
CONDA_ENV="${CONDA_ENV-0622}"                 # 置空串则不激活 conda

log()  { printf '\n\033[1;34m[evolve-exp]\033[0m %s\n' "$*" >&2; }
warn() { printf '\n\033[1;33m[evolve-exp] WARN:\033[0m %s\n' "$*" >&2; }
die()  { printf '\n\033[1;31m[evolve-exp] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

[[ -n "$BENCHMARK" ]] || die "请设置 BENCHMARK（deep-swe / swe-atlas-qa / swe-atlas-tw / swe-atlas-rf / swebench）"

# ---------- swebench：把 parquet / 非flat 目录转成 harbor flat task 目录 ----------
# harbor 的 -p 只接受「每实例一个子目录、含 task.toml（或 task.yaml）」的 flat 目录；parquet 不行。
# 本函数：若给定的 SWEBENCH_TASK_PATH 已是 flat 且实例数够 → 原样返回；
# 否则（parquet，或 flat 但实例不足）调 adapter 生成 / 补齐（HF 离线缓存 + 代理），
# 产物落 SWEBENCH_TASKS_GEN（默认 tmp/harbor/datasets/swebench-verified），幂等可复用。
_swebench_count() { find "$1" -maxdepth 2 \( -name task.toml -o -name task.yaml \) 2>/dev/null | wc -l; }

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
  ( cd "$adapter_dir" \
    && HTTP_PROXY="$proxy" HTTPS_PROXY="$proxy" http_proxy="$proxy" https_proxy="$proxy" \
       UV_HTTP_TIMEOUT=300 HF_DATASETS_OFFLINE=1 \
       uv run python src/swebench_adapter/main.py --all --limit "$need" \
         --task-dir "$target" --overwrite ) \
    || die "[swebench] adapter 生成 flat 任务目录失败（见上方输出）。可手动重试：
  cd '$adapter_dir' && HTTP_PROXY=$proxy HTTPS_PROXY=$proxy UV_HTTP_TIMEOUT=300 HF_DATASETS_OFFLINE=1 \
    uv run python src/swebench_adapter/main.py --all --limit $need --task-dir '$target' --overwrite"
  have="$(_swebench_count "$target")"
  [[ "$have" -ge 1 ]] || die "[swebench] adapter 跑完但未生成任何 task 目录（$target）"
  log "[swebench] flat 任务就绪：$target（$have 个实例）"
  printf '%s\n' "$target"
}

# ---------- benchmark 元信息（镜像 src/evolve/evolve_v3_cycle.py 的 BENCHMARKS） ----------
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
  *) die "未知 BENCHMARK=$BENCHMARK（支持：deep-swe / swe-atlas-qa / swe-atlas-tw / swe-atlas-rf / swebench）";;
esac

[[ -d "$SOURCE_TASK_DIR" ]] || die "源任务目录不存在：$SOURCE_TASK_DIR"

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

# ---------- 输出目录（带时间戳，可被环境变量覆盖以便 resume） ----------
TS="$(date +%m%d-%H%M%S)"
[[ -n "$WORK_DIR"  ]] || WORK_DIR="${ROOT_DIR}/results/v3_cycle/${BENCHMARK}/${TS}"
# dry-run 不在仓库根建 .evolve_scripts_v3_*（v3 __init__ 也会 mkdir，故指到 WORK_DIR 下，
# 落在 gitignored 的 results/ 里，避免反复 dry-run 在仓库根留下一堆空目录）。
if [[ "${DRY_RUN}" == "1" ]]; then
  [[ -n "$SCRIPTS_DIR" ]] || SCRIPTS_DIR="${WORK_DIR}/scripts_dryrun"
else
  [[ -n "$SCRIPTS_DIR" ]] || SCRIPTS_DIR="${ROOT_DIR}/.evolve_scripts_v3_${BENCHMARK}_${TS}"
fi
EVAL_CASES_FILE="${WORK_DIR}/eval_cases.txt"
mkdir -p "$WORK_DIR" "$SCRIPTS_DIR"

# ---------- 步骤 0：采样 N 个 case id（排序取前 N，确定性可复现） ----------
mapfile -t CASE_IDS < <(
  find "$SOURCE_TASK_DIR" -maxdepth 1 -mindepth 1 -type d -printf '%f\n' 2>/dev/null \
    | sort | head -n "$EVOLVE_CASE_COUNT"
)
N=${#CASE_IDS[@]}
[[ "$N" -ge 1 ]] || die "源任务目录 $SOURCE_TASK_DIR 下未找到 case 子目录"
printf '%s\n' "${CASE_IDS[@]}" > "$EVAL_CASES_FILE"
log "[$BENCHMARK] 采样 $N 个 case（evolve 来源 + 回验集）-> $EVAL_CASES_FILE"

# ---------- 步骤 1：v3 闭环 run（生成 evolved scripts） ----------
V3_CMD=(python -m src.evolve.evolve_v3_cycle run
  --benchmark "$BENCHMARK"
  --config "$LLM_CONFIG"
  --scripts-dir "$SCRIPTS_DIR"
  --work-dir "$WORK_DIR"
  --eval-cases-file "$EVAL_CASES_FILE"
  --max-rounds "$MAX_ROUNDS"
  --n-concurrent "$N_CONCURRENT"
  --n-tasks "$EVAL_N_TASKS"
  --log-file "${WORK_DIR}/v3_cycle.log")
# swebench：给 v3 本地任务目录，使其只在采样到的 N 个 case 上跑（temp task dir 软链）
if [[ "$BENCHMARK" == "swebench" ]]; then
  V3_CMD+=(--swebench-task-path "$SOURCE_TASK_DIR")
fi

log "[$BENCHMARK] 步骤 1：v3 闭环 run（无 baseline-dir，第 1 轮空脚本=baseline）"
log "  scripts -> $SCRIPTS_DIR"
log "  work    -> $WORK_DIR"
if [[ "$DRY_RUN" == "1" ]]; then
  warn "[DRY_RUN] $(printf '%q ' "${V3_CMD[@]}")"
else
  ( cd "$ROOT_DIR" && "${V3_CMD[@]}" ) \
    || die "[$BENCHMARK] v3 闭环 run 失败（见 ${WORK_DIR}/v3_cycle.log）"
fi

# ---------- 步骤 2：装 scripts 最终评测（不跳过 case） ----------
if [[ "$SKIP_FINAL_EVAL" == "1" ]]; then
  log "[$BENCHMARK] SKIP_FINAL_EVAL=1，跳过步骤 2（最终评测）"
  log "[$BENCHMARK] 完成。evolved scripts：$SCRIPTS_DIR"
  exit 0
fi

# 校验 v3 确实产出了 scripts（至少有一个 <name>/intro.json 或 instruction.md）
if [[ "$DRY_RUN" != "1" ]]; then
  if ! ls "$SCRIPTS_DIR"/*/intro.json >/dev/null 2>&1 && [[ ! -f "$SCRIPTS_DIR/instruction.md" ]]; then
    warn "[$BENCHMARK] scripts_dir 未见 evolved scripts（$SCRIPTS_DIR），步骤 2 将以空脚本运行"
  fi
fi

EVAL_RUN_ID="evolve-v3-${BENCHMARK}-${TS}"
EVAL_ENV=(
  EVOLVE_SCRIPTS_DIR="$SCRIPTS_DIR"
  EVOLVE_SKIP_FILE=""                # 强制不跳过任何 case
  RUN_ID="$EVAL_RUN_ID"
  N_TASKS="$EVAL_N_TASKS"
  N_CONCURRENT="$N_CONCURRENT"
  LLM_CONFIG="$LLM_CONFIG"
)
[[ -n "$SPLIT" ]] && EVAL_ENV+=(SWE_ATLAS_SPLITS="$SPLIT")
[[ "$BENCHMARK" == "swebench" ]] && EVAL_ENV+=(SWEBENCH_TASK_PATH="$SOURCE_TASK_DIR")

log "[$BENCHMARK] 步骤 2：装 scripts 最终评测（RUN_ID=$EVAL_RUN_ID，不跳过 case，N_TASKS=$EVAL_N_TASKS）"
log "  结果目录 -> ${ROOT_DIR}/results/${RESULTS_SUBDIR}/${EVAL_RUN_ID}"
if [[ "$DRY_RUN" == "1" ]]; then
  warn "[DRY_RUN] env ${EVAL_ENV[*]} bash ${SCRIPT_DIR}/${RUN_SCRIPT}"
else
  env "${EVAL_ENV[@]}" bash "${SCRIPT_DIR}/${RUN_SCRIPT}" \
    || warn "[$BENCHMARK] 最终评测退出非零（部分 case 可能失败），结果仍保留。"
fi

log "[$BENCHMARK] 完成。"
log "  evolved scripts：$SCRIPTS_DIR"
log "  v3 闭环日志：  ${WORK_DIR}/v3_cycle.log"
log "  最终评测结果：  ${ROOT_DIR}/results/${RESULTS_SUBDIR}/${EVAL_RUN_ID}/"
