#!/usr/bin/env bash
set -euo pipefail

# ensure_services - Check and start common services (PostgreSQL, Redis, MySQL, etc.), or reset stuck services
# Usage: ensure_services [--reset] [--reinit] [--check-logs] [service1 service2 ...]
#   If no services specified, checks all known services.
#   Known services: postgresql, redis, mysql, mongodb, elasticsearch
#   --reset: Force-clean and restart the specified services (kill processes, clean shared memory, remove pid/lock files)
#   --reinit: Re-initialize PostgreSQL data directory with initdb (for corrupted data dirs); implies --reset
#   --check-logs: After checking/starting PostgreSQL, if not running, show last 30 log lines and process status
#
# Examples:
#   ensure_services postgresql redis
#   ensure_services --reset postgresql
#   ensure_services --reinit postgresql
#   ensure_services --check-logs postgresql

KNOWN_SERVICES="postgresql redis mysql mongodb elasticsearch"
RESET_MODE=false
REINIT_MODE=false
CHECK_LOGS=false
SERVICES=()

# Parse args
while [[ $# -gt 0 ]]; do
    case "$1" in
        --reset)
            RESET_MODE=true
            shift
            ;;
        --reinit)
            REINIT_MODE=true
            shift
            ;;
        --check-logs)
            CHECK_LOGS=true
            shift
            ;;
        -*)
            echo "Unknown option: $1" >&2
            exit 1
            ;;
        *)
            SERVICES+=("$1")
            shift
            ;;
    esac
done

if [[ ${#SERVICES[@]} -eq 0 ]]; then
    # Default to all known services
    for svc in $KNOWN_SERVICES; do
        SERVICES+=("$svc")
    done
fi

check_postgresql_logs() {
    local pg_log="/var/log/postgresql/postgresql-15-main.log"
    if [[ -f "$pg_log" ]]; then
        echo "=== PostgreSQL log (last 30 lines) ==="
        tail -30 "$pg_log" 2>/dev/null || echo "(could not read log)"
    else
        # Try other versions
        for ver in 16 14 13 12 11 10; do
            pg_log="/var/log/postgresql/postgresql-${ver}-main.log"
            if [[ -f "$pg_log" ]]; then
                echo "=== PostgreSQL log (last 30 lines) ==="
                tail -30 "$pg_log" 2>/dev/null || echo "(could not read log)"
                break
            fi
        done
    fi
    echo
    echo "=== PostgreSQL processes ==="
    ps aux 2>/dev/null | grep -E 'postgres|postmaster' | grep -v grep || echo "(no postgres processes found)"
    echo
    echo "=== Shared memory segments ==="
    ipcs -m 2>/dev/null | grep -v "^$\|key\|------" | tail -20 || echo "(no shared memory info)"
}

reset_postgresql() {
    echo "Resetting PostgreSQL..."
    # Kill all postgres processes
    pkill -9 postgres 2>/dev/null || true
    pkill -9 postmaster 2>/dev/null || true
    sleep 1
    # Clean shared memory
    for key in $(ipcs -m 2>/dev/null | grep -v "^$" | tail -n +4 | awk '{print $1}'); do
        ipcrm -M "$key" 2>/dev/null || true
    done
    # Remove pid and lock files
    rm -f /var/run/postgresql/.s.PGSQL.5432 /var/run/postgresql/.s.PGSQL.5432.lock 2>/dev/null || true
    # Try known pg versions
    for ver in 15 16 14 13 12 11 10; do
        local pg_data="/var/lib/postgresql/${ver}/main"
        if [[ -f "${pg_data}/postmaster.pid" ]]; then
            rm -f "${pg_data}/postmaster.pid" 2>/dev/null || true
        fi
        if [[ -d "$pg_data" ]] && command -v pg_resetwal >/dev/null 2>&1; then
            su -s /bin/bash -c "/usr/lib/postgresql/${ver}/bin/pg_resetwal -f ${pg_data}" postgres 2>/dev/null || true
        fi
    done
    sleep 1
    echo "PostgreSQL reset complete. Starting fresh..."
    if start_postgresql; then
        return 0
    fi
    # If normal start fails, try re-initializing the data directory with initdb
    echo "Normal start failed, trying initdb re-initialization..."
    for ver in 15 16 14 13 12 11 10; do
        local pg_data="/var/lib/postgresql/${ver}/main"
        local pg_bin="/usr/lib/postgresql/${ver}/bin"
        if [[ -d "$pg_data" ]] && [[ -x "${pg_bin}/initdb" ]]; then
            echo "Re-initializing PostgreSQL ${ver} data directory with initdb..."
            rm -rf "${pg_data:?}"/* "${pg_data:?}"/.* 2>/dev/null || true
            rm -f "/var/log/postgresql/postgresql-${ver}-main.log" 2>/dev/null || true
            if su -s /bin/bash -c "${pg_bin}/initdb -D ${pg_data} --locale=C.UTF-8 --encoding=UTF8" postgres 2>/dev/null; then
                echo "initdb successful for PostgreSQL ${ver}. Starting..."
                if su -s /bin/bash -c "${pg_bin}/pg_ctl start -D ${pg_data} -l /var/log/postgresql/postgresql-${ver}-main.log -o '-c config_file=/etc/postgresql/${ver}/main/postgresql.conf -c listen_addresses=localhost' -w -t 30" postgres 2>/dev/null; then
                    sleep 2
                    echo "PostgreSQL ${ver} started successfully after initdb"
                    return 0
                fi
            fi
        fi
    done
    echo "Warning: could not start PostgreSQL even after initdb" >&2
    return 1
}


reinit_postgresql() {
    echo "Re-initializing PostgreSQL data directory with initdb..."
    # Kill all postgres processes
    pkill -9 postgres 2>/dev/null || true
    pkill -9 postmaster 2>/dev/null || true
    sleep 1
    # Clean shared memory
    for key in $(ipcs -m 2>/dev/null | grep -v "^$" | tail -n +4 | awk '{print $1}'); do
        ipcrm -M "$key" 2>/dev/null || true
    done
    # Try known pg versions
    for ver in 15 16 14 13 12 11 10; do
        local pg_data="/var/lib/postgresql/${ver}/main"
        local pg_bin="/usr/lib/postgresql/${ver}/bin"
        if [[ -d "$pg_data" ]] && [[ -x "${pg_bin}/initdb" ]]; then
            echo "Re-initializing PostgreSQL ${ver} with initdb..."
            # Remove pid and lock files
            rm -f /var/run/postgresql/.s.PGSQL.5432 /var/run/postgresql/.s.PGSQL.5432.lock 2>/dev/null || true
            rm -f "${pg_data}/postmaster.pid" 2>/dev/null || true
            # Clean data directory
            rm -rf "${pg_data:?}"/* "${pg_data:?}"/.[!.]* 2>/dev/null || true
            rm -f "/var/log/postgresql/postgresql-${ver}-main.log" 2>/dev/null || true
            # Run initdb
            if su -s /bin/bash -c "${pg_bin}/initdb -D ${pg_data} --locale=C.UTF-8 --encoding=UTF8" postgres 2>/dev/null; then
                echo "initdb successful for PostgreSQL ${ver}. Starting..."
                if su -s /bin/bash -c "${pg_bin}/pg_ctl start -D ${pg_data} -l /var/log/postgresql/postgresql-${ver}-main.log -o '-c config_file=/etc/postgresql/${ver}/main/postgresql.conf -c listen_addresses=localhost' -w -t 30" postgres 2>/dev/null; then
                    sleep 2
                    echo "PostgreSQL ${ver} started successfully after initdb"
                    return 0
                fi
            fi
            echo "Warning: initdb failed for PostgreSQL ${ver}" >&2
        fi
    done
    echo "Warning: could not re-initialize PostgreSQL with initdb" >&2
    return 1
}

reset_redis() {
    echo "Resetting Redis..."
    pkill -9 redis-server 2>/dev/null || true
    sleep 1
    rm -f /var/run/redis/redis-server.sock /var/run/redis.pid 2>/dev/null || true
    sleep 1
    echo "Redis reset complete. Starting fresh..."
    start_redis
}

start_postgresql() {
    if command -v pg_isready >/dev/null 2>&1 && pg_isready -h localhost -p 5432 >/dev/null 2>&1; then
        echo "PostgreSQL already running on localhost:5432"
        return 0
    fi
    echo "Starting PostgreSQL..."
    if command -v service >/dev/null 2>&1; then
        service postgresql start 2>/dev/null && sleep 2 && return 0
    fi
    # Try pg_ctl
    local pg_versions="15 16 14 13 12 11 10"
    for ver in $pg_versions; do
        local pg_data="/var/lib/postgresql/${ver}/main"
        if [[ -d "$pg_data" ]]; then
            if command -v pg_ctl >/dev/null 2>&1; then
                su -s /bin/bash -c "/usr/lib/postgresql/${ver}/bin/pg_ctl start -D ${pg_data} -l /var/log/postgresql/postgresql-${ver}-main.log -w -t 300" postgres 2>/dev/null && sleep 2 && return 0
            fi
        fi
    done
    echo "Warning: could not start PostgreSQL" >&2
    return 1
}

start_redis() {
    if command -v redis-cli >/dev/null 2>&1 && redis-cli ping >/dev/null 2>&1; then
        echo "Redis already running (PONG)"
        return 0
    fi
    echo "Starting Redis..."
    if command -v service >/dev/null 2>&1; then
        service redis-server start 2>/dev/null && sleep 1 && return 0
    fi
    if command -v redis-server >/dev/null 2>&1; then
        redis-server --daemonize yes 2>/dev/null && sleep 1 && return 0
    fi
    echo "Warning: could not start Redis" >&2
    return 1
}

start_mysql() {
    if command -v mysqladmin >/dev/null 2>&1 && mysqladmin ping -h localhost >/dev/null 2>&1; then
        echo "MySQL already running"
        return 0
    fi
    echo "Starting MySQL..."
    if command -v service >/dev/null 2>&1; then
        service mysql start 2>/dev/null && sleep 2 && return 0
    fi
    if command -v mysqld >/dev/null 2>&1; then
        mysqld --daemonize 2>/dev/null && sleep 2 && return 0
    fi
    echo "Warning: could not start MySQL" >&2
    return 1
}

start_mongodb() {
    if command -v mongosh >/dev/null 2>&1; then
        mongosh --eval "db.runCommand({ping:1})" --quiet >/dev/null 2>&1 && echo "MongoDB already running" && return 0
    elif command -v mongo >/dev/null 2>&1; then
        mongo --eval "db.runCommand({ping:1})" --quiet >/dev/null 2>&1 && echo "MongoDB already running" && return 0
    fi
    echo "Starting MongoDB..."
    if command -v service >/dev/null 2>&1; then
        service mongod start 2>/dev/null && sleep 2 && return 0
    fi
    if command -v mongod >/dev/null 2>&1; then
        mongod --fork --logpath /var/log/mongodb/mongod.log 2>/dev/null && sleep 2 && return 0
    fi
    echo "Warning: could not start MongoDB" >&2
    return 1
}

start_elasticsearch() {
    if curl -s http://localhost:9200 >/dev/null 2>&1; then
        echo "Elasticsearch already running"
        return 0
    fi
    echo "Starting Elasticsearch..."
    if command -v service >/dev/null 2>&1; then
        service elasticsearch start 2>/dev/null && sleep 5 && return 0
    fi
    echo "Warning: could not start Elasticsearch" >&2
    return 1
}

# Main
for svc in "${SERVICES[@]}"; do
    case "$svc" in
        postgresql|postgres|pg)
            if $REINIT_MODE; then
                reinit_postgresql || true
            elif $RESET_MODE; then
                reset_postgresql || true
            else
                start_postgresql || true
            fi
            if $CHECK_LOGS; then
                # Even if start succeeded, check if we can connect
                if command -v pg_isready >/dev/null 2>&1 && ! pg_isready -h localhost -p 5432 >/dev/null 2>&1; then
                    check_postgresql_logs
                fi
            fi
            ;;
        redis|redis-server)
            if $RESET_MODE; then
                reset_redis || true
            else
                start_redis || true
            fi
            ;;
        mysql|mariadb)
            if $RESET_MODE; then
                echo "Resetting MySQL..."
                pkill -9 mysql 2>/dev/null || true
                sleep 1
                start_mysql
            else
                start_mysql || true
            fi
            ;;
        mongodb|mongo)
            start_mongodb || true
            ;;
        elasticsearch|es)
            start_elasticsearch || true
            ;;
        *)
            echo "Unknown service: $svc (known: $KNOWN_SERVICES)" >&2
            ;;
    esac
done
