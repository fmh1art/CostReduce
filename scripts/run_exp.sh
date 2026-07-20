#!/usr/bin/env bash
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "$ROOT_DIR" || { echo "[run_exp] cd $ROOT_DIR 失败" >&2; exit 1; }

# BENCHMARKS="${BENCHMARKS:-deep-swe swe-atlas-qa swe-atlas-tw swe-atlas-rf swebench}"
# BENCHMARKS="${BENCHMARKS:-deep-swe swe-atlas-qa swe-atlas-tw swe-atlas-rf swebench datamind}"
BENCHMARKS="${BENCHMARKS:-deep-swe}"
N_CONCURRENT="${N_CONCURRENT:-16}"
EVOLVE_WORKERS="${EVOLVE_WORKERS:-16}"
EVAL_N_TASKS="${EVAL_N_TASKS:-64}"
EVAL_ALL_CASES="${EVAL_ALL_CASES:-0}"
EVOLVE_CASE_COUNT="${EVOLVE_CASE_COUNT:-16}"
LLM_CONFIG="${LLM_CONFIG:-${ROOT_DIR}/_config/deepseekv4_flash.yaml}"
RESULTS_ROOT="${RESULTS_ROOT:-${ROOT_DIR}/results}"
SWEBENCH_TASK_PATH="${SWEBENCH_TASK_PATH:-}"
DATAMIND_TASK_PATH="${DATAMIND_TASK_PATH:-}"
DAB_TASK_PATH="${DAB_TASK_PATH:-}"
DRY_RUN="${DRY_RUN:-0}"
SKIP_FINAL_EVAL="${SKIP_FINAL_EVAL:-0}"
FORCE_PREP="${FORCE_PREP:-0}"
PHASE="${PHASE:-all}"
RUN_NO_EVOLVE_AFTER="${RUN_NO_EVOLVE_AFTER:-0}"
API_RETRY_PAUSE_SECONDS="${API_RETRY_PAUSE_SECONDS:-60}"
MSWEA_MODEL_RETRY_WAIT_SECONDS="${MSWEA_MODEL_RETRY_WAIT_SECONDS:-${API_RETRY_PAUSE_SECONDS}}"
# 唯一主框架是 COAT；v6.1 仅作为持久 schema/artifact 版本保留。
EVOLVE_FRAMEWORK="coat"
EVOLVE_VERSION="v6.1"
COAT_N_CYCLES="${COAT_N_CYCLES:-${V61_N_CYCLES:-4}}"
V61_N_CYCLES="$COAT_N_CYCLES"
V61_MAX_PROMPT_CHARS="${V61_MAX_PROMPT_CHARS:-50000}"
V61_MAX_OBSERVATION_CHARS="${V61_MAX_OBSERVATION_CHARS:-1000}"
V61_ANNOTATE_EXECUTION="${V61_ANNOTATE_EXECUTION:-exact-global}"
V61_ANNOTATE_CHECKPOINT="${V61_ANNOTATE_CHECKPOINT:-1}"
log()  { printf '\n\033[1;35m[run_exp]\033[0m %s\n' "$*"; }
warn() { printf '\n\033[1;33m[run_exp] WARN:\033[0m %s\n' "$*" >&2; }
die()  { printf '\n\033[1;31m[run_exp] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

[[ "$EVOLVE_CASE_COUNT" =~ ^[1-9][0-9]*$ ]] \
  || die "EVOLVE_CASE_COUNT 必须是正整数（当前=$EVOLVE_CASE_COUNT）"
[[ "$EVAL_ALL_CASES" == "0" || "$EVAL_ALL_CASES" == "1" ]] \
  || die "EVAL_ALL_CASES 必须是 0 或 1（当前=$EVAL_ALL_CASES）"
if [[ "$EVAL_ALL_CASES" != "1" ]]; then
  [[ "$EVAL_N_TASKS" =~ ^[1-9][0-9]*$ ]] \
    || die "EVAL_N_TASKS 必须是正整数（当前=$EVAL_N_TASKS）"
fi
[[ "$COAT_N_CYCLES" =~ ^[1-9][0-9]*$ ]] \
  || die "COAT_N_CYCLES 必须是正整数（当前=$COAT_N_CYCLES）"
[[ "$API_RETRY_PAUSE_SECONDS" =~ ^[1-9][0-9]*$ ]] \
  || die "API_RETRY_PAUSE_SECONDS 必须是正整数秒数（当前=$API_RETRY_PAUSE_SECONDS）"
[[ "$RUN_NO_EVOLVE_AFTER" == "0" || "$RUN_NO_EVOLVE_AFTER" == "1" ]] \
  || die "RUN_NO_EVOLVE_AFTER 必须是 0 或 1（当前=$RUN_NO_EVOLVE_AFTER）"
if [[ "$LLM_CONFIG" != /* ]]; then
  LLM_CONFIG="${ROOT_DIR}/${LLM_CONFIG#./}"
fi
[[ -f "$LLM_CONFIG" ]] || die "LLM_CONFIG 不存在：$LLM_CONFIG"
LLM_CONFIG="$(cd "$(dirname "$LLM_CONFIG")" && pwd)/$(basename "$LLM_CONFIG")"
LLM_CONFIG_NAME="$(basename "$LLM_CONFIG")"
LLM_CONFIG_NAME="${LLM_CONFIG_NAME%.*}"
LLM_CONFIG_NAME="${LLM_CONFIG_NAME//[^[:alnum:]._-]/_}"
[[ -n "$LLM_CONFIG_NAME" ]] || die "无法从 LLM_CONFIG 生成结果目录名：$LLM_CONFIG"

# 每组实验以 LLM 配置文件名和 evolve/eval case 数隔离。具体 run 目录仍保留
# 原来的 MMDD-HHMMSS 后缀，便于按时间追溯与 resume。
EVAL_CASE_TAG="$EVAL_N_TASKS"
[[ "$EVAL_ALL_CASES" == "1" ]] && EVAL_CASE_TAG="all"
EXPERIMENT_RESULTS_ROOT="${RESULTS_ROOT}/${LLM_CONFIG_NAME}/evolve${EVOLVE_CASE_COUNT}_eval${EVAL_CASE_TAG}"

# 入口统一确定并导出 LLM_CONFIG，所有 code-agent、annotate、rollout、evolve agent、
# LLM-as-Judge 与 final eval 都沿这条参数链使用同一配置。SWE-Atlas verifier/evaluate
# 是唯一例外，由 run_swe_atlas.sh 的 ATLAS_EVAL_CONFIG 独立控制。
export LLM_CONFIG LLM_CONFIG_NAME RESULTS_ROOT EXPERIMENT_RESULTS_ROOT
export EVAL_N_TASKS EVAL_ALL_CASES EVOLVE_CASE_COUNT SWEBENCH_TASK_PATH DATAMIND_TASK_PATH DAB_TASK_PATH DRY_RUN SKIP_FINAL_EVAL FORCE_PREP PHASE RUN_NO_EVOLVE_AFTER API_RETRY_PAUSE_SECONDS MSWEA_MODEL_RETRY_WAIT_SECONDS EVOLVE_FRAMEWORK EVOLVE_VERSION COAT_N_CYCLES V61_N_CYCLES V61_MAX_PROMPT_CHARS V61_MAX_OBSERVATION_CHARS V61_ANNOTATE_EXECUTION V61_ANNOTATE_CHECKPOINT

EVOLVE_CASES_PER_PROMPT="${EVOLVE_CASES_PER_PROMPT:-2}"   # 每 prompt 含几个 case（透传给 --batch-size）

EVAL_DISPLAY="$EVAL_N_TASKS"
[[ "$EVAL_ALL_CASES" == "1" ]] && EVAL_DISPLAY="all（每个 benchmark 的完整任务池，包含 evolve cases）"
log "BENCHMARKS=[$BENCHMARKS]  N_CONCURRENT=$N_CONCURRENT  EVOLVE_WORKERS=$EVOLVE_WORKERS  EVAL_N_TASKS=$EVAL_DISPLAY  EVOLVE_CASE_COUNT=$EVOLVE_CASE_COUNT  EVOLVE_FRAMEWORK=$EVOLVE_FRAMEWORK  COAT_N_CYCLES=$COAT_N_CYCLES  EVOLVE_CASES_PER_PROMPT=$EVOLVE_CASES_PER_PROMPT"
log "PHASE=$PHASE  API_RETRY_PAUSE_SECONDS=$API_RETRY_PAUSE_SECONDS  RUN_NO_EVOLVE_AFTER=$RUN_NO_EVOLVE_AFTER"
log "LLM_CONFIG=$LLM_CONFIG"
log "EXPERIMENT_RESULTS_ROOT=$EXPERIMENT_RESULTS_ROOT"

FAILED=()
for BENCH in $BENCHMARKS; do
  log "=================== $BENCH ==================="
  # run_evolve_experiment.sh 内部 set -e，失败会非零退出；用 if 捕获，不中断循环。
  if N_CONCURRENT="$N_CONCURRENT" EVOLVE_WORKERS="$EVOLVE_WORKERS" \
     EVOLVE_FRAMEWORK="$EVOLVE_FRAMEWORK" EVOLVE_VERSION="$EVOLVE_VERSION" \
     COAT_N_CYCLES="$COAT_N_CYCLES" EVOLVE_CASES_PER_PROMPT="$EVOLVE_CASES_PER_PROMPT" \
     BENCHMARK="$BENCH" \
     bash "${SCRIPT_DIR}/run_evolve_experiment.sh"; then
    log "[$BENCH] 完成"
  else
    rc=$?
    warn "[$BENCH] 退出非零 (rc=$rc)，结果可能部分保留；继续下一个 benchmark"
    FAILED+=("$BENCH")
  fi
done

log "=================== 汇总 ==================="
if [[ ${#FAILED[@]} -eq 0 ]]; then
  log "全部 benchmark 完成。"
  RUN_RC=0
else
  warn "以下 benchmark 退出非零（可重跑续接）：${FAILED[*]}"
  RUN_RC=1
fi

# Optional paired control: only after every regular benchmark invocation has
# returned do we run the same final-eval cases again without evolved scripts.
NO_EVOLVE_FAILED=()
if [[ "$RUN_NO_EVOLVE_AFTER" == "1" && "$PHASE" == "all" ]]; then
  log "=================== no-evolve 对照 ==================="
  for BENCH in $BENCHMARKS; do
    skip=0
    for failed_bench in "${FAILED[@]}"; do
      [[ "$BENCH" == "$failed_bench" ]] && skip=1 && break
    done
    if [[ "$skip" == "1" ]]; then
      warn "[$BENCH] 主实验失败，跳过 no-evolve，避免产生不完整配对"
      NO_EVOLVE_FAILED+=("$BENCH")
      continue
    fi
    log "[$BENCH] 主实验完成，开始 no-evolve 对照"
    # 主实验可能为了 resume 显式设置了 WORK_DIR/SCRIPTS_DIR；no-evolve 必须使用
    # 自己的 work namespace，否则会覆盖主实验的 eval_cases.txt 和 scripts 状态。
    if WORK_DIR= SCRIPTS_DIR= PHASE=no_evolve RUN_NO_EVOLVE_AFTER=0 \
       N_CONCURRENT="$N_CONCURRENT" EVOLVE_WORKERS="$EVOLVE_WORKERS" \
       BENCHMARK="$BENCH" bash "${SCRIPT_DIR}/run_evolve_experiment.sh"; then
      log "[$BENCH] no-evolve 完成"
    else
      rc=$?
      warn "[$BENCH] no-evolve 退出非零 (rc=$rc)"
      NO_EVOLVE_FAILED+=("$BENCH")
    fi
  done
  if [[ ${#NO_EVOLVE_FAILED[@]} -gt 0 ]]; then
    warn "以下 no-evolve 对照未完成：${NO_EVOLVE_FAILED[*]}"
    RUN_RC=1
  fi
fi
log "实验结果根：  $EXPERIMENT_RESULTS_ROOT"
log "可复用 prep： $EXPERIMENT_RESULTS_ROOT/prep/{runs,handles}/"
log "evolve 产物： $EXPERIMENT_RESULTS_ROOT/evolve/coat/<bench>/<TS>/"
log "最终评测：    $EXPERIMENT_RESULTS_ROOT/eval/<bench>/<run-id>/"
log "no-evolve：   $EXPERIMENT_RESULTS_ROOT/no_evolve/<bench>/<run-id>/"
exit "$RUN_RC"
