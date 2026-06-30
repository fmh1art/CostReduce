#!/usr/bin/env bash
set -euo pipefail

# file_patch - Modify files with structured actions (replace, insert, delete, append, prepend, multi-replace, batch-replace).
# Usage: file_patch [--dir=DIR] <file> <action> [args...]
# Actions:
#   replace <old> <new>            - Replace first occurrence of old with new
#   replace --stdin <file>         - Read old and new from stdin (for multi-line replacements)
#   multi-replace <old1> <new1> [old2 new2 ...]  - Replace ALL occurrences of multiple pairs
#   multi-replace --first <old> <new>  - Replace only first occurrence
#   multi-replace -f <script.py> <file>  - Apply custom Python transform
#   batch-replace [--all|--first] --stdin  - Multiple old/new pairs from stdin in one read-write
#   insert-before <pattern> <text>  - Insert text before matching line
#   insert-after <pattern> <text>   - Insert text after matching line (or --line=N <text>)
#   delete-matching <pattern>       - Delete all lines matching pattern
#   delete-lines <start> <end>      - Delete lines by line number range (1-indexed)
#   append <text>                   - Append text to end of file
#   prepend <text>                  - Prepend text to beginning of file
#   replace-block <start-marker> <new-content>  - Replace from start marker to matching close brace
#   replace-block <start-marker> -f <file>      - Read replacement from file
#   replace-block <start-marker> --stdin        - Read replacement from stdin

WORKDIR=""

# Parse --dir option
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dir=*)
            WORKDIR="${1#*=}"
            shift
            ;;
        --dir)
            WORKDIR="$2"
            shift 2
            ;;
        *)
            break
            ;;
    esac
done

if [[ $# -lt 2 ]]; then
    echo "Usage: file_patch [--dir=DIR] <file> <action> [args...]" >&2
    echo "Actions: replace, multi-replace, batch-replace, insert-before, insert-after, delete-matching, delete-lines, append, prepend, replace-block" >&2
    exit 1
fi

# Change to working directory if specified
if [[ -n "$WORKDIR" ]]; then
    cd "$WORKDIR" || { echo "Error: Cannot cd to $WORKDIR" >&2; exit 1; }
fi

FILE="$1"
ACTION="$2"
shift 2

if [[ ! -f "$FILE" ]]; then
    echo "Error: File not found: $FILE" >&2
    exit 1
fi

case "$ACTION" in
    replace)
        # Check for --stdin mode: read old and new content from stdin
        if [[ $# -ge 1 && "$1" == "--stdin" ]]; then
            python3 -c "
import sys

filepath = sys.argv[1]

# Read all stdin content
stdin_data = sys.stdin.read()

# Split on the delimiter line (---).
separator = '---'
idx = stdin_data.find(chr(10) + separator + chr(10))
if idx == -1:
    if stdin_data.startswith(separator + chr(10)):
        idx = len(separator)
        old_content = ''
        new_content = stdin_data[len(separator)+1:]
    else:
        print('Error: No delimiter (---) found in stdin input', file=sys.stderr)
        sys.exit(1)
else:
    if idx == 0:
        old_content = ''
    else:
        old_content = stdin_data[:idx]
    new_content = stdin_data[idx + len(separator) + 2:]

if old_content.endswith(chr(10)):
    old_content = old_content[:-1]
if new_content.endswith(chr(10)):
    new_content = new_content[:-1]

with open(filepath, 'r') as f:
    content = f.read()

if old_content not in content:
    print('Error: Old content not found in ' + filepath, file=sys.stderr)
    sys.exit(1)

content = content.replace(old_content, new_content, 1)

with open(filepath, 'w') as f:
    f.write(content)

print('Replaced first occurrence in ' + filepath)
" "$FILE"
            exit 0
        fi

        if [[ $# -lt 2 ]]; then
            echo "Error: replace requires <old> <new> or --stdin for stdin mode" >&2
            exit 1
        fi
        OLD="$1"
        NEW="$2"
        python3 -c "
import sys
f = sys.argv[1]
old = sys.argv[2]
new = sys.argv[3]
with open(f) as fh:
    content = fh.read()
content = content.replace(old, new, 1)
with open(f, 'w') as fh:
    fh.write(content)
print('Replaced first occurrence in ' + f)
" "$FILE" "$OLD" "$NEW"
        ;;

    multi-replace)
        FIRST_ONLY=false
        TRANSFORM_SCRIPT=""
        STDIN_MODE=false

        while [[ $# -gt 0 ]]; do
            case "$1" in
                --first)
                    FIRST_ONLY=true
                    shift
                    ;;
                -f)
                    TRANSFORM_SCRIPT="$2"
                    shift 2
                    ;;
                --stdin)
                    STDIN_MODE=true
                    shift
                    ;;
                -*)
                    echo "Unknown multi-replace option: $1" >&2
                    exit 1
                    ;;
                *)
                    break
                    ;;
            esac
        done

        if [[ "$STDIN_MODE" == true ]]; then
            if [[ $(($# % 2)) -ne 0 ]]; then
                echo "Error: multi-replace --stdin requires even number of remaining arguments" >&2
                exit 1
            fi
            python3 -c "
import sys
filepath = sys.argv[1]
pairs = sys.argv[2:]
with open(filepath, 'r') as f:
    content = f.read()
stdin_content = sys.stdin.read()
if stdin_content:
    content = stdin_content
for i in range(0, len(pairs), 2):
    old = pairs[i]
    new = pairs[i+1]
    content = content.replace(old, new)
sys.stdout.write(content)
" "$FILE" "$@"
            exit 0
        fi

        if [[ -n "$TRANSFORM_SCRIPT" ]]; then
            if [[ ! -f "$TRANSFORM_SCRIPT" ]]; then
                echo "Error: Transform script not found: $TRANSFORM_SCRIPT" >&2
                exit 1
            fi
            python3 "$TRANSFORM_SCRIPT" "$FILE"
            exit 0
        fi

        if [[ $# -lt 2 ]]; then
            echo "Error: multi-replace requires at least one <old> <new> pair" >&2
            exit 1
        fi
        if [[ $(($# % 2)) -ne 0 ]]; then
            echo "Error: multi-replace requires even number of arguments" >&2
            exit 1
        fi

        if [[ "$FIRST_ONLY" == true ]]; then
            python3 -c "
import sys
f = sys.argv[1]
pairs = sys.argv[2:]
with open(f) as fh:
    content = fh.read()
for i in range(0, len(pairs), 2):
    old = pairs[i]
    new = pairs[i+1]
    content = content.replace(old, new, 1)
with open(f, 'w') as fh:
    fh.write(content)
print('Applied ' + str(len(pairs)//2) + ' first-occurrence replacements to ' + f)
" "$FILE" "$@"
        else
            python3 -c "
import sys
f = sys.argv[1]
pairs = sys.argv[2:]
with open(f) as fh:
    content = fh.read()
for i in range(0, len(pairs), 2):
    old = pairs[i]
    new = pairs[i+1]
    content = content.replace(old, new)
with open(f, 'w') as fh:
    fh.write(content)
print('Applied ' + str(len(pairs)//2) + ' replacements to ' + f)
" "$FILE" "$@"
        fi
        ;;

    batch-replace)
        BATCH_MODE="first"

        while [[ $# -gt 0 ]]; do
            case "$1" in
                --all)
                    BATCH_MODE="all"
                    shift
                    ;;
                --first)
                    BATCH_MODE="first"
                    shift
                    ;;
                --stdin)
                    shift
                    break
                    ;;
                -*)
                    echo "Unknown batch-replace option: $1" >&2
                    exit 1
                    ;;
                *)
                    break
                    ;;
            esac
        done

        SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
        python3 "$SCRIPT_DIR/batch_replace.py" "$FILE" "$BATCH_MODE"
        ;;

    insert-before)
        if [[ $# -lt 2 ]]; then
            echo "Error: insert-before requires <pattern> <text>" >&2
            exit 1
        fi
        PATTERN="$1"
        TEXT="$2"
        python3 -c "
import sys
f = sys.argv[1]
pattern = sys.argv[2]
text = sys.argv[3]
with open(f) as fh:
    lines = fh.readlines()
new_lines = []
for line in lines:
    if pattern in line:
        new_lines.append(text + chr(10))
    new_lines.append(line)
with open(f, 'w') as fh:
    fh.writelines(new_lines)
print('Inserted before pattern in ' + f)
" "$FILE" "$PATTERN" "$TEXT"
        ;;

    insert-after)
        if [[ $# -lt 2 ]]; then
            echo "Error: insert-after requires <pattern> <text>" >&2
            exit 1
        fi
        # Check for --line=N mode
        if [[ "$1" == "--line="* ]]; then
            LINE_NUM="${1#*=}"
            shift
            if [[ $# -lt 1 ]]; then
                echo "Error: insert-after --line=N requires <text>" >&2
                exit 1
            fi
            TEXT="$1"
            python3 -c "
import sys
f = sys.argv[1]
line_num = int(sys.argv[2])
text = sys.argv[3]
with open(f) as fh:
    lines = fh.readlines()
insert_pos = min(line_num, len(lines))
lines.insert(insert_pos, text + chr(10))
with open(f, 'w') as fh:
    fh.writelines(lines)
print('Inserted after line ' + str(line_num) + ' in ' + f)
" "$FILE" "$LINE_NUM" "$TEXT"
            exit 0
        fi
        PATTERN="$1"
        TEXT="$2"
        python3 -c "
import sys
f = sys.argv[1]
pattern = sys.argv[2]
text = sys.argv[3]
with open(f) as fh:
    lines = fh.readlines()
new_lines = []
for line in lines:
    new_lines.append(line)
    if pattern in line:
        new_lines.append(text + chr(10))
with open(f, 'w') as fh:
    fh.writelines(new_lines)
print('Inserted after pattern in ' + f)
" "$FILE" "$PATTERN" "$TEXT"
        ;;

    delete-matching)
        if [[ $# -lt 1 ]]; then
            echo "Error: delete-matching requires <pattern>" >&2
            exit 1
        fi
        PATTERN="$1"
        python3 -c "
import sys
f = sys.argv[1]
pattern = sys.argv[2]
with open(f) as fh:
    lines = fh.readlines()
new_lines = [l for l in lines if pattern not in l]
with open(f, 'w') as fh:
    fh.writelines(new_lines)
print('Deleted matching lines from ' + f)
" "$FILE" "$PATTERN"
        ;;
    delete-lines)
        if [[ $# -lt 2 ]]; then
            echo "Error: delete-lines requires <start_line> <end_line>" >&2
            exit 1
        fi
        START_LINE="$1"
        END_LINE="$2"
        python3 -c "
import sys
f = sys.argv[1]
start = int(sys.argv[2])
end = int(sys.argv[3])
with open(f) as fh:
    lines = fh.readlines()
# Delete lines start to end (1-indexed)
if start < 1:
    start = 1
if end > len(lines):
    end = len(lines)
del lines[start-1:end]
with open(f, 'w') as fh:
    fh.writelines(lines)
print('Deleted lines ' + str(start) + '-' + str(end) + ' from ' + f)
" "$FILE" "$START_LINE" "$END_LINE"
        ;;



    prepend)
        if [[ $# -lt 1 ]]; then
            echo "Error: prepend requires <text>" >&2
            exit 1
        fi
        TEXT="$1"
        python3 -c "
import sys
f = sys.argv[1]
text = sys.argv[2]
with open(f) as fh:
    content = fh.read()
with open(f, 'w') as fh:
    fh.write(text + chr(10) + content)
print('Prepended to ' + f)
" "$FILE" "$TEXT"
        ;;

    replace-block)
        if [[ $# -lt 1 ]]; then
            echo "Error: replace-block requires <start-marker> [content|-f file|--stdin]" >&2
            exit 1
        fi
        START_MARKER="$1"
        shift

        REPLACEMENT=""
        FROM_FILE=""
        FROM_STDIN=false

        if [[ $# -ge 2 && "$1" == "-f" ]]; then
            FROM_FILE="$2"
            if [[ ! -f "$FROM_FILE" ]]; then
                echo "Error: Replacement file not found: $FROM_FILE" >&2
                exit 1
            fi
            REPLACEMENT=$(cat "$FROM_FILE")
            shift 2
        elif [[ $# -ge 1 && "$1" == "--stdin" ]]; then
            FROM_STDIN=true
            REPLACEMENT=$(cat)
            shift
        elif [[ $# -ge 1 ]]; then
            REPLACEMENT="$1"
            shift
        else
            echo "Error: replace-block requires content, -f file, or --stdin" >&2
            exit 1
        fi

        python3 -c "
import sys
filepath = sys.argv[1]
start_marker = sys.argv[2]
new_content = sys.argv[3]

with open(filepath, 'r') as f:
    content = f.read()

lines = content.split(chr(10))

# Find the start line
start_idx = -1
for i, line in enumerate(lines):
    if start_marker in line:
        start_idx = i
        break

if start_idx == -1:
    print('Error: Start marker not found in ' + filepath, file=sys.stderr)
    sys.exit(1)

# Get indentation of start line
start_line = lines[start_idx]
indent = len(start_line) - len(start_line.lstrip())

# Find matching closing brace at same indentation level
brace_count = 0
found_opening = False
end_idx = start_idx

for i in range(start_idx, len(lines)):
    line = lines[i]
    for ch in line:
        if ch == '{':
            brace_count += 1
            found_opening = True
        elif ch == '}':
            brace_count -= 1
    if found_opening and brace_count == 0:
        end_idx = i
        break

if not found_opening or brace_count != 0:
    print('Error: Could not find matching closing brace for block', file=sys.stderr)
    sys.exit(1)

# Determine indentation for new content
indent_str = ' ' * indent
new_lines_list = new_content.split(chr(10))
if len(new_lines_list) > 1:
    indented_new = new_lines_list[0] + chr(10) + chr(10).join(indent_str + l if l.strip() else l for l in new_lines_list[1:])
else:
    indented_new = new_content

# Replace lines from start_idx to end_idx (inclusive)
result_lines = lines[:start_idx] + [indented_new] + lines[end_idx + 1:]
result = chr(10).join(result_lines)

with open(filepath, 'w') as f:
    f.write(result)

print('Replaced block from line ' + str(start_idx+1) + ' to ' + str(end_idx+1) + ' in ' + filepath)
" "$FILE" "$START_MARKER" "$REPLACEMENT"
        ;;

    *)
        echo "Error: Unknown action '$ACTION'" >&2
        echo "Actions: replace, multi-replace, batch-replace, insert-before, insert-after, delete-matching, delete-lines, append, prepend, replace-block" >&2
        exit 1
        ;;
esac
