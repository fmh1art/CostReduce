#!/usr/bin/env bash
# batch_json - Extract values from JSON files by key path without piping through Python.

set -euo pipefail

usage() {
  echo "Usage: $0 <file.json> [key1 key2...]"
  echo "  $0 <file.json> --keys         List top-level keys"
  echo "  $0 <file.json> --indent=N     Pretty-print with N spaces indent"
  echo "  $0 <file.json> key1.subkey    Access nested keys with dot notation"
  echo "If no keys given, prints the whole file as formatted JSON."
  exit 1
}

if [[ $# -lt 1 ]]; then
  usage
fi

file="$1"
shift

if [[ ! -f "$file" ]]; then
  echo "Error: file not found: $file" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Build Python code and pass to batch_python
PY_CODE=$(cat << 'PYEOF'
import json, sys

filepath = sys.argv[1]
keys = sys.argv[2:]

with open(filepath) as f:
    data = json.load(f)

if not keys:
    print(json.dumps(data, indent=2))
    sys.exit(0)

def get_by_dot(obj, path):
    parts = path.split('.')
    current = obj
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, (list, tuple)) and part.lstrip('-').isdigit():
            idx = int(part)
            current = current[idx]
        else:
            return None
    return current

for key in keys:
    if key == '--keys':
        if isinstance(data, dict):
            for k in data:
                print(k)
    elif key.startswith('--indent='):
        indent = int(key.split('=', 1)[1])
        print(json.dumps(data, indent=indent))
    else:
        val = get_by_dot(data, key)
        if val is None:
            print(f"Key not found: {key}")
        elif isinstance(val, (dict, list)):
            print(json.dumps(val, indent=2))
        elif isinstance(val, bool):
            print(str(val).lower())
        else:
            print(val)
PYEOF
)

"$SCRIPT_DIR/../batch_python/main.sh" "$PY_CODE" -- "$file" "$@"
