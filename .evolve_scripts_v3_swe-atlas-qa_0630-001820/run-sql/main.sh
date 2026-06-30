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
#   --compact: Compact output (no headers, unaligned, minimal whitespace)
#   --batch: Run multiple semicolon-separated SQL statements, label each result with a short query preview, uses compact mode automatically (collapses N separate run-sql calls into 1)
#   --csv: Output in CSV format
#   --list-dbs: List all databases instead of running a query
#   --list-tables: List all tables in the database
#   --list-users: List all PostgreSQL users
#   --describe=TABLES: Describe one or more tables (comma-separated) via \d+, collapses N parallel \d+ calls into 1 step
#   SQL can also be passed as a positional argument (last non-flag arg)

db=""
db_user="${PGUSER:-postgres}"
host="${PGHOST:-localhost}"
port="${PGPORT:-5432}"
no_header=false
compact_mode=false
batch_mode=false
csv_mode=false
list_dbs=false
list_tables=false
list_users=false
describe_tables=""
positional_sql=""

for arg in "$@"; do
  case "$arg" in
    --db=*) db="${arg#*=}" ;;
    --user=*) db_user="${arg#*=}" ;;
    --host=*) host="${arg#*=}" ;;
    --port=*) port="${arg#*=}" ;;
    --no-header) no_header=true ;;
    --compact) compact_mode=true ;;
    --batch) batch_mode=true ;;
    --csv) csv_mode=true ;;
    --list-dbs) list_dbs=true ;;
    --list-tables) list_tables=true ;;
    --list-users) list_users=true ;;
    --describe=*) describe_tables="${arg#*=}" ;;
    --help|-h)
      echo "Usage: run-sql/main.sh --db=DATABASE [--user=USER] [--host=HOST] [--port=PORT] [--no-header] [--compact] [--batch] [--csv] \"SQL_QUERY\""
      echo "   or: echo \"SQL_QUERY\" | run-sql/main.sh --db=DATABASE"
      echo "   or: run-sql/main.sh --list-dbs"
      echo "   or: run-sql/main.sh --db=DATABASE --list-tables"
      echo "   or: run-sql/main.sh --db=DATABASE --describe=TABLES (comma-separated)"
      exit 0
      ;;
    *) positional_sql="$arg" ;;
  esac
done

# Build psql args
batch_compact=false
if [ "$batch_mode" = true ]; then
  batch_compact=true
fi

psql_cmd="psql"

if [ "$no_header" = true ] || [ "$compact_mode" = true ] || [ "$batch_compact" = true ]; then
  psql_cmd="$psql_cmd -t"
fi

if [ "$compact_mode" = true ] || [ "$batch_compact" = true ]; then
  psql_cmd="$psql_cmd -A"
fi

if [ "$csv_mode" = true ]; then
  psql_cmd="$psql_cmd --csv"
fi

# Common connection args
conn_args="-h $host -p $port -U $db_user"

# Function to run a single query
run_query() {
  local label="$1"
  local query="$2"
  if [ -n "$label" ]; then
    echo "[$label]"
  fi
  PGPASSWORD="" $psql_cmd $conn_args -d "$db" -c "$query" 2>&1
  local rc=$?
  echo ""
  return $rc
}

# Handle --describe: run \d+ for each table in the comma-separated list
if [ -n "$describe_tables" ]; then
  if [ -z "$db" ]; then
    echo "Error: --db is required for --describe" >&2
    exit 1
  fi
  IFS=',' read -ra tables <<< "$describe_tables"
  for table in "${tables[@]}"; do
    table=$(echo "$table" | xargs)  # trim whitespace
    if [ -n "$table" ]; then
      echo "=== $table ==="
      PGPASSWORD="" $psql_cmd $conn_args -d "$db" -c "\d+ $table" 2>&1
      echo ""
    fi
  done
  exit $?
fi

# Handle list operations
if [ "$list_dbs" = true ]; then
  if [ "$batch_compact" = true ]; then
    PGPASSWORD="" $psql_cmd $conn_args -d postgres -c "SELECT datname FROM pg_database WHERE datistemplate = false ORDER BY datname;" 2>&1
  else
    PGPASSWORD="" psql $conn_args -d postgres -c "SELECT datname FROM pg_database WHERE datistemplate = false ORDER BY datname;" 2>&1
  fi
  exit $?
fi

if [ "$list_users" = true ]; then
  if [ "$batch_compact" = true ]; then
    PGPASSWORD="" $psql_cmd $conn_args -d postgres -c "SELECT rolname, rolsuper, rolcanlogin FROM pg_roles ORDER BY rolname;" 2>&1
  else
    PGPASSWORD="" psql $conn_args -d postgres -c "SELECT rolname, rolsuper, rolcanlogin FROM pg_roles ORDER BY rolname;" 2>&1
  fi
  exit $?
fi

if [ "$list_tables" = true ]; then
  if [ -z "$db" ]; then
    echo "Error: --db is required for --list-tables" >&2
    exit 1
  fi
  if [ "$batch_compact" = true ]; then
    PGPASSWORD="" $psql_cmd $conn_args -d "$db" -c "SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname = 'public' ORDER BY tablename;" 2>&1
  else
    PGPASSWORD="" psql $conn_args -d "$db" -c "\dt" 2>&1
  fi
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

# --batch mode: split on semicolons, run each query separately with labels
if [ "$batch_mode" = true ]; then
  # Write SQL to temp file, then read line by line collecting statements
  tmpfile=$(mktemp /tmp/runsql_batch_XXXXXX)
  printf '%s\n' "$sql" > "$tmpfile"
  
  overall_rc=0
  current_stmt=""
  stmt_num=0
  
  # Read the SQL, split by semicolons
  while IFS= read -r line || [ -n "$line" ]; do
    trimmed=$(echo "$line" | xargs)
    # Skip empty lines
    [ -z "$trimmed" ] && continue
    # Skip comment lines
    [[ "$trimmed" == --* ]] && continue
    
    current_stmt="${current_stmt}${line} "
    
    # Check if line ends with semicolon (statement boundary)
    if [[ "$line" == *\; ]]; then
      # Remove trailing semicolons and whitespace
      clean_stmt=$(echo "$current_stmt" | sed 's/;[[:space:]]*$//' | xargs)
      [ -n "$clean_stmt" ] || { current_stmt=""; continue; }
      
      stmt_num=$((stmt_num + 1))
      # Build a short preview label: first 60 chars of the statement
      preview="${clean_stmt:0:60}"
      run_query "$preview" "$clean_stmt"
      rc=$?
      [ $rc -ne 0 ] && overall_rc=$rc
      current_stmt=""
    fi
  done < "$tmpfile"
  
  # Handle any trailing statement without a semicolon
  if [ -n "$(echo "$current_stmt" | xargs)" ]; then
    clean_stmt=$(echo "$current_stmt" | xargs)
    stmt_num=$((stmt_num + 1))
    preview="${clean_stmt:0:60}"
    run_query "$preview" "$clean_stmt"
    rc=$?
    [ $rc -ne 0 ] && overall_rc=$rc
  fi
  
  rm -f "$tmpfile"
  exit $overall_rc
fi

# Execute the query (single SQL)
PGPASSWORD="" $psql_cmd $conn_args -d "$db" -c "$sql" 2>&1
