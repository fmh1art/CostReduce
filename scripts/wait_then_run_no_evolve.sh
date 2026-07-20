#!/usr/bin/env bash
# Wait for one detached run_exp.sh process, then run its paired no-evolve phase.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
MAIN_PID="${MAIN_PID:?set MAIN_PID to the background run_exp.sh PID}"
WAIT_POLL_SECONDS="${WAIT_POLL_SECONDS:-60}"

[[ "$MAIN_PID" =~ ^[1-9][0-9]*$ ]] || { echo "invalid MAIN_PID=$MAIN_PID" >&2; exit 2; }
[[ "$WAIT_POLL_SECONDS" =~ ^[1-9][0-9]*$ ]] \
  || { echo "invalid WAIT_POLL_SECONDS=$WAIT_POLL_SECONDS" >&2; exit 2; }

main_start_ticks=""
if [[ -r "/proc/${MAIN_PID}/stat" ]]; then
  main_start_ticks="$(awk '{print $22}' "/proc/${MAIN_PID}/stat")"
fi

printf '[no-evolve-waiter] waiting for main pid=%s start_ticks=%s\n' \
  "$MAIN_PID" "${main_start_ticks:-already-finished}"
while [[ -n "$main_start_ticks" && -r "/proc/${MAIN_PID}/stat" ]]; do
  current_start_ticks="$(awk '{print $22}' "/proc/${MAIN_PID}/stat" 2>/dev/null || true)"
  current_state="$(awk '{print $3}' "/proc/${MAIN_PID}/stat" 2>/dev/null || true)"
  [[ "$current_start_ticks" == "$main_start_ticks" && "$current_state" != "Z" ]] || break
  sleep "$WAIT_POLL_SECONDS"
done

printf '[no-evolve-waiter] main process finished; starting paired controls\n'
cd "$ROOT_DIR"
exec env PHASE=no_evolve RUN_NO_EVOLVE_AFTER=0 bash "${SCRIPT_DIR}/run_exp.sh"
