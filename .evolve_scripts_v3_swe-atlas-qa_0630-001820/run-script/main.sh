#!/bin/bash
# Run an inline script - create temp file and execute it
# Usage: run-script/main.sh <interpreter> [--config=<env_file>] [--cd=DIR] [--env KEY=VALUE]... [--head=N] [--tail=N] [--timeout=N] [--quiet] [code...]
#   or:  echo "code" | run-script/main.sh <interpreter> [--config=<env_file>] [--cd=DIR] [--env KEY=VALUE]... [--head=N] [--tail=N] [--timeout=N] [--quiet]
#   --quiet:    Suppress stderr (useful for verbose Python debug output from app initialization)
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

# Apply inline env vars
for e in "${env_vars[@]}"; do
  export "$e"
done

# Build the temp script
# Create temp file in CWD so that node/python can find project modules
# via parent-directory walk (Node module resolution).
# Fall back to /tmp if CWD is not writable.
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

# Clean up any previous cached scripts older than 1 hour (avoid accumulation)
if [ -d ".run_scripts_cache" ]; then
  find ".run_scripts_cache" -name "run_script_*" -mmin +60 -delete 2>/dev/null || true
fi

if [ ${#code_args[@]} -gt 0 ]; then
  # Write inline code (each arg is a line)
  printf '%s\n' "${code_args[@]}" > "$script_file"
elif [ ! -t 0 ]; then
  # Read from stdin
  cat > "$script_file"
else
  echo "Error: no code provided. Pass code as args or pipe to stdin." >&2
  exit 1
fi

# Auto-detect venv Python when interpreter is python/python3
case "$interpreter" in
  python|python3)
    if [ -f "venv/bin/python" ]; then
      interpreter="venv/bin/python"
    elif [ -f ".venv/bin/python" ]; then
      interpreter=".venv/bin/python"
    fi
    ;;
esac

# Build output redirection
stderr_redir=""
if [ "$quiet" = true ]; then
  stderr_redir="2>/dev/null"
else
  stderr_redir="2>&1"
fi

# Build timeout prefix if specified
timeout_prefix=""
if [ -n "$timeout_n" ]; then
  timeout_prefix="timeout $timeout_n "
fi

# Execute with optional output limiting
if [ -n "$head_n" ] && [ -n "$tail_n" ]; then
  # Both specified: tail wins (last N lines)
  eval "${timeout_prefix}\"$interpreter\" \"$script_file\" $stderr_redir | tail -n \"$tail_n\""
elif [ -n "$head_n" ]; then
  eval "${timeout_prefix}\"$interpreter\" \"$script_file\" $stderr_redir | head -n \"$head_n\""
elif [ -n "$tail_n" ]; then
  eval "${timeout_prefix}\"$interpreter\" \"$script_file\" $stderr_redir | tail -n \"$tail_n\""
else
  if [ "$quiet" = true ]; then
    if [ -n "$timeout_n" ]; then
      timeout "$timeout_n" "$interpreter" "$script_file" 2>/dev/null
    else
      "$interpreter" "$script_file" 2>/dev/null
    fi
  else
    if [ -n "$timeout_n" ]; then
      timeout "$timeout_n" "$interpreter" "$script_file" 2>&1
    else
      "$interpreter" "$script_file" 2>&1
    fi
  fi
fi
rc=$?

# Clean up
rm -f "$script_file"
exit $rc
