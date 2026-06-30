#!/usr/bin/env bash
set -euo pipefail

# quick_map - Generate a compact tree view of project structure with file sizes and extension stats
# Usage: quick_map [directory] [max_depth=4] [--filter=GLOBS]

DIR="."
MAX_DEPTH=4
FILTER=""

if [[ $# -ge 1 ]] && [[ "$1" != -* ]]; then
    DIR="$1"; shift
fi
if [[ $# -ge 1 ]] && [[ "$1" != -* ]]; then
    MAX_DEPTH="$1"; shift
fi
while [[ $# -gt 0 ]]; do
    case "$1" in
        --filter=*) FILTER="${1#*=}"; shift ;;
        -f) FILTER="$2"; shift 2 ;;
        *) shift ;;
    esac
done

[[ -d "$DIR" ]] || { echo "Error: directory not found: $DIR" >&2; exit 1; }

# Write Python helper to temp file
PY_SCRIPT=$(mktemp)
cat > "$PY_SCRIPT" << 'PYEOF'
import os, sys
from collections import Counter

root = sys.argv[1]
max_depth = int(sys.argv[2])
filter_val = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] else ""

# Build find command
find_cmd = ['find', root, '-maxdepth', str(max_depth)]
find_cmd += ['!', '-name', '.git', '!', '-name', 'node_modules']

if filter_val:
    globs = filter_val.split(',')
    find_cmd.extend(['('])
    for i, g in enumerate(globs):
        if i > 0:
            find_cmd.extend(['-o'])
        find_cmd.extend(['-name', g.strip()])
    find_cmd.extend([')'])

import subprocess
result = subprocess.run(find_cmd, capture_output=True, text=True)
if result.returncode != 0 and result.returncode != 1:
    sys.exit(0)

tree_lines = [l.strip() for l in result.stdout.strip().split('\n') if l.strip()]

if not tree_lines:
    print("(empty or no matching files)")
    sys.exit(0)

entries = []
for path in tree_lines:
    if not path.startswith(root):
        continue
    rel = os.path.relpath(path, root)
    if rel == '.':
        continue
    is_dir = os.path.isdir(path)
    depth = rel.count(os.sep) + 1
    if is_dir:
        entries.append((depth, 0, rel, ''))
    else:
        try:
            sz = os.path.getsize(path)
            if sz < 1024:
                szs = f'{sz}B'
            elif sz < 1024*1024:
                szs = f'{sz/1024:.0f}K'
            else:
                szs = f'{sz/(1024*1024):.1f}M'
        except:
            szs = '?'
        entries.append((depth, 1, rel, szs))

entries.sort(key=lambda x: (x[0], x[1], x[2].lower()))

root_name = os.path.basename(root.rstrip('/')) or root
print(f'{root_name}/')

for depth, is_file, rel, size in entries:
    indent = '    ' * (depth - 1)
    name = rel.split(os.sep)[-1]
    if is_file:
        print(f'{indent}├── {name}  ({size})')
    else:
        print(f'{indent}├── {name}/')

# Extension stats
if not filter_val:
    ext_counts = Counter()
    ext_sizes = Counter()
    for path in tree_lines:
        if not path.startswith(root):
            continue
        rel = os.path.relpath(path, root)
        if rel == '.' or os.path.isdir(path):
            continue
        ext = os.path.splitext(path)[1] or '(no ext)'
        ext_counts[ext] += 1
        try:
            ext_sizes[ext] += os.path.getsize(path)
        except:
            pass

    if ext_counts:
        print()
        print('── Extension Stats ──')
        for ext, count in ext_counts.most_common():
            ts = ext_sizes[ext]
            if ts < 1024:
                ss = f'{ts}B'
            elif ts < 1024*1024:
                ss = f'{ts/1024:.0f}K'
            else:
                ss = f'{ts/(1024*1024):.1f}M'
            print(f'  {ext:10s} {count:4d} files  {ss:>8s}')
PYEOF

python3 "$PY_SCRIPT" "$DIR" "$MAX_DEPTH" "$FILTER"
rm -f "$PY_SCRIPT"
