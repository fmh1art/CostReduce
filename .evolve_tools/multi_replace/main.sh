#!/bin/bash
# multi_replace - Perform multiple string replacements in a file in one step
# Usage: multi_replace <file> <old1> <new1> [old2 new2] ...
#        multi_replace <file> --pairs old1 new1 old2 new2 ...
#        multi_replace <file> -f <script.py>
#
# Performs multiple string replacements in a file in a single tool call.
# Each old/new pair is applied sequentially.
# Use -f to run a custom Python script for complex transformations
# (the script receives `content` and `filepath` variables).
#
# More efficient than running file_patch multiple times for different
# replacements in the same file.
#
# Examples:
#   multi_replace file.go "l.fill(" "l.frame.fill(l.selector, "
#   multi_replace file.go --pairs "l.fill(" "l.frame.fill(l.selector, " "l.click(" "l.frame.click(l.selector, "
#   multi_replace file.go -f transform.py

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}" )" && pwd)"
python3 "$DIR/replace.py" "$@"
