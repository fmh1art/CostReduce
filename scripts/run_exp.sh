
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
EVOLVE_CASE_COUNT="${EVOLVE_CASE_COUNT:-16}"
SWEBENCH_TASK_PATH="${SWEBENCH_TASK_PATH:-}"
DATAMIND_TASK_PATH="${DATAMIND_TASK_PATH:-}"
DAB_TASK_PATH="${DAB_TASK_PATH:-}"
DRY_RUN="${DRY_RUN:-0}"
SKIP_FINAL_EVAL="${SKIP_FINAL_EVAL:-0}"
FORCE_PREP="${FORCE_PREP:-0}"
# 本分支只保留最终确定的 v6.1 框架。
EVOLVE_VERSION="v6.1"
V61_N_CYCLES="${V61_N_CYCLES:-4}"
V61_MAX_PROMPT_CHARS="${V61_MAX_PROMPT_CHARS:-50000}"
V61_MAX_OBSERVATION_CHARS="${V61_MAX_OBSERVATION_CHARS:-1000}"
V61_ANNOTATE_EXECUTION="${V61_ANNOTATE_EXECUTION:-exact-global}"
V61_ANNOTATE_CHECKPOINT="${V61_ANNOTATE_CHECKPOINT:-1}"
# 这些在 run_exp.sh 内用 ${:-} 设了默认，须 export 才能传给子脚本；
# LLM_CONFIG / CONDA_ENV 不在此设默认 —— 若用户从前缀传入则作为 env 自动透传，
# 否则子脚本用其自带默认（${VAR:-default}）。
export EVAL_N_TASKS EVOLVE_CASE_COUNT SWEBENCH_TASK_PATH DATAMIND_TASK_PATH DAB_TASK_PATH DRY_RUN SKIP_FINAL_EVAL FORCE_PREP EVOLVE_VERSION V61_N_CYCLES V61_MAX_PROMPT_CHARS V61_MAX_OBSERVATION_CHARS V61_ANNOTATE_EXECUTION V61_ANNOTATE_CHECKPOINT

log()  { printf '\n\033[1;35m[run_exp]\033[0m %s\n' "$*"; }
warn() { printf '\n\033[1;33m[run_exp] WARN:\033[0m %s\n' "$*" >&2; }

EVOLVE_CASES_PER_PROMPT="${EVOLVE_CASES_PER_PROMPT:-2}"   # 每 prompt 含几个 case（透传给 --batch-size）

log "BENCHMARKS=[$BENCHMARKS]  N_CONCURRENT=$N_CONCURRENT  EVOLVE_WORKERS=$EVOLVE_WORKERS  EVAL_N_TASKS=$EVAL_N_TASKS  EVOLVE_CASE_COUNT=$EVOLVE_CASE_COUNT  EVOLVE_VERSION=$EVOLVE_VERSION  EVOLVE_CASES_PER_PROMPT=$EVOLVE_CASES_PER_PROMPT"

FAILED=()
for BENCH in $BENCHMARKS; do
  log "=================== $BENCH ==================="
  # run_evolve_experiment.sh 内部 set -e，失败会非零退出；用 if 捕获，不中断循环。
  if N_CONCURRENT="$N_CONCURRENT" EVOLVE_WORKERS="$EVOLVE_WORKERS" \
     EVOLVE_VERSION="$EVOLVE_VERSION" EVOLVE_CASES_PER_PROMPT="$EVOLVE_CASES_PER_PROMPT" \
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
log "可复用 prep： results/prep/{runs,handles}/"
log "evolve 产物： results/evolve/<version>/<bench>/<TS>/"
log "最终评测：    results/eval/<bench>/<run-id>/"
log "no-evolve：   results/no_evolve/<bench>/<run-id>/"
exit "$RUN_RC"
