#!/usr/bin/env bash
# batch_json - Read and edit JSON files: extract values by key path, delete keys, set keys.

set -euo pipefail

usage() {
  echo "Usage: $0 <file.json> [key1 key2...]"
  echo "  $0 <file.json> --keys                    List top-level keys"
  echo "  $0 <file.json> --indent=N                Pretty-print with N spaces indent"
  echo "  $0 <file.json> key1.subkey               Access nested keys with dot notation"
  echo "  $0 <file.json> --delete-key=KEY           Delete a key (dot notation) from JSON file"
  echo "  $0 <file.json> --set-key=KEY=VALUE        Set a key (dot notation) to a JSON value"
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
import json, sys, os

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

def set_by_dot(obj, path, value):
    """Set a value at a dot-separated path, creating parent dicts as needed."""
    parts = path.split('.')
    current = obj
    for i, part in enumerate(parts[:-1]):
        if isinstance(current, dict):
            if part not in current:
                current[part] = {}
            current = current[part]
        else:
            return False
    if isinstance(current, dict):
        current[parts[-1]] = value
        return True
    return False

def delete_by_dot(obj, path):
    """Delete a key at a dot-separated path."""
    parts = path.split('.')
    current = obj
    for i, part in enumerate(parts[:-1]):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return False
    if isinstance(current, dict) and parts[-1] in current:
        del current[parts[-1]]
        return True
    return False

def parse_json_value(s):
    """Try to parse a string as JSON value; fall back to string."""
    s = s.strip()
    if s.lower() == 'true':
        return True
    elif s.lower() == 'false':
        return False
    elif s.lower() == 'null' or s.lower() == 'none':
        return None
    try:
        return json.loads(s)
    except (json.JSONDecodeError, ValueError):
        return s

edited = False
for key in keys:
    if key == '--keys':
        if isinstance(data, dict):
            for k in data:
                print(k)
    elif key.startswith('--indent='):
        indent = int(key.split('=', 1)[1])
        print(json.dumps(data, indent=indent))
    elif key.startswith('--delete-key='):
        del_path = key.split('=', 1)[1]
        if delete_by_dot(data, del_path):
            print(f"Deleted key '{del_path}' from {filepath}")
            edited = True
        else:
            print(f"Key not found: {del_path}")
    elif key.startswith('--set-key='):
        rest = key.split('=', 1)[1]
        if '=' in rest:
            set_path, val_str = rest.split('=', 1)
            value = parse_json_value(val_str)
            if set_by_dot(data, set_path, value):
                print(f"Set key '{set_path}' in {filepath}")
                edited = True
            else:
                print(f"Failed to set key: {set_path}")
        else:
            print(f"Invalid --set-key format (expected KEY=VALUE): {rest}")
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

if edited:
    with open(filepath, 'w') as f:
        json.dump(data, f, indent='\t')
    # Ensure trailing newline
    with open(filepath, 'a') as f:
        f.write('\n')
PYEOF
)

"$SCRIPT_DIR/../batch_python/main.sh" "$PY_CODE" -- "$file" "$@"
