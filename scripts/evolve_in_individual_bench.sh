#!/usr/bin/env bash
# =============================================================================
# evolve_in_individual_bench.sh
#
# 在每个 benchmark 上独立做 script-evolution 实验：
#   deep-swe / swe-atlas-qa / swe-atlas-tw / swe-atlas-rf
#
# 每个 benchmark 的流程：
#   1) baseline（无脚本）：作为 evolve 的 trajectory 来源，同时也是“无脚本”对照组。
#      - deep-swe：复用既有 without-scripts baseline（用户未要求重跑）。
#      - swe-atlas-qa/tw/rf：重跑一份完整 without-scripts baseline（用户要求；既有
#        baseline-sweatlas-* 是带 skip 的部分历史结果、且与本实验选的 16 个 evolve case
#        不一致，不可直接对照）。既作 trajectory 来源，也作对照组——分析时用
#        evolve_used_case_id.txt 过滤掉 16 个 evolve case，即可与 evolved 评估在同一
#        non-16 case 集上对比。断点续跑：已 rerun 过会自动复用最新一份，不重复重跑。
#   2) 选 16 个 case 用于 evolve：优先复用 .evolve_scripts/evolve_used_case_id.txt 里
#      本 benchmark 已有的 16 个（trajectory 取自 results/without_scripts_total_cases，
#      取不到再回退到该 benchmark 的 baseline 目录），不足 16 时用 baseline 目录里
#      “按 case id 排序、未选中”的 trajectory 补齐。把这 16 个 case id 写入
#      .evolve_scripts_{bench}/evolve_used_case_id.txt（eval 阶段自动据此跳过）。
#   3) evolve：把这 16 个 case 的 trajectory.json 拷到一个干净 staging 目录（避免污染
#      baseline），用 src/evolve/evolve_v2_chunk.py 演化脚本到 .evolve_scripts_{bench}/。
#   4) eval（装脚本）：EVOLVE_SCRIPTS_DIR=.evolve_scripts_{bench} 跑对应 benchmark 脚本，
#      自动跳过上述 16 个 evolve case，即在“其余 case”上测评开销与 accuracy。
#
# 关于对照组：每个 benchmark 的 baseline（步骤1）结果即“无脚本”对照组。
#   - deep-swe / qa / tw：baseline 排除了历史 16 个 evolve case，所以对照组本就在 non-16 上。
#   - 当某个被选中的 evolve case 也出现在 baseline 里时（例如 tw/rf 用排序回退选的 case），
#     分析时用 evolve_used_case_id.txt 把这 16 个从 baseline 结果里过滤掉即可，
#     保证 baseline 与 evolved 在同一 non-16 case 集上对比。
#
# 并发：所有 benchmark 顺序执行；单步内 N_CONCURRENT 默认 16（= 你的 API 并行上限）。
#       不要并行多个 benchmark，否则会超出 16 的总并行预算。
#
# 常用环境变量（均有默认值）：
#   BENCHMARKS           要跑的 benchmark 列表（空格分隔）
#   N_CONCURRENT         单 benchmark 内并发（默认 16）
#   N_TASKS              eval/baseline 跑的 case 数上限（默认 1000=全部）
#   EVOLVE_VARIANT       evolve 变体：v2_chunk | v1_chunk | baseline（默认 v2_chunk）
#   EVOLVE_WORKERS       evolve 标注阶段 LLM 并发（默认 8）
#   EVOLVE_BATCH_SIZE    evolve 每个 batch 的 sample 数（默认 4）
#   LLM_CONFIG           LLM 配置 yaml（默认 _config/deepseekv4_flash.yaml）
#   EVOLVE_CASE_COUNT    每 benchmark 选多少 case 做 evolve（默认 16）
#   SOURCE_CASE_LIST     复用的历史 16 清单（默认 .evolve_scripts/evolve_used_case_id.txt）
#   TRAINING_TRAJ_POOL   历史 evolve 训练 trajectory 池（默认 results/without_scripts_total_cases）
#   BASELINE_MIN_TRAJ    baseline 目录少于该数则判为不足、需重跑（默认 16）
#   RUN_BASELINE=1       强制对所有 benchmark 重跑 baseline（不复用）
#   BASELINE_REUSE_ONLY=1 永不重跑 baseline，缺失则报错
#   BASELINE_FORCE_RERUN_BENCHES  这些 benchmark 必须重跑 without-scripts baseline
#                        （默认 "swe-atlas-qa swe-atlas-tw swe-atlas-rf"；要连 deep-swe
#                        一起重跑，把它加进列表）。已 rerun 过会自动复用最新一份。
#   BASELINE_DIR_<KEY>   指定某 benchmark 的 baseline 目录（KEY 见下方映射，如 DEEP_SWE）
#   CLEAN_EVOLVE_SCRIPTS=1  evolve 前清空 .evolve_scripts_{bench}（否则 resume）
#   CONDA_ENV            需要激活的 conda 环境名（默认 0622；置空则不激活）
#   DRY_RUN=1            不执行 baseline/evolve/eval，只打印将运行的命令（rf 等 baseline
#                        未就绪的 benchmark 会优雅跳过）
#   SELECT_ONLY=1        只做 baseline（复用）+ 选16 + staging，跳过 evolve/eval。
#                        用于实跑前预览每个 benchmark 选了哪 16 个 case、staging 是否齐。
#
# 用法：
#   bash scripts/evolve_in_individual_bench.sh
#   BENCHMARKS="deep-swe" DRY_RUN=1 bash scripts/evolve_in_individual_bench.sh
#   BENCHMARKS="swe-atlas-rf" bash scripts/evolve_in_individual_bench.sh
# =============================================================================
set -euo pipefail

# ---------- 路径 & 公共变量 ----------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
export ROOT_DIR
# 必须在 source _bench_common.sh 之前先 export 并发/任务数默认值：_bench_common.sh 用
# N_CONCURRENT="${N_CONCURRENT:-8}" 读取，若此时已 export，会保留我们的值；否则会被
# 它设成 8，之后再用 ${N_CONCURRENT:-16} 已经失效（变量非空，:- 不触发）。
# N_TASKS / N_ATTEMPTS 两边默认值一致，提前 export 只为语义清晰。
export N_CONCURRENT="${N_CONCURRENT:-16}"
export N_TASKS="${N_TASKS:-1000}"
export N_ATTEMPTS="${N_ATTEMPTS:-1}"
# 复用 _bench_common.sh 里的 ROOT_DIR / RESULTS_DIR / UV_BIN / load_llm_config 等。
source "${SCRIPT_DIR}/_bench_common.sh"

# ---------- 配置（env 可覆盖） ----------
BENCHMARKS="${BENCHMARKS:-deep-swe swe-atlas-qa swe-atlas-tw swe-atlas-rf}"
EVOLVE_VARIANT="${EVOLVE_VARIANT:-v2_chunk}"
EVOLVE_WORKERS="${EVOLVE_WORKERS:-8}"
EVOLVE_BATCH_SIZE="${EVOLVE_BATCH_SIZE:-4}"
EVOLVE_CASE_COUNT="${EVOLVE_CASE_COUNT:-16}"
LLM_CONFIG="${LLM_CONFIG:-${ROOT_DIR}/_config/deepseekv4_flash.yaml}"
SOURCE_CASE_LIST="${SOURCE_CASE_LIST:-${ROOT_DIR}/.evolve_scripts/evolve_used_case_id.txt}"
TRAINING_TRAJ_POOL="${TRAINING_TRAJ_POOL:-${ROOT_DIR}/results/without_scripts_total_cases}"
STAGING_ROOT="${STAGING_ROOT:-${ROOT_DIR}/results/evolve16_staging}"
BASELINE_MIN_TRAJ="${BASELINE_MIN_TRAJ:-16}"
RUN_BASELINE="${RUN_BASELINE:-0}"
BASELINE_REUSE_ONLY="${BASELINE_REUSE_ONLY:-0}"
# 哪些 benchmark 必须重跑一份完整 without-scripts baseline（不复用历史部分结果）。
# 默认 swe-atlas 三个 sub-task：用户要求它们也跑 without-scripts 对照（既有 qa=106/tw=73/rf=3
# 是带 skip 的部分历史结果、且与本实验选的 16 个 evolve case 不一致，不可直接对照）。
# deep-swe 复用既有 baseline（用户未要求重跑）。
# 断点续跑友好：若 results/<sub>/baseline-<bench>-* 已有 ≥ BASELINE_MIN_TRAJ 条 trajectory，
# 会自动复用最新一份，避免中断后重复重跑。要连 deep-swe 一起重跑，把它加进这个列表即可。
BASELINE_FORCE_RERUN_BENCHES="${BASELINE_FORCE_RERUN_BENCHES:-swe-atlas-qa swe-atlas-tw swe-atlas-rf}"
CLEAN_EVOLVE_SCRIPTS="${CLEAN_EVOLVE_SCRIPTS:-0}"
# 仅在未设置时默认 0622；置空串（CONDA_ENV=""）则跳过激活。
CONDA_ENV="${CONDA_ENV-0622}"
DRY_RUN="${DRY_RUN:-0}"

# 这些会传给子脚本（run_deep_swe.sh / run_swe_atlas.sh 重新 source _bench_common.sh，
# 用 ${VAR:-default} 读取，故 export 后子脚本会沿用）。
# N_CONCURRENT / N_TASKS / N_ATTEMPTS 已在 source _bench_common.sh 之前提前 export
# （见文件开头），避免被 _bench_common 的默认值 8 覆盖。
export LLM_CONFIG
export RESULTS_DIR

TS="$(date +%m%d-%H%M%S)"

# ---------- 日志 ----------
log()  { printf '\n\033[1;34m[evolve-bench]\033[0m %s\n' "$*"; }
warn() { printf '\n\033[1;33m[evolve-bench] WARN:\033[0m %s\n' "$*" >&2; }
err()  { printf '\n\033[1;31m[evolve-bench] ERROR:\033[0m %s\n' "$*" >&2; }
die()  { err "$*"; exit 1; }

# ---------- conda 激活（可选） ----------
maybe_activate_conda() {
  [[ -z "${CONDA_ENV}" ]] && return 0
  if ! command -v conda >/dev/null 2>&1; then
    warn "未找到 conda，跳过激活（请确保当前环境已有 uv / python 依赖）。"
    return 0
  fi
  # shellcheck disable=SC1091
  source "$(conda info --base)/etc/profile.d/conda.sh"
  if conda activate "${CONDA_ENV}" 2>/dev/null; then
    log "已激活 conda 环境：${CONDA_ENV}"
  else
    warn "conda activate ${CONDA_ENV} 失败，沿用当前环境。"
  fi
}

# ---------- benchmark 元信息 ----------
# key -> "results 子目录"（baseline 输出落点）。eval/baseline 的 -o = $RESULTS_DIR/<这个>
bench_results_subdir() {
  case "$1" in
    deep-swe)      echo "deep-swe" ;;
    swe-atlas-qa)  echo "swe-atlas-qa" ;;
    swe-atlas-tw)  echo "swe-atlas-tw" ;;
    swe-atlas-rf)  echo "swe-atlas-rf" ;;
    *) die "未知 benchmark: $1" ;;
  esac
}
# 跑 eval / baseline 用哪个 scripts/run_*.sh
bench_run_script() {
  case "$1" in
    deep-swe)      echo "${SCRIPT_DIR}/run_deep_swe.sh" ;;
    swe-atlas-*)   echo "${SCRIPT_DIR}/run_swe_atlas.sh" ;;
    *) die "未知 benchmark: $1" ;;
  esac
}
# swe-atlas 的 split 名（deep-swe 为空）
bench_split() {
  case "$1" in
    swe-atlas-qa) echo "qa" ;;
    swe-atlas-tw) echo "tw" ;;
    swe-atlas-rf) echo "rf" ;;
    *) echo "" ;;
  esac
}
# benchmark 的任务目录，用于判断 case id 属于哪个 benchmark
bench_tasks_dir() {
  case "$1" in
    deep-swe)     echo "${ROOT_DIR}/benchmark/deep-swe/tasks" ;;
    swe-atlas-qa) echo "${ROOT_DIR}/benchmark/SWE-Atlas/data/qa" ;;
    swe-atlas-tw) echo "${ROOT_DIR}/benchmark/SWE-Atlas/data/tw" ;;
    swe-atlas-rf) echo "${ROOT_DIR}/benchmark/SWE-Atlas/data/rf" ;;
    *) die "未知 benchmark: $1" ;;
  esac
}
# 默认复用的 baseline 目录
bench_default_baseline_dir() {
  case "$1" in
    deep-swe)     echo "${ROOT_DIR}/results/deep-swe/deepseek-flash-without-evolve-tools" ;;
    swe-atlas-qa) echo "${ROOT_DIR}/results/swe-atlas-qa/baseline-sweatlas-0625-171649" ;;
    swe-atlas-tw) echo "${ROOT_DIR}/results/swe-atlas-tw/baseline-sweatlas-0625-171649" ;;
    swe-atlas-rf) echo "${ROOT_DIR}/results/swe-atlas-rf/baseline-sweatlas-0625-171649" ;;
    *) die "未知 benchmark: $1" ;;
  esac
}
# evolve 输出脚本目录：.evolve_scripts_{bench}
bench_evolve_scripts_dir() {
  echo "${ROOT_DIR}/.evolve_scripts_${1}"
}
# benchmark key -> 环境变量后缀（用于 BASELINE_DIR_<KEY>）
bench_env_key() {
  echo "$1" | tr '[:lower:]-' '[:upper:]_'
}

# 统计某目录下 trajectory.json 数量
count_trajectories() {
  [[ -d "$1" ]] || { echo 0; return 0; }
  find "$1" -type f -name trajectory.json 2>/dev/null | wc -l
}

# benchmark 是否需要重跑 without-scripts baseline（swe-atlas 各 sub-task 默认需要）
is_force_rerun_bench() {
  local b="$1"
  [[ " ${BASELINE_FORCE_RERUN_BENCHES} " == *" ${b} "* ]]
}

# 找本 benchmark 最近一次 rerun 出来的 baseline 目录（baseline-<bench>-<TS>）。
# 用于断点续跑：已跑过就复用最新一份，不重跑。只匹配我们自己的 rerun 命名
# (baseline-<bench>-<TS>)，不会误复用历史的 baseline-sweatlas-* 部分结果。
latest_rerun_baseline_dir() {
  local bench="$1" sub="$2"
  [[ -d "${RESULTS_DIR}/${sub}" ]] || return 0
  # 用 printf 过滤空行；sort 保证按名字（含时间戳）升序，tail -1 取最新
  find "${RESULTS_DIR}/${sub}" -maxdepth 1 -type d -name "baseline-${bench}-*" 2>/dev/null \
    | sort | tail -1
}

# ---------- 步骤 1：解析 baseline 目录（复用 or 重跑） ----------
# 产出：把最终 baseline 目录写到全局 $BASELINE_DIR，把其 RUN_ID（若有）写到 $BASELINE_RUN_ID
#
# 解析优先级：
#   1) BASELINE_DIR_<KEY> 显式指定 → 直接用。
#   2) BASELINE_REUSE_ONLY=1 → 只复用默认 baseline 目录（永不重跑；缺失则报错）。
#   3) 复用已有合适 baseline：
#      - force-rerun benchmark（默认 swe-atlas qa/tw/rf）：只复用我们自己 rerun 出来的
#        baseline-<bench>-<TS> 目录（断点续跑），不复用历史的 baseline-sweatlas-* 部分结果。
#      - 其他 benchmark（deep-swe）：复用默认 baseline 目录（trajectory 够即可）。
#   4) （重）跑一份完整 without-scripts baseline（无脚本、不跳过任何 case）。
#      既作 evolve 的 trajectory 来源，也作"无脚本"对照组——分析时用
#      evolve_used_case_id.txt 把 16 个 evolve case 过滤掉，即可与 evolved 评估
#      在同一 non-16 case 集上对比。
resolve_baseline_dir() {
  local bench="$1" sub split default_dir n env_key explicit run_id out reuse_dir
  sub="$(bench_results_subdir "$bench")"
  split="$(bench_split "$bench")"
  default_dir="$(bench_default_baseline_dir "$bench")"
  env_key="$(bench_env_key "$bench")"
  local explicit_var="BASELINE_DIR_${env_key}"
  explicit="${!explicit_var:-}"

  BASELINE_DIR=""
  BASELINE_RUN_ID=""

  # 1) 显式指定
  if [[ -n "${explicit}" ]]; then
    [[ -d "${explicit}" ]] || die "BASELINE_DIR_${env_key}='${explicit}' 不是目录"
    BASELINE_DIR="${explicit}"
    log "[$bench] baseline：使用显式指定 ${BASELINE_DIR}（trajectory=$(count_trajectories "${BASELINE_DIR}")）"
    return 0
  fi

  # 2) 仅复用（永不重跑）
  if [[ "${BASELINE_REUSE_ONLY}" == "1" ]]; then
    [[ -d "${default_dir}" ]] || die "[$bench] BASELINE_REUSE_ONLY=1 但默认 baseline 不存在：${default_dir}"
    BASELINE_DIR="${default_dir}"
    log "[$bench] baseline：仅复用 ${BASELINE_DIR}（trajectory=$(count_trajectories "${BASELINE_DIR}")）"
    return 0
  fi

  # 3) 复用已有合适 baseline
  if is_force_rerun_bench "$bench"; then
    # swe-atlas：只复用我们自己 rerun 出来的目录（断点续跑），不复用历史部分结果
    reuse_dir="$(latest_rerun_baseline_dir "$bench" "$sub")"
    if [[ -n "${reuse_dir}" ]] && [[ "$(count_trajectories "${reuse_dir}")" -ge "${BASELINE_MIN_TRAJ}" ]]; then
      BASELINE_DIR="${reuse_dir}"
      log "[$bench] baseline：复用已 rerun 的 ${BASELINE_DIR}（trajectory=$(count_trajectories "${BASELINE_DIR}") ≥ ${BASELINE_MIN_TRAJ}）"
      return 0
    fi
  else
    n="$(count_trajectories "${default_dir}")"
    if [[ "${RUN_BASELINE}" != "1" ]] && [[ "${n}" -ge "${BASELINE_MIN_TRAJ}" ]]; then
      BASELINE_DIR="${default_dir}"
      log "[$bench] baseline：复用 ${BASELINE_DIR}（trajectory=${n} ≥ ${BASELINE_MIN_TRAJ}）"
      return 0
    fi
  fi

  # 4) （重）跑一份完整 without-scripts baseline（无脚本、不跳过任何 case）
  run_id="baseline-${bench}-${TS}"
  BASELINE_RUN_ID="${run_id}"
  log "[$bench] baseline：（重）跑 without-scripts baseline（RUN_ID=${run_id}）"
  if [[ "${DRY_RUN}" == "1" ]]; then
    warn "[DRY_RUN] 跳过 baseline 运行；假设输出目录为 ${RESULTS_DIR}/${sub}/${run_id}"
    BASELINE_DIR="${RESULTS_DIR}/${sub}/${run_id}"
    return 0
  fi
  EVOLVE_SCRIPTS_DIR="" EVOLVE_SKIP_FILE="" \
  RUN_ID="${run_id}" \
  SWE_ATLAS_SPLITS="${split}" \
  bash "$(bench_run_script "$bench")" \
    || warn "[$bench] baseline 运行退出非零（部分 case 可能失败），继续后续步骤。"
  # 运行结果目录：results/<sub>/<RUN_ID>/
  out="${RESULTS_DIR}/${sub}/${run_id}"
  if [[ ! -d "${out}" ]]; then
    # 兜底：在 results/<sub>/ 下找匹配 RUN_ID 的目录
    out="$(find "${RESULTS_DIR}/${sub}" -maxdepth 1 -type d -name "${run_id}" 2>/dev/null | head -1 || true)"
  fi
  [[ -d "${out}" ]] || die "[$bench] baseline 跑完后找不到输出目录：${RESULTS_DIR}/${sub}/${run_id}"
  BASELINE_DIR="${out}"
  log "[$bench] baseline 完成：${BASELINE_DIR}（trajectory=$(count_trajectories "${BASELINE_DIR}")）"
}

# ---------- 步骤 2：选 16 个 case + staging ----------
# 用一个内联 python 做：过滤历史清单到本 bench、找 trajectory、补齐、写 evolve_used_case_id.txt、拷 trajectory.json 到 staging。
select_and_stage_16() {
  local bench="$1" scripts_dir="$2" staging_dir="$3" tasks_dir="$4"
  mkdir -p "${scripts_dir}" "${staging_dir}"
  # 清空 staging（每次重选）
  rm -rf "${staging_dir:?}"/* 2>/dev/null || true

  BENCH="${bench}" \
  SOURCE_CASE_LIST="${SOURCE_CASE_LIST}" \
  TRAINING_TRAJ_POOL="${TRAINING_TRAJ_POOL}" \
  BASELINE_DIR="${BASELINE_DIR}" \
  EVOLVE_SCRIPTS_DIR="${scripts_dir}" \
  STAGING_DIR="${staging_dir}" \
  TASKS_DIR="${tasks_dir}" \
  COUNT="${EVOLVE_CASE_COUNT}" \
  python - <<'PY'
import json, os, shutil, sys
from pathlib import Path

bench        = os.environ["BENCH"]
src_list     = Path(os.environ["SOURCE_CASE_LIST"])
pool_dir     = Path(os.environ["TRAINING_TRAJ_POOL"]) if os.environ["TRAINING_TRAJ_POOL"] else None
baseline_dir = Path(os.environ["BASELINE_DIR"])
scripts_dir  = Path(os.environ["EVOLVE_SCRIPTS_DIR"])
staging_dir  = Path(os.environ["STAGING_DIR"])
tasks_dir    = Path(os.environ["TASKS_DIR"])
count        = int(os.environ["COUNT"])

def is_bench_case(cid):
    return (tasks_dir / cid).is_dir()

# 找某 case 的 without-scripts trajectory：先查历史训练池，再查 baseline 目录
def find_traj(cid, dirs):
    for d in dirs:
        if not d or not d.exists():
            continue
        for pat in (f"**/{cid}/agent/trajectory.json", f"**/{cid}__*/agent/trajectory.json"):
            hits = sorted(d.glob(pat))
            if hits:
                return hits[0]
    return None

def case_id_of(traj_path):
    # trajectory.json 的上级目录名去掉 __<suffix>
    return traj_path.parent.parent.name.split("__", 1)[0]

# 1) 历史清单里属于本 bench 的 16 个
historical = []
if src_list.exists():
    for line in src_list.read_text().splitlines():
        cid = line.strip()
        if cid and not cid.startswith("#") and is_bench_case(cid):
            historical.append(cid)
historical = list(dict.fromkeys(historical))  # 去重保序

# baseline 里所有可用 case id（排序）
avail_in_baseline = []
if baseline_dir.exists():
    seen = set()
    for t in sorted(baseline_dir.glob("**/agent/trajectory.json")):
        cid = case_id_of(t)
        if cid not in seen:
            seen.add(cid); avail_in_baseline.append(cid)

# 2) 选中：优先历史 16 里能找到 trajectory 的
search_dirs = [pool_dir, baseline_dir]
selected = []  # list of (cid, traj_path)
selected_cids = set()
missing = []
for cid in historical:
    p = find_traj(cid, search_dirs)
    if p:
        selected.append((cid, p)); selected_cids.add(cid)
    else:
        missing.append(cid)
# 3) 不足 count：用 baseline 里排序靠前、未选中的补齐
if len(selected) < count:
    for cid in avail_in_baseline:
        if len(selected) >= count:
            break
        if cid in selected_cids:
            continue
        p = find_traj(cid, [baseline_dir])
        if p:
            selected.append((cid, p)); selected_cids.add(cid)

if len(selected) < count:
    print(f"[select] ERROR: {bench} 仅选出 {len(selected)} 个有 trajectory 的 case，不足 {count}。"
          f" baseline={baseline_dir} traj={len(avail_in_baseline)}", file=sys.stderr)
    sys.exit(2)

# 4) 写 evolve_used_case_id.txt（eval 跳过列表，每行一个 case id）
skip_file = scripts_dir / "evolve_used_case_id.txt"
skip_file.write_text("\n".join(cid for cid, _ in selected) + "\n", encoding="utf-8")

# 5) staging：把每个 trajectory.json 拷到 staging/<case_dir>/agent/trajectory.json
staged = 0
for cid, p in selected:
    case_dir = p.parent.parent.name  # 保留原 __<suffix> 命名
    dst_dir = staging_dir / case_dir / "agent"
    dst_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(p, dst_dir / "trajectory.json")
    staged += 1

# 汇总打印
hist_set = set(historical)
hist_hit = sum(1 for cid, _ in selected if cid in hist_set)
fallback = len(selected) - hist_hit
print(f"[select] {bench}: 选 {len(selected)} 个 case "
      f"（历史清单命中 {hist_hit}，回退 {fallback}）"
      f" -> {skip_file}")
print(f"[select] staging: {staged} 个 trajectory.json -> {staging_dir}")
if missing:
    print(f"[select] 历史清单里无 trajectory、已回退: {missing}")
PY
}

# ---------- 步骤 3：evolve ----------
run_evolve() {
  local bench="$1" staging_dir="$2" scripts_dir="$3" module out_dir
  case "${EVOLVE_VARIANT}" in
    v2_chunk) module="src.evolve.evolve_v2_chunk" ;;  # 有 run 子命令；不接受 --chunk-size
    v1_chunk) module="src.evolve.evolve_v1_chunk" ;;  # 有 run 子命令
    baseline) module="src.evolve.evolve_baseline" ;;  # 无子命令，直接传参
    *) die "未知 EVOLVE_VARIANT: ${EVOLVE_VARIANT}" ;;
  esac
  out_dir="${staging_dir}/evolve_logs"
  mkdir -p "${out_dir}"

  # 注意：scripts_dir 的清空在 select_and_stage_16 之前做（见 main 循环），
  # 不能在这里清——否则会删掉 select 刚写入的 evolve_used_case_id.txt，
  # 导致 eval 阶段找不到 skip 列表而不跳过 16 个 case。
  local cmd=()
  if [[ "${EVOLVE_VARIANT}" == "baseline" ]]; then
    # evolve_baseline.py 没有 annotate 段、CLI 不接受 --workers（只 _add_common/_add_config/_add_evolve）。
    cmd=(python -m "${module}"
         "${staging_dir}"
         --config "${LLM_CONFIG}"
         --scripts-dir "${scripts_dir}"
         --mini-swe-agent-dir "${ROOT_DIR}/agent/mini-swe-agent"
         --batch-size "${EVOLVE_BATCH_SIZE}"
         --output-dir "${out_dir}")
  else
    cmd=(python -m "${module}" run
         "${staging_dir}"
         --config "${LLM_CONFIG}"
         --scripts-dir "${scripts_dir}"
         --mini-swe-agent-dir "${ROOT_DIR}/agent/mini-swe-agent"
         --workers "${EVOLVE_WORKERS}"
         --batch-size "${EVOLVE_BATCH_SIZE}"
         --output-dir "${out_dir}")
  fi

  log "[$bench] evolve：${EVOLVE_VARIANT} on ${staging_dir} -> ${scripts_dir}"
  if [[ "${DRY_RUN}" == "1" ]]; then
    warn "[DRY_RUN] 跳过 evolve：$(printf '%q ' "${cmd[@]}")"
    return 0
  fi
  ( cd "${ROOT_DIR}" && "${cmd[@]}" )
}

# ---------- 步骤 4：装脚本 eval（自动跳过 16 个 evolve case） ----------
run_eval_with_scripts() {
  local bench="$1" scripts_dir="$2" split run_id
  split="$(bench_split "$bench")"
  run_id="evolve-${EVOLVE_VARIANT}-${bench}-${TS}"

  # EVOLVE_SCRIPTS_DIR 非空 + 目录下有 evolve_used_case_id.txt ->
  # _bench_common.sh 的 evolve_skip_exclude_args 会自动把 16 个 case 作为 -x 跳过。
  # 显式 EVOLVE_SKIP_FILE=auto 强制走自动解析。
  log "[$bench] eval（装脚本）：RUN_ID=${run_id}，跳过 evolve_used_case_id.txt 里的 16 个 case"
  if [[ "${DRY_RUN}" == "1" ]]; then
    warn "[DRY_RUN] 跳过 eval：EVOLVE_SCRIPTS_DIR=${scripts_dir} SWE_ATLAS_SPLITS=${split} RUN_ID=${run_id} bash $(bench_run_script "$bench")"
    return 0
  fi
  EVOLVE_SCRIPTS_DIR="${scripts_dir}" \
  EVOLVE_SKIP_FILE="auto" \
  RUN_ID="${run_id}" \
  SWE_ATLAS_SPLITS="${split}" \
  bash "$(bench_run_script "$bench")" \
    || warn "[$bench] eval 运行退出非零（部分 case 可能失败），结果仍保留。"
}

# ---------- 主流程 ----------
main() {
  maybe_activate_conda
  load_llm_config  # 解析 LLM 配置，供 evolve / eval 使用

  local bench scripts_dir staging_dir tasks_dir
  for bench in ${BENCHMARKS}; do
    log "================ ${bench} ================"
    scripts_dir="$(bench_evolve_scripts_dir "${bench}")"
    staging_dir="${STAGING_ROOT}/${bench}"
    tasks_dir="$(bench_tasks_dir "${bench}")"

    # 1) baseline（复用或重跑）
    resolve_baseline_dir "${bench}"
    # baseline 必须有足够的 trajectory 才能选出 16 个。
    local _n
    _n="$(count_trajectories "${BASELINE_DIR:-/nonexistent}")"
    if [[ "${_n}" -lt "${EVOLVE_CASE_COUNT}" ]]; then
      if [[ "${DRY_RUN}" == "1" ]]; then
        warn "[$bench] DRY_RUN：baseline 未就绪（${BASELINE_DIR:-<空>}，trajectory=${_n} < ${EVOLVE_CASE_COUNT}），跳过 select/evolve/eval。实际运行时会先跑 baseline。"
        log "================ ${bench} 完成（DRY_RUN，baseline 未就绪） ================"
        continue
      fi
      die "[$bench] baseline trajectory 不足 ${EVOLVE_CASE_COUNT}（实际 ${_n}），无法选 case。baseline：${BASELINE_DIR:-<空>}"
    fi

    # 2) 选 16 + staging
    #    若 CLEAN_EVOLVE_SCRIPTS=1：先清空 evolve 脚本目录（必须在 select 之前，
    #    否则会删掉 select 即将写入的 evolve_used_case_id.txt，破坏 eval 跳过）。
    #    select 会重新 mkdir -p scripts_dir 并写入 evolve_used_case_id.txt。
    if [[ "${CLEAN_EVOLVE_SCRIPTS}" == "1" ]] && [[ "${DRY_RUN}" != "1" ]]; then
      log "[$bench] 清空 evolve 脚本目录 ${scripts_dir}"
      rm -rf "${scripts_dir:?}"
    fi
    select_and_stage_16 "${bench}" "${scripts_dir}" "${staging_dir}" "${tasks_dir}"

    # SELECT_ONLY=1：只做 baseline+选16+staging，跳过耗时的 evolve/eval（用于预览选了哪些 case）。
    if [[ "${SELECT_ONLY:-0}" == "1" ]]; then
      log "[$bench] SELECT_ONLY=1：跳过 evolve/eval。evolve_used_case_id.txt 已写到 ${scripts_dir}/"
      log "================ ${bench} 完成（SELECT_ONLY） ================"
      continue
    fi

    # 3) evolve
    run_evolve "${bench}" "${staging_dir}" "${scripts_dir}"

    # 4) 装脚本 eval（跳过 16）
    run_eval_with_scripts "${bench}" "${scripts_dir}"

    log "================ ${bench} 完成 ================"
  done

  log "全部完成。各 benchmark 结果："
  log "  evolve 脚本：.evolve_scripts_<bench>/   (含 evolve_used_case_id.txt / instruction.md / 工具脚本)"
  log "  eval 结果：results/<bench>/evolve-${EVOLVE_VARIANT}-<bench>-${TS}/"
  log "  对照 baseline：results/<bench>/ 下的 baseline 目录"
}

main "$@"
