#!/usr/bin/env bash
set -euo pipefail

# start-services: Start or check common services (PostgreSQL, Redis) in one step.
# Usage: start-services [--check-only] [--pg-port=PORT] [--redis-port=PORT]

CHECK_ONLY=false
PG_PORT=5432
REDIS_PORT=6379

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check-only|-c)
      CHECK_ONLY=true
      shift
      ;;
    --pg-port=*)
      PG_PORT="${1#*=}"
      shift
      ;;
    --redis-port=*)
      REDIS_PORT="${1#*=}"
      shift
      ;;
    -*)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
    *)
      echo "Error: unexpected argument: $1" >&2
      exit 1
      ;;
  esac
done

pg_ok=false
redis_ok=false

# Check PostgreSQL
if command -v pg_isready &>/dev/null; then
  if pg_isready -h localhost -p "$PG_PORT" &>/dev/null; then
    echo "PostgreSQL is running on port $PG_PORT"
    pg_ok=true
  else
    echo "PostgreSQL is NOT running on port $PG_PORT"
  fi
else
  echo "pg_isready not found, skipping PostgreSQL check"
fi

# Check Redis
if command -v redis-cli &>/dev/null; then
  if redis-cli -h localhost -p "$REDIS_PORT" ping 2>/dev/null | grep -q PONG; then
    echo "Redis is running on port $REDIS_PORT"
    redis_ok=true
  else
    echo "Redis is NOT running on port $REDIS_PORT"
  fi
else
  echo "redis-cli not found, skipping Redis check"
fi

if $CHECK_ONLY; then
  if $pg_ok && $redis_ok; then
    exit 0
  fi
  exit 1
fi

# Start missing services
STARTED=false

if ! $pg_ok; then
  if command -v service &>/dev/null && service postgresql start 2>/dev/null; then
    echo "Started PostgreSQL"
    STARTED=true
  elif command -v pg_ctl &>/dev/null; then
    # Try to find and start PostgreSQL data directory
    PG_DATA=$(ls -d /var/lib/postgresql/*/main 2>/dev/null | head -1) || true
    if [[ -n "$PG_DATA" ]]; then
      pg_ctl -D "$PG_DATA" start 2>/dev/null && echo "Started PostgreSQL" && STARTED=true
    fi
  fi
  # Wait for it to be ready
  for i in $(seq 1 10); do
    if pg_isready -h localhost -p "$PG_PORT" &>/dev/null; then
      pg_ok=true
      break
    fi
    sleep 0.5
  done
fi

if ! $redis_ok; then
  if command -v service &>/dev/null && service redis-server start 2>/dev/null; then
    echo "Started Redis"
    STARTED=true
  elif command -v redis-server &>/dev/null; then
    redis-server --daemonize yes --port "$REDIS_PORT" 2>/dev/null && echo "Started Redis" && STARTED=true
  fi
  # Wait for it to be ready
  for i in $(seq 1 10); do
    if redis-cli -h localhost -p "$REDIS_PORT" ping 2>/dev/null | grep -q PONG; then
      redis_ok=true
      break
    fi
    sleep 0.5
  done
fi

if $pg_ok && $redis_ok; then
  echo "All services are running"
  exit 0
else
  echo "Some services could not be started" >&2
  exit 1
fi
