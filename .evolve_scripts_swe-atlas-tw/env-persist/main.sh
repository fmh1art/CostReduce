#!/usr/bin/env bash
set -euo pipefail

# env-persist: Save and reload environment variables to avoid repeating long export chains.
# Usage:
#   env-persist --save <dir> [--venv <venv_path>] KEY=VAL [KEY=VAL ...]
#   env-persist --load <dir>
#   env-persist --exec <dir> [--] <command...>
#   env-persist --python <dir> <code_string_or_script>
#   env-persist --list

ENV_DIR="${HOME}/.env_persist"
mkdir -p "$ENV_DIR"

save_env() {
  local TARGET_DIR=""
  local VENV_PATH=""
  local VARS=()
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --venv)
        VENV_PATH="$2"
        shift 2
        ;;
      *=*)
        VARS+=("$1")
        shift
        ;;
      *)
        if [[ -z "$TARGET_DIR" ]]; then
          TARGET_DIR="$1"
        else
          echo "Error: unexpected argument: $1" >&2
          exit 1
        fi
        shift
        ;;
    esac
  done
  if [[ -z "$TARGET_DIR" ]]; then
    echo "Usage: $0 --save <directory> [--venv <venv_path>] KEY=VAL [KEY=VAL ...]" >&2
    exit 1
  fi
  local SAVE_FILE="${ENV_DIR}/$(echo "$TARGET_DIR" | tr '/' '_')"
  {
    echo "cd ${TARGET_DIR}"
    if [[ -n "$VENV_PATH" ]]; then
      echo "VENV=${VENV_PATH}"
    fi
    for var in "${VARS[@]}"; do
      key="${var%%=*}"
      if [[ "$key" == "PYTHONPATH" ]]; then
        echo "export PYTHONPATH=\"${var#*=}\""
      else
        echo "export ${var}"
      fi
    done
  } > "$SAVE_FILE"
  echo "Saved ${#VARS[@]} env vars for ${TARGET_DIR}"
}

load_env() {
  local TARGET_DIR="${1:-}"
  if [[ -z "$TARGET_DIR" ]]; then
    echo "Usage: $0 --load <directory>" >&2
    exit 1
  fi
  local SAVE_FILE="${ENV_DIR}/$(echo "$TARGET_DIR" | tr '/' '_')"
  if [[ ! -f "$SAVE_FILE" ]]; then
    echo "Error: No saved environment for ${TARGET_DIR}. Use --save first." >&2
    exit 1
  fi
  cat "$SAVE_FILE"
}

exec_env() {
  local TARGET_DIR="${1:-}"
  if [[ -z "$TARGET_DIR" ]]; then
    echo "Usage: $0 --exec <directory> [--] <command...>" >&2
    exit 1
  fi
  shift
  local SAVE_FILE="${ENV_DIR}/$(echo "$TARGET_DIR" | tr '/' '_')"
  if [[ ! -f "$SAVE_FILE" ]]; then
    echo "Error: No saved environment for ${TARGET_DIR}. Use --save first." >&2
    exit 1
  fi
  # Skip '--' separator if present
  if [[ "${1:-}" == "--" ]]; then
    shift
  fi
  # Source the saved env
  set +euo pipefail
  source "$SAVE_FILE"
  # If VENV was saved, activate it
  if [[ -n "${VENV:-}" ]] && [[ -f "$VENV" ]]; then
    . "$VENV"
  fi
  set -euo pipefail
  # Run the command
  if [[ $# -eq 0 ]]; then
    echo "Error: No command specified for --exec" >&2
    exit 1
  fi
  "$@"
}

list_envs() {
  echo "Saved environments:"
  for f in "$ENV_DIR"/*; do
    if [[ -f "$f" ]]; then
      local name
      name=$(basename "$f" | tr '_' '/')
      local var_count
      var_count=$(grep -c '^export ' "$f" 2>/dev/null || true)
      local has_venv
      has_venv=$(grep -c '^VENV=' "$f" 2>/dev/null || true)
      local venv_note=""
      if [[ "$has_venv" -gt 0 ]]; then
        venv_note=" +venv"
      fi
      echo "  ${name} (${var_count} vars${venv_note})"
    fi
  done
}

case "${1:-}" in
  --save)
    shift
    save_env "$@"
    ;;
  --load)
    shift
    load_env "$@"
    ;;
  --exec)
    shift
    exec_env "$@"
    ;;
  --list)
    list_envs
    ;;
  *)
    echo "Usage: $0 --save|--load|--exec|--list [args...]" >&2
    exit 1
    ;;
esac
