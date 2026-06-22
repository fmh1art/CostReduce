#!/bin/bash
# file_patch - Reliable file patching using Python
# Usage: file_patch <file> <action> [args...]
# 
# Actions:
#   replace <old> <new>     - Replace old text with new text
#   insert-before <pat> <txt> - Insert text before matching line
#   insert-after <pat> <txt>  - Insert text after matching line
#   delete-matching <pat>   - Delete lines containing pattern
#   append <text>           - Append text at end of file
#   prepend <text>          - Prepend text at beginning of file
#
# More reliable than sed for multi-line edits and special characters.
# Saves steps by avoiding trial-and-error with sed escaping issues.

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 "$DIR/patch.py" "$@"
