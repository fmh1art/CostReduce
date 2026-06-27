#!/usr/bin/env python3
"""
batch_patch - Apply the same file replacement to multiple files matching a glob pattern.
Uses the same replacement logic as file_patch but across multiple files.

Usage:
  main.sh <glob_pattern> replace <old_text> <new_text>
  main.sh <glob_pattern> insert-before <pattern> <text>
  main.sh <glob_pattern> insert-after <pattern> <text>
  main.sh <glob_pattern> delete-matching <pattern>
  main.sh <glob_pattern> append <text>
  main.sh <glob_pattern> prepend <text>
  main.sh <glob_pattern> replace-range <start_pattern> <end_pattern> <new_text>
  main.sh <glob_pattern> replace-line <pattern> <new_line_text>
  main.sh <glob_pattern> replace-block <pattern> <new_body>

The glob pattern finds all matching files. Each file is patched in sequence.
Reports which files were modified and any errors.

The replace-block action finds the first line matching pattern (containing a '{'),
then replaces everything from that '{' to its matching '}' (handling nested braces)
with new_body. Useful for replacing function/method/if/loop bodies.

Examples:
  main.sh "src/*-core/query-builders/select.ts" insert-after \
    "import { SQL, View } from '~/sql/sql.ts';" \
    "import type { WindowSpec } from '~/sql/expressions/window.ts';"

  main.sh "src/*-core/query-builders/select.ts" insert-before \
    "getSQL()" \
    "\twindow(name: string, spec: WindowSpec): this {\n\t\t...\n\t}"

  main.sh "src/schema/actions/dto/getSchemaDTO/*.ts" replace-range \
    "): " "=> {" "  definitions?: Map<string, ISchemaDTO>\n): "

  main.sh "src/**/modifiers/added.ts" replace-block \
    "return <T extends TraitOrRelation[]>(" \
    "{\n    const traits: Trait[] = [];\n    for (const input of inputs) {\n        ...\n    }\n    return createModifier(...);\n}"
"""
import sys
import os
import glob


def read_file(path):
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        return f.read()


def write_file(path, content):
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)


def resolve_text(text):
    """Resolve escape sequences (\n, \t, \r, \\) to actual characters."""
    text = text.replace('\\n', '\n')
    text = text.replace('\\t', '\t')
    text = text.replace('\\r', '\r')
    text = text.replace('\\\\', '\\')
    return text


def replace_text(filepath, old_text, new_text):
    """Replace old_text with new_text in file (all occurrences)."""
    content = read_file(filepath)
    old_text = resolve_text(old_text)
    new_text = resolve_text(new_text)

    if old_text not in content:
        return False, "Pattern not found"

    new_content = content.replace(old_text, new_text)
    if new_content == content:
        return False, "No change (replacement produced same content)"

    write_file(filepath, new_content)
    return True, "Replaced"


def insert_before(filepath, pattern, text):
    """Insert text before the first occurrence of pattern."""
    content = read_file(filepath)
    pattern = resolve_text(pattern)
    text = resolve_text(text)

    if pattern not in content:
        return False, "Pattern not found"

    idx = content.index(pattern)
    new_content = content[:idx] + text + '\n' + content[idx:]

    write_file(filepath, new_content)
    return True, "Inserted before"


def insert_after(filepath, pattern, text):
    """Insert text after the first occurrence of pattern."""
    content = read_file(filepath)
    pattern = resolve_text(pattern)
    text = resolve_text(text)

    if pattern not in content:
        return False, "Pattern not found"

    idx = content.index(pattern) + len(pattern)
    new_content = content[:idx] + '\n' + text + content[idx:]

    write_file(filepath, new_content)
    return True, "Inserted after"


def delete_matching(filepath, pattern):
    """Delete all lines containing pattern."""
    content = read_file(filepath)
    lines = content.split('\n')
    new_lines = [l for l in lines if pattern not in l]
    removed = len(lines) - len(new_lines)

    if removed == 0:
        return False, "Pattern not found in any line"

    write_file(filepath, '\n'.join(new_lines))
    return True, f"Deleted {removed} line(s)"


def cmd_append(filepath, text):
    """Append text at end of file."""
    content = read_file(filepath)
    text = resolve_text(text)
    if not content.endswith('\n'):
        content += '\n'
    content += text.rstrip('\n') + '\n'
    write_file(filepath, content)
    return True, "Appended"


def cmd_prepend(filepath, text):
    """Prepend text at beginning of file."""
    content = read_file(filepath)
    text = resolve_text(text)
    content = text.rstrip('\n') + '\n' + content
    write_file(filepath, content)
    return True, "Prepended"


def cmd_replace_range(filepath, start_pattern, end_pattern, new_text):
    """
    Replace all content between (and including) the first line matching start_pattern
    and the first line matching end_pattern (on the same or later line).
    """
    content = read_file(filepath)
    lines = content.split('\n')
    new_text = resolve_text(new_text)

    start_idx = None
    end_idx = None

    for i, line in enumerate(lines):
        if start_idx is None and start_pattern in line:
            start_idx = i
        if start_idx is not None and end_pattern in line and end_idx is None:
            end_idx = i
            break

    if start_idx is None:
        return False, "Start pattern not found"
    if end_idx is None:
        return False, "End pattern not found after start"

    before = '\n'.join(lines[:start_idx])
    after = '\n'.join(lines[end_idx + 1:])

    if before:
        before += '\n'

    new_content = before + new_text.rstrip('\n') + '\n' + after

    write_file(filepath, new_content)
    return True, f"Replaced range (lines {start_idx + 1}-{end_idx + 1}, {end_idx - start_idx + 1} lines)"


def cmd_replace_block(filepath, pattern, new_body):
    """
    Replace everything between (and including) the first '{' found on a line
    matching pattern (outside of string literals) and its matching closing '}',
    handling nested braces. The new_body replaces the content including both braces.
    """
    content = read_file(filepath)
    new_body = resolve_text(new_body)

    # Find the line containing the pattern with a '{' outside string literals
    start_line_idx = None
    brace_global_idx = None
    lines = content.split('\n')
    offset = 0

    for i, line in enumerate(lines):
        if pattern in line:
            # Find first '{' in this line that is outside strings/templates
            in_string = False
            string_char = None
            for ch_idx, ch in enumerate(line):
                if in_string:
                    if ch == '\\' and ch_idx + 1 < len(line):
                        continue
                    if ch == string_char:
                        in_string = False
                else:
                    if ch in ('"', "'", '`'):
                        in_string = True
                        string_char = ch
                    elif ch == '{':
                        start_line_idx = i
                        brace_global_idx = offset + ch_idx
                        break
            if start_line_idx is not None:
                break
        offset += len(line) + 1  # +1 for newline

    if start_line_idx is None:
        return False, "Pattern with '{' (outside strings) not found"

    # Find matching closing brace, skipping strings/templates
    brace_count = 0
    in_string = False
    string_char = None
    for idx in range(brace_global_idx, len(content)):
        ch = content[idx]
        if in_string:
            if ch == '\\' and idx + 1 < len(content):
                continue
            if ch == string_char:
                in_string = False
        else:
            if ch in ('"', "'", '`'):
                in_string = True
                string_char = ch
            elif ch == '{':
                brace_count += 1
            elif ch == '}':
                brace_count -= 1
                if brace_count == 0:
                    new_content = content[:brace_global_idx] + new_body + content[idx + 1:]
                    write_file(filepath, new_content)
                    old_lines_count = len(content[:idx + 1].split('\n')) - len(content[:brace_global_idx].split('\n'))
                    return True, f"Replaced block (line {start_line_idx + 1}, {old_lines_count} lines)"
    
    return False, "Could not find matching closing brace"



def cmd_replace_line(filepath, pattern, new_line_text):
    """Replace the first line that contains the pattern with new_line_text."""
    content = read_file(filepath)
    lines = content.split('\n')
    new_line_text = resolve_text(new_line_text)

    found = False
    for i, line in enumerate(lines):
        if pattern in line:
            lines[i] = new_line_text
            found = True
            break

    if not found:
        return False, "Pattern not found in any line"

    write_file(filepath, '\n'.join(lines))
    return True, "Line replaced"



def cmd_stdin_replace(filepath, old_text, new_text):
    """Read old and new content from stdin, separated by '=====REPLACE=====' delimiter."""
    # old_text and new_text are passed as arguments from main()
    content = read_file(filepath)
    if old_text not in content:
        return False, "Pattern not found"
    new_content = content.replace(old_text, new_text)
    count = content.count(old_text)
    write_file(filepath, new_content)
    return True, f"Replaced {count} occurrence(s)"
    
    content = read_file(filepath)
    if old_text not in content:
        return False, "Pattern not found"
    new_content = content.replace(old_text, new_text)
    count = content.count(old_text)
    write_file(filepath, new_content)
    return True, f"Replaced {count} occurrence(s)"

def main():
    if len(sys.argv) < 3:
        print("Usage: main.sh <glob_pattern> <action> [args...]", file=sys.stderr)
        print("Actions:", file=sys.stderr)
        print("  replace <old> <new>             - Replace old text with new text", file=sys.stderr)
        print("  insert-before <pat> <txt>       - Insert text before first pattern match", file=sys.stderr)
        print("  insert-after <pat> <txt>        - Insert text after first pattern match", file=sys.stderr)
        print("  delete-matching <pat>           - Delete all lines containing pattern", file=sys.stderr)
        print("  append <txt>                    - Append text at end of file", file=sys.stderr)
        print("  prepend <txt>                   - Prepend text at beginning of file", file=sys.stderr)
        print("  replace-range <start> <end> <new> - Replace range between two patterns", file=sys.stderr)
        print("  replace-line <pat> <new_line>   - Replace first line matching pattern", file=sys.stderr)
        print("  stdin-replace           - Read old/new content from stdin (heredoc/pipe)", file=sys.stderr)
        print("  replace-block <pat> <new_body> - Replace block from '{' to matching '}'", file=sys.stderr)
        print("Use \\n for newlines in text arguments.", file=sys.stderr)
        sys.exit(1)

    glob_pattern = sys.argv[1]
    action = sys.argv[2]

    # Find all matching files
    files = sorted(glob.glob(glob_pattern, recursive=True))

    if not files:
        print(f"Error: No files matching pattern: {glob_pattern}", file=sys.stderr)
        sys.exit(1)

    # For stdin-replace action, read old/new content from stdin once
    stdin_old_text = None
    stdin_new_text = None
    if action == 'stdin-replace':
        import sys as _sys
        stdin_data = _sys.stdin.read()
        delimiter = '=====REPLACE====='
        if delimiter not in stdin_data:
            print("Error: Stdin must contain '=====REPLACE=====' delimiter between old and new content", file=sys.stderr)
            sys.exit(1)
        parts = stdin_data.split(delimiter, 1)
        stdin_old_text = parts[0]
        stdin_new_text = parts[1]
        # Remove trailing newline from old_text if present
        if stdin_old_text.endswith('\n'):
            stdin_old_text = stdin_old_text[:-1]
        # Remove leading newline from new_text if present
        if stdin_new_text.startswith('\n'):
            stdin_new_text = stdin_new_text[1:]

    print(f"Found {len(files)} file(s) matching '{glob_pattern}':")
    for f in files:
        print(f"  {f}")
    print()

    success_count = 0
    skip_count = 0
    error_count = 0

    for filepath in files:
        if not os.path.isfile(filepath):
            print(f"  SKIP (not a file): {filepath}")
            skip_count += 1
            continue

        try:
            if action == 'replace':
                if len(sys.argv) < 5:
                    print(f"  ERROR: replace requires <old> <new> arguments", file=sys.stderr)
                    sys.exit(1)
                ok, msg = replace_text(filepath, sys.argv[3], sys.argv[4])
            elif action == 'insert-before':
                if len(sys.argv) < 5:
                    print(f"  ERROR: insert-before requires <pattern> <text>", file=sys.stderr)
                    sys.exit(1)
                ok, msg = insert_before(filepath, sys.argv[3], sys.argv[4])
            elif action == 'insert-after':
                if len(sys.argv) < 5:
                    print(f"  ERROR: insert-after requires <pattern> <text>", file=sys.stderr)
                    sys.exit(1)
                ok, msg = insert_after(filepath, sys.argv[3], sys.argv[4])
            elif action == 'delete-matching':
                if len(sys.argv) < 4:
                    print(f"  ERROR: delete-matching requires <pattern>", file=sys.stderr)
                    sys.exit(1)
                ok, msg = delete_matching(filepath, sys.argv[3])
            elif action == 'append':
                if len(sys.argv) < 4:
                    print(f"  ERROR: append requires <text>", file=sys.stderr)
                    sys.exit(1)
                ok, msg = cmd_append(filepath, sys.argv[3])
            elif action == 'prepend':
                if len(sys.argv) < 4:
                    print(f"  ERROR: prepend requires <text>", file=sys.stderr)
                    sys.exit(1)
                ok, msg = cmd_prepend(filepath, sys.argv[3])
            elif action == 'replace-range':
                if len(sys.argv) < 6:
                    print(f"  ERROR: replace-range requires <start> <end> <new_text>", file=sys.stderr)
                    sys.exit(1)
                ok, msg = cmd_replace_range(filepath, sys.argv[3], sys.argv[4], sys.argv[5])
            elif action == 'replace-line':
                if len(sys.argv) < 5:
                    print(f"  ERROR: replace-line requires <pattern> <new_line_text>", file=sys.stderr)
                    sys.exit(1)
                ok, msg = cmd_replace_line(filepath, sys.argv[3], sys.argv[4])
            elif action == 'stdin-replace':
                ok, msg = cmd_stdin_replace(filepath, stdin_old_text, stdin_new_text)
            elif action == 'replace-block':
                if len(sys.argv) < 5:
                    print(f"  ERROR: replace-block requires <pattern> <new_body>", file=sys.stderr)
                    sys.exit(1)
                ok, msg = cmd_replace_block(filepath, sys.argv[3], sys.argv[4])
            else:
                print(f"  ERROR: Unknown action '{action}'", file=sys.stderr)
                sys.exit(1)

            if ok:
                print(f"  OK: {filepath} ({msg})")
                success_count += 1
            else:
                print(f"  SKIP: {filepath} ({msg})")
                skip_count += 1
        except Exception as e:
            print(f"  ERROR: {filepath} - {e}")
            error_count += 1

    print()
    print(f"Summary: {success_count} modified, {skip_count} skipped, {error_count} errors")

    if error_count > 0:
        sys.exit(1)


if __name__ == '__main__':
    main()
