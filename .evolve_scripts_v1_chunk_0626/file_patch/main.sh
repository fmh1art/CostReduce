#!/usr/bin/env bash
set -euo pipefail

# file_patch - Reliable file patching using Python
# Usage: main.sh <file> <action> [args...]
#
# Actions:
#   replace <old> <new>       - Replace old text with new text (use \n for newlines)
#   stdin-replace              - Read old/new content from stdin via heredoc with '=====REPLACE=====' delimiter
#   insert-before <pat> <txt> - Insert text before first line matching pattern
#   insert-after <pat> <txt>  - Insert text after first line matching pattern
#   delete-matching <pat>     - Delete all lines containing pattern
#   append <text>             - Append text at end of file
#   prepend <text>            - Prepend text at beginning of file
#   replace-range <start> <end> <new> - Replace range between two patterns
#   replace-line <pat> <new>  - Replace first line matching pattern
#   replace-block <pat> <new> - Replace block from '{' to matching '}' handling nested braces
#   replace-pyblock <pat> <new> - Replace Python indentation-based block (function/method/class)
#   stdin-replace-pyblock <pat> - Replace Python block (read new body from stdin via heredoc)
#
# More reliable than sed for multi-line edits and special characters.
# Uses pattern-based matching, not line numbers, so edits don't break
# when other modifications shift line positions.

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$DIR/patch.py" "$@"
