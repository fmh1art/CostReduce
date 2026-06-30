#!/bin/bash
# Extract values from JSON files using dot-separated key paths, replacing cat | python3 -c chains.
# Usage: extract-json/main.sh <file> [--key=KEY_PATH] [--pick=KEY1,KEY2] [--indent=N] [--raw] [--keys] [--search=STRING]
#   --key=KEY_PATH:  Dot-separated path to extract (e.g., scripts.jest, name, version). Default: print all.
#   --pick=KEY1,KEY2: Extract only these top-level keys from a JSON object (subset), e.g. --pick=scripts,jest
#   --indent=N:      Pretty-print with N-space indent (default: 2). Use 0 for compact.
#   --raw:           Print raw value without JSON quoting (for strings only).
#   --keys:          List only the top-level key names in the file.
#   --search=STRING: Find all keys or values containing STRING at any depth (case-insensitive).

file="$1"
shift

key_path=""
pick_keys=""
indent=2
raw=false
list_keys=false
search=""

for arg in "$@"; do
  if [[ "$arg" =~ ^--key=(.*)$ ]]; then
    key_path="${BASH_REMATCH[1]}"
  elif [[ "$arg" =~ ^--pick=(.*)$ ]]; then
    pick_keys="${BASH_REMATCH[1]}"
  elif [[ "$arg" =~ ^--indent=(.*)$ ]]; then
    indent="${BASH_REMATCH[1]}"
  elif [[ "$arg" =~ ^--search=(.*)$ ]]; then
    search="${BASH_REMATCH[1]}"
  elif [ "$arg" = "--raw" ]; then
    raw=true
  elif [ "$arg" = "--keys" ]; then
    list_keys=true
  fi
done

if [ -z "$file" ]; then
  echo "Error: file path is required" >&2
  echo "Usage: extract-json/main.sh <file> [--key=KEY_PATH] [--pick=KEY1,KEY2] [--indent=N] [--raw] [--keys] [--search=STRING]" >&2
  exit 1
fi

if [ ! -f "$file" ]; then
  echo "Error: file '$file' not found" >&2
  exit 1
fi

# Write python script to temp file
script=$(cat <<'PYEOF'
import json, os, sys

file = os.environ.get('EXTRACT_JSON_FILE', '')
key_path = os.environ.get('EXTRACT_JSON_KEY', '')
pick_keys = os.environ.get('EXTRACT_JSON_PICK', '')
search = os.environ.get('EXTRACT_JSON_SEARCH', '')
indent = int(os.environ.get('EXTRACT_JSON_INDENT', '2'))
raw = os.environ.get('EXTRACT_JSON_RAW', 'false') == 'true'
list_keys = os.environ.get('EXTRACT_JSON_KEYS', 'false') == 'true'

with open(file) as f:
    data = json.load(f)

if list_keys:
    if isinstance(data, dict):
        for k in data.keys():
            print(k)
    else:
        print('Error: root is not a dict')
    sys.exit(0)

# --pick mode: extract subset of top-level keys
if pick_keys:
    if not isinstance(data, dict):
        print('Error: --pick requires root to be a JSON object (dict)')
        sys.exit(1)
    wanted = [k.strip() for k in pick_keys.split(',') if k.strip()]
    result = {}
    for k in wanted:
        if k in data:
            result[k] = data[k]
        else:
            print(f'Warning: key "{k}" not found', file=sys.stderr)
    if indent > 0:
        print(json.dumps(result, indent=indent, ensure_ascii=False))
    else:
        print(json.dumps(result, ensure_ascii=False))
    sys.exit(0)

if key_path:
    parts = key_path.split('.')
    current = data
    for p in parts:
        if isinstance(current, dict):
            current = current.get(p)
            if current is None:
                print(f'Key "{p}" not found')
                sys.exit(1)
        elif isinstance(current, list):
            try:
                idx = int(p)
                current = current[idx]
            except (ValueError, IndexError):
                print(f'Cannot index list with "{p}"')
                sys.exit(1)
        else:
            print(f'Cannot traverse into {type(current).__name__}')
            sys.exit(1)
    value = current
else:
    value = data

if search:
    results = []
    def _search(obj, path=''):
        if isinstance(obj, dict):
            for k, v in obj.items():
                np = f'{path}.{k}' if path else k
                # Also check keys
                if search.lower() in k.lower():
                    val_str = str(v)[:100]
                    results.append((np, val_str))
                _search(v, np)
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                _search(v, f'{path}[{i}]')
        elif isinstance(obj, str) and search.lower() in obj.lower():
            results.append((path, obj))
        elif isinstance(obj, (int, float)) and search in str(obj):
            results.append((path, str(obj)))
    _search(value)
    if results:
        for path, val in results:
            print(f'{path}: {val}')
    else:
        print(f'No results containing "{search}" found')
    sys.exit(0)

if raw and isinstance(value, str):
    print(value)
else:
    if indent > 0:
        print(json.dumps(value, indent=indent, ensure_ascii=False))
    else:
        print(json.dumps(value, ensure_ascii=False))
PYEOF
)

tmpfile="/tmp/extract_json_$$.py"
echo "$script" > "$tmpfile"

EXTRACT_JSON_FILE="$file" \
EXTRACT_JSON_KEY="$key_path" \
EXTRACT_JSON_PICK="$pick_keys" \
EXTRACT_JSON_SEARCH="$search" \
EXTRACT_JSON_INDENT="$indent" \
EXTRACT_JSON_RAW="$raw" \
EXTRACT_JSON_KEYS="$list_keys" \
python3 "$tmpfile"
exit_code=$?

rm -f "$tmpfile"
exit $exit_code
