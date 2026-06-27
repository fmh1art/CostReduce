#!/usr/bin/env bash
# multi-edit: Apply edits (replace, insert, delete, append, prepend, pairs, custom transform) to a file in one step,
# with --stdin for multi-line replacements, and --code for inline Python transforms.
# Usage: multi-edit <file> <action> <old_or_pattern> [<new_text>]
#        multi-edit <file> --pairs old1 new1 [old2 new2 ...]
#        multi-edit <file> --code <inline_python_code>
#        multi-edit <file> -f <script.py>

set -euo pipefail


# If first arg is --stdin-code, read Python transform from stdin
if [[ "${1:-}" == "--stdin-code" ]]; then
    shift
    FILE="${1:-}"
    [[ -z "$FILE" ]] && { echo "Error: --stdin-code needs a file path" >&2; exit 1; }
    [[ ! -f "$FILE" ]] && { echo "Error: File not found: $FILE" >&2; exit 1; }
    # Read Python transform code from stdin
    CODE="$(cat)"
    python3 -c "
import sys
filepath = sys.argv[1]
code = sys.argv[2]
with open(filepath, 'r') as f:
    content = f.read()
try:
    exec(code)
except Exception as e:
    print(f'Error executing transform: {e}', file=sys.stderr)
    sys.exit(1)
with open(filepath, 'w') as f:
    f.write(content)
print('Applied stdin transform')
" "$FILE" "$CODE"
    exit $?
fi

# Also accept --stdin-code without the flag: if only one arg and stdin is not a terminal, read transform from stdin
if [[ $# -eq 1 ]] && [[ ! -t 0 ]]; then
    FILE="$1"
    [[ ! -f "$FILE" ]] && { echo "Error: File not found: $FILE" >&2; exit 1; }
    CODE="$(cat)"
    python3 -c "
import sys
filepath = sys.argv[1]
code = sys.argv[2]
with open(filepath, 'r') as f:
    content = f.read()
try:
    exec(code)
except Exception as e:
    print(f'Error executing transform: {e}', file=sys.stderr)
    sys.exit(1)
with open(filepath, 'w') as f:
    f.write(content)
print('Applied stdin transform')
" "$FILE" "$CODE"
    exit $?
fi

show_help() {
    cat << 'EOF'
Usage: multi-edit <file> <action> <old> [<new>]
       multi-edit <file> --pairs old1 new1 [old2 new2 ...]
       multi-edit <file> --code <inline_python_code>
       multi-edit <file> -f <transform.py>
       multi-edit --stdin-code <file>   (read Python transform from stdin, matching 'python3 << PYEOF' pattern)

Actions:
  replace <old> <new>        Replace all occurrences of old with new
  insert-before <pattern> <text>  Insert text before first line matching pattern
  insert-after <pattern> <text>   Insert text after first line matching pattern
  delete-matching <pattern>   Delete all lines matching pattern
  append <text>              Append text to end of file
  prepend <text>             Prepend text to beginning of file

--pairs: Perform multiple replacements in one step (old1->new1, old2->new2, ...)
--code:  Inline Python transform code. The code receives variables 'content' (file text)
         and 'filepath' (file path). Must produce output via writing to 'content' or
         printing to stdout which replaces the file.
         Example: multi-edit file.py --code 'content = content.replace("old", "new")'
-f:      Apply a custom Python transform script (receives 'content', 'filepath')
EOF
    exit 0
}

[[ $# -lt 2 ]] && show_help

FILE="$1"
shift

[[ ! -f "$FILE" ]] && { echo "Error: File not found: $FILE" >&2; exit 1; }

# Handle --code (inline Python transform) mode
if [[ "$1" == "--code" ]]; then
    shift
    [[ $# -lt 1 ]] && { echo "Error: --code needs Python code" >&2; exit 1; }
    CODE="$1"
    python3 -c "
import sys
filepath = sys.argv[1]
code = sys.argv[2]
with open(filepath, 'r') as f:
    content = f.read()
try:
    exec(code)
except Exception as e:
    print(f'Error executing transform: {e}', file=sys.stderr)
    sys.exit(1)
with open(filepath, 'w') as f:
    f.write(content)
print('Applied inline transform')
" "$FILE" "$CODE"
    exit $?
fi

# Handle --pairs mode
if [[ "$1" == "--pairs" ]]; then
    shift
    [[ $# -lt 2 ]] && { echo "Error: --pairs needs at least one old/new pair" >&2; exit 1; }
    python3 -c "
import sys
filepath = sys.argv[1]
args = sys.argv[2:]
pairs = []
for i in range(0, len(args)-1, 2):
    pairs.append((args[i], args[i+1]))
with open(filepath, 'r') as f:
    content = f.read()
count = 0
for old, new in pairs:
    if old in content:
        content = content.replace(old, new)
        count += 1
with open(filepath, 'w') as f:
    f.write(content)
print(f'Replaced {count} pattern(s)')
" "$FILE" "$@"
    exit $?
fi

# Handle -f (custom transform script) mode
if [[ "$1" == "-f" ]]; then
    shift
    [[ $# -lt 1 ]] && { echo "Error: -f needs a Python script path" >&2; exit 1; }
    SCRIPT="$1"
    [[ ! -f "$SCRIPT" ]] && { echo "Error: Transform script not found: $SCRIPT" >&2; exit 1; }
    shift
    python3 "$SCRIPT" "$FILE" "$@"
    exit $?
fi

ACTION="$1"
shift

case "$ACTION" in
    replace)
        if [[ "$1" == "--stdin" ]]; then
            # Read old/new from stdin (two paragraphs separated by blank line or delimiter)
            shift
            python3 -c "
import sys
filepath = sys.argv[1]
# Read all stdin, split into old and new on '---DELIM---' line
lines = sys.stdin.read()
parts = lines.split('---DELIM---')
if len(parts) >= 2:
    old_text = parts[0]
    new_text = parts[1]
else:
    # Split on first blank line
    import re
    match = re.split(r'\n\n', lines, maxsplit=1)
    if len(match) >= 2:
        old_text, new_text = match[0], match[1]
    else:
        # Just use all as old, empty new
        old_text = lines
        new_text = ''
# Strip trailing newlines but preserve intentional ones
old_text = old_text.rstrip('\n')
new_text = new_text.rstrip('\n')
with open(filepath, 'r') as f:
    content = f.read()
if old_text in content:
    content = content.replace(old_text, new_text)
    with open(filepath, 'w') as f:
        f.write(content)
    print('Replaced')
else:
    print('Pattern not found - no changes made')
    sys.exit(1)
" "$FILE"
        else
            [[ $# -lt 2 ]] && { echo "Error: replace needs old and new text" >&2; exit 1; }
            python3 -c "
import sys, os
filepath = sys.argv[1]
old = sys.argv[2]
new = sys.argv[3]
with open(filepath, 'r') as f:
    content = f.read()
count = content.count(old)
if count > 0:
    content = content.replace(old, new)
    with open(filepath, 'w') as f:
        f.write(content)
    print(f'Replaced {count} occurrence(s)')
else:
    print('Pattern not found - no changes made')
    sys.exit(1)
" "$FILE" "$1" "$2"
        fi
        ;;
    insert-before)
        [[ $# -lt 2 ]] && { echo "Error: insert-before needs pattern and text" >&2; exit 1; }
        PATTERN="$1"; TEXT="$2"
        EDIT_FILE="$FILE" EDIT_PATTERN="$PATTERN" EDIT_TEXT="$TEXT" python3 -c "
import os, sys
filepath = os.environ['EDIT_FILE']
pattern = os.environ['EDIT_PATTERN']
raw_text = os.environ['EDIT_TEXT']
text = raw_text.encode('utf-8').decode('unicode_escape')
with open(filepath, 'r') as f:
    lines = f.readlines()
result = []
inserted = False
for line in lines:
    if not inserted and pattern in line:
        result.append(text + ('\n' if not text.endswith('\n') else ''))
        inserted = True
    result.append(line)
with open(filepath, 'w') as f:
    f.writelines(result)
print('Inserted before pattern')
"
        ;;
    insert-after)
        [[ $# -lt 2 ]] && { echo "Error: insert-after needs pattern and text" >&2; exit 1; }
        PATTERN="$1"; TEXT="$2"
        EDIT_FILE="$FILE" EDIT_PATTERN="$PATTERN" EDIT_TEXT="$TEXT" python3 -c "
import os, sys
filepath = os.environ['EDIT_FILE']
pattern = os.environ['EDIT_PATTERN']
raw_text = os.environ['EDIT_TEXT']
text = raw_text.encode('utf-8').decode('unicode_escape')
with open(filepath, 'r') as f:
    lines = f.readlines()
result = []
inserted = False
for line in lines:
    result.append(line)
    if not inserted and pattern in line:
        result.append(text + ('\n' if not text.endswith('\n') else ''))
        inserted = True
with open(filepath, 'w') as f:
    f.writelines(result)
print('Inserted after pattern')
"
        ;;
    delete-matching)
        [[ $# -lt 1 ]] && { echo "Error: delete-matching needs pattern" >&2; exit 1; }
        PATTERN="$1"
        EDIT_FILE="$FILE" EDIT_PATTERN="$PATTERN" python3 -c "
import os
filepath = os.environ['EDIT_FILE']
pattern = os.environ['EDIT_PATTERN']
with open(filepath, 'r') as f:
    lines = f.readlines()
result = [l for l in lines if pattern not in l]
removed = len(lines) - len(result)
with open(filepath, 'w') as f:
    f.writelines(result)
print(f'Removed {removed} line(s)')
"
        ;;
    append)
        [[ $# -lt 1 ]] && { echo "Error: append needs text" >&2; exit 1; }
        TEXT="$1"
        printf '%b' "$TEXT\n" >> "$FILE"
        echo "Appended to file"
        ;;
    prepend)
        [[ $# -lt 1 ]] && { echo "Error: prepend needs text" >&2; exit 1; }
        TEXT="$1"
        EDIT_FILE="$FILE" EDIT_TEXT="$TEXT" python3 -c "
import os
filepath = os.environ['EDIT_FILE']
raw_text = os.environ['EDIT_TEXT']
text = raw_text.encode('utf-8').decode('unicode_escape')
with open(filepath, 'r') as f:
    content = f.read()
with open(filepath, 'w') as f:
    f.write(text + ('\n' if not text.endswith('\n') else '') + content)
print('Prepended to file')
"
        ;;
    *)
        echo "Error: Unknown action '$ACTION'" >&2
        show_help
        ;;
esac
