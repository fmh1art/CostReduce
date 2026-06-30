#!/bin/bash
# Run an inline script - create temp file and execute it
# Usage: run-script/main.sh <interpreter> [--config=<env_file>] [--cd=DIR] [--env KEY=VALUE]... [--head=N] [--tail=N] [--timeout=N] [--quiet] [code...]
#   or:  echo "code" | run-script/main.sh <interpreter> [--config=<env_file>] [--cd=DIR] [--env KEY=VALUE]... [--head=N] [--tail=N] [--timeout=N] [--quiet]
#   --quiet:    Suppress stderr on success; if command fails (exit != 0), stderr is shown
#   --timeout=N: Run with timeout (seconds), replaces `timeout N` wrapper

if [ $# -lt 1 ]; then
  echo "Error: interpreter required (node, python, bash)" >&2
  echo "Usage: run-script/main.sh <interpreter> [--config=<env_file>] [--cd=DIR] [--env KEY=VALUE]... [--head=N] [--tail=N] [--timeout=N] [--quiet] [code...]" >&2
  exit 1
fi

interpreter="$1"
shift

workdir=""
config=""
env_vars=()
code_args=()
head_n=""
tail_n=""
timeout_n=""
quiet=false

for arg in "$@"; do
  if [[ "$arg" =~ ^--cd=(.*)$ ]]; then
    workdir="${BASH_REMATCH[1]}"
  elif [[ "$arg" =~ ^--config=(.*)$ ]]; then
    config="${BASH_REMATCH[1]}"
  elif [[ "$arg" =~ ^--env=(.*)$ ]]; then
    env_vars+=("${BASH_REMATCH[1]}")
  elif [[ "$arg" =~ ^--head=(.*)$ ]]; then
    head_n="${BASH_REMATCH[1]}"
  elif [[ "$arg" =~ ^--tail=(.*)$ ]]; then
    tail_n="${BASH_REMATCH[1]}"
  elif [[ "$arg" =~ ^--timeout=(.*)$ ]]; then
    timeout_n="${BASH_REMATCH[1]}"
  elif [ "$arg" = "--quiet" ]; then
    quiet=true
  else
    code_args+=("$arg")
  fi
done

# Change to working directory if specified (do this before reading config)
if [ -n "$workdir" ]; then
  cd "$workdir" 2>/dev/null || { echo "Error: directory '$workdir' not found" >&2; exit 1; }
fi

# Load env config if specified
if [ -n "$config" ]; then
  if [ ! -f "$config" ]; then
    echo "Error: config file '$config' not found" >&2
    exit 1
  fi
  while IFS='=' read -r key value; do
    [[ -z "$key" || "$key" == \#* ]] && continue
    value="${value%\"}"
    value="${value#\"}"
    export "$key=$value"
  done < "$config"
fi

# Apply inline env vars
for e in "${env_vars[@]}"; do
  export "$e"
done

# Create temp script file
id="$$_$RANDOM"
case "$interpreter" in
  node) ext=".js";;
  python|python3) ext=".py";;
  bash|sh) ext=".sh";;
  *) ext=".txt";;
esac

if [ -w "." ]; then
  script_dir=".run_scripts_cache"
  mkdir -p "$script_dir" 2>/dev/null
  script_file="${script_dir}/run_script_${id}${ext}"
else
  script_file="/tmp/run_script_${id}${ext}"
fi

if [ -d ".run_scripts_cache" ]; then
  find ".run_scripts_cache" -name "run_script_*" -mmin +60 -delete 2>/dev/null || true
fi

if [ ${#code_args[@]} -gt 0 ]; then
  printf '%s\n' "${code_args[@]}" > "$script_file"
elif [ ! -t 0 ]; then
  cat > "$script_file"
else
  echo "Error: no code provided. Pass code as args or pipe to stdin." >&2
  exit 1
fi

# Auto-detect venv Python
case "$interpreter" in
  python|python3)
    if [ -f "venv/bin/python" ]; then
      interpreter="venv/bin/python"
    elif [ -f ".venv/bin/python" ]; then
      interpreter=".venv/bin/python"
    fi
    ;;
esac

# Build timeout prefix
timeout_prefix=""
if [ -n "$timeout_n" ]; then
  timeout_prefix="timeout $timeout_n "
fi

# Build output filter suffix
output_filter=""
if [ -n "$head_n" ] && [ -n "$tail_n" ]; then
  output_filter=" | tail -n $tail_n"
elif [ -n "$head_n" ]; then
  output_filter=" | head -n $head_n"
elif [ -n "$tail_n" ]; then
  output_filter=" | tail -n $tail_n"
fi

if [ "$quiet" = true ]; then
  # Quiet mode: capture stderr, show only on failure
  stderr_file=$(mktemp /tmp/run_script_stderr_XXXXXX)
  eval "${timeout_prefix}\"$interpreter\" \"$script_file\" 2>\"$stderr_file\" $output_filter"
  rc=$?
  if [ $rc -ne 0 ] && [ -s "$stderr_file" ]; then
    cat "$stderr_file" >&2
  fi
  rm -f "$stderr_file"
else
  # Normal mode: merge stderr with stdout
  eval "${timeout_prefix}\"$interpreter\" \"$script_file\" 2>&1 $output_filter"
  rc=$?
fi

rm -f "$script_file"
exit $rc
