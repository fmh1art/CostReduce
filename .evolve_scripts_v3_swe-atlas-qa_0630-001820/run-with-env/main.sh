#!/bin/bash
# Run a command with environment variables loaded from config file or inline --env flags
# Also supports --get=KEY / --all to read values from env files (replaces env-get).
# Usage: run-with-env/main.sh [--config=<env_file>] [--cd=DIR] [--env=KEY=VALUE]... [--head=N] [--tail=N] <command> [args...]
#   or:  run-with-env/main.sh --config=<env_file> --get=KEY [--value-only]
#   or:  run-with-env/main.sh --config=<env_file> --all [--strip-comments]
#   or:  run-with-env/main.sh --env=DB_URI=postgresql://... --env=URL=http://... --cd=/app --head=100 python script.py
#   --head=N:  Show first N lines of command output (replaces | head -N)
#   --tail=N:  Show last N lines of command output (replaces | tail -N)

config=""
workdir=""
env_vars=()
cmd=()
get_key=""
show_all=false
value_only=false
strip_comments=false
head_n=""
tail_n=""

for arg in "$@"; do
  case "$arg" in
    --config=*) config="${arg#*=}" ;;
    --cd=*) workdir="${arg#*=}" ;;
    --env=*) env_vars+=("${arg#*=}") ;;
    --get=*) get_key="${arg#*=}" ;;
    --all) show_all=true ;;
    --value-only) value_only=true ;;
    --strip-comments) strip_comments=true ;;
    --head=*) head_n="${arg#*=}" ;;
    --tail=*) tail_n="${arg#*=}" ;;
    --) shift; cmd=("$@"); break ;;
    *) cmd+=("$arg") ;;
  esac
  shift
done

# Change to working directory if specified
if [ -n "$workdir" ]; then
  cd "$workdir" 2>/dev/null || { echo "Error: directory '$workdir' not found" >&2; exit 1; }
fi

# Load config file if specified
config_data=""
if [ -n "$config" ]; then
  if [ ! -f "$config" ]; then
    echo "Error: config file '$config' not found" >&2
    exit 1
  fi
  config_data=$(cat "$config")
fi

# --get=KEY mode: read a single value from config file without running a command
if [ -n "$get_key" ]; then
  if [ -z "$config_data" ]; then
    echo "Error: --get=KEY requires --config=FILE" >&2
    exit 1
  fi
  result=$(echo "$config_data" | grep -m1 "^${get_key}=" | head -1)
  if [ -z "$result" ]; then
    result=$(echo "$config_data" | grep -m1 "^export ${get_key}=" | head -1)
  fi
  if [ -n "$result" ]; then
    if [ "$value_only" = true ]; then
      echo "$result" | sed 's/^export //' | sed 's/^[^=]*=//' | sed 's/^"//;s/"$//' | sed "s/^'//;s/'$//"
    else
      echo "$result" | sed 's/^export //'
    fi
  fi
  exit 0
fi

# --all mode: list all key-value pairs from config file
if [ "$show_all" = true ]; then
  if [ -z "$config_data" ]; then
    echo "Error: --all requires --config=FILE" >&2
    exit 1
  fi
  if [ "$strip_comments" = true ]; then
    echo "$config_data" | grep -vE '^[[:space:]]*($|#)'
  else
    echo "$config_data"
  fi
  exit 0
fi

# Normal mode: run a command
if [ ${#cmd[@]} -eq 0 ]; then
  echo "Error: no command specified. Use --get=KEY or --all to read config, or provide a command to run." >&2
  echo "Usage: run-with-env/main.sh [--config=<env_file>] [--cd=DIR] [--env=KEY=VALUE]... <command> [args...]" >&2
  exit 1
fi

# Export vars from config file (robust parsing)
if [ -n "$config_data" ]; then
  while IFS='=' read -r key value; do
    # Skip comments and empty lines
    [[ -z "$key" || "$key" == \#* ]] && continue
    # Remove surrounding quotes from value if present
    value="${value%\"}"
    value="${value#\"}"
    export "$key=$value"
  done <<< "$config_data"
fi

# Apply inline env vars (override any from config)
for e in "${env_vars[@]}"; do
  export "$e"
done

# Run the command with optional output limiting
if [ -n "$head_n" ] && [ -n "$tail_n" ]; then
  "${cmd[@]}" 2>&1 | head -n "$head_n" | tail -n "$tail_n"
elif [ -n "$head_n" ]; then
  "${cmd[@]}" 2>&1 | head -n "$head_n"
elif [ -n "$tail_n" ]; then
  "${cmd[@]}" 2>&1 | tail -n "$tail_n"
else
  exec "${cmd[@]}"
fi
