#!/usr/bin/env bash
set -euo pipefail

# multi_replace - Perform multiple string replacements or structured edits in a file in one step.
# Merged from file_patch: also supports replace, insert-before, insert-after, delete-matching, append, prepend.
#
# Usage:
#   multi_replace <file> <old1> <new1> [old2 new2 ...]       # string replacement
#   multi_replace <file> --pairs old1 new1 [old2 new2 ...]    # explicit pairs
#   multi_replace <file> -f <script.py>                       # Python transform via file
#   multi_replace <file> -c <inline_python_code>              # Python transform inline
#   multi_replace <file> replace <old> <new>                  # replace all occurrences (sed-safe)
#   multi_replace <file> insert-before <pattern> [<text>]     # insert before matching line (text or stdin)
#   multi_replace <file> insert-after <pattern> [<text>]      # insert after matching line (text or stdin)
#   multi_replace <file> insert-at-line <N> [<text>]          # insert at line N (text or stdin)
#   multi_replace <file> insert-file-at-line <N> <path>       # insert content from file at line N
#   multi_replace <file> insert-file-before <pattern> <path>  # insert file content before matching line
#   multi_replace <file> insert-file-after <pattern> <path>   # insert file content after matching line
#   multi_replace <file> delete-matching <pattern>            # delete lines matching pattern
#   multi_replace <file> append <text>                        # append to end of file
#   multi_replace <file> prepend <text>                       # prepend to beginning of file

if [[ $# -lt 2 ]]; then
    echo "Usage: $0 <file> <old1> <new1> [old2 new2 ...]"
    echo "   or: $0 <file> --pairs old1 new1 old2 new2 ..."
    echo "   or: $0 <file> -f <script.py>"
    echo "   or: $0 <file> -c <inline_code>"
    echo "   or: $0 <file> replace <old> <new>"
    echo "   or: $0 <file> insert-before <pattern> [<text>]"
    echo "   or: $0 <file> insert-after <pattern> [<text>]"
    echo "   or: $0 <file> insert-at-line <N> [<text>]"
    echo "   or: $0 <file> insert-file-at-line <N> <path>"
    echo "   or: $0 <file> insert-file-before <pattern> <path>"
    echo "   or: $0 <file> insert-file-after <pattern> <path>"
    echo "   or: $0 <file> delete-matching <pattern>"
    echo "   or: $0 <file> append <text>"
    echo "   or: $0 <file> prepend <text>"
    exit 1
fi

FILE="$1"
shift

if [[ ! -f "$FILE" ]]; then
    echo "Error: file not found: $FILE" >&2
    exit 1
fi

# ---- Helper: read text from argument or stdin ----
get_text() {
    if [[ $# -ge 1 ]] && [[ -n "$1" ]]; then
        echo "$1"
    elif [[ ! -t 0 ]]; then
        cat
    else
        echo ""
    fi
}

# ---- Helper: escape for sed ----
sed_escape() {
    printf '%s' "$1" | sed 's/[\\/&]/\\&/g'
}

# ---- Structured actions ----
case "${1:-}" in
    replace)
        if [[ $# -lt 2 ]]; then
            echo "Usage: $0 <file> replace <old> <new>" >&2
            exit 1
        fi
        OLD="$2"
        NEW="$3"
        OLD_ESC=$(sed_escape "$OLD")
        NEW_ESC=$(sed_escape "$NEW")
        sed -i "s/$OLD_ESC/$NEW_ESC/g" "$FILE"
        echo "Replaced all occurrences of \"$OLD\" with \"$NEW\" in $FILE"
        exit 0
        ;;
    insert-before)
        if [[ $# -lt 1 ]]; then
            echo "Usage: $0 <file> insert-before <pattern> [<text>]" >&2
            exit 1
        fi
        PATTERN="$2"
        TEXT="$(get_text "${3:-}")"
        PATTERN_ESC=$(sed_escape "$PATTERN")
        # Use Python for reliable multi-line insertion
        python3 -c "
import sys
file_path = '''$FILE'''
pattern = '''$PATTERN'''
text_to_insert = '''$TEXT'''
with open(file_path, 'r') as f:
    lines = f.readlines()
result = []
for line in lines:
    if pattern in line:
        if text_to_insert:
            for il in text_to_insert.split('\\n'):
                result.append(il + '\\n' if not il.endswith('\\n') else il)
    result.append(line)
with open(file_path, 'w') as f:
    f.writelines(result)
print('Inserted before lines matching: $PATTERN')
"
        exit 0
        ;;
    insert-after)
        if [[ $# -lt 1 ]]; then
            echo "Usage: $0 <file> insert-after <pattern> [<text>]" >&2
            exit 1
        fi
        PATTERN="$2"
        TEXT="$(get_text "${3:-}")"
        python3 -c "
import sys
file_path = '''$FILE'''
pattern = '''$PATTERN'''
text_to_insert = '''$TEXT'''
with open(file_path, 'r') as f:
    lines = f.readlines()
result = []
for line in lines:
    result.append(line)
    if pattern in line:
        if text_to_insert:
            for il in text_to_insert.split('\\n'):
                result.append(il + '\\n' if not il.endswith('\\n') else il)
with open(file_path, 'w') as f:
    f.writelines(result)
print('Inserted after lines matching: $PATTERN')
"
        exit 0
        ;;
    insert-at-line)
        if [[ $# -lt 1 ]]; then
            echo "Usage: $0 <file> insert-at-line <N> [<text>]" >&2
            echo "  Inserts text at line N (1-indexed), shifting existing lines down." >&2
            echo "  If text is omitted, reads from stdin." >&2
            exit 1
        fi
        LINE_NUM="$2"
        if ! [[ "$LINE_NUM" =~ ^[0-9]+$ ]] || [[ "$LINE_NUM" -lt 1 ]]; then
            echo "Error: invalid line number: $LINE_NUM" >&2
            exit 1
        fi
        TEXT="$(get_text "${3:-}")"
        python3 -c "
import sys
file_path = '''$FILE'''
line_num = int('''$LINE_NUM''')
text_to_insert = '''$TEXT'''
with open(file_path, 'r') as f:
    lines = f.readlines()
if text_to_insert:
    text_lines = text_to_insert.split('\\n')
else:
    text_lines = []
new_lines = lines[:line_num-1]
for l in text_lines:
    new_lines.append(l + '\\n' if not l.endswith('\\n') else l)
new_lines += lines[line_num-1:]
with open(file_path, 'w') as f:
    f.writelines(new_lines)
print('Inserted at line $LINE_NUM in $FILE')
"
        exit 0
        ;;
    insert-file-at-line)
        if [[ $# -lt 2 ]]; then
            echo "Usage: $0 <file> insert-file-at-line <N> <source_file>" >&2
            exit 1
        fi
        LINE_NUM="$2"
        if ! [[ "$LINE_NUM" =~ ^[0-9]+$ ]] || [[ "$LINE_NUM" -lt 1 ]]; then
            echo "Error: invalid line number: $LINE_NUM" >&2
            exit 1
        fi
        SRC_FILE="$3"
        if [[ ! -f "$SRC_FILE" ]]; then
            echo "Error: source file not found: $SRC_FILE" >&2
            exit 1
        fi
        python3 -c "
import sys
file_path = '''$FILE'''
src_path = '''$SRC_FILE'''
line_num = int('''$LINE_NUM''')
with open(file_path, 'r') as f:
    lines = f.readlines()
with open(src_path, 'r') as f:
    insert_lines = f.readlines()
new_lines = lines[:line_num-1] + insert_lines + lines[line_num-1:]
with open(file_path, 'w') as f:
    f.writelines(new_lines)
print('Inserted content from $SRC_FILE at line $LINE_NUM in $FILE')
"
        exit 0
        ;;
    insert-file-before)
        if [[ $# -lt 2 ]]; then
            echo "Usage: $0 <file> insert-file-before <pattern> <source_file>" >&2
            exit 1
        fi
        PATTERN="$2"
        SRC_FILE="$3"
        if [[ ! -f "$SRC_FILE" ]]; then
            echo "Error: source file not found: $SRC_FILE" >&2
            exit 1
        fi
        python3 -c "
import sys
file_path = '''$FILE'''
pattern = '''$PATTERN'''
src_path = '''$SRC_FILE'''
with open(file_path, 'r') as f:
    lines = f.readlines()
with open(src_path, 'r') as f:
    insert_lines = f.readlines()
result = []
for line in lines:
    if pattern in line:
        result.extend(insert_lines)
    result.append(line)
with open(file_path, 'w') as f:
    f.writelines(result)
print('Inserted file content before lines matching: $PATTERN')
"
        exit 0
        ;;
    insert-file-after)
        if [[ $# -lt 2 ]]; then
            echo "Usage: $0 <file> insert-file-after <pattern> <source_file>" >&2
            exit 1
        fi
        PATTERN="$2"
        SRC_FILE="$3"
        if [[ ! -f "$SRC_FILE" ]]; then
            echo "Error: source file not found: $SRC_FILE" >&2
            exit 1
        fi
        python3 -c "
import sys
file_path = '''$FILE'''
pattern = '''$PATTERN'''
src_path = '''$SRC_FILE'''
with open(file_path, 'r') as f:
    lines = f.readlines()
with open(src_path, 'r') as f:
    insert_lines = f.readlines()
result = []
for line in lines:
    result.append(line)
    if pattern in line:
        result.extend(insert_lines)
with open(file_path, 'w') as f:
    f.writelines(result)
print('Inserted file content after lines matching: $PATTERN')
"
        exit 0
        ;;
    delete-matching)
        if [[ $# -lt 1 ]]; then
            echo "Usage: $0 <file> delete-matching <pattern>" >&2
            exit 1
        fi
        PATTERN="$2"
        PATTERN_ESC=$(sed_escape "$PATTERN")
        sed -i "/$PATTERN_ESC/d" "$FILE"
        echo "Deleted lines matching: $PATTERN"
        exit 0
        ;;
    append)
        if [[ $# -lt 1 ]]; then
            echo "Usage: $0 <file> append <text>" >&2
            exit 1
        fi
        TEXT="$(get_text "${2:-}")"
        echo "$TEXT" >> "$FILE"
        echo "Appended to $FILE"
        exit 0
        ;;
    prepend)
        if [[ $# -lt 1 ]]; then
            echo "Usage: $0 <file> prepend <text>" >&2
            exit 1
        fi
        TEXT="$(get_text "${2:-}")"
        python3 -c "
import sys
file_path = '''$FILE'''
text_to_insert = '''$TEXT'''
with open(file_path, 'r') as f:
    content = f.read()
with open(file_path, 'w') as f:
    f.write(text_to_insert + '\\n' + content)
print('Prepended to $FILE')
"
        exit 0
        ;;
esac

# ---- Inline Python transform from a file (-f) ----
if [[ "$1" == "-f" ]]; then
    SCRIPT="$2"
    if [[ ! -f "$SCRIPT" ]]; then
        echo "Error: script not found: $SCRIPT" >&2
        exit 1
    fi
    python3 -c "
import sys
with open('''$FILE''', 'r') as f:
    content = f.read()
filepath = '''$FILE'''
exec(open('''$SCRIPT''').read())
with open('''$FILE''', 'w') as f:
    f.write(content)
print('Applied transform from $SCRIPT to $FILE')
"
    exit 0
fi

# ---- Inline Python transform via -c/--code ----
if [[ "$1" == "-c" || "$1" == "--code" ]]; then
    INLINE_CODE="$2"
    python3 -c "
import sys
with open('''$FILE''', 'r') as f:
    content = f.read()
filepath = '''$FILE'''
$INLINE_CODE
with open('''$FILE''', 'w') as f:
    f.write(content)
print('Applied inline transform to $FILE')
"
    exit 0
fi

# ---- Handle --pairs prefix ----
if [[ "$1" == "--pairs" ]]; then
    shift
fi

# ---- Expect even number of remaining args (old/new pairs) ----
if [[ $(($# % 2)) -ne 0 ]]; then
    echo "Error: odd number of replacement arguments (need old/new pairs)" >&2
    exit 1
fi

# ---- Build a temporary Python script for string replacements ----
TMPFILE=$(mktemp)
cat > "$TMPFILE" << 'PYEOF'
import sys

filepath = sys.argv[1]
pairs = sys.argv[2:]

with open(filepath, 'r') as f:
    content = f.read()

for i in range(0, len(pairs), 2):
    old = pairs[i]
    new = pairs[i+1]
    # Unescape \n to actual newlines
    old = old.replace('\\n', '\n')
    new = new.replace('\\n', '\n')
    content = content.replace(old, new)

with open(filepath, 'w') as f:
    f.write(content)

print(f"Applied {len(pairs)//2} replacement(s) to {filepath}")
PYEOF

python3 "$TMPFILE" "$FILE" "$@"
rm -f "$TMPFILE"
