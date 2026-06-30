#!/usr/bin/env bash
set -euo pipefail

# psql - Run PostgreSQL SQL queries with simplified connection string boilerplate
# Usage: psql [options] -c "SQL" [--head=N] [--grep=PATTERN] [--quiet]
#   or: psql [options] -f script.sql
#   or: echo "SELECT 1" | psql [options]
#
# Options:
#   --db=NAME, -d NAME    Database name (default: test)
#   --user=NAME, -U NAME  Username (default: test)
#   --pass=PASS, -w PASS  Password (default: test)
#   --host=HOST, -h HOST  Hostname (default: localhost)
#   --port=PORT, -p PORT  Port (default: 5432)
#   -c "SQL"              SQL command to run
#   -f script.sql         Run SQL from file
#   --head=N              Limit output to first N lines (like piping through head -N)
#   --grep=PATTERN        Filter output lines matching extended regex
#   --quiet, -q           Do not redirect stderr to /dev/null (show errors)
#   --                    Remaining args passed through to psql
#
# Examples:
#   psql -c "\\dt"
#   psql -c "SELECT * FROM users;" --head=20
#   psql --db=mydb --user=admin --pass=secret -c "SELECT count(*) FROM users;"
#   psql -c "\\l" --grep=template
#   psql -f /app/init.sql

DB="test"
USER="test"
PASS="test"
HOST="localhost"
PORT="5432"
SQL=""
SQL_FILE=""
HEAD_N=""
GREP_PATTERN=""
QUIET=false
HAS_DASH_DASH=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --db=*)
            DB="${1#*=}"
            shift
            ;;
        -d)
            DB="$2"
            shift 2
            ;;
        --user=*)
            USER="${1#*=}"
            shift
            ;;
        -U)
            USER="$2"
            shift 2
            ;;
        --pass=*)
            PASS="${1#*=}"
            shift
            ;;
        -w)
            PASS="$2"
            shift 2
            ;;
        --host=*)
            HOST="${1#*=}"
            shift
            ;;
        -h)
            HOST="$2"
            shift 2
            ;;
        --port=*)
            PORT="${1#*=}"
            shift
            ;;
        -p)
            PORT="$2"
            shift 2
            ;;
        -c)
            SQL="$2"
            shift 2
            ;;
        -f)
            SQL_FILE="$2"
            shift 2
            ;;
        --head=*)
            HEAD_N="${1#*=}"
            shift
            ;;
        --grep=*)
            GREP_PATTERN="${1#*=}"
            shift
            ;;
        --quiet|-q)
            QUIET=true
            shift
            ;;
        --)
            HAS_DASH_DASH=true
            shift
            break
            ;;
        *)
            echo "Unknown option: $1" >&2
            echo "Usage: psql [--db=NAME] [--user=USER] [--pass=PASS] [--host=HOST] [--port=PORT] -c \"SQL\"" >&2
            exit 1
            ;;
    esac
done

# Build psql args array
PSQL_ARGS=(-U "$USER" -h "$HOST" -p "$PORT" -d "$DB")

# Add passthrough args after --
if $HAS_DASH_DASH && [[ $# -gt 0 ]]; then
    PSQL_ARGS+=("$@")
fi

# Determine SQL source
if [[ -n "$SQL" ]]; then
    PSQL_ARGS+=(-c "$SQL")
elif [[ -n "$SQL_FILE" ]]; then
    PSQL_ARGS+=(-f "$SQL_FILE")
elif [[ ! -t 0 ]]; then
    # stdin has data (pipe/heredoc) - no extra args needed
    :
else
    echo "Error: no SQL query provided. Use -c \"SQL\" or -f script.sql or pipe input." >&2
    exit 1
fi

# Build filter pipeline
FILTER_CMD="cat"
if [[ -n "$GREP_PATTERN" && -n "$HEAD_N" ]]; then
    FILTER_CMD="grep -E \"$GREP_PATTERN\" | head -n $HEAD_N"
elif [[ -n "$GREP_PATTERN" ]]; then
    FILTER_CMD="grep -E \"$GREP_PATTERN\""
elif [[ -n "$HEAD_N" ]]; then
    FILTER_CMD="head -n $HEAD_N"
fi

# Run psql with env var for password
export PGPASSWORD="$PASS"
if $QUIET; then
    psql "${PSQL_ARGS[@]}" 2>/dev/null | eval "$FILTER_CMD"
else
    psql "${PSQL_ARGS[@]}" 2>&1 | eval "$FILTER_CMD"
fi
