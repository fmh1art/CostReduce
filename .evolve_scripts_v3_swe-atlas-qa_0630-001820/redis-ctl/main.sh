#!/bin/bash
# Control Redis server: start, stop, restart, or check status
# Usage: redis-ctl/main.sh [--port=N] [action]
#   action: start (default), stop, restart, status, check
#   --port=N: Redis port (default: 6379)

action="start"
port=6379

for arg in "$@"; do
  case "$arg" in
    start|stop|restart|status|check) action="$arg" ;;
    --port=*) port="${arg#*=}" ;;
  esac
done

case "$action" in
  start)
    # Check if already running
    if redis-cli -p "$port" ping 2>/dev/null | grep -q PONG; then
      echo "Redis is already running on port $port"
      exit 0
    fi
    # Start Redis
    if ! command -v redis-server >/dev/null 2>&1; then
      echo "Error: redis-server not found" >&2
      exit 1
    fi
    redis-server --daemonize yes --port "$port" 2>&1
    sleep 1
    if redis-cli -p "$port" ping 2>/dev/null | grep -q PONG; then
      echo "Redis started on port $port"
    else
      echo "Redis may have failed to start. Checking logs..." >&2
      exit 1
    fi
    ;;
  stop)
    if redis-cli -p "$port" ping 2>/dev/null | grep -q PONG; then
      redis-cli -p "$port" shutdown 2>&1
      echo "Redis stopped"
    else
      echo "Redis is not running on port $port"
    fi
    ;;
  restart)
    "$0" --port="$port" stop
    sleep 1
    "$0" --port="$port" start
    ;;
  status|check)
    if redis-cli -p "$port" ping 2>/dev/null | grep -q PONG; then
      echo "Redis is running on port $port"
      redis-cli -p "$port" info server 2>/dev/null | grep -E "(redis_version|uptime_in_seconds|process_id)" || true
    else
      echo "Redis is NOT running on port $port"
      exit 1
    fi
    ;;
esac
