#!/bin/bash
# Script: db_schema_analyzer
# Description: Analyze PostgreSQL database table schema - columns, constraints, indexes in one call
# Usage: main.sh <table_name> [db_uri=postgresql://myuser:mypassword@localhost:5432/simplelogin]

TABLE_NAME="$1"
if [ -z "$TABLE_NAME" ]; then
  echo "ERROR: Table name is required. Usage: main.sh <table_name> [db_uri]"
  exit 1
fi

DB_URI="${2:-postgresql://myuser:mypassword@localhost:5432/simplelogin}"

# Extract connection details from URI
if command -v psql &>/dev/null; then
  PSQL_CMD="psql"
else
  echo "ERROR: psql not found"
  exit 1
fi

echo "=== Database Schema Analysis: '$TABLE_NAME' ==="
echo ""

echo "--- Columns ---"
su - postgres -c "psql -d simplelogin -c \"SELECT column_name, is_nullable, data_type, character_maximum_length, column_default FROM information_schema.columns WHERE table_name = '$TABLE_NAME' ORDER BY ordinal_position;\"" 2>&1
echo ""

echo "--- Constraints ---"
su - postgres -c "psql -d simplelogin -c \"SELECT conname AS constraint_name, CASE contype WHEN 'p' THEN 'PRIMARY KEY' WHEN 'u' THEN 'UNIQUE' WHEN 'f' THEN 'FOREIGN KEY' WHEN 'c' THEN 'CHECK' ELSE contype::text END AS constraint_type, pg_get_constraintdef(oid) AS constraint_def FROM pg_constraint WHERE conrelid = '$TABLE_NAME'::regclass;\"" 2>&1
echo ""

echo "--- Indexes ---"
su - postgres -c "psql -d simplelogin -c \"SELECT indexname, indexdef FROM pg_indexes WHERE tablename = '$TABLE_NAME';\"" 2>&1
echo ""

echo "--- Sample Data (first 5 rows) ---"
su - postgres -c "psql -d simplelogin -c \"SELECT * FROM $TABLE_NAME LIMIT 5;\"" 2>&1
