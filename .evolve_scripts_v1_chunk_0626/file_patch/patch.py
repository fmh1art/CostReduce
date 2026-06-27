#!/usr/bin/env python3
"""
Reliable file patching tool.
Supports:
- Replace text in a file
- Insert before/after a pattern (first occurrence only)
- Delete lines matching a pattern
- Append/prepend to a file
- Replace range between two patterns
- Replace a specific line matching a pattern
- Replace block between matching braces (handles nested braces)
- stdin-replace: read old/new content from stdin (heredoc-style) for multiline replacements

Usage:
  python3 patch.py <file> replace <old_text> <new_text>
  python3 patch.py <file> insert-before <pattern> <text>
  python3 patch.py <file> insert-after <pattern> <text>
  python3 patch.py <file> delete-matching <pattern>
  python3 patch.py <file> append <text>
  python3 patch.py <file> prepend <text>
  python3 patch.py <file> replace-range <start_pattern> <end_pattern> <new_text>
  python3 patch.py <file> replace-line <pattern> <new_line_text>
  python3 patch.py <file> replace-block <pattern> <new_body>
  python3 patch.py <file> stdin-replace           # Read from stdin (pipe/heredoc)
"""

import sys
import os


def read_file(path):
    with open(path, 'r') as f:
        return f.read()


def write_file(path, content):
    with open(path, 'w') as f:
        f.write(content)


def resolve_text(text):
    """Resolve escape sequences (\\n, \\t, \\r, \\\\) to actual characters."""
    text = text.replace('\\n', '\n')
    text = text.replace('\\t', '\t')
    text = text.replace('\\r', '\r')
    text = text.replace('\\\\', '\\')
    return text


def cmd_replace(filepath, old, new):
    content = read_file(filepath)
    old = resolve_text(old)
    new = resolve_text(new)
    if old not in content:
        print(f"ERROR: Pattern not found in file: {old[:80]}...")
        return False
    new_content = content.replace(old, new)
    write_file(filepath, new_content)
    print(f"Replaced in {filepath}")
    return True


def cmd_stdin_replace(filepath):
    """Read old and new content from stdin, separated by '=====REPLACE=====' delimiter."""
    import sys as _sys
    stdin_data = _sys.stdin.read()
    
    delimiter = '=====REPLACE====='
    if delimiter not in stdin_data:
        print(f"ERROR: stdin must contain '{delimiter}' delimiter between old and new content")
        print("Format: <old_text>\\n=====REPLACE=====\\n<new_text>")
        return False
    
    parts = stdin_data.split(delimiter, 1)
    old_text = parts[0]
    new_text = parts[1]
    
    # Remove trailing newline from old_text if present (from heredoc formatting)
    if old_text.endswith('\n'):
        old_text = old_text[:-1]
    # Remove leading newline from new_text if present (from heredoc formatting)
    if new_text.startswith('\n'):
        new_text = new_text[1:]
    
    content = read_file(filepath)
    if old_text not in content:
        print(f"ERROR: Pattern not found in file (first 80 chars): {old_text[:80]}...")
        return False
    new_content = content.replace(old_text, new_text)
    write_file(filepath, new_content)
    
    # Count how many replacements were made
    count = content.count(old_text)
    print(f"Replaced {count} occurrence(s) in {filepath}")
    return True


def cmd_insert_before(filepath, pattern, text):
    content = read_file(filepath)
    if pattern not in content:
        print(f"ERROR: Pattern not found: {pattern[:80]}...")
        return False
    text = resolve_text(text)
    # Only replace the FIRST occurrence
    idx = content.index(pattern)
    new_content = content[:idx] + text + '\n' + content[idx:]
    write_file(filepath, new_content)
    print(f"Inserted before '{pattern[:50]}...' in {filepath}")
    return True


def cmd_insert_after(filepath, pattern, text):
    content = read_file(filepath)
    if pattern not in content:
        print(f"ERROR: Pattern not found: {pattern[:80]}...")
        return False
    text = resolve_text(text)
    # Only replace the FIRST occurrence
    idx = content.index(pattern) + len(pattern)
    new_content = content[:idx] + '\n' + text + content[idx:]
    write_file(filepath, new_content)
    print(f"Inserted after '{pattern[:50]}...' in {filepath}")
    return True


def cmd_delete_matching(filepath, pattern):
    content = read_file(filepath)
    lines = content.split('\n')
    new_lines = [l for l in lines if pattern not in l]
    removed = len(lines) - len(new_lines)
    if removed == 0:
        print(f"WARNING: No lines matched pattern '{pattern[:50]}...'")
        return True  # Not an error, just nothing to do
    write_file(filepath, '\n'.join(new_lines))
    print(f"Deleted {removed} line(s) matching '{pattern[:50]}...' in {filepath}")
    return True


def cmd_append(filepath, text):
    content = read_file(filepath)
    text = resolve_text(text)
    if not content.endswith('\n'):
        content += '\n'
    content += text.rstrip('\n') + '\n'
    write_file(filepath, content)
    print(f"Appended to {filepath}")
    return True


def cmd_prepend(filepath, text):
    content = read_file(filepath)
    text = resolve_text(text)
    content = text.rstrip('\n') + '\n' + content
    write_file(filepath, content)
    print(f"Prepended to {filepath}")
    return True


def cmd_replace_range(filepath, start_pattern, end_pattern, new_text):
    """
    Replace all content between (and including) the first line matching start_pattern
    and the first line matching end_pattern after the start line.
    """
    content = read_file(filepath)
    lines = content.split('\n')
    new_text = resolve_text(new_text)

    start_idx = None
    end_idx = None

    for i, line in enumerate(lines):
        if start_pattern in line and start_idx is None:
            start_idx = i
            continue
        if start_idx is not None and end_pattern in line and end_idx is None:
            end_idx = i
            break

    if start_idx is None:
        print(f"ERROR: Start pattern not found: '{start_pattern[:80]}...'")
        return False
    if end_idx is None:
        print(f"ERROR: End pattern not found after start: '{end_pattern[:80]}...'")
        return False

    before = '\n'.join(lines[:start_idx])
    after = '\n'.join(lines[end_idx + 1:])

    if before:
        before += '\n'

    new_content = before + new_text
    if after:
        new_content += '\n' + after

    write_file(filepath, new_content)
    lines_replaced = end_idx - start_idx + 1
    print(f"Replaced range (lines {start_idx + 1}-{end_idx + 1}, {lines_replaced} lines) in {filepath}")
    return True


def cmd_replace_line(filepath, pattern, new_line_text):
    """Replace the first line that contains the given pattern with the new text."""
    content = read_file(filepath)
    lines = content.split('\n')

    for i, line in enumerate(lines):
        if pattern in line:
            lines[i] = new_line_text
            write_file(filepath, '\n'.join(lines))
            print(f"Replaced line {i + 1} in {filepath}")
            return True

    print(f"ERROR: Pattern not found: '{pattern[:80]}...'")
    return False



def cmd_replace_pyblock(filepath, pattern, new_body):
    """Replace a Python indentation-based block (function/method/class) matching pattern.
    
    Finds the line containing the pattern, then uses Python indentation rules
    to determine where the block ends (next line with same or less indentation).
    Replaces everything from the pattern line through the end of the block.
    Handles decorators preceding the definition.
    """
    content = read_file(filepath)
    lines = content.split('\n')
    new_body = resolve_text(new_body)
    new_body = new_body.rstrip('\n') + '\n'
    
    # Find the FIRST line containing the pattern
    line_idx = None
    for i, line in enumerate(lines):
        if pattern in line:
            line_idx = i
            break
    
    if line_idx is None:
        print(f"ERROR: Pattern not found in file: {pattern[:80]}...")
        return False
    
    # Start from the definition line
    base_indent = len(lines[line_idx]) - len(lines[line_idx].lstrip())
    
    # Find where the block ends
    # Look for the next line at the same or lesser indentation
    # Skip empty lines and continuation lines
    end_idx = line_idx + 1
    while end_idx < len(lines):
        stripped = lines[end_idx].strip()
        if not stripped:
            # Empty line - could be between body and next function
            # Check if the next non-empty line has indentation <= base_indent
            next_non_empty = end_idx + 1
            while next_non_empty < len(lines) and not lines[next_non_empty].strip():
                next_non_empty += 1
            if next_non_empty < len(lines):
                next_indent = len(lines[next_non_empty]) - len(lines[next_non_empty].lstrip())
                if next_indent <= base_indent:
                    # The next non-empty line is at same or lower indentation
                    break
            
            end_idx += 1
            continue
        
        current_indent = len(lines[end_idx]) - len(lines[end_idx].lstrip())
        
        if current_indent <= base_indent and not stripped.startswith('#'):
            # This line is at same or lower indentation - end of block
            break
        
        end_idx += 1
    
    # Replace from line_idx to end_idx (exclusive) with new_body
    before = '\n'.join(lines[:line_idx])
    after = '\n'.join(lines[end_idx:])
    
    # Add newline between before and new_body if needed
    if before and not before.endswith('\n'):
        before += '\n'
    
    # Add newline between new_body and after if needed
    if after and not new_body.endswith('\n'):
        new_body += '\n'
    
    new_content = before + new_body + after
    write_file(filepath, new_content)
    print(f"Replaced Python block from line {line_idx + 1} to {end_idx} matching '{pattern[:50]}...' in {filepath}")
    return True

def cmd_replace_block(filepath, pattern, new_body):
    """
    Replace everything from the first '{' on the line matching pattern,
    up to its matching '}', handling nested braces.
    Skips braces inside string literals (single/double quotes and backticks).
    Properly scans from the start of the matched line, tracking string state,
    to find the first opening brace that is NOT inside a string literal.
    """
    content = read_file(filepath)
    lines = content.split('\n')
    new_body = resolve_text(new_body)

    # Find the first line containing the pattern
    line_idx = None
    for i, line in enumerate(lines):
        if pattern in line:
            line_idx = i
            break

    if line_idx is None:
        print(f"ERROR: Pattern not found in any line: '{pattern[:80]}...'")
        return False

    # Find the position of the start of the matched line
    start_of_line = sum(len(l) + 1 for l in lines[:line_idx])

    # Scan from start of line, tracking string state, to find first '{' outside strings
    in_single_quote = False
    in_double_quote = False
    in_backtick = False
    escape_next = False
    abs_brace_pos = -1

    for i in range(start_of_line, len(content)):
        ch = content[i]

        if escape_next:
            escape_next = False
            continue

        if ch == '\\':
            escape_next = True
            continue

        # Toggle string states
        if ch == "'" and not in_double_quote and not in_backtick:
            in_single_quote = not in_single_quote
        elif ch == '"' and not in_single_quote and not in_backtick:
            in_double_quote = not in_double_quote
        elif ch == '`' and not in_single_quote and not in_double_quote:
            in_backtick = not in_backtick

        # Look for opening brace when not inside a string
        if not in_single_quote and not in_double_quote and not in_backtick:
            if ch == '{':
                abs_brace_pos = i
                break

    if abs_brace_pos == -1:
        print(f"ERROR: No opening brace found outside strings after pattern '{pattern[:40]}...'")
        return False

    # Now find the matching closing brace from abs_brace_pos
    # Continue tracking string state for the rest of the content
    depth = 0
    i = abs_brace_pos

    while i < len(content):
        ch = content[i]

        if escape_next:
            escape_next = False
            i += 1
            continue

        if ch == '\\':
            escape_next = True
            i += 1
            continue

        # Toggle string states
        if ch == "'" and not in_double_quote and not in_backtick:
            in_single_quote = not in_single_quote
        elif ch == '"' and not in_single_quote and not in_backtick:
            in_double_quote = not in_double_quote
        elif ch == '`' and not in_single_quote and not in_double_quote:
            in_backtick = not in_backtick

        # Only count braces when not inside a string
        if not in_single_quote and not in_double_quote and not in_backtick:
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    # Found the matching closing brace
                    # Replace from abs_brace_pos to i+1 (inclusive) with new_body
                    before_block = content[:abs_brace_pos]
                    after_block = content[i + 1:]
                    new_content = before_block + new_body + after_block
                    write_file(filepath, new_content)
                    print(f"Replaced block from line {line_idx + 1} matching '{pattern[:40]}...' in {filepath}")
                    return True

        i += 1

    print(f"ERROR: Could not find matching closing brace for pattern '{pattern[:40]}...'")
    return False


def cmd_stdin_replace_pyblock(filepath, pattern):
    """Replace a Python indentation-based block by reading the new body from stdin.
    
    Takes the pattern (e.g., 'def format_sql_string') from command-line args
    and reads the new body content from stdin via heredoc/pipe.
    This avoids shell escaping issues for complex multi-line function bodies.
    
    Usage:
      cat << 'PYEOF' | patch.py file.py stdin-replace-pyblock "def my_function"
      def my_function(...):
          # body here
      PYEOF
    """
    new_body = sys.stdin.read()
    if not new_body:
        print("ERROR: No input received from stdin")
        return False
    # Use the existing cmd_replace_pyblock logic
    return cmd_replace_pyblock(filepath, pattern, new_body)



def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    filepath = sys.argv[1]
    action = sys.argv[2]

    if not os.path.exists(filepath):
        print(f"ERROR: File not found: {filepath}")
        sys.exit(1)

    success = True

    if action == 'replace':
        if len(sys.argv) < 5:
            print("Usage: patch.py <file> replace <old> <new>")
            sys.exit(1)
        success = cmd_replace(filepath, sys.argv[3], sys.argv[4])
    elif action == 'stdin-replace':
        success = cmd_stdin_replace(filepath)
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
    elif action == 'replace-range':
        if len(sys.argv) < 6:
            print("Usage: patch.py <file> replace-range <start_pattern> <end_pattern> <new_text>")
            sys.exit(1)
        success = cmd_replace_range(filepath, sys.argv[3], sys.argv[4], sys.argv[5])
    elif action == 'replace-block':
        if len(sys.argv) < 5:
            print("Usage: patch.py <file> replace-block <pattern> <new_body>")
            sys.exit(1)
        success = cmd_replace_block(filepath, sys.argv[3], sys.argv[4])
    elif action == 'stdin-replace-pyblock':
        if len(sys.argv) < 4:
            print("Usage: patch.py <file> stdin-replace-pyblock <pattern>")
            sys.exit(1)
        success = cmd_stdin_replace_pyblock(filepath, sys.argv[3])
    elif action == 'replace-pyblock':
        if len(sys.argv) < 5:
            print("Usage: patch.py <file> replace-pyblock <pattern> <new_body>")
            sys.exit(1)
        success = cmd_replace_pyblock(filepath, sys.argv[3], sys.argv[4])
    elif action == 'replace-line':
        if len(sys.argv) < 5:
            print("Usage: patch.py <file> replace-line <pattern> <new_line_text>")
            sys.exit(1)
        success = cmd_replace_line(filepath, sys.argv[3], sys.argv[4])
    else:
        print(f"Unknown action: {action}")
        print(__doc__)
        sys.exit(1)

    if not success:
        sys.exit(1)


if __name__ == '__main__':
    main()
