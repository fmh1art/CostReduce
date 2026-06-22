#!/usr/bin/env python3
"""
Reliable file patching tool.
Supports:
- Replace text in a file
- Insert before/after a pattern
- Delete lines matching a pattern
- Append/prepend to a file

Usage:
  python3 patch.py <file> replace <old_text> <new_text>
  python3 patch.py <file> insert-before <pattern> <text>
  python3 patch.py <file> insert-after <pattern> <text>
  python3 patch.py <file> delete-matching <pattern>
  python3 patch.py <file> append <text>
  python3 patch.py <file> prepend <text>
"""

import sys
import os


def read_file(path):
    with open(path, 'r') as f:
        return f.read()


def write_file(path, content):
    with open(path, 'w') as f:
        f.write(content)


def cmd_replace(filepath, old, new):
    content = read_file(filepath)
    if old not in content:
        print(f"ERROR: Pattern not found in file: {old[:80]}...")
        return False
    new_content = content.replace(old, new)
    write_file(filepath, new_content)
    print(f"Replaced '{old[:50]}...' with '{new[:50]}...' in {filepath}")
    return True


def cmd_insert_before(filepath, pattern, text):
    content = read_file(filepath)
    if pattern not in content:
        print(f"ERROR: Pattern not found: {pattern[:80]}...")
        return False
    new_content = content.replace(pattern, text + '\n' + pattern)
    write_file(filepath, new_content)
    print(f"Inserted before '{pattern[:50]}...' in {filepath}")
    return True


def cmd_insert_after(filepath, pattern, text):
    content = read_file(filepath)
    if pattern not in content:
        print(f"ERROR: Pattern not found: {pattern[:80]}...")
        return False
    new_content = content.replace(pattern, pattern + '\n' + text)
    write_file(filepath, new_content)
    print(f"Inserted after '{pattern[:50]}...' in {filepath}")
    return True


def cmd_delete_matching(filepath, pattern):
    content = read_file(filepath)
    lines = content.split('\n')
    new_lines = [l for l in lines if pattern not in l]
    removed = len(lines) - len(new_lines)
    write_file(filepath, '\n'.join(new_lines))
    print(f"Deleted {removed} lines matching '{pattern[:50]}...' in {filepath}")
    return True


def cmd_append(filepath, text):
    content = read_file(filepath)
    if not content.endswith('\n'):
        content += '\n'
    content += text + '\n'
    write_file(filepath, content)
    print(f"Appended to {filepath}")
    return True


def cmd_prepend(filepath, text):
    content = read_file(filepath)
    content = text + '\n' + content
    write_file(filepath, content)
    print(f"Prepended to {filepath}")
    return True


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    filepath = sys.argv[1]
    action = sys.argv[2]

    if not os.path.exists(filepath):
        print(f"ERROR: File not found: {filepath}")
        sys.exit(1)

    if action == 'replace':
        if len(sys.argv) < 5:
            print("Usage: patch.py <file> replace <old> <new>")
            sys.exit(1)
        success = cmd_replace(filepath, sys.argv[3], sys.argv[4])
    elif action == 'insert-before':
        if len(sys.argv) < 5:
            print("Usage: patch.py <file> insert-before <pattern> <text>")
            sys.exit(1)
        success = cmd_insert_before(filepath, sys.argv[3], sys.argv[4])
    elif action == 'insert-after':
        if len(sys.argv) < 5:
            print("Usage: patch.py <file> insert-after <pattern> <text>")
            sys.exit(1)
        success = cmd_insert_after(filepath, sys.argv[3], sys.argv[4])
    elif action == 'delete-matching':
        if len(sys.argv) < 4:
            print("Usage: patch.py <file> delete-matching <pattern>")
            sys.exit(1)
        success = cmd_delete_matching(filepath, sys.argv[3])
    elif action == 'append':
        if len(sys.argv) < 4:
            print("Usage: patch.py <file> append <text>")
            sys.exit(1)
        success = cmd_append(filepath, sys.argv[3])
    elif action == 'prepend':
        if len(sys.argv) < 4:
            print("Usage: patch.py <file> prepend <text>")
            sys.exit(1)
        success = cmd_prepend(filepath, sys.argv[3])
    else:
        print(f"Unknown action: {action}")
        print(__doc__)
        sys.exit(1)

    if not success:
        sys.exit(1)


if __name__ == '__main__':
    main()
