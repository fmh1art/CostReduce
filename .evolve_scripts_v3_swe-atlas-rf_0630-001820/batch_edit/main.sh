#!/usr/bin/env bash
# batch_edit - Edit files with Python-powered operations: multi-line replace, line range replace/delete/insert,
# pattern operations, indent fixing, and Python transform.

set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage: batch_edit <action> <file> [args...]

Actions:
  replace <file> <old> <new>
      Simple text replacement (literal strings, first occurrence only).

  replace-lines <file> <start> <end> <content>
      Replace lines start-end (1-indexed, inclusive) with new content.

  delete-lines <file> <start> <end>
      Delete lines start-end (1-indexed, inclusive).

  insert <file> <line> <content>
      Insert content before given line number (1-indexed).

  fix-indent <file> <from_n> <to_n> [start] [end]
      Convert leading tabs in lines start-end.

  append <file> <content>
      Append content to end of file.

  prepend <file> <content>
      Prepend content to beginning of file.

  multi-replace <file> <old1> <new1> [old2 new2...]
      Perform multiple text replacements (all occurrences) in one file.

  delete-pattern <file> <pattern1> [pattern2...]
      Delete lines matching patterns.

  insert-at-line <file> <line> <content>
      Insert content after given line number (1-indexed, like sed -i 'Na...').

  insert-before <file> <pattern> <line>
      Insert a line before the first line matching pattern.

  insert-after <file> <pattern> <line>
      Insert a line after the first line matching pattern.

  transform <file> <python_expr>
      Transform file content using a Python expression that reads 'content' and writes result.
      Example: content.replace('old', 'new') or '\\n'.join(content.splitlines()[1:])

  script <file> [python_code]
      Apply inline Python code to transform the file content. The code reads
      'content' variable and writes result to 'content' or sets 'result'.
      If no arguments given, reads multi-line Python code from stdin.
      Replaces the cat>file.py + python3 file.py heredoc pattern.

  exec <file> <python_script_file>
      Execute a Python script file to transform the file content. The script
      reads 'content', writes result.

  sed <file> <sed_expression1> [expression2...]

  check-balance <file>
      Check brace {}, paren (), and bracket [] balance in a file.
      Replaces inline Python scripts for counting braces/parens/brackets.

  show-indent <file> <start_line> [end_line]
      Show indentation analysis (tab/space counts) for a line range.
      Replaces python3 PYEOF heredoc patterns for analyzing indentation.
      Apply sed -i expressions to the file (standard sed syntax).
      Examples: 's/old/new/' (first per line), 's/old/new/g' (all per line),
      '/pattern/d' (delete matching lines), '3s/old/new/' (line-specific).

Examples:
  /app/.preinstalled_scripts/batch_edit/main.sh replace file.go "old" "new"
  /app/.preinstalled_scripts/batch_edit/main.sh replace-lines file.go 10 20 "new line1\\nnew line2"
  /app/.preinstalled_scripts/batch_edit/main.sh delete-lines file.go 10 20
  /app/.preinstalled_scripts/batch_edit/main.sh insert file.go 5 "new line"
  /app/.preinstalled_scripts/batch_edit/main.sh fix-indent file.go 3 2
  /app/.preinstalled_scripts/batch_edit/main.sh append file.go "// end"
  /app/.preinstalled_scripts/batch_edit/main.sh prepend file.go "// header"
  /app/.preinstalled_scripts/batch_edit/main.sh multi-replace file.go "old1" "new1" "old2" "new2"
  /app/.preinstalled_scripts/batch_edit/main.sh delete-pattern file.go "debugger;" "TODO"
  /app/.preinstalled_scripts/batch_edit/main.sh insert-before file.go "func main" "// before main"
  /app/.preinstalled_scripts/batch_edit/main.sh insert-after file.go "func main" "// after main"
  /app/.preinstalled_scripts/batch_edit/main.sh transform file.go "content.replace('old', 'new')"
  /app/.preinstalled_scripts/batch_edit/main.sh exec file.go /tmp/patch.py
  /app/.preinstalled_scripts/batch_edit/main.sh script file.go 'content = content.replace("old", "new")'
  /app/.preinstalled_scripts/batch_edit/main.sh sed file.go "s/oldFunc/newFunc/" "/TODO/d"
  /app/.preinstalled_scripts/batch_edit/main.sh check-balance file.go
      Shows {} () [] balance and reports mismatches.

  /app/.preinstalled_scripts/batch_edit/main.sh show-indent file.go 10 30
EOF
  exit 1
}

if [[ $# -lt 2 ]]; then
  usage
fi

ACTION="$1"
FILE="$2"
shift 2

if [[ "$ACTION" != "exec" && "$ACTION" != "script" && ! -f "$FILE" ]]; then
  echo "Error: file not found: $FILE" >&2
  exit 1
fi

case "$ACTION" in
  replace)
    if [[ $# -lt 2 ]]; then
      echo "Error: replace requires old and new strings" >&2
      exit 1
    fi
    python3 -c "
import sys
f = sys.argv[1]
old = sys.argv[2]
new = sys.argv[3]
with open(f) as fh:
    c = fh.read()
c = c.replace(old, new, 1)
with open(f, 'w') as fh:
    fh.write(c)
print('Replaced in', f)
" "$FILE" "$1" "$2"
    ;;

  replace-lines)
    START="$1"
    END="$2"
    shift 2
    CONTENT="$*"
    # If no args after start/end and stdin has data, read from stdin (supports heredoc/pipe)
    if [[ $# -eq 0 && ! -t 0 ]]; then
      CONTENT=$(cat)
    elif [[ $# -lt 1 && -t 0 ]]; then
      echo "Error: replace-lines requires start, end, and content" >&2
      exit 1
    fi
    python3 -c "
import sys
f = sys.argv[1]
start = int(sys.argv[2]) - 1
end = int(sys.argv[3])
new_text = sys.argv[4]
with open(f) as fh:
    lines = fh.readlines()
new_lines = []
for part in new_text.split('\\\\n'):
    new_lines.append(part)
    if not part.endswith('\\n'):
        new_lines[-1] += '\\n'
lines[start:end] = new_lines
with open(f, 'w') as fh:
    fh.writelines(lines)
print(f'Replaced lines {start+1}-{end} in {f}')
" "$FILE" "$START" "$END" "$CONTENT"
    ;;

  delete-lines)
    if [[ $# -lt 2 ]]; then
      echo "Error: delete-lines requires start and end" >&2
      exit 1
    fi
    python3 -c "
import sys
f = sys.argv[1]
start = int(sys.argv[2]) - 1
end = int(sys.argv[3])
with open(f) as fh:
    lines = fh.readlines()
del lines[start:end]
with open(f, 'w') as fh:
    fh.writelines(lines)
print(f'Deleted lines {start+1}-{end} from {f}')
" "$FILE" "$1" "$2"
    ;;

  insert)
    if [[ $# -lt 2 ]]; then
      echo "Error: insert requires line number and content" >&2
      exit 1
    fi
    LINE="$1"
    shift
    CONTENT="$*"
    python3 -c "
import sys
f = sys.argv[1]
insert_at = int(sys.argv[2]) - 1
content = sys.argv[3]
with open(f) as fh:
    lines = fh.readlines()
if not content.endswith('\\n'):
    content += '\\n'
lines.insert(insert_at, content)
with open(f, 'w') as fh:
    fh.writelines(lines)
print(f'Inserted at line {insert_at+1} in {f}')
" "$FILE" "$LINE" "$CONTENT"
    ;;

  insert-at-line)
    if [[ $# -lt 2 ]]; then
      echo "Error: insert-at-line requires line number and content" >&2
      exit 1
    fi
    LINE="$1"
    shift
    CONTENT="$*"
    python3 -c "
import sys
f = sys.argv[1]
insert_after = int(sys.argv[2])
content = sys.argv[3]
with open(f) as fh:
    lines = fh.readlines()
if not content.endswith('\\n'):
    content += '\\n'
lines.insert(insert_after, content)
with open(f, 'w') as fh:
    fh.writelines(lines)
print(f'Inserted after line {insert_after} in {f}')
" "$FILE" "$LINE" "$CONTENT"
    ;;

  fix-indent)
    if [[ $# -lt 2 ]]; then
      echo "Error: fix-indent requires from_tabs and to_tabs" >&2
      exit 1
    fi
    FROM="$1"
    TO="$2"
    START="${3:-}"
    END="${4:-}"
    python3 -c "
import sys
f = sys.argv[1]
from_t = int(sys.argv[2])
to_t = int(sys.argv[3])
s = sys.argv[4] if len(sys.argv) > 4 else ''
e = sys.argv[5] if len(sys.argv) > 5 else ''
with open(f) as fh:
    lines = fh.readlines()
start_idx = int(s) - 1 if s else 0
end_idx = int(e) if e else len(lines)
from_pref = '\\t' * from_t
to_pref = '\\t' * to_t
for i in range(start_idx, min(end_idx, len(lines))):
    if lines[i].startswith(from_pref):
        lines[i] = to_pref + lines[i][from_t:]
with open(f, 'w') as fh:
    fh.writelines(lines)
print(f'Fixed indent in {f}')
" "$FILE" "$FROM" "$TO" "${START:-}" "${END:-}"
    ;;

  append)
    CONTENT="$*"
    python3 -c "
import sys
f = sys.argv[1]
content = sys.argv[2]
with open(f, 'a') as fh:
    fh.write(content)
    if not content.endswith('\\n'):
        fh.write('\\n')
print(f'Appended to {f}')
" "$FILE" "$CONTENT"
    ;;

  prepend)
    CONTENT="$*"
    python3 -c "
import sys
f = sys.argv[1]
content = sys.argv[2]
with open(f) as fh:
    lines = fh.readlines()
if not content.endswith('\\n'):
    content += '\\n'
lines.insert(0, content)
with open(f, 'w') as fh:
    fh.writelines(lines)
print(f'Prepended to {f}')
" "$FILE" "$CONTENT"
    ;;

  multi-replace)
    if [[ $# -lt 2 || $(( $# % 2 )) -ne 0 ]]; then
      echo "Error: multi-replace requires pairs of old and new strings" >&2
      exit 1
    fi
    python3 -c "
import sys
f = sys.argv[1]
pairs = sys.argv[2:]
with open(f) as fh:
    c = fh.read()
changes = 0
for i in range(0, len(pairs), 2):
    old = pairs[i]
    new = pairs[i+1]
    count = c.count(old)
    if count > 0:
        c = c.replace(old, new)
        changes += 1
        print(f'Replaced {count} occurrence(s) of \"{old}\" with \"{new}\" in {f}')
if changes == 0:
    print('No replacements needed in', f)
with open(f, 'w') as fh:
    fh.write(c)
" "$FILE" "$@"
    ;;

  delete-pattern)
    if [[ $# -lt 1 ]]; then
      echo "Error: delete-pattern requires at least one pattern" >&2
      exit 1
    fi
    python3 -c "
import sys, re
f = sys.argv[1]
patterns = sys.argv[2:]
with open(f) as fh:
    lines = fh.readlines()
remaining = []
removed = 0
for line in lines:
    matched = False
    for pat in patterns:
        if re.search(pat, line):
            matched = True
            break
    if matched:
        removed += 1
    else:
        remaining.append(line)
with open(f, 'w') as fh:
    fh.writelines(remaining)
print(f'Deleted {removed} line(s) matching patterns in {f}')
" "$FILE" "$@"
    ;;

  insert-before)
    if [[ $# -lt 2 ]]; then
      echo "Error: insert-before requires pattern and line content" >&2
      exit 1
    fi
    PATTERN="$1"
    shift
    CONTENT="$*"
    python3 -c "
import sys
f = sys.argv[1]
pattern = sys.argv[2]
content = sys.argv[3]
with open(f) as fh:
    lines = fh.readlines()
inserted = False
new_lines = []
for line in lines:
    if not inserted and pattern in line:
        if not content.endswith('\\n'):
            content += '\\n'
        new_lines.append(content)
        inserted = True
    new_lines.append(line)
with open(f, 'w') as fh:
    fh.writelines(new_lines)
if inserted:
    print(f'Inserted before pattern \"{pattern}\" in {f}')
else:
    print(f'Pattern \"{pattern}\" not found in {f}')
" "$FILE" "$PATTERN" "$CONTENT"
    ;;

  insert-after)
    if [[ $# -lt 2 ]]; then
      echo "Error: insert-after requires pattern and line content" >&2
      exit 1
    fi
    PATTERN="$1"
    shift
    CONTENT="$*"
    python3 -c "
import sys
f = sys.argv[1]
pattern = sys.argv[2]
content = sys.argv[3]
with open(f) as fh:
    lines = fh.readlines()
inserted = False
new_lines = []
for line in lines:
    new_lines.append(line)
    if not inserted and pattern in line:
        if not content.endswith('\\n'):
            content += '\\n'
        new_lines.append(content)
        inserted = True
with open(f, 'w') as fh:
    fh.writelines(new_lines)
if inserted:
    print(f'Inserted after pattern \"{pattern}\" in {f}')
else:
    print(f'Pattern \"{pattern}\" not found in {f}')
" "$FILE" "$PATTERN" "$CONTENT"
    ;;

  transform)
    if [[ $# -lt 1 ]]; then
      echo "Error: transform requires a Python expression" >&2
      exit 1
    fi
    EXPR="$*"
    python3 -c "
import sys
f = sys.argv[1]
expr = sys.argv[2]
with open(f) as fh:
    content = fh.read()
result = eval(expr)
if result is not None:
    with open(f, 'w') as fh:
        fh.write(str(result))
print(f'Transformed {f} with: {expr}')
" "$FILE" "$EXPR"
    ;;

  script)
    CODE=""
    if [[ ! -t 0 ]]; then
      # Read multi-line Python code from stdin (for complex edits)
      CODE=$(cat)
    elif [[ $# -ge 1 ]]; then
      CODE="$*"
    else
      echo "Error: script requires inline Python code or stdin pipe" >&2
      exit 1
    fi
    python3 -c "
import sys
f = sys.argv[1]
code = sys.argv[2]
with open(f) as fh:
    content = fh.read()
local_vars = {'content': content, 'f': f, 'result': None}
try:
    exec(code, local_vars)
except Exception as e:
    print(f'Error executing script: {e}', file=sys.stderr)
    sys.exit(1)
if 'result' in local_vars and local_vars['result'] is not None:
    with open(f, 'w') as fh:
        fh.write(str(local_vars['result']))
elif 'content' in local_vars and local_vars['content'] != content:
    with open(f, 'w') as fh:
        fh.write(str(local_vars['content']))
print(f'Applied script to {f}')
" "$FILE" "$CODE"
    ;;

  exec)
    if [[ $# -lt 1 ]]; then
      echo "Error: exec requires a Python script file path" >&2
      exit 1
    fi
    SCRIPT="$1"
    if [[ ! -f "$SCRIPT" ]]; then
      echo "Error: script file not found: $SCRIPT" >&2
      exit 1
    fi
    python3 -c "
import sys
f = sys.argv[1]
script_path = sys.argv[2]
with open(f) as fh:
    content = fh.read()
local_vars = {'content': content, 'f': f}
exec(compile(open(script_path).read(), script_path, 'exec'), local_vars)
if 'result' in local_vars and local_vars['result'] is not None:
    with open(f, 'w') as fh:
        fh.write(str(local_vars['result']))
print(f'Applied script {script_path} to {f}')
" "$FILE" "$SCRIPT"
    ;;

  sed)
    if [[ $# -lt 1 ]]; then
      echo "Error: sed requires at least one sed expression" >&2
      exit 1
    fi
    # Build sed expressions array and apply them
    EXPRESSIONS=()
    while [[ $# -gt 0 ]]; do
      EXPRESSIONS+=("$1")
      shift
    done
    # Build -e args for each expression (handles multiple expressions)
    SED_ARGS=(-i.bak)
    for expr in "${EXPRESSIONS[@]}"; do
      SED_ARGS+=(-e "$expr")
    done
    SED_ARGS+=("$FILE")
    sed "${SED_ARGS[@]}"
    rm -f "${FILE}.bak"
    echo "Applied sed expressions to $FILE"
    ;;


  check-balance)
    python3 - "$FILE" << 'PYEOF'
import sys
filepath = sys.argv[1]
with open(filepath) as f:
    content = f.read()
open_braces = content.count('{')
close_braces = content.count('}')
open_parens = content.count('(')
close_parens = content.count(')')
open_brackets = content.count('[')
close_brackets = content.count(']')
braces_diff = open_braces - close_braces
parens_diff = open_parens - close_parens
brackets_diff = open_brackets - close_brackets
print(f"{filepath} brace/paren/bracket balance:")
print(f"  Braces:  {{ = {open_braces}, }} = {close_braces}, diff = {braces_diff:+d}")
print(f"  Parens:  ( = {open_parens}, ) = {close_parens}, diff = {parens_diff:+d}")
print(f"  Brackets: [ = {open_brackets}, ] = {close_brackets}, diff = {brackets_diff:+d}")
if braces_diff == 0 and parens_diff == 0 and brackets_diff == 0:
    print('  Balanced! All braces, parens, and brackets are balanced.')
else:
    unbalanced = []
    if braces_diff != 0: unbalanced.append('braces')
    if parens_diff != 0: unbalanced.append('parens')
    if brackets_diff != 0: unbalanced.append('brackets')
    print(f"  UNBALANCED: {', '.join(unbalanced)} are not balanced.")
PYEOF
    ;;



  show-indent)
    if [[ $# -lt 1 ]]; then
      echo "Error: show-indent requires start_line [end_line]" >&2
      exit 1
    fi
    START="$1"
    END="${2:-$START}"
    python3 -c "
import sys
filepath = sys.argv[1]
start = int(sys.argv[2])
end = int(sys.argv[3])
with open(filepath) as f:
    lines = f.readlines()
for i in range(start - 1, min(end, len(lines))):
    line = lines[i]
    stripped = line.lstrip('	')
    leading_tabs = len(line) - len(stripped)
    leading_spaces = len(line) - len(line.lstrip(' '))
    rline = repr(line[:60])
    print(f\"{i+1}: tabs={leading_tabs} spaces={leading_spaces} {rline}\")
" "$FILE" "$START" "$END"
    ;;


  *)
    echo "Unknown action: $ACTION" >&2
    usage
    ;;
esac
