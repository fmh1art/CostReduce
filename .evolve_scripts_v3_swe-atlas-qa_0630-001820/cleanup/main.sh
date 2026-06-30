#!/bin/bash
# Clean up temp files and database test data in one step, collapsing rm -f + psql DELETE chains.
# Usage: cleanup/main.sh [--files=GLOB]... [--all-temp] [--db=DB --tables=T1,T2 [--where=COND]] [--dry-run]
#   --files=GLOB:  Delete files matching glob (repeatable, supports /tmp/*.txt /tmp/cookies* etc.)
#   --all-temp:    Clean common temp files (/tmp/*.txt, /tmp/*.log, /tmp/*.env, /tmp/cookies*, /tmp/server_output*, /tmp/run_app*)
#   --db=DB:       Database to clean (requires --tables)
#   --tables=T1,T2: Comma-separated table names to DELETE FROM (ignores non-existent tables)
#   --where=COND:  WHERE condition for DELETE (default: no condition, deletes all rows)
#   --dry-run:     Show what would be deleted without actually deleting

files_to_rm=()
all_temp=false
db=""
tables=""
where_cond=""
dry_run=false

for arg in "$@"; do
  case "$arg" in
    --files=*) files_to_rm+=("${arg#*=}") ;;
    --all-temp) all_temp=true ;;
    --db=*) db="${arg#*=}" ;;
    --tables=*) tables="${arg#*=}" ;;
    --where=*) where_cond="${arg#*=}" ;;
    --dry-run) dry_run=true ;;
  esac
done

# ---- Temp file cleanup ----
if [ "$all_temp" = true ]; then
  files_to_rm+=("/tmp/*.txt" "/tmp/*.log" "/tmp/*.env" "/tmp/cookies*.txt" "/tmp/server_output*.log" "/tmp/run_app*.py" "/tmp/test_api_key*" "/tmp/test_run*")
fi

if [ ${#files_to_rm[@]} -gt 0 ]; then
  echo "=== Cleaning temp files ==="
  for glob in "${files_to_rm[@]}"; do
    # Expand glob
    for f in $glob; do
      if [ -f "$f" ]; then
        if [ "$dry_run" = true ]; then
          echo "[dry-run] rm -f $f"
        else
          rm -f "$f"
          echo "Deleted: $f"
        fi
      fi
    done
  done
fi

# ---- Database cleanup ----
if [ -n "$db" ] && [ -n "$tables" ]; then
  echo "=== Cleaning database: $db ==="
  IFS=',' read -ra table_list <<< "$tables"
  for table in "${table_list[@]}"; do
    table="$(echo "$table" | xargs)"  # trim whitespace
    if [ -z "$table" ]; then continue; fi
    
    # Build DELETE SQL
    if [ -n "$where_cond" ]; then
      sql="DELETE FROM $table WHERE $where_cond;"
    else
      sql="DELETE FROM $table;"
    fi
    
    if [ "$dry_run" = true ]; then
      echo "[dry-run] su - postgres -c \"psql -d $db -c '$sql'\""
    else
      # Try the SQL, ignore errors for non-existent tables
      if command -v sudo >/dev/null 2>&1; then
        result=$(sudo -u postgres psql -d "$db" -c "$sql" 2>&1)
      else
        result=$(su - postgres -c "psql -d $db -c \"$sql\"" 2>&1)
      fi
      # Check if table exists
      if echo "$result" | grep -q "relation.*does not exist"; then
        table_name=$(echo "$table" | sed 's/["\'"'"']//g')
        echo "Table '$table_name' does not exist, skipping"
      else
        echo "$result" | head -2
      fi
    fi
  done
  
  # Show remaining row counts for cleaned tables
  if [ "$dry_run" = false ]; then
    echo "--- Row counts ---"
    IFS=',' read -ra table_list <<< "$tables"
    for table in "${table_list[@]}"; do
      table="$(echo "$table" | xargs)"
      if [ -z "$table" ]; then continue; fi
      if command -v sudo >/dev/null 2>&1; then
        count=$(sudo -u postgres psql -d "$db" -t -A -c "SELECT count(*) FROM $table;" 2>/dev/null || echo "N/A")
      else
        count=$(su - postgres -c "psql -d $db -t -A -c \"SELECT count(*) FROM $table;\"" 2>/dev/null || echo "N/A")
      fi
      if [ "$count" != "N/A" ]; then
        echo "$table: $count rows remaining"
      fi
    done
  fi
fi

if [ "$dry_run" = true ]; then
  echo "[dry-run] No changes were made"
fi
