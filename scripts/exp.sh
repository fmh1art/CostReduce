#!/usr/bin/env bash
# 在 4 个 tmux 窗口里并行起 4 个实验：
#   1) SWE-Atlas + evolve_scripts 工具
#   2) SWE-Atlas (无工具，仅 skip case ids)
#   3) DataMind/LongDS + evolve_scripts 工具
#   4) DataMind/LongDS (无工具，仅 skip case ids)
#
# 全部 4 个实验都使用 .evolve_scripts/evolve_used_case_id.txt 作为 skip list；
# 并发度 N_CONCURRENT=2；conda env 名为 0622；工作目录为 ROOT_DIR。

set -euo pipefail

ROOT_DIR="/home/fanmeihao/projects/CostReduce"
SCRIPTS_DIR="${ROOT_DIR}/.evolve_scripts"
SKIP_FILE="${SCRIPTS_DIR}/evolve_used_case_id.txt"
SESSION="${SESSION:-costreduce_exp}"
CONDA_ENV="${CONDA_ENV:-0622}"
N_CONCURRENT="${N_CONCURRENT:-2}"
TS="$(date +%m%d-%H%M%S)"

if ! command -v tmux >/dev/null 2>&1; then
  echo "tmux is required but not installed" >&2
  exit 1
fi
if [[ ! -f "${SKIP_FILE}" ]]; then
  echo "Skip file not found: ${SKIP_FILE}" >&2
  exit 1
fi
if [[ ! -d "${SCRIPTS_DIR}" ]]; then
  echo "Scripts dir not found: ${SCRIPTS_DIR}" >&2
  exit 1
fi

# 生成在 tmux 窗口里执行的命令：cd → conda activate → export → 跑脚本 → 保留 shell。
# 第三个参数为空字符串时表示"不挂载/不安装 .evolve_scripts"；非空时按目录挂载。
build_cmd() {
  local label="$1"
  local script="$2"
  local scripts_dir_value="$3"
  cat <<EOF
cd "${ROOT_DIR}" && \
source "\$(conda info --base)/etc/profile.d/conda.sh" && \
conda activate "${CONDA_ENV}" && \
export N_CONCURRENT="${N_CONCURRENT}" \
       RUN_ID="${label}-${TS}" \
       EVOLVE_SKIP_FILE="${SKIP_FILE}" \
       EVOLVE_SCRIPTS_DIR="${scripts_dir_value}" && \
bash "${script}"; \
status=\$?; \
echo "[${label}] exited with status \$status"; \
exec bash
EOF
}

# 如果同名 session 已存在则先杀掉，确保四个窗口都是新起。
if tmux has-session -t "${SESSION}" 2>/dev/null; then
  tmux kill-session -t "${SESSION}"
fi

tmux new-session -d -s "${SESSION}" -n swe_atlas_with    -c "${ROOT_DIR}"
tmux send-keys   -t "${SESSION}:swe_atlas_with" \
  "$(build_cmd swe_atlas_with    scripts/run_swe_atlas.sh "${SCRIPTS_DIR}")" C-m

tmux new-window  -t "${SESSION}" -n swe_atlas_without -c "${ROOT_DIR}"
tmux send-keys   -t "${SESSION}:swe_atlas_without" \
  "$(build_cmd swe_atlas_without scripts/run_swe_atlas.sh "")" C-m

tmux new-window  -t "${SESSION}" -n datamind_with    -c "${ROOT_DIR}"
tmux send-keys   -t "${SESSION}:datamind_with" \
  "$(build_cmd datamind_with    scripts/run_datamind.sh "${SCRIPTS_DIR}")" C-m

tmux new-window  -t "${SESSION}" -n datamind_without -c "${ROOT_DIR}"
tmux send-keys   -t "${SESSION}:datamind_without" \
  "$(build_cmd datamind_without scripts/run_datamind.sh "")" C-m

echo "Started tmux session '${SESSION}' with 4 windows:"
tmux list-windows -t "${SESSION}" -F "  #{window_index}: #{window_name}"
echo
echo "Attach:        tmux attach -t ${SESSION}"
echo "Switch window: Ctrl-b 0/1/2/3 (or Ctrl-b w)"
echo "Detach:        Ctrl-b d"
echo "Kill all:      tmux kill-session -t ${SESSION}"
