#!/usr/bin/env bash
set -euo pipefail

# read_json - Read specific keys/fields from JSON files
# Usage: read_json <file> [key1 key2 ...]
#   If no keys given, prints entire JSON prettified.
#   Keys are dot-separated paths (e.g., scripts.test).
#   --filter=STRING to only show entries containing STRING in key or value.
#   --scripts is shorthand for extracting the "scripts" object from package.json.

FILTER=""
FILE=""
KEYS=()
SCRIPTS_MODE=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --filter=*)
            FILTER="${1#*=}"
            shift
            ;;
        --scripts)
            SCRIPTS_MODE=true
            shift
            ;;
        -*)
            echo "Unknown option: $1" >&2
            exit 1
            ;;
        *)
            if [[ -z "$FILE" ]]; then
                FILE="$1"
            else
                KEYS+=("$1")
            fi
            shift
            ;;
    esac
done

if [[ -z "$FILE" ]]; then
    echo "Usage: $0 <file> [key1 key2 ...]" >&2
    echo "  --filter=STRING    Only show entries containing STRING" >&2
    echo "  --scripts          Shorthand for extracting scripts from package.json" >&2
    exit 1
fi

if [[ ! -f "$FILE" ]]; then
    echo "Error: file not found: $FILE" >&2
    exit 1
fi

# Build Python script
TMPFILE=$(mktemp)
cat > "$TMPFILE" << 'PYEOF'
import json, sys, os

filepath = os.environ.get('RJ_FILE', '')
filter_str = os.environ.get('RJ_FILTER', '')
scripts_mode = os.environ.get('RJ_SCRIPTS_MODE', '') == 'true'
keys_str = os.environ.get('RJ_KEYS', '')

try:
    with open(filepath) as f:
        data = json.load(f)
except json.JSONDecodeError as e:
    print(f"Error: {filepath} is not valid JSON: {e}", file=sys.stderr)
    sys.exit(1)
except Exception as e:
    print(f"Error reading {filepath}: {e}", file=sys.stderr)
    sys.exit(1)

if scripts_mode:
    scripts = data.get('scripts', {})
    for k, v in scripts.items():
        if not filter_str or filter_str.lower() in k.lower() or filter_str.lower() in v.lower():
            print(f'{k}: {v}')
    sys.exit(0)

if not keys_str:
    print(json.dumps(data, indent=2))
    sys.exit(0)

for key in keys_str.split('|'):
    parts = key.split('.')
    cur = data
    try:
        for p in parts:
            if isinstance(cur, dict):
                cur = cur[p]
            elif isinstance(cur, list):
                cur = cur[int(p)]
            else:
                raise KeyError(p)
        if isinstance(cur, dict):
            for k, v in cur.items():
                if not filter_str or filter_str.lower() in k.lower() or filter_str.lower() in str(v).lower():
                    print(f'{k}: {v}')
        elif isinstance(cur, list):
            for item in cur:
                print(item)
        else:
            print(cur)
    except (KeyError, ValueError, IndexError, TypeError) as e:
        print(f'Key not found: {key}', file=sys.stderr)
PYEOF

# Set env vars and run
RJ_FILE="$FILE" \
RJ_FILTER="$FILTER" \
RJ_SCRIPTS_MODE=$SCRIPTS_MODE \
RJ_KEYS="$(IFS='|'; echo "${KEYS[*]}")" \
python3 "$TMPFILE"

rm -f "$TMPFILE"
