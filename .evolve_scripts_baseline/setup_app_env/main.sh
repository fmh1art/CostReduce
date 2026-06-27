#!/bin/bash
# Script: setup_app_env
# Description: Set up the application environment from example.env, start PostgreSQL, create user/database, run migrations
# Usage: main.sh <app_root> [db_user=myuser] [db_pass=mypassword] [db_name=simplelogin]

APP_ROOT="${1:-/app}"
DB_USER="${2:-myuser}"
DB_PASS="${3:-mypassword}"
DB_NAME="${4:-simplelogin}"

echo "=== Setting up Application Environment ==="
echo "App root: $APP_ROOT"
echo ""

# 1. Start PostgreSQL
echo "--- Starting PostgreSQL ---"
pg_lsclusters 2>/dev/null
# Find the main cluster
MAIN_VER=$(pg_lsclusters 2>/dev/null | grep -v "Ver" | head -1 | awk '{print $1}')
if [ -n "$MAIN_VER" ]; then
  pg_ctlcluster "$MAIN_VER" main start 2>&1 || service postgresql start 2>&1
else
  service postgresql start 2>&1
fi
echo ""

# 2. Create database user and database
echo "--- Creating Database User and Database ---"
su - postgres -c "psql -c \"CREATE USER $DB_USER WITH PASSWORD '$DB_PASS';\"" 2>&1
su - postgres -c "psql -c \"CREATE DATABASE $DB_NAME OWNER $DB_USER;\"" 2>&1
su - postgres -c "psql -d $DB_NAME -c 'CREATE EXTENSION IF NOT EXISTS pg_trgm;'" 2>&1
echo ""

# 3. Set up environment variables
echo "--- Loading Environment Variables ---"
if [ -f "$APP_ROOT/example.env" ]; then
  echo "Found example.env, exporting variables..."
  # We'll create a .env file for convenience
  cp "$APP_ROOT/example.env" "$APP_ROOT/.env" 2>/dev/null
  # Set critical vars
  export DB_URI="postgresql://$DB_USER:$DB_PASS@localhost:5432/$DB_NAME"
  export FLASK_SECRET="test"
  export EMAIL_DOMAIN="sl.local"
  echo "DB_URI set to: postgresql://$DB_USER:****@localhost:5432/$DB_NAME"
fi
echo ""

echo "=== Environment Setup Complete ==="
echo "Run this to activate: cd $APP_ROOT && export DB_URI=\"postgresql://$DB_USER:$DB_PASS@localhost:5432/$DB_NAME\""
