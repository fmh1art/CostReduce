#!/bin/bash
# Run alembic database migrations with config file, replacing cd + CONFIG= + venv/bin/alembic chains
# Usage: run-alembic/main.sh [--cd=DIR] [--config=FILE] [--python=PATH] [--alembic-cfg=FILE] [--head=N] [--tail=N] [--brief] <command> [args...]
#   command: upgrade head, downgrade -1, current, history, revision --autogenerate -m "msg", etc.
#   --cd=DIR:        Working directory (default: /app)
#   --config=FILE:   Path to env config file (sets CONFIG env var for the app)
#   --python=PATH:   Path to Python interpreter (default: auto-detect venv/bin/python or /app/venv/bin/python)
#   --alembic-cfg=FILE: Path to alembic.ini (default: alembic.ini in workdir)
#   --brief:         Filter out verbose INFO/DEBUG log lines from app and alembic, keeping only migration status lines
#   --head=N:        Show only first N lines of output (replaces | head -N chains)
#   --tail=N:        Show only last N lines of output (replaces | tail -N chains)

workdir="/app"
config=""
python_bin=""
alembic_cfg=""
head_n=""
tail_n=""
brief_mode=false
alembic_args=()

for arg in "$@"; do
  case "$arg" in
    --cd=*) workdir="${arg#*=}" ;;
    --config=*) config="${arg#*=}" ;;
    --python=*) python_bin="${arg#*=}" ;;
    --alembic-cfg=*) alembic_cfg="${arg#*=}" ;;
    --brief) brief_mode=true ;;
    --head=*) head_n="${arg#*=}" ;;
    --tail=*) tail_n="${arg#*=}" ;;
    *)
      alembic_args+=("$arg")
      ;;
  esac
done

if [ ${#alembic_args[@]} -eq 0 ]; then
  echo "Error: alembic command required (e.g., upgrade head, current, history)" >&2
  echo "Usage: run-alembic/main.sh [--cd=DIR] [--config=FILE] [--python=PATH] [--head=N] [--tail=N] [--brief] <command> [args...]" >&2
  exit 1
fi

# Change to working directory
cd "$workdir" 2>/dev/null || { echo "Error: directory '$workdir' not found" >&2; exit 1; }

# Load config file if specified (sets CONFIG env var for the app)
if [ -n "$config" ]; then
  if [ ! -f "$config" ]; then
    echo "Error: config file '$config' not found" >&2
    exit 1
  fi
  export CONFIG="$config"
  # Also source the env vars
  # Parse config file robustly (handle values with special chars like []() that break bash source)
  while IFS='=' read -r key value; do
    # Skip comments and empty lines
    [[ -z "$key" || "$key" == \#* ]] && continue
    # Remove surrounding quotes from value if present
    value="${value%\"}"
    value="${value#\"}"
    export "$key=$value"
  done < "$config"
fi

# Auto-detect Python
if [ -z "$python_bin" ]; then
  if [ -f "venv/bin/python" ]; then
    python_bin="venv/bin/python"
  elif [ -f ".venv/bin/python" ]; then
    python_bin=".venv/bin/python"
  elif [ -f "/app/venv/bin/python" ]; then
    python_bin="/app/venv/bin/python"
  else
    python_bin="python"
  fi
fi

if ! command -v "$python_bin" >/dev/null 2>&1; then
  echo "Error: Python interpreter '$python_bin' not found" >&2
  exit 1
fi

# Find alembic binary next to python interpreter
python_dir=$(dirname "$python_bin")
alembic_bin="${python_dir}/alembic"

# Build the output filtering pipe
pipe_cmd="cat"
if [ "$brief_mode" = true ]; then
  # Filter out verbose app DEBUG/INFO lines and alembic INFO lines, keeping only migration status lines
  # Also keep error/warning lines for debugging
  pipe_cmd="grep -v -E '^(DEBUG|INFO).*[-]' | grep -v -E '^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2},[0-9]+ - ' | grep -v -E '(load config|Paddle param|WARNING: Use a temp|Upload files|init logging|load words file)' | grep -v -E '^>>>' | grep . || true"
fi

if [ -n "$head_n" ]; then
  if [ "$brief_mode" = true ]; then
    pipe_cmd="$pipe_cmd | head -n $head_n"
  else
    pipe_cmd="head -n $head_n"
  fi
elif [ -n "$tail_n" ]; then
  if [ "$brief_mode" = true ]; then
    pipe_cmd="$pipe_cmd | tail -n $tail_n"
  else
    pipe_cmd="tail -n $tail_n"
  fi
fi

# Run alembic
if [ ! -f "$alembic_bin" ]; then
  # Fall back to python -m alembic
  if [ -n "$alembic_cfg" ]; then
    CONFIG="$config" "$python_bin" -m alembic --config "$alembic_cfg" "${alembic_args[@]}" 2>&1 | eval "$pipe_cmd"
  else
    CONFIG="$config" "$python_bin" -m alembic "${alembic_args[@]}" 2>&1 | eval "$pipe_cmd"
  fi
  exit $?
fi

if [ -n "$alembic_cfg" ]; then
  CONFIG="$config" "$alembic_bin" --config "$alembic_cfg" "${alembic_args[@]}" 2>&1 | eval "$pipe_cmd"
else
  CONFIG="$config" "$alembic_bin" "${alembic_args[@]}" 2>&1 | eval "$pipe_cmd"
fi
