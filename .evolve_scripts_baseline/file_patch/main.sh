#!/bin/bash
# Script: file_patch
# Description: Apply Python-based multi-line string replacements and other file modifications in one step.
# Handles multi-line patterns reliably using Python.
# Usage: main.sh <file_path> <action> <old_or_pattern> [new_text]
#   Actions:
#     replace <old> <new>       - Replace old text with new text (use \n for newlines)
#     insert-before <pattern> <text> - Insert text before line matching pattern
#     insert-after <pattern> <text>  - Insert text after line matching pattern
#     delete-matching <pattern>      - Delete lines containing pattern
#     append <text>                  - Append text to end of file
#     prepend <text>                 - Prepend text to beginning of file
#
# For the 'replace' action with complex multi-line text, pipe the old and new
# content via heredoc:
#   main.sh file.py replace << 'PATCH'
#   OLD_TEXT_HERE
#   ---SEPARATOR---
#   NEW_TEXT_HERE
#   PATCH

FILE_PATH="$1"
ACTION="$2"

if [ -z "$FILE_PATH" ] || [ -z "$ACTION" ]; then
  echo "ERROR: Usage: main.sh <file_path> <action> <old_or_pattern> [new_text]"
  echo ""
  echo "Actions:"
  echo "  replace <old> <new>             - Replace old text with new text"
  echo "  insert-before <pattern> <text>  - Insert text before line matching pattern"
  echo "  insert-after <pattern> <text>   - Insert text after line matching pattern"
  echo "  delete-matching <pattern>       - Delete lines containing pattern"
  echo "  append <text>                   - Append text to end of file"
  echo "  prepend <text>                  - Prepend text to beginning of file"
  echo ""
  echo "For multi-line replace, pipe via heredoc:"
  echo "  main.sh file.py replace << 'PATCH'"
  echo "  OLD_TEXT"
  echo "  ---SEPARATOR---"
  echo "  NEW_TEXT"
  echo "  PATCH"
  exit 1
fi

if [ ! -f "$FILE_PATH" ]; then
  echo "ERROR: File not found: $FILE_PATH"
  exit 1
fi

OLD_OR_PATTERN="$3"
NEW_TEXT="$4"
TMP_SCRIPT=$(mktemp /tmp/file_patch_XXXXXX.py)

# Cleanup temp script on exit
cleanup() { rm -f "$TMP_SCRIPT"; }
trap cleanup EXIT

case "$ACTION" in
  replace)
    if [ -z "$OLD_OR_PATTERN" ]; then
      # Read old/new from stdin (heredoc with ---SEPARATOR---)
      OLD_FILE=$(mktemp /tmp/file_patch_old_XXXXXX)
      NEW_FILE=$(mktemp /tmp/file_patch_new_XXXXXX)
      cleanup_extra() { rm -f "$OLD_FILE" "$NEW_FILE"; }
      trap cleanup_extra EXIT
      
      # Read until separator
      found_sep=0
      while IFS= read -r line; do
        if [ "$line" = "---SEPARATOR---" ]; then
          found_sep=1
          break
        fi
        printf '%s\n' "$line" >> "$OLD_FILE"
      done
      
      if [ "$found_sep" -eq 0 ]; then
        echo "ERROR: Missing ---SEPARATOR--- in stdin input"
        exit 1
      fi
      
      # Read remaining stdin as new text
      while IFS= read -r line; do
        printf '%s\n' "$line" >> "$NEW_FILE"
      done
      
      cat > "$TMP_SCRIPT" << 'PYEOF'
import sys, os

file_path = os.environ['FILE_PATH']
old_file = os.environ['OLD_FILE']
new_file = os.environ['NEW_FILE']

with open(old_file, 'r') as f:
    old_text = f.read()
with open(new_file, 'r') as f:
    new_text = f.read()
with open(file_path, 'r') as f:
    content = f.read()

if old_text in content:
    content = content.replace(old_text, new_text)
    with open(file_path, 'w') as f:
        f.write(content)
    print("Replacement applied successfully.")
else:
    print("WARNING: Pattern not found in file.")
    sys.exit(1)
PYEOF
      
      FILE_PATH="$FILE_PATH" OLD_FILE="$OLD_FILE" NEW_FILE="$NEW_FILE" python3 "$TMP_SCRIPT"
      exit $?
    fi
    
    # Simple inline replace with \n support
    cat > "$TMP_SCRIPT" << 'PYEOF'
import sys, os

file_path = os.environ['FILE_PATH']
old_text = os.environ['OLD_TEXT']
new_text = os.environ['NEW_TEXT']

with open(file_path, 'r') as f:
    content = f.read()

# Handle \n as newlines in the input (replace \n with actual newline)
old_text = old_text.replace('\\n', '\n')
new_text = new_text.replace('\\n', '\n')

if old_text in content:
    content = content.replace(old_text, new_text)
    with open(file_path, 'w') as f:
        f.write(content)
    print("Replacement applied successfully.")
else:
    print("WARNING: Pattern not found in file.")
    sys.exit(1)
PYEOF
    
    FILE_PATH="$FILE_PATH" OLD_TEXT="$OLD_OR_PATTERN" NEW_TEXT="$NEW_TEXT" python3 "$TMP_SCRIPT"
    ;;
    
  insert-before)
    if [ -z "$NEW_TEXT" ]; then
      echo "ERROR: insert-before action requires both <pattern> and <text> arguments"
      exit 1
    fi
    cat > "$TMP_SCRIPT" << 'PYEOF'
import sys, os

file_path = os.environ['FILE_PATH']
pattern = os.environ['PATTERN']
insert_text = os.environ['INSERT_TEXT']

with open(file_path, 'r') as f:
    lines = f.readlines()

found = False
new_lines = []
for line in lines:
    if pattern in line:
        new_lines.append(insert_text.rstrip('\n') + '\n')
        found = True
    new_lines.append(line)

if found:
    with open(file_path, 'w') as f:
        f.writelines(new_lines)
    print("Insert before applied successfully.")
else:
    print("WARNING: Pattern not found in file.")
    sys.exit(1)
PYEOF
    FILE_PATH="$FILE_PATH" PATTERN="$OLD_OR_PATTERN" INSERT_TEXT="$NEW_TEXT" python3 "$TMP_SCRIPT"
    ;;
    
  insert-after)
    if [ -z "$NEW_TEXT" ]; then
      echo "ERROR: insert-after action requires both <pattern> and <text> arguments"
      exit 1
    fi
    cat > "$TMP_SCRIPT" << 'PYEOF'
import sys, os

file_path = os.environ['FILE_PATH']
pattern = os.environ['PATTERN']
insert_text = os.environ['INSERT_TEXT']

with open(file_path, 'r') as f:
    lines = f.readlines()

found = False
new_lines = []
for line in lines:
    new_lines.append(line)
    if pattern in line:
        new_lines.append(insert_text.rstrip('\n') + '\n')
        found = True

if found:
    with open(file_path, 'w') as f:
        f.writelines(new_lines)
    print("Insert after applied successfully.")
else:
    print("WARNING: Pattern not found in file.")
    sys.exit(1)
PYEOF
    FILE_PATH="$FILE_PATH" PATTERN="$OLD_OR_PATTERN" INSERT_TEXT="$NEW_TEXT" python3 "$TMP_SCRIPT"
    ;;
    
  delete-matching)
    if [ -z "$OLD_OR_PATTERN" ]; then
      echo "ERROR: delete-matching action requires a <pattern> argument"
      exit 1
    fi
    cat > "$TMP_SCRIPT" << 'PYEOF'
import sys, os

file_path = os.environ['FILE_PATH']
pattern = os.environ['PATTERN']

with open(file_path, 'r') as f:
    lines = f.readlines()

new_lines = [line for line in lines if pattern not in line]
deleted = len(lines) - len(new_lines)

with open(file_path, 'w') as f:
    f.writelines(new_lines)
print(f"Deleted {deleted} matching line(s).")
PYEOF
    FILE_PATH="$FILE_PATH" PATTERN="$OLD_OR_PATTERN" python3 "$TMP_SCRIPT"
    ;;
    
  append)
    if [ -z "$OLD_OR_PATTERN" ]; then
      echo "ERROR: append action requires a <text> argument"
      exit 1
    fi
    cat > "$TMP_SCRIPT" << 'PYEOF'
import sys, os

file_path = os.environ['FILE_PATH']
text = os.environ['TEXT']

# Handle \n as newlines
text = text.replace('\\n', '\n')

with open(file_path, 'a') as f:
    f.write(text)
print("Appended successfully.")
PYEOF
    FILE_PATH="$FILE_PATH" TEXT="$OLD_OR_PATTERN" python3 "$TMP_SCRIPT"
    ;;
    
  prepend)
    if [ -z "$OLD_OR_PATTERN" ]; then
      echo "ERROR: prepend action requires a <text> argument"
      exit 1
    fi
    cat > "$TMP_SCRIPT" << 'PYEOF'
import sys, os

file_path = os.environ['FILE_PATH']
text = os.environ['TEXT']

# Handle \n as newlines
text = text.replace('\\n', '\n')

with open(file_path, 'r') as f:
    content = f.read()
with open(file_path, 'w') as f:
    f.write(text + content)
print("Prepended successfully.")
PYEOF
    FILE_PATH="$FILE_PATH" TEXT="$OLD_OR_PATTERN" python3 "$TMP_SCRIPT"
    ;;
    
  *)
    echo "ERROR: Unknown action '$ACTION'. Use: replace, insert-before, insert-after, delete-matching, append, prepend"
    exit 1
    ;;
esac
