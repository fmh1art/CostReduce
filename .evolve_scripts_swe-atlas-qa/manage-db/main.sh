#!/bin/bash
# manage-db - Manage PostgreSQL databases: start cluster, create/delete DB, run SQL, list tables, set passwords
# Usage: manage-db [options] <action> [args...]
# Options:
#   --dir=DIR       Working directory to cd into first
#   --host=HOST     PostgreSQL host (default: localhost)
#   --port=PORT     PostgreSQL port (default: 5432)
#   --user=USER     PostgreSQL user (default: postgres)
# Actions:
#   status                    Check if PostgreSQL is running
#   start [version]           Start PostgreSQL cluster (default: 15)
#   stop [version]            Stop PostgreSQL cluster
#   create-db <name>          Create a database
#   drop-db <name>            Drop a database
#   run-sql <db> <"sql">      Run SQL query against database
#   list-tables <db>          List all tables in database
#   set-password <user> <pass> Set a user's password
#   create-user <name>        Create a new user
#   list-dbs                  List all databases
#   check-connection          Test database connection with current settings
#   change-port <ver> <port>  Change PostgreSQL port and restart cluster

set -euo pipefail

DIR=""
HOST=""
PORT=""
DB_USER=""

positional=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dir=*)
            DIR="${1#*=}"
            shift
            ;;
        --host=*)
            HOST="${1#*=}"
            shift
            ;;
        --port=*)
            PORT="${1#*=}"
            shift
            ;;
        --user=*)
            DB_USER="${1#*=}"
            shift
            ;;
        --help|-h)
            sed -n '2,8p' "$0"
            exit 0
            ;;
        -*)
            echo "Unknown option: $1" >&2
            exit 1
            ;;
        *)
            positional+=("$1")
            shift
            ;;
    esac
done

set -- "${positional[@]}"

if [ $# -eq 0 ]; then
    echo "Usage: manage-db [--dir=DIR] [--host=HOST] [--port=PORT] [--user=USER] <action> [args...]" >&2
    echo "Actions: status, start [version], stop [version], create-db <name>, drop-db <name>," >&2
    echo "  run-sql <db> <\"sql\">, list-tables <db>, set-password <user> <pass>," >&2
    echo "  create-user <name>, list-dbs, check-connection" >&2
    exit 1
fi

ACTION="$1"
shift

if [ -n "$DIR" ]; then
    cd "$DIR"
fi

# Default connection settings
HOST="${HOST:-localhost}"
PORT="${PORT:-5432}"
DB_USER="${DB_USER:-postgres}"

# Find PostgreSQL tools
PSQL="$(which psql 2>/dev/null || echo '/usr/bin/psql')"
PG_CTL="$(which pg_ctlcluster 2>/dev/null || echo '/usr/bin/pg_ctlcluster')"
PG_ISREADY="$(which pg_isready 2>/dev/null || echo '/usr/bin/pg_isready')"
PG_LSCLUSTERS="$(which pg_lsclusters 2>/dev/null || echo '/usr/bin/pg_lsclusters')"

# Try to run psql: prefers direct connection if available, falls back to su - postgres
run_psql() {
    local db="$1"
    local sql="$2"
    shift 2
    
    # Try direct connection first (works when pg_hba.conf allows trust/md5)
    if "$PSQL" -h "$HOST" -p "$PORT" -U "$DB_USER" -d "$db" -c "$sql" "$@" 2>/dev/null; then
        return 0
    fi
    
    # Fall back to su - postgres (works in container/CI with peer auth on socket)
    if command -v su &>/dev/null && [ "$(whoami 2>/dev/null || echo '')" != "postgres" ]; then
        su - postgres -c "psql -d \"$db\" -c \"$sql\" $*" 2>&1
    else
        # Last attempt: try socket connection
        "$PSQL" -h /var/run/postgresql -U "$DB_USER" -d "$db" -c "$sql" "$@" 2>&1
    fi
}

run_psql_no_db() {
    local sql="$1"
    shift
    
    # Try direct connection
    if "$PSQL" -h "$HOST" -p "$PORT" -U "$DB_USER" -d postgres -c "$sql" "$@" 2>/dev/null; then
        return 0
    fi
    
    # Fall back to su - postgres
    if command -v su &>/dev/null && [ "$(whoami 2>/dev/null || echo '')" != "postgres" ]; then
        su - postgres -c "psql -c \"$sql\" $*" 2>&1
    else
        "$PSQL" -h /var/run/postgresql -U "$DB_USER" -d postgres -c "$sql" "$@" 2>&1
    fi
}

case "$ACTION" in
    status)
        "$PG_ISREADY" -h "$HOST" -p "$PORT" 2>&1 || echo "PostgreSQL is not responding on $HOST:$PORT"
        ;;
    check-connection)
        echo "Checking connection to PostgreSQL on $HOST:$PORT as user $DB_USER..."
        "$PG_ISREADY" -h "$HOST" -p "$PORT" 2>&1
        run_psql_no_db "SELECT 1 AS connected;" 2>&1 || echo "Connection failed: cannot execute query"
        ;;
    start)
        ver="${1:-15}"
        "$PG_LSCLUSTERS" 2>&1 | grep -q "$ver.*down" && {
            "$PG_CTL" "$ver" main start 2>&1 || pg_ctlcluster "$ver" main start 2>&1
        } || {
            echo "Cluster $ver main is already running or not found"
            "$PG_LSCLUSTERS" 2>&1 | head -5
        }
        ;;
    stop)
        ver="${1:-15}"
        "$PG_CTL" "$ver" main stop 2>&1 || pg_ctlcluster "$ver" main stop 2>&1
        ;;
    create-db)
        [ $# -lt 1 ] && { echo "Usage: manage-db create-db <dbname>" >&2; exit 1; }
        run_psql_no_db "CREATE DATABASE $1;"
        ;;
    drop-db)
        [ $# -lt 1 ] && { echo "Usage: manage-db drop-db <dbname>" >&2; exit 1; }
        run_psql_no_db "DROP DATABASE IF EXISTS $1;"
        ;;
    run-sql)
        [ $# -lt 2 ] && { echo "Usage: manage-db run-sql <dbname> \"<sql>\"" >&2; exit 1; }
        run_psql "$1" "$2"
        ;;
    list-tables)
        [ $# -lt 1 ] && { echo "Usage: manage-db list-tables <dbname>" >&2; exit 1; }
        run_psql "$1" "SELECT table_name FROM information_schema.tables WHERE table_schema='public' ORDER BY table_name;"
        ;;
    set-password)
        [ $# -lt 2 ] && { echo "Usage: manage-db set-password <username> <password>" >&2; exit 1; }
        run_psql_no_db "ALTER USER \"$1\" PASSWORD '$2';"
        ;;
    create-user)
        [ $# -lt 1 ] && { echo "Usage: manage-db create-user <username>" >&2; exit 1; }
        run_psql_no_db "CREATE USER $1;"
        ;;
    list-dbs)
        run_psql_no_db "SELECT datname FROM pg_database WHERE datistemplate = false ORDER BY datname;"
        ;;
    change-port)
        [ $# -lt 2 ] && { echo "Usage: manage-db change-port <version> <new_port>" >&2; exit 1; }
        local ver="$1"
        local new_port="$2"
        local conf_file="/etc/postgresql/$ver/main/postgresql.conf"
        if [ ! -f "$conf_file" ]; then
            echo "Error: config file not found: $conf_file" >&2
            exit 1
        fi
        echo "Changing PostgreSQL $ver port to $new_port..."
        sed -i "s/^port\s*=\s*[0-9]\+/port = $new_port/" "$conf_file" 2>/dev/null || \
            sed -i "s/^#port\s*=\s*[0-9]\+/port = $new_port/" "$conf_file" 2>/dev/null || \
            echo "port = $new_port" >> "$conf_file"
        echo "Restarting PostgreSQL $ver..."
        "$PG_CTL" "$ver" main restart 2>&1 || pg_ctlcluster "$ver" main restart 2>&1
        echo "Port changed to $new_port and cluster restarted."
        ;;
    reset-port)
        ver="${1:-15}"
        echo "Resetting PostgreSQL $ver port to default 5432..."
        conf_file="/etc/postgresql/$ver/main/postgresql.conf"
        if [ ! -f "$conf_file" ]; then
            echo "Error: config file not found: $conf_file" >&2
            exit 1
        fi
        sed -i "s/^port\s*=\s*[0-9]\+/port = 5432/" "$conf_file" 2>/dev/null || \
            sed -i "s/^#port\s*=\s*[0-9]\+/port = 5432/" "$conf_file" 2>/dev/null || \
            echo "port = 5432" >> "$conf_file"
        echo "Restarting PostgreSQL $ver..."
        "$PG_CTL" "$ver" main restart 2>&1 || pg_ctlcluster "$ver" main restart 2>&1
        echo "Port reset to 5432 and cluster restarted."
        ;;
        
    *)
        echo "Unknown action: $ACTION" >&2
        echo "Valid actions: status, start, stop, create-db, drop-db, run-sql, list-tables," >&2
        echo "  set-password, create-user, list-dbs, check-connection, change-port, reset-port" >&2
        exit 1
        ;;
esac
