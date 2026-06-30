#!/bin/bash
# Control PostgreSQL server: start, stop, restart, recreate, status, list databases/users, or run SQL
# Usage: postgres-ctl/main.sh [action] [--db=DB] [--user=USER] [--port=PORT] [--host=HOST]
#   action: start, stop, restart, recreate, status, check (default: status)
#   recreate: Drop entire cluster and create fresh (for unrecoverable corruption/stuck recovery)
#   --list-dbs: List all databases
#   --list-users: List all PostgreSQL users/roles
#   --list-config: Show postgres config (port, etc.)
#   --psql=QUERY: Run a SQL query (or pipe SQL via stdin, or use -f FILE)
#   SQL can also be passed as a positional argument (like run-sql syntax):
#     postgres-ctl/main.sh --db=DB "SELECT * FROM users;"
#   --reset-db=DBNAME: Drop and recreate a database
#   --create-ext=EXTNAME: Create a PostgreSQL extension
#   --port=N: PostgreSQL port (default: 5432)
#   --no-sudo: Run psql directly without sudo su
#   --force: Force start with aggressive cleanup (kill existing, remove stale pid, pg_resetwal)
#   --log[=N]: Show last N lines from PostgreSQL log (default: 30)
#   --check-process: Show running PostgreSQL processes
#   --trust-auth:   Set trust authentication in pg_hba.conf and restart PostgreSQL (collapses sed + restart + pg_isready into one step)

#   --reset-auth:   Revert trust authentication back to scram-sha-256 in pg_hba.conf and restart PostgreSQL (collapses sed + restart + pg_isready into one step)

#   --wait[=SECONDS]: Wait up to SECONDS for PostgreSQL to become ready (poll pg_isready; default: 120)
#   --wait-interval=N: Poll interval in seconds when --wait is active (default: 5)

action="status"
db_user="postgres"
db=""
host=""
port=""
psql_query=""
psql_file=""
reset_db=""
create_ext=""
list_dbs=false
list_users=false
list_config=false
use_sudo=true
force=false
log_lines=""
check_process=false
trust_auth=false

reset_auth=false
no_header=false
csv_mode=false

wait_timeout=""
wait_interval=5

# Parse arguments
parsed_action=false
positional_sql=""
for arg in "$@"; do
  case "$arg" in
    start|stop|restart|recreate|status|check)
      if [ "$parsed_action" = false ]; then
        action="$arg"
        parsed_action=true
      else
        # Second positional arg that isn't a flag - treat as SQL
        positional_sql="$arg"
      fi
      ;;
    --db=*) db="${arg#*=}" ;;
    --user=*) db_user="${arg#*=}" ;;
    --host=*) host="${arg#*=}" ;;
    --port=*) port="${arg#*=}" ;;
    --psql=*) psql_query="${arg#*=}" ;;
    -f=*) psql_file="${arg#*=}" ;;
    -f) ;;
    --reset-db=*) reset_db="${arg#*=}" ;;
    --create-ext=*) create_ext="${arg#*=}" ;;
    --list-dbs) list_dbs=true ;;
    --list-users) list_users=true ;;
    --list-config) list_config=true ;;
    --no-sudo) use_sudo=false ;;
    --force) force=true ;;
    --log=*) log_lines="${arg#*=}" ;;
    --log) log_lines=30 ;;
    --check-process) check_process=true ;;
    --trust-auth) trust_auth=true ;;
    --reset-auth) reset_auth=true ;;
    --no-header) no_header=true ;;
    --csv) csv_mode=true ;;
    --wait=*) wait_timeout="${arg#*=}" ;;
    --wait) wait_timeout=120 ;;
    --wait-interval=*) wait_interval="${arg#*=}" ;;
    *)
      # Non-flag positional arg that isn't an action - treat as SQL query
      if [ "$parsed_action" = true ]; then
        positional_sql="$arg"
      else
        # Could be an action we don't recognize, or SQL without action prefix
        positional_sql="$arg"
      fi
      ;;
  esac
done

# If positional SQL was given and no --psql, use it
if [ -n "$positional_sql" ] && [ -z "$psql_query" ]; then
  psql_query="$positional_sql"
fi

# Build psql execute function
exec_psql() {
  local sql="$1"
  local dbname="$2"
  local psql_cmd="psql"
  # Apply --no-header: use -t (tuples only, no header/footer)
  if [ "$no_header" = true ]; then
    psql_cmd="$psql_cmd -t"
  fi
  # Apply --csv: use CSV output
  if [ "$csv_mode" = true ]; then
    psql_cmd="$psql_cmd --csv"
  else
    psql_cmd="$psql_cmd -A"
  fi
  [ -n "$dbname" ] && psql_cmd="$psql_cmd -d $dbname"
  [ -n "$host" ] && psql_cmd="$psql_cmd -h $host"
  [ -n "$port" ] && psql_cmd="$psql_cmd -p $port"
  
  if [ "$use_sudo" = true ]; then
    su - "$db_user" -c "$psql_cmd -c $(printf '%q' "$sql")" 2>&1
  else
    $psql_cmd -c "$sql" 2>&1
  fi
}

# Helper: find postgres data directory
find_pg_data_dir() {
  local version
  for v in 16 15 14 13 12 11 10; do
    local dir="/var/lib/postgresql/$v/main"
    [ -d "$dir" ] && echo "$dir" && return 0
  done
  return 1
}

# Helper: find postgres config directory
find_pg_config_dir() {
  local version
  for v in 16 15 14 13 12 11 10; do
    local dir="/etc/postgresql/$v/main"
    [ -d "$dir" ] && echo "$dir" && return 0
  done
  return 1
}

# Helper: find PostgreSQL version
find_pg_version() {
  local ver
  ver=$(pg_lsclusters 2>/dev/null | awk 'NR>1{print $1; exit}')
  [ -n "$ver" ] && echo "$ver" && return 0
  for v in 16 15 14 13 12 11 10; do
    [ -d "/etc/postgresql/$v" ] && echo "$v" && return 0
  done
  echo "15"
}

# Helper: check if psql is available
check_psql() {
  if [ "$use_sudo" = true ]; then
    su - "$db_user" -c "command -v psql" 2>/dev/null || return 1
  else
    command -v psql 2>/dev/null || return 1
  fi
}

# Helper: wait for PostgreSQL to become ready (poll pg_isready in a loop)
# Usage: wait_for_ready [--timeout=N] [--interval=N]
wait_for_ready() {
  local timeout="${wait_timeout:-120}"
  local interval="${wait_interval:-5}"
  local elapsed=0
  local start_time
  start_time=$(date +%s)
  
  echo "Waiting for PostgreSQL to become ready (timeout=${timeout}s, interval=${interval}s)..."
  while [ "$elapsed" -lt "$timeout" ]; do
    if pg_isready -h localhost ${port:+-p $port} 2>/dev/null | grep -q "accepting connections"; then
      local end_time
      end_time=$(date +%s)
      echo "PostgreSQL is ready after ${elapsed}s"
      return 0
    fi
    sleep "$interval"
    elapsed=$(( $(date +%s) - start_time ))
  done
  
  echo "Warning: PostgreSQL did not become ready within ${timeout}s"
  pg_isready -h localhost ${port:+-p $port} 2>&1 || true
  return 1
}


case "$action" in
  start)
    echo "Starting PostgreSQL..."
    pg_lsclusters 2>/dev/null | head -5
    
    if [ "$force" = true ]; then
      echo "Force mode: cleaning up stale processes and files..."
      # Kill existing postgres processes
      pkill -9 postgres 2>/dev/null || true
      sleep 1
      # Remove stale PID files
      pg_ver=$(find_pg_version)
      rm -f "/var/run/postgresql/.s.PGSQL.${port:-5432}" "/var/run/postgresql/.s.PGSQL.${port:-5432}.lock" 2>/dev/null || true
      rm -f "/var/run/postgresql/${pg_ver}-main.pid" 2>/dev/null || true
      rm -f "/var/lib/postgresql/$pg_ver/main/postmaster.pid" 2>/dev/null || true
      # Try pg_resetwal if data dir exists
      data_dir=$(find_pg_data_dir)
      if [ -n "$data_dir" ] && [ -f "$data_dir/postmaster.pid" ]; then
        echo "Stale PID file found, attempting recovery..."
        su - "$db_user" -c "/usr/lib/postgresql/$pg_ver/bin/pg_resetwal -f $data_dir" 2>/dev/null || true
      fi
      sleep 1
    fi
    
    service postgresql start 2>&1 || pg_ctlcluster "$(find_pg_version)" main start 2>&1 || true
    sleep 2
    if [ -n "$wait_timeout" ]; then
      wait_for_ready
    else
      # Default wait
      saved_timeout="$wait_timeout"
      wait_timeout=30
      wait_for_ready
      wait_timeout="$saved_timeout"
    fi
    echo "=== PostgreSQL Status ==="
    pg_isready -h localhost ${port:+-p $port} 2>&1 || echo "PostgreSQL is NOT running on port ${port:-5432}"
    pg_lsclusters 2>/dev/null | head -5
    # Check TCP listeners (not all postgres instances listen on TCP)
    if command -v ss >/dev/null 2>&1; then
      ss -tlnp 2>/dev/null | grep -E "5432|15432" || true
    elif command -v netstat >/dev/null 2>&1; then
      netstat -tlnp 2>/dev/null | grep -E "5432|15432" || true
    fi
    if pg_isready -h localhost ${port:+-p $port} >/dev/null 2>&1; then
      echo "PostgreSQL listening on port ${port:-5432} (TCP/socket)"
    else
      echo "PostgreSQL (process-level only - check socket)"
    fi
    ;;
  stop)
    echo "Stopping PostgreSQL..."
    if [ "$force" = true ]; then
      pkill -9 postgres 2>/dev/null || true
      sleep 1
    fi
    service postgresql stop 2>&1 || pg_ctlcluster "$(find_pg_version)" main stop 2>&1 || true
    sleep 1
    if pg_isready -h localhost ${port:+-p $port} 2>/dev/null; then
      echo "Warning: PostgreSQL still running"
    else
      echo "PostgreSQL stopped"
    fi
    ;;
  restart)
    echo "Restarting PostgreSQL..."
    if [ "$force" = true ]; then
      pkill -9 postgres 2>/dev/null || true
      sleep 1
    fi
    service postgresql restart 2>&1 || pg_ctlcluster "$(find_pg_version)" main restart 2>&1 || true
    sleep 2
    if [ -n "$wait_timeout" ]; then
      wait_for_ready
    fi
    pg_isready -h localhost ${port:+-p $port} 2>&1
    ;;
  recreate)
    echo "Recreating PostgreSQL cluster..."
    pg_ver=$(find_pg_version)
    echo "Stopping and dropping cluster $pg_ver/main..."
    pg_dropcluster "$pg_ver" main --stop 2>/dev/null || pg_ctlcluster "$pg_ver" main stop 2>/dev/null || true
    sleep 1
    # Kill any remaining postgres processes
    pkill -9 postgres 2>/dev/null || true
    sleep 1
    echo "Creating fresh cluster $pg_ver/main..."
    pg_createcluster "$pg_ver" main --start 2>&1
    sleep 2
    if pg_isready -h localhost ${port:+-p $port} 2>/dev/null; then
      echo "PostgreSQL cluster recreated and ready on port ${port:-5432}"
    else
      echo "Cluster created, waiting for readiness..."
      if [ -n "$wait_timeout" ]; then
        wait_for_ready
      else
        # Default wait after recreate
        saved_timeout="$wait_timeout"
        wait_timeout=60
        wait_for_ready
        wait_timeout="$saved_timeout"
      fi
    fi
    ;;
  status|check)
    echo "=== PostgreSQL Status ==="
    pg_isready -h localhost ${port:+-p $port} 2>&1 || echo "PostgreSQL is NOT running on port ${port:-5432}"
    echo ""
    pg_lsclusters 2>/dev/null || echo "pg_lsclusters not available"
    echo ""
    # If --wait is specified with status/check, wait for PostgreSQL to become ready
    if [ -n "$wait_timeout" ]; then
      wait_for_ready
    fi
    # Check TCP listeners (not all postgres instances listen on TCP)
    if command -v ss >/dev/null 2>&1; then
      ss -tlnp 2>/dev/null | grep -E "5432|15432" || true
    elif command -v netstat >/dev/null 2>&1; then
      netstat -tlnp 2>/dev/null | grep -E "5432|15432" || true
    fi
    if pg_isready -h localhost ${port:+-p $port} >/dev/null 2>&1; then
      echo "PostgreSQL listening on port ${port:-5432} (TCP/socket)"
    else
      echo "PostgreSQL (process-level only - check socket)"
    fi
    ;;
esac

# Handle --trust-auth (set all auth methods to trust in pg_hba.conf and restart)
if [ "$trust_auth" = true ]; then
  echo "=== Setting trust authentication in pg_hba.conf ==="
  pg_config_dir=$(find_pg_config_dir)
  if [ -z "$pg_config_dir" ]; then
    echo "Error: Cannot find PostgreSQL config directory" >&2
    exit 1
  fi
  hba_file="$pg_config_dir/pg_hba.conf"
  if [ ! -f "$hba_file" ]; then
    echo "Error: pg_hba.conf not found at $hba_file" >&2
    exit 1
  fi
  # Replace all auth methods with trust for local and host connections
  # Keep comments and empty lines intact
  sed -i 's/\(^local\s\+all\s\+all\s\+\)[^#]*/\1trust/' "$hba_file"
  sed -i 's/\(^local\s\+all\s\+postgres\s\+\)[^#]*/\1trust/' "$hba_file"
  sed -i 's/\(^host\s\+all\s\+all\s\+127\.0\.0\.1\/32\s\+\)[^#]*/\1trust/' "$hba_file"
  sed -i 's/\(^host\s\+all\s\+all\s\+::1\/128\s\+\)[^#]*/\1trust/' "$hba_file"
  sed -i 's/\(^host\s\+all\s\+all\s\+0\.0\.0\.0\/0\s\+\)[^#]*/\1trust/' "$hba_file"
  # Also add trust entries for common patterns if not present
  if ! grep -q "^host\s\+all\s\+all\s\+127.0.0.1/32\s\+trust" "$hba_file" 2>/dev/null; then
    echo "host    all             all             127.0.0.1/32            trust" >> "$hba_file"
  fi
  if ! grep -q "^host\s\+all\s\+all\s\+::1/128\s\+trust" "$hba_file" 2>/dev/null; then
    echo "host    all             all             ::1/128                 trust" >> "$hba_file"
  fi
  echo "pg_hba.conf updated to use trust authentication"
  # Restart PostgreSQL to apply changes
  echo "Restarting PostgreSQL..."
  service postgresql restart 2>&1 || pg_ctlcluster "$(find_pg_version)" main restart 2>&1 || true
  sleep 2
  if [ -n "$wait_timeout" ]; then
    wait_for_ready
  else
    # Default wait
    saved_timeout="$wait_timeout"
    wait_timeout=30
    wait_for_ready
    wait_timeout="$saved_timeout"
  fi
  pg_isready -h localhost ${port:+-p $port} 2>&1
fi

# Handle --reset-auth (revert trust to scram-sha-256 in pg_hba.conf and restart)
if [ "$reset_auth" = true ]; then
  echo "=== Resetting authentication from trust to scram-sha-256 in pg_hba.conf ==="
  pg_config_dir=$(find_pg_config_dir)
  if [ -z "$pg_config_dir" ]; then
    echo "Error: Cannot find PostgreSQL config directory" >&2
    exit 1
  fi
  hba_file="$pg_config_dir/pg_hba.conf"
  if [ ! -f "$hba_file" ]; then
    echo "Error: pg_hba.conf not found at $hba_file" >&2
    exit 1
  fi
  # Replace trust with scram-sha-256 for local and host connections
  sed -i 's/^local\s\+all\s\+all\s\+trust/local   all             all             scram-sha-256/' "$hba_file"
  sed -i 's/^local\s\+all\s\+postgres\s\+trust/local   all             postgres         scram-sha-256/' "$hba_file"
  sed -i 's/^host\s\+all\s\+all\s\+127\.0\.0\.1\/32\s\+trust/host    all             all             127.0.0.1\/32            scram-sha-256/' "$hba_file"
  sed -i 's/^host\s\+all\s\+all\s\+::1\/128\s\+trust/host    all             all             ::1\/128                 scram-sha-256/' "$hba_file"
  sed -i 's/^host\s\+all\s\+all\s\+0\.0\.0\.0\/0\s\+trust/host    all             all             0.0.0.0\/0                 scram-sha-256/' "$hba_file"
  echo "pg_hba.conf updated: trust replaced with scram-sha-256"
  # Restart PostgreSQL to apply changes
  echo "Restarting PostgreSQL..."
  service postgresql restart 2>&1 || pg_ctlcluster "$(find_pg_version)" main restart 2>&1 || true
  sleep 2
  if [ -n "$wait_timeout" ]; then
    wait_for_ready
  else
    saved_timeout="$wait_timeout"
    wait_timeout=30
    wait_for_ready
    wait_timeout="$saved_timeout"
  fi
  pg_isready -h localhost ${port:+-p $port} 2>&1
fi


# Handle --check-process

if [ "$check_process" = true ]; then
  echo ""
  echo "=== PostgreSQL Processes ==="
  ps aux | grep postgres | grep -v grep || echo "No postgres processes found"
fi

# Handle --log
if [ -n "$log_lines" ]; then
  echo ""
  echo "=== PostgreSQL Log (last $log_lines lines) ==="
  pg_ver=$(find_pg_version)
  log_file="/var/log/postgresql/postgresql-${pg_ver}-main.log"
  if [ -f "$log_file" ]; then
    tail -"$log_lines" "$log_file" 2>/dev/null || echo "Cannot read log file"
  else
    # Try alternative log locations
    for alt in /var/log/postgresql/*.log /var/log/postgresql/postgresql-*.log; do
      [ -f "$alt" ] && { tail -"$log_lines" "$alt" 2>/dev/null; break; }
    done || echo "No PostgreSQL log found"
  fi
fi

# Handle --list-dbs
if [ "$list_dbs" = true ]; then
  echo ""
  echo "=== Databases ==="
  exec_psql "SELECT datname FROM pg_database WHERE datistemplate = false ORDER BY datname;"
fi

# Handle --list-users
if [ "$list_users" = true ]; then
  echo ""
  echo "=== Users/Roles ==="
  exec_psql "SELECT rolname, rolsuper, rolcanlogin FROM pg_roles ORDER BY rolname;"
fi

# Handle --list-config (show postgres config like port)
if [ "$list_config" = true ]; then
  echo ""
  echo "=== Config ==="
  exec_psql "SELECT name, setting, unit FROM pg_settings WHERE name IN ('port', 'listen_addresses', 'max_connections', 'shared_buffers') ORDER BY name;"
fi

# Handle --psql=QUERY (inline, from -f FILE, or stdin pipe, or positional arg)
if [ -n "$psql_query" ]; then
  echo ""
  echo "=== Query Result ==="
  exec_psql "$psql_query" "$db"
fi

# Handle -f FILE (read SQL from file)
if [ -n "$psql_file" ]; then
  if [ ! -f "$psql_file" ]; then
    echo "Error: file "$psql_file" not found" >&2
    exit 1
  fi
  query_content=$(cat "$psql_file")
  echo ""
  echo "=== Query Result (from $psql_file) ==="
  exec_psql "$query_content" "$db"
fi

# Handle stdin pipe (if no other query source and stdin is not a terminal)
if [ -z "$psql_query" ] && [ -z "$psql_file" ] && [ -z "$reset_db" ] && [ -z "$create_ext" ] && [ "$list_dbs" = false ] && [ "$list_users" = false ] && [ "$list_config" = false ] && [ ! -t 0 ]; then
  query_content=$(cat)
  if [ -n "$query_content" ]; then
    echo ""
    echo "=== Query Result (from stdin) ==="
    exec_psql "$query_content" "$db"
  fi
fi

# Handle --reset-db
if [ -n "$reset_db" ]; then
  echo ""
  exec_psql "DROP DATABASE IF EXISTS $reset_db;" "" 2>/dev/null
  exec_psql "CREATE DATABASE $reset_db OWNER $db_user;" ""
  echo "Database '$reset_db' reset"
fi

# Handle --create-ext
if [ -n "$create_ext" ]; then
  echo ""
  exec_psql "CREATE EXTENSION IF NOT EXISTS $create_ext;" "$db"
  echo "Extension '$create_ext' created"
fi

# Exit successfully if no errors
