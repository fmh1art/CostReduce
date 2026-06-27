#!/usr/bin/env bash
set -euo pipefail

# python_import_manager - Safely manage Python imports
# Usage: main.sh <file> <action> [args...]
#
# Actions:
#   add <import_stmt>        - Add an import statement with proper placement
#                              (grouped with stdlib, third-party, or local imports)
#   remove <import_stmt>     - Remove an import statement (checks usage first)
#   check-usage <name>       - Check if a name is referenced in the file
#   force-remove <import_stmt> - Remove an import without checking usage
#   list                     - List all import lines with their category
#
# Examples:
#   main.sh app.py add "from contextlib import contextmanager"
#   main.sh app.py remove "import os"
#   main.sh app.py check-usage "os"
#   main.sh app.py list

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$DIR/importer.py" "$@"
