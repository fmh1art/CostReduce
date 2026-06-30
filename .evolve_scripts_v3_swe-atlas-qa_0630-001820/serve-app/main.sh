#!/bin/bash
# Start a Gunicorn web app server in background with env config
# Usage: serve-app/main.sh --config=<env_file> [--port=N] [--cd=DIR] [--wait=N] [--app=APP] [--log-prefix=NAME]

config=""
port=7777
workdir="/app"
wait_seconds=5
app="wsgi:app"
log_prefix="server"

for arg in "$@"; do
  case "$arg" in
    --config=*) config="${arg#*=}" ;;
    --port=*) port="${arg#*=}" ;;
    --cd=*) workdir="${arg#*=}" ;;
    --wait=*) wait_seconds="${arg#*=}" ;;
    --app=*) app="${arg#*=}" ;;
    --log-prefix=*) log_prefix="${arg#*=}" ;;
  esac
done

if [ -z "$config" ]; then
  echo "Error: --config=<env_file> is required" >&2
  echo "Usage: serve-app/main.sh --config=<env_file> [--port=N] [--cd=DIR] [--wait=N]" >&2
  exit 1
fi

cd "$workdir" 2>/dev/null || { echo "Error: directory '$workdir' not found" >&2; exit 1; }

if [ ! -f "$config" ]; then
  echo "Error: config file '$config' not found in $workdir" >&2
  exit 1
fi

log_file="/tmp/${log_prefix}.log"
pid_file="/tmp/${log_prefix}.pid"

# Find gunicorn
if [ -f "venv/bin/gunicorn" ]; then
  gunicorn_bin="venv/bin/gunicorn"
elif command -v gunicorn &>/dev/null; then
  gunicorn_bin="gunicorn"
else
  echo "Error: gunicorn not found (checked venv/bin/gunicorn and PATH)" >&2
  exit 1
fi

export CONFIG="$config"
nohup "$gunicorn_bin" -b 0.0.0.0:"$port" --access-logfile - --error-logfile - --log-level debug "$app" > "$log_file" 2>&1 &
pid=$!
echo "$pid" > "$pid_file"
echo "Server PID: $pid (started with CONFIG=$config, port=$port)"

sleep "$wait_seconds"

if kill -0 "$pid" 2>/dev/null; then
  echo "Server running on http://0.0.0.0:$port (PID: $pid, log: $log_file)"
else
  echo "Server failed to start. Last 20 log lines:"
  tail -20 "$log_file"
  exit 1
fi
