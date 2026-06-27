#!/bin/bash
# Edit files safely: insert lines, replace text, or show context.
# Usage: main.sh <action> <file> <pattern> [text|replacement]
#   insert-before <file> <pattern> <text>  - Insert line before first match
#   insert-after  <file> <pattern> <text>  - Insert line after first match
#   replace       <file> <old> <new>       - Replace first occurrence of old with new
#   replace-all   <file> <old> <new>       - Replace all occurrences of old with new
#   replace-block <file> <old-file> <new-file> - Multi-line block replacement via Python
#   show          <file> [pattern|start] [end] - Show lines
#   check         <file> <pattern>         - Check if pattern exists (exit 0/1)

set -euo pipefail

usage() {
  echo "Usage: main.sh <action> <file> <pattern> [text|replacement]" >&2
  echo "Actions: insert-before, insert-after, replace, replace-all, replace-block, show, check" >&2
  exit 1
}

[ $# -ge 2 ] || usage

ACTION="$1"
FILE="$2"
shift 2

if [ ! -f "$FILE" ]; then
  echo "Error: file not found: $FILE" >&2
  exit 1
fi

case "$ACTION" in
  insert-before)
    [ $# -ge 2 ] || { echo "Usage: main.sh insert-before <file> <pattern> <text>" >&2; exit 1; }
    PATTERN="$1"
    TEXT="$2"
    # Use awk with index() for literal string matching (not regex)
    awk -v pat="$PATTERN" -v txt="$TEXT" '
      index($0, pat) && !found { print txt; found=1 }
      { print }
      END { if (found == 0) print "(pattern not found)" > "/dev/stderr" }
    ' "$FILE" > "${FILE}.tmp" && mv "${FILE}.tmp" "$FILE"
    echo "Inserted before first match of: $PATTERN"
    ;;
  insert-after)
    [ $# -ge 2 ] || { echo "Usage: main.sh insert-after <file> <pattern> <text>" >&2; exit 1; }
    PATTERN="$1"
    TEXT="$2"
    awk -v pat="$PATTERN" -v txt="$TEXT" '
      { print }
      index($0, pat) && !found { print txt; found=1 }
      END { if (found == 0) print "(pattern not found)" > "/dev/stderr" }
    ' "$FILE" > "${FILE}.tmp" && mv "${FILE}.tmp" "$FILE"
    echo "Inserted after first match of: $PATTERN"
    ;;
  replace)
    [ $# -ge 2 ] || { echo "Usage: main.sh replace <file> <old-pattern> <new-text>" >&2; exit 1; }
    OLD="$1"
    NEW="$2"
    # Use awk with index() for literal string matching
    awk -v old="$OLD" -v new="$NEW" '
      !replaced && index($0, old) {
        $0 = substr($0, 1, index($0, old)-1) new substr($0, index($0, old)+length(old))
        replaced=1
      }
      { print }
      END { if (replaced == 0) print "(pattern not found)" > "/dev/stderr" }
    ' "$FILE" > "${FILE}.tmp" && mv "${FILE}.tmp" "$FILE"
    echo "Replaced first occurrence of: $OLD"
    ;;
  replace-all)
    [ $# -ge 2 ] || { echo "Usage: main.sh replace-all <file> <old-pattern> <new-text>" >&2; exit 1; }
    OLD="$1"
    NEW="$2"
    # Use sed with | delimiter for replace-all (sed handles g flag well)
    # Escape pipe characters in patterns
    OLD_ESC="${OLD//|/\\|}"
    NEW_ESC="${NEW//|/\\|}"
    sed -i "s|${OLD_ESC}|${NEW_ESC}|g" "$FILE"
    echo "Replaced all occurrences of: $OLD"
    ;;
  replace-block)
    [ $# -ge 2 ] || { echo "Usage: main.sh replace-block <file> <old-text-file> <new-text-file>" >&2; exit 1; }
    OLD_FILE="$1"
    NEW_FILE="$2"
    if [ ! -f "$OLD_FILE" ]; then
      echo "Error: old text file not found: $OLD_FILE" >&2
      exit 1
    fi
    if [ ! -f "$NEW_FILE" ]; then
      echo "Error: new text file not found: $NEW_FILE" >&2
      exit 1
    fi
    python3 -c "
import sys
with open('$OLD_FILE', 'r') as f:
    old_text = f.read()
with open('$NEW_FILE', 'r') as f:
    new_text = f.read()
with open('$FILE', 'r') as f:
    content = f.read()
if old_text not in content:
    print('(pattern not found)', file=sys.stderr)
    sys.exit(1)
content = content.replace(old_text, new_text, 1)
with open('$FILE', 'w') as f:
    f.write(content)
print('Replaced block in: $FILE')
"
    ;;
  show)
    if [ $# -ge 2 ] && [[ "$1" =~ ^[0-9]+$ ]] && [[ "$2" =~ ^[0-9]+$ ]]; then
      SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
      "$SCRIPT_DIR/../read-lines/main.sh" "$FILE" "$1" "$2"
    elif [ $# -ge 1 ]; then
      PATTERN="$1"
      grep -n "$PATTERN" "$FILE" || echo "(no matches for: $PATTERN)"
    else
      head -30 "$FILE"
    fi
    ;;
  check)
    [ $# -ge 1 ] || { echo "Usage: main.sh check <file> <pattern>" >&2; exit 1; }
    PATTERN="$1"
    if grep -q "$PATTERN" "$FILE"; then
      echo "Found: $PATTERN"
      exit 0
    else
      echo "Not found: $PATTERN"
      exit 1
    fi
    ;;
  *)
    usage
    ;;
esac
