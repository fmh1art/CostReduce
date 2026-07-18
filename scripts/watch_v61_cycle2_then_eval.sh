#!/usr/bin/env bash
# Supervise an already-running v6.1 experiment, stop only after the requested
# cycle is durably complete, then resume the same work directory with that
# cycle count so run_exp.sh naturally proceeds to final eval.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_EXP="${RUN_EXP:-/home/fanmeihao/projects/OptiHarnessForCost/scripts/run_exp.sh}"
WORK_DIR="${WORK_DIR:-}"
SCRIPTS_DIR="${SCRIPTS_DIR:-}"
ORIGINAL_PID="${ORIGINAL_PID:-}"
TARGET_CYCLES="${TARGET_CYCLES:-2}"
POLL_SECONDS="${POLL_SECONDS:-5}"
HEARTBEAT_SECONDS="${HEARTBEAT_SECONDS:-600}"
MAX_RESUME_ATTEMPTS="${MAX_RESUME_ATTEMPTS:-3}"
BENCHMARK="${BENCHMARK:-swebench}"

usage() {
  printf 'Usage: WORK_DIR=<existing-v6.1-work-dir> [ORIGINAL_PID=<pid>] %s\n' "$0" >&2
  printf 'Optional: TARGET_CYCLES=2 POLL_SECONDS=5 HEARTBEAT_SECONDS=600\n' >&2
}

log() {
  printf '[%s] [v61-watchdog] %s\n' "$(date -Is)" "$*"
}

die() {
  log "ERROR: $*" >&2
  exit 1
}

is_uint() {
  [[ "$1" =~ ^[0-9]+$ ]]
}

[[ -n "$WORK_DIR" ]] || { usage; exit 2; }
WORK_DIR="${WORK_DIR%/}"
[[ "$WORK_DIR" = /* ]] || die "WORK_DIR must be absolute: $WORK_DIR"
[[ -d "$WORK_DIR" ]] || die "WORK_DIR does not exist: $WORK_DIR"
[[ -f "$WORK_DIR/v6_1_run_manifest.json" ]] \
  || die "not a v6.1 work directory: $WORK_DIR"
[[ "$WORK_DIR" == */results/evolve/v61cycle/* ]] \
  || die "refusing unexpected work-directory layout: $WORK_DIR"
is_uint "$TARGET_CYCLES" && (( TARGET_CYCLES >= 1 )) \
  || die "TARGET_CYCLES must be a positive integer"
is_uint "$POLL_SECONDS" && (( POLL_SECONDS >= 1 )) \
  || die "POLL_SECONDS must be a positive integer"
is_uint "$HEARTBEAT_SECONDS" && (( HEARTBEAT_SECONDS >= 1 )) \
  || die "HEARTBEAT_SECONDS must be a positive integer"
is_uint "$MAX_RESUME_ATTEMPTS" && (( MAX_RESUME_ATTEMPTS >= 1 )) \
  || die "MAX_RESUME_ATTEMPTS must be a positive integer"

SCRIPTS_DIR="${SCRIPTS_DIR:-$WORK_DIR/scripts}"
CYCLE_DIR="$WORK_DIR/cycle-$TARGET_CYCLES"
STATE_FILE="$CYCLE_DIR/cycle_state.json"
SNAPSHOT_DIR="$CYCLE_DIR/harness_after"
REPORT_FILE="$WORK_DIR/v6_1_report.json"
WATCHDOG_LOG="$WORK_DIR/cycle${TARGET_CYCLES}_to_eval_watchdog.log"
mkdir -p "$WORK_DIR"
exec > >(tee -a "$WATCHDOG_LOG") 2>&1

target_complete() {
  [[ -f "$STATE_FILE" && -d "$SNAPSHOT_DIR" && -f "$CYCLE_DIR/harness_snapshot.json" \
      && -f "$REPORT_FILE" ]] || return 1
  jq -e '
    .stages.rollout == true and
    .stages.annotate == true and
    .stages.contrastive == true and
    .stages.evolve == true
  ' "$STATE_FILE" >/dev/null 2>&1 || return 1
  jq -e --argjson cycle "$TARGET_CYCLES" '
    any(.cycles[]?;
      .cycle == $cycle and
      .annotated == true and
      .contrastive_built == true and
      .evolved == true)
  ' "$REPORT_FILE" >/dev/null 2>&1
}

progress_line() {
  local stages trajectory_count sample_count done_count prompt_count
  stages="$(jq -c '.stages // {}' "$STATE_FILE" 2>/dev/null || printf '{}')"
  trajectory_count="$(jq -r '.trajectory_files // 0' "$STATE_FILE" 2>/dev/null || printf '0')"
  sample_count="$(jq -r '.contrastive_samples // 0' "$STATE_FILE" 2>/dev/null || printf '0')"
  done_count=0
  prompt_count=0
  if [[ -d "$CYCLE_DIR/evolve_logs" ]]; then
    done_count="$(find "$CYCLE_DIR/evolve_logs" -maxdepth 1 -type f \
      -name 'evolve_batch_*.traj.done' | wc -l)"
    prompt_count="$(find "$CYCLE_DIR/evolve_logs" -maxdepth 1 -type f \
      -name 'evolve_batch_*.traj.prompt.md' | wc -l)"
  fi
  log "cycle=$TARGET_CYCLES stages=$stages trajectories=$trajectory_count contrastive=$sample_count evolve_batches=$done_count/$prompt_count"
}

pid_is_expected_orchestrator() {
  local pid="$1" cmd work_tag
  is_uint "$pid" || return 1
  [[ -r "/proc/$pid/cmdline" ]] || return 1
  cmd="$(tr '\0' ' ' < "/proc/$pid/cmdline")"
  work_tag="$(basename "$WORK_DIR")"
  [[ "$cmd" == *"src.evolve.evolve_v6_1_cycle"* \
      && "$cmd" == *"--work-dir"* \
      && "$cmd" == *"$work_tag"* ]]
}

discover_original_pid() {
  local pid
  while read -r pid; do
    if pid_is_expected_orchestrator "$pid"; then
      printf '%s\n' "$pid"
      return 0
    fi
  done < <(pgrep -f 'python(3)? -m src\.evolve\.evolve_v6_1_cycle run' || true)
  return 1
}

collect_process_tree() {
  local root="$1" child
  while read -r child; do
    [[ -n "$child" ]] || continue
    collect_process_tree "$child"
  done < <(pgrep -P "$root" 2>/dev/null || true)
  printf '%s\n' "$root"
}

stop_original_after_target() {
  local root="$1" pid
  local -a tree descendants
  pid_is_expected_orchestrator "$root" \
    || die "refusing to signal unexpected or missing PID: $root"

  # Freeze the orchestrator first: after the durable cycle-N markers exist it
  # must not advance far enough for cycle N+1 to mutate the live harness.
  kill -STOP "$root"
  mapfile -t tree < <(collect_process_tree "$root")
  descendants=()
  for pid in "${tree[@]}"; do
    [[ "$pid" == "$root" ]] || descendants+=("$pid")
  done
  if (( ${#descendants[@]} > 0 )); then
    kill -TERM "${descendants[@]}" 2>/dev/null || true
  fi
  for _ in {1..15}; do
    local any_alive=0
    for pid in "${descendants[@]}"; do
      if kill -0 "$pid" 2>/dev/null; then
        any_alive=1
        break
      fi
    done
    (( any_alive == 0 )) && break
    sleep 1
  done
  for pid in "${descendants[@]}"; do
    kill -KILL "$pid" 2>/dev/null || true
  done
  kill -CONT "$root" 2>/dev/null || true
  kill -TERM "$root" 2>/dev/null || true
  for _ in {1..30}; do
    kill -0 "$root" 2>/dev/null || break
    sleep 1
  done
  kill -KILL "$root" 2>/dev/null || true
  log "stopped original 4-cycle orchestrator pid=$root after cycle $TARGET_CYCLES was durable"
}

remove_future_cycle_artifacts() {
  local path name cycle
  for path in "$WORK_DIR"/cycle-*; do
    [[ -e "$path" ]] || continue
    name="${path##*/cycle-}"
    is_uint "$name" || continue
    cycle="$name"
    if (( cycle > TARGET_CYCLES )); then
      rm -rf -- "$path"
      log "removed partial future-cycle artifact: $path"
    fi
  done
  for path in "$WORK_DIR/taskdirs"/v61c*-"$BENCHMARK"-* \
              "$WORK_DIR/.rollout_staging"/*/v61c*-"$BENCHMARK"-*; do
    [[ -e "$path" ]] || continue
    name="$(basename "$path")"
    cycle="${name#v61c}"
    cycle="${cycle%%-*}"
    if is_uint "$cycle" && (( cycle > TARGET_CYCLES )); then
      rm -rf -- "$path"
      log "removed partial future-cycle staging: $path"
    fi
  done
}

restore_target_snapshot() {
  [[ -d "$SNAPSHOT_DIR" ]] || die "target harness snapshot missing: $SNAPSHOT_DIR"
  mkdir -p "$SCRIPTS_DIR"
  rsync -a --delete "$SNAPSHOT_DIR/" "$SCRIPTS_DIR/"
  diff -qr "$SNAPSHOT_DIR" "$SCRIPTS_DIR" >/dev/null \
    || die "failed to restore the exact cycle-$TARGET_CYCLES harness snapshot"
  log "restored exact cycle-$TARGET_CYCLES harness snapshot -> $SCRIPTS_DIR"
}

resume_and_eval() {
  local baseline attempt child rc now next_heartbeat
  baseline="$(jq -r '.baseline_run // empty' \
    "$WORK_DIR/final_eval_case_selection.json" 2>/dev/null || true)"
  [[ -n "$baseline" && -d "$baseline" ]] \
    || die "cannot recover locked no-evolve baseline from final_eval_case_selection.json"
  [[ -x "$RUN_EXP" || -f "$RUN_EXP" ]] || die "run_exp.sh missing: $RUN_EXP"

  # Match the original experiment settings, changing only the cycle count and
  # pinning WORK_DIR/SCRIPTS_DIR so completed work is resumed in place.
  source /home/fanmeihao/anaconda3/etc/profile.d/conda.sh
  conda activate 0622
  log "continuation environment: conda=$CONDA_DEFAULT_ENV python=$(command -v python)"

  for (( attempt = 1; attempt <= MAX_RESUME_ATTEMPTS; attempt++ )); do
    log "resume attempt $attempt/$MAX_RESUME_ATTEMPTS via exact entry: $RUN_EXP"
    env \
      BENCHMARKS="$BENCHMARK" \
      EVOLVE_VERSION=v6.1 \
      N_CONCURRENT=16 \
      EVOLVE_WORKERS=16 \
      EVAL_N_TASKS=64 \
      EVOLVE_CASE_COUNT=16 \
      EVOLVE_CASES_PER_PROMPT=2 \
      V61_N_CYCLES="$TARGET_CYCLES" \
      V61_MAX_PROMPT_CHARS=50000 \
      V61_MAX_OBSERVATION_CHARS=1000 \
      V61_ANNOTATE_EXECUTION=exact-global \
      V61_ANNOTATE_CHECKPOINT=1 \
      SWEBENCH_TASK_PATH=/home/fanmeihao/projects/OptiHarnessForCost/tmp/harbor/datasets/swebench-verified \
      LLM_CONFIG=/home/fanmeihao/projects/OptiHarnessForCost/_config/deepseekv4_flash.yaml \
      FINAL_BASELINE_DIR="$baseline" \
      WORK_DIR="$WORK_DIR" \
      SCRIPTS_DIR="$SCRIPTS_DIR" \
      CONDA_ENV=0622 \
      bash "$RUN_EXP" &
    child=$!
    next_heartbeat=$(( $(date +%s) + HEARTBEAT_SECONDS ))
    while kill -0 "$child" 2>/dev/null; do
      now="$(date +%s)"
      if (( now >= next_heartbeat )); then
        progress_line
        log "continuation pid=$child is alive; waiting for final eval"
        next_heartbeat=$(( now + HEARTBEAT_SECONDS ))
      fi
      sleep "$POLL_SECONDS"
    done
    if wait "$child"; then
      rc=0
    else
      rc=$?
    fi
    if (( rc == 0 )); then
      log "two-cycle evolve + final eval entry completed successfully"
      return 0
    fi
    log "resume attempt $attempt exited rc=$rc; resumable artifacts are preserved"
    (( attempt < MAX_RESUME_ATTEMPTS )) && sleep 30
  done
  return 1
}

log "watching $WORK_DIR; target_cycles=$TARGET_CYCLES heartbeat=${HEARTBEAT_SECONDS}s poll=${POLL_SECONDS}s"
progress_line
if [[ -z "$ORIGINAL_PID" ]]; then
  ORIGINAL_PID="$(discover_original_pid || true)"
fi

next_heartbeat=$(( $(date +%s) + HEARTBEAT_SECONDS ))
while ! target_complete; do
  if [[ -n "$ORIGINAL_PID" ]] && pid_is_expected_orchestrator "$ORIGINAL_PID"; then
    :
  else
    ORIGINAL_PID="$(discover_original_pid || true)"
    if [[ -z "$ORIGINAL_PID" ]]; then
      log "original run stopped before cycle $TARGET_CYCLES completed; resuming in place with the two-cycle target"
      resume_and_eval
      exit $?
    fi
  fi
  now="$(date +%s)"
  if (( now >= next_heartbeat )); then
    progress_line
    next_heartbeat=$(( now + HEARTBEAT_SECONDS ))
  fi
  sleep "$POLL_SECONDS"
done

progress_line
log "cycle $TARGET_CYCLES is durably complete (state + report + harness snapshot)"
if [[ -n "$ORIGINAL_PID" ]] && pid_is_expected_orchestrator "$ORIGINAL_PID"; then
  stop_original_after_target "$ORIGINAL_PID"
fi
remove_future_cycle_artifacts
restore_target_snapshot
resume_and_eval
