#!/bin/bash
# Compare two JSON files and show key differences in compact format.
# Replaces inline python3 -c "import json; d1=json.load(open('file1.json')); d2=json.load(open('file2.json')); ..." chains.
# Usage: diff-json/main.sh <file1> <file2> [--key=KEY] [--no-header] [--indent=N] [--show-equal]
#   --key=KEY:     Compare only this dot-separated sub-key (e.g., features, serverData.features)
#   --no-header:   Suppress header lines for machine-readable output
#   --indent=N:    Pretty-print matching values with N spaces (default: 0 = compact)
#   --show-equal:  Also show keys where values are equal
#   --only-in1:    Show keys only present in file1 (not in file2)
#   --only-in2:    Show keys only present in file2 (not in file1)

file1="$1"
file2="$2"
shift 2

key=""
no_header=false
indent=0
show_equal=false
only_in1=false
only_in2=false

for arg in "$@"; do
  case "$arg" in
    --key=*) key="${arg#*=}" ;;
    --no-header) no_header=true ;;
    --indent=*) indent="${arg#*=}" ;;
    --show-equal) show_equal=true ;;
    --only-in1) only_in1=true ;;
    --only-in2) only_in2=true ;;
  esac
done

if [ -z "$file1" ] || [ -z "$file2" ]; then
  echo "Error: two file paths required" >&2
  echo "Usage: diff-json/main.sh <file1> <file2> [--key=KEY] [--no-header] [--indent=N] [--show-equal]" >&2
  exit 1
fi

if [ ! -f "$file1" ]; then
  echo "Error: file '$file1' not found" >&2
  exit 1
fi

if [ ! -f "$file2" ]; then
  echo "Error: file '$file2' not found" >&2
  exit 1
fi

# Write Python script to temp file
py_script=$(cat <<'PYEOF'
import json, os, sys

file1 = os.environ.get('DIFF_JSON_FILE1', '')
file2 = os.environ.get('DIFF_JSON_FILE2', '')
key = os.environ.get('DIFF_JSON_KEY', '')
no_header = os.environ.get('DIFF_JSON_NO_HEADER', 'false') == 'true'
indent = int(os.environ.get('DIFF_JSON_INDENT', '0'))
show_equal = os.environ.get('DIFF_JSON_SHOW_EQUAL', 'false') == 'true'
only_in1 = os.environ.get('DIFF_JSON_ONLY_IN1', 'false') == 'true'
only_in2 = os.environ.get('DIFF_JSON_ONLY_IN2', 'false') == 'true'

with open(file1) as f:
    d1 = json.load(f)
with open(file2) as f:
    d2 = json.load(f)

# Resolve sub-key if specified
if key:
    parts = key.split('.')
    for p in parts:
        if isinstance(d1, dict):
            d1 = d1.get(p, {})
        else:
            d1 = {}
        if isinstance(d2, dict):
            d2 = d2.get(p, {})
        else:
            d2 = {}

def format_val(v):
    if isinstance(v, str):
        if len(v) > 80:
            return v[:77] + '...'
        return v
    if isinstance(v, (dict, list)):
        return json.dumps(v, indent=indent if indent > 0 else None, ensure_ascii=False)
    return json.dumps(v, ensure_ascii=False)

def compare_dicts(a, b, path=''):
    diffs = []
    all_keys = set(list(a.keys()) + list(b.keys())) if isinstance(a, dict) and isinstance(b, dict) else set()
    
    if not isinstance(a, dict) or not isinstance(b, dict):
        if a != b:
            diffs.append((path, format_val(a), format_val(b)))
        elif show_equal:
            diffs.append((path, format_val(a), '(equal)'))
        return diffs
    
    for k in sorted(all_keys):
        cur_path = f'{path}.{k}' if path else k
        in1 = k in a
        in2 = k in b
        
        if only_in1:
            if in1 and not in2:
                diffs.append((cur_path, format_val(a[k]), '(missing)'))
            elif in1 and in2 and isinstance(a[k], dict) and isinstance(b[k], dict):
                diffs.extend(compare_dicts(a[k], b[k], cur_path))
            continue
        if only_in2:
            if in2 and not in1:
                diffs.append((cur_path, '(missing)', format_val(b[k])))
            elif in1 and in2 and isinstance(a[k], dict) and isinstance(b[k], dict):
                diffs.extend(compare_dicts(a[k], b[k], cur_path))
            continue
        
        if in1 and not in2:
            diffs.append((cur_path, format_val(a[k]), '(missing)'))
        elif in2 and not in1:
            diffs.append((cur_path, '(missing)', format_val(b[k])))
        elif isinstance(a[k], dict) and isinstance(b[k], dict):
            diffs.extend(compare_dicts(a[k], b[k], cur_path))
        elif a[k] != b[k]:
            diffs.append((cur_path, format_val(a[k]), format_val(b[k])))
        elif show_equal:
            diffs.append((cur_path, format_val(a[k]), '(equal)'))
    
    return diffs

diffs = compare_dicts(d1, d2)

if not diffs:
    if not no_header:
        print('=== JSON diff: identical ===')
    sys.exit(0)

if not no_header:
    f1_base = os.path.basename(file1)
    f2_base = os.path.basename(file2)
    if only_in1:
        print(f'=== Keys only in {f1_base} (not in {f2_base}) ===')
    elif only_in2:
        print(f'=== Keys only in {f2_base} (not in {f1_base}) ===')
    else:
        print(f'=== Differences ({f1_base} vs {f2_base}) ===')

for path, v1, v2 in diffs:
    if only_in1:
        print(f'  {path}: {v1}')
    elif only_in2:
        print(f'  {path}: {v2}')
    elif v2 == '(missing)':
        print(f'  {path}: {v1} | (missing)')
    elif v1 == '(missing)':
        print(f'  {path}: (missing) | {v2}')
    elif v2 == '(equal)':
        print(f'  {path}: {v1} (equal)')
    else:
        print(f'  {path}: {v1} | {v2}')

sys.exit(0 if diffs else 1)
PYEOF
)

tmpfile="/tmp/diff_json_$$.py"
echo "$py_script" > "$tmpfile"

DIFF_JSON_FILE1="$file1" \
DIFF_JSON_FILE2="$file2" \
DIFF_JSON_KEY="$key" \
DIFF_JSON_NO_HEADER="$no_header" \
DIFF_JSON_INDENT="$indent" \
DIFF_JSON_SHOW_EQUAL="$show_equal" \
DIFF_JSON_ONLY_IN1="$only_in1" \
DIFF_JSON_ONLY_IN2="$only_in2" \
python3 "$tmpfile"
exit_code=$?

rm -f "$tmpfile"
exit $exit_code
