#!/usr/bin/env bash
set -euo pipefail

# code_section - Read a named code section (function/method/struct/interface/enum/trait/impl/var/const/class block) from a file
# Usage: main.sh <file> <section_name>
#   <file>        - Path to the source file
#   <section_name> - Name of the function/struct/interface/enum/trait/var/class to find (e.g., "requireFn", "moduleCache", "GetFns", "run", "User")
#
# Supports Go, Rust, Python (indentation-based blocks), TypeScript, C, and other brace-delimited languages.

file="$1"
section_name="$2"

if [ ! -f "$file" ]; then
    echo "Error: File '$file' not found."
    exit 1
fi

if [ -z "$section_name" ]; then
    echo "Error: Section name is required."
    exit 1
fi

# Pass arguments to Python via stdin to avoid shell expansion issues
python3 - "$file" "$section_name" << 'INNERPYEOF'
import re
import sys

filepath = sys.argv[1]
name = sys.argv[2]

with open(filepath, 'r') as f:
    lines = f.readlines()

# Find the definition line by matching various language patterns
target_line = None
matched_language = None
for i, line in enumerate(lines, 1):
    stripped = line.strip()

    # Rust: fn <name>(, pub fn <name>(, pub(crate) fn <name>(, async fn <name>(
    if re.match(r'(pub\s*\(\s*\w+\s*\)\s+)?(pub\s+)?(const\s+)?(async\s+)?(unsafe\s+)?fn\s+' + re.escape(name) + r'\s*[<\(]', stripped):
        target_line = i
        matched_language = 'brace'
        break
    # Rust: impl <name> (impl block for the type itself)
    if re.match(r'(pub\s+)?(unsafe\s+)?impl\s+' + re.escape(name) + r'(\s+for|\s*<|\s*\{|$)', stripped):
        target_line = i
        matched_language = 'brace'
        break
    # Rust: impl ... for <name> (trait implementation for a type)
    if re.match(r'(pub\s+)?(unsafe\s+)?impl\s+.*\s+for\s+' + re.escape(name) + r'(\s*<|\s*\{|$)', stripped):
        target_line = i
        matched_language = 'brace'
        break
    # Rust: struct <name>, enum <name>, trait <name>, union <name>
    if re.match(r'(pub\s+)?(struct|enum|trait|union)\s+' + re.escape(name) + r'(\s*<|\s*\{|\s*;|$)', stripped):
        target_line = i
        matched_language = 'brace'
        break
    # Rust: type <name> = (type alias)
    if re.match(r'(pub\s+)?type\s+' + re.escape(name) + r'\s*=', stripped):
        target_line = i
        matched_language = 'brace'
        break
    # Rust: mod <name> (module declaration)
    if re.match(r'(pub\s+)?mod\s+' + re.escape(name) + r'(\s*;|\s*\{|$)', stripped):
        target_line = i
        matched_language = 'brace'
        break
    # Rust: use/const/static/let (static/constant definitions)
    if re.match(r'(pub\s+)?(const|static)\s+' + re.escape(name) + r'\s*:', stripped):
        target_line = i
        matched_language = 'brace'
        break
    # Go: func <name>(, func (recv) <name>(
    if re.match(r'func\s+(?:\([^)]*\)\s+)?' + re.escape(name) + r'\s*\(', stripped):
        target_line = i
        matched_language = 'brace'
        break
    # Generic: type <name> struct|interface|...
    if re.match(r'(pub\s+)?type\s+' + re.escape(name) + r'\s', stripped):
        target_line = i
        matched_language = 'brace'
        break
    # Generic: var/const <name>
    if re.match(r'(pub\s+)?(var|const)\s+' + re.escape(name) + r'\s', stripped):
        target_line = i
        matched_language = 'brace'
        break
    # Python: def <name>(, class <name>[, async def <name>(
    if re.match(r'(async\s+)?(def|class)\s+' + re.escape(name) + r'\s*[\(:]', stripped):
        target_line = i
        matched_language = 'python'
        break
    # TypeScript: function <name>(, class <name>, interface <name>, export ...
    if re.match(r'(export\s+)?(function|class|interface|type|enum)\s+' + re.escape(name) + r'(\s*[<\(]|\s*\{|\s+extends|\s+implements|\s*=|$)', stripped):
        target_line = i
        matched_language = 'brace'
        break
    # C/C++: int|void|char|... <name>( (function)
    if re.match(r'(static\s+)?(inline\s+)?(int|void|char|long|float|double|size_t|bool|unsigned|struct\s+\w+|const\s+\w+)\s+\*?\s*' + re.escape(name) + r'\s*\(', stripped):
        target_line = i
        matched_language = 'brace'
        break
    # C/C++: struct <name>, class <name>, union <name>
    if re.match(r'(typedef\s+)?(struct|class|union|enum)\s+' + re.escape(name) + r'(\s*\{|\s*;|\s*$)', stripped):
        target_line = i
        matched_language = 'brace'
        break

if target_line is None:
    print(f'Error: Section "{name}" not found in {filepath}')
    sys.exit(1)

if matched_language == 'python':
    # For Python, use indentation-based block detection
    def_line = lines[target_line - 1]
    base_indent = len(def_line) - len(def_line.lstrip())

    end_line = target_line

    # Check if this is a one-liner (like `class X: pass`)
    after_header = def_line.split(':')[-1].strip() if ':' in def_line else ''
    if after_header:
        end_line = target_line
    else:
        # Find end of indented block
        for j in range(target_line, len(lines)):
            line = lines[j]
            if line.strip() == '':
                continue
            indent = len(line) - len(line.lstrip())
            if indent <= base_indent and line.strip() != '':
                end_line = j
                break
        else:
            end_line = len(lines)

    # Print the section with line numbers
    total_lines = len(lines)
    for j in range(target_line - 1, min(end_line, total_lines)):
        print(f'    {j+1}\t{lines[j]}', end='')
    sys.exit(0)

elif matched_language == 'brace':
    # Find the end of this code block by counting braces
    found_first_brace = False
    brace_count = 0

    for i in range(target_line - 1, len(lines)):
        line = lines[i]
        in_string = False
        string_char = None
        for ch_idx, ch in enumerate(line):
            if in_string:
                if ch == '\\' and ch_idx + 1 < len(line):
                    continue
                if ch == string_char:
                    in_string = False
            else:
                if ch in ('"', "'"):
                    in_string = True
                    string_char = ch
                elif ch == '{':
                    brace_count += 1
                    found_first_brace = True
                elif ch == '}':
                    brace_count -= 1
                    if found_first_brace and brace_count == 0:
                        end_line = i + 1
                        total_lines = len(lines)
                        for j in range(target_line - 1, min(end_line, total_lines)):
                            print(f'    {j+1}\t{lines[j]}', end='')
                        sys.exit(0)

    if not found_first_brace:
        for j in range(target_line - 1, min(target_line + 2, len(lines))):
            print(f'    {j+1}\t{lines[j]}', end='')
        sys.exit(0)

    # Fallback: print from target to end of file
    for j in range(target_line - 1, len(lines)):
        print(f'    {j+1}\t{lines[j]}', end='')
INNERPYEOF
