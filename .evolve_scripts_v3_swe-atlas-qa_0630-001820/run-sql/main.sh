#!/bin/bash
# Run a SQL query against PostgreSQL and output results concisely.
# Usage: run-sql/main.sh --db=DATABASE "SQL_QUERY"
#   or:  echo "SQL_QUERY" | run-sql/main.sh --db=DATABASE
#   or:  run-sql/main.sh --db=DATABASE < query.sql
#   --db=DB: Database name (required unless using --list-dbs or --help)
#   --user=USER: PostgreSQL user (default: uses env PGUSER or postgres)
#   --host=HOST: PostgreSQL host (default: localhost)
#   --port=PORT: PostgreSQL port (default: 5432)
#   --no-header: Suppress column headers in output
#   --csv: Output in CSV format
#   --list-dbs: List all databases instead of running a query
#   --list-tables: List all tables in the database
#   --list-users: List all PostgreSQL users
#   SQL can also be passed as a positional argument (last non-flag arg)

db=""
db_user="${PGUSER:-postgres}"
host="${PGHOST:-localhost}"
port="${PGPORT:-5432}"
no_header=false
csv_mode=false
list_dbs=false
list_tables=false
list_users=false
positional_sql=""

for arg in "$@"; do
  case "$arg" in
    --db=*) db="${arg#*=}" ;;
    --user=*) db_user="${arg#*=}" ;;
    --host=*) host="${arg#*=}" ;;
    --port=*) port="${arg#*=}" ;;
    --no-header) no_header=true ;;
    --csv) csv_mode=true ;;
    --list-dbs) list_dbs=true ;;
    --list-tables) list_tables=true ;;
    --list-users) list_users=true ;;
    --help|-h)
      echo "Usage: run-sql/main.sh --db=DATABASE [--user=USER] [--host=HOST] [--port=PORT] [--no-header] [--csv] \"SQL_QUERY\""
      echo "   or: echo \"SQL_QUERY\" | run-sql/main.sh --db=DATABASE"
      echo "   or: run-sql/main.sh --list-dbs"
      echo "   or: run-sql/main.sh --db=DATABASE --list-tables"
      exit 0
      ;;
    *) positional_sql="$arg" ;;
  esac
done

# Build psql args
psql_cmd="psql"

if [ "$no_header" = true ]; then
  psql_cmd="$psql_cmd -t"
fi

if [ "$csv_mode" = true ]; then
  psql_cmd="$psql_cmd --csv"
fi

# Handle list operations
if [ "$list_dbs" = true ]; then
  PGPASSWORD="" $psql_cmd -h "$host" -p "$port" -U "$db_user" -d postgres -c "SELECT datname FROM pg_database WHERE datistemplate = false ORDER BY datname;" 2>&1
  exit $?
fi

if [ "$list_users" = true ]; then
  PGPASSWORD="" $psql_cmd -h "$host" -p "$port" -U "$db_user" -d postgres -c "SELECT rolname, rolsuper, rolcanlogin FROM pg_roles ORDER BY rolname;" 2>&1
  exit $?
fi

if [ "$list_tables" = true ]; then
  if [ -z "$db" ]; then
    echo "Error: --db is required for --list-tables" >&2
    exit 1
  fi
  PGPASSWORD="" $psql_cmd -h "$host" -p "$port" -U "$db_user" -d "$db" -c "\dt" 2>&1
  exit $?
fi

# Get the SQL query: from positional arg, stdin pipe, or file redirect
sql=""
if [ -n "$positional_sql" ]; then
  sql="$positional_sql"
elif [ ! -t 0 ]; then
  sql=$(cat)
fi

if [ -z "$sql" ]; then
  echo "Error: No SQL query provided" >&2
  echo "Usage: run-sql/main.sh --db=DATABASE \"SQL_QUERY\"" >&2
  exit 1
fi

if [ -z "$db" ]; then
  echo "Error: --db is required" >&2
  echo "Usage: run-sql/main.sh --db=DATABASE \"SQL_QUERY\"" >&2
  exit 1
fi

# Execute the query
PGPASSWORD="" $psql_cmd -h "$host" -p "$port" -U "$db_user" -d "$db" -c "$sql" 2>&1
