#!/usr/bin/env python3
"""
python_import_manager - Safely manage Python imports.

Actions:
  add <import_stmt>         - Add an import with proper placement
  remove <import_stmt>      - Remove an import (checks usage first)
  force-remove <import_stmt> - Remove an import without checking usage
  check-usage <name>        - Check if a name/module is referenced
  list                      - List all import lines grouped by category

Usage:
  python3 importer.py <file> add "from contextlib import contextmanager"
  python3 importer.py <file> remove "import os"
  python3 importer.py <file> check-usage "os"
  python3 importer.py <file> list
"""

import sys
import os
import re
import ast


def read_file(path):
    with open(path, 'r') as f:
        return f.read()


def write_file(path, content):
    with open(path, 'w') as f:
        f.write(content)


def classify_import(import_stmt):
    """Classify an import as stdlib, third-party, or local."""
    m = re.match(r'^\s*(?:from\s+(\S+)\s+import)|(?:import\s+(\S+))', import_stmt)
    if not m:
        return 'unknown'
    
    module = m.group(1) or m.group(2)
    if not module:
        return 'unknown'
    
    base_module = module.split('.')[0]
    
    stdlib_modules = {
        'abc', 'aifc', 'argparse', 'array', 'ast', 'asynchat', 'asyncio',
        'asyncore', 'atexit', 'audioop', 'base64', 'bdb', 'binascii',
        'binhex', 'bisect', 'builtins', 'bz2', 'calendar', 'cgi', 'cgitb',
        'chunk', 'cmath', 'cmd', 'code', 'codecs', 'codeop', 'collections',
        'colorsys', 'compileall', 'concurrent', 'configparser', 'contextlib',
        'contextvars', 'copy', 'copyreg', 'cProfile', 'crypt', 'csv',
        'ctypes', 'curses', 'dataclasses', 'datetime', 'dbm', 'decimal',
        'difflib', 'dis', 'distutils', 'doctest', 'email', 'encodings',
        'enum', 'errno', 'faulthandler', 'fcntl', 'filecmp', 'fileinput',
        'fnmatch', 'fractions', 'ftplib', 'functools', 'gc', 'getopt',
        'getpass', 'gettext', 'glob', 'graphlib', 'grp', 'gzip',
        'hashlib', 'heapq', 'hmac', 'html', 'http', 'idlelib', 'imaplib',
        'imghdr', 'imp', 'importlib', 'inspect', 'io', 'ipaddress',
        'itertools', 'json', 'keyword', 'lib2to3', 'linecache', 'locale',
        'logging', 'lzma', 'mailbox', 'mailcap', 'marshal', 'math',
        'mimetypes', 'mmap', 'modulefinder', 'multiprocessing', 'netrc',
        'nis', 'nntplib', 'numbers', 'operator', 'optparse', 'os',
        'ossaudiodev', 'pathlib', 'pdb', 'pickle', 'pickletools', 'pipes',
        'pkgutil', 'platform', 'plistlib', 'poplib', 'posix', 'posixpath',
        'pprint', 'profile', 'pstats', 'pty', 'pwd', 'py_compile',
        'pyclbr', 'pydoc', 'queue', 'quopri', 'random', 're', 'readline',
        'reprlib', 'resource', 'rlcompleter', 'runpy', 'sched', 'secrets',
        'select', 'selectors', 'shelve', 'shlex', 'shutil', 'signal',
        'site', 'smtpd', 'smtplib', 'sndhdr', 'socket', 'socketserver',
        'sqlite3', 'ssl', 'stat', 'statistics', 'string', 'stringprep',
        'struct', 'subprocess', 'sunau', 'symtable', 'sys', 'sysconfig',
        'syslog', 'tabnanny', 'tarfile', 'telnetlib', 'tempfile', 'termios',
        'test', 'textwrap', 'threading', 'time', 'timeit', 'tkinter',
        'token', 'tokenize', 'tomllib', 'trace', 'traceback', 'tracemalloc',
        'tty', 'turtle', 'turtledemo', 'types', 'typing', 'unicodedata',
        'unittest', 'urllib', 'uu', 'uuid', 'venv', 'warnings', 'wave',
        'weakref', 'webbrowser', 'winreg', 'winsound', 'wsgiref', 'xdrlib',
        'xml', 'xmlrpc', 'zipapp', 'zipfile', 'zipimport', 'zlib', 'zoneinfo',
        '__future__',
    }
    
    if base_module in stdlib_modules:
        return 'stdlib'
    elif base_module.startswith('.'):
        return 'local'
    else:
        return 'third_party'


def find_insert_position(lines, import_category):
    """Find where to insert a new import based on its category."""
    stdlib_end = 0
    third_party_end = 0
    local_end = 0
    in_import_block = False
    last_import_line = -1
    
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            if in_import_block and stripped.startswith('#'):
                continue
            if not in_import_block:
                continue
            else:
                # Check if this is continuation of multi-line import
                prev = lines[i-1].strip() if i > 0 else ''
                if prev.endswith('\\') or prev.endswith('('):
                    continue
                # Blank line after imports - end of block if it was between groups
                if last_import_line >= 0:
                    # Check what's after - might be a blank line between groups
                    pass
                # If we're past all imports, break
                if stripped == '':
                    # Could be a separator between groups, continue
                    continue
                break
        if stripped.startswith('import ') or stripped.startswith('from '):
            in_import_block = True
            cat = classify_import(stripped)
            if cat == 'stdlib':
                stdlib_end = i + 1
            elif cat == 'third_party':
                third_party_end = i + 1
            elif cat == 'local':
                local_end = i + 1
            last_import_line = i
            continue
        if in_import_block:
            # Check for continuation lines
            s = lines[i-1].strip() if i > 0 else ''
            if s.endswith('\\') or s.endswith('(') or stripped == ')':
                # Continuation of multi-line import
                # Check if this line is also an import continuation
                prev_cat = classify_import(lines[i-1])
                if prev_cat == 'stdlib':
                    stdlib_end = i + 1
                elif prev_cat == 'third_party':
                    third_party_end = i + 1
                elif prev_cat == 'local':
                    local_end = i + 1
                last_import_line = i
                continue
            # Non-import, non-continuation line after imports
            break
    
    if import_category == 'stdlib':
        if stdlib_end > 0:
            return stdlib_end
        # No stdlib imports yet, find where to start
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped and not stripped.startswith('#') and not stripped.startswith('"') and not stripped.startswith("'"):
                return i
        return 0
    
    elif import_category == 'third_party':
        if third_party_end > 0:
            return third_party_end
        if stdlib_end > 0:
            return stdlib_end
        return find_insert_position(lines, 'stdlib')
    
    elif import_category == 'local':
        if local_end > 0:
            return local_end
        if third_party_end > 0:
            return third_party_end
        if stdlib_end > 0:
            return stdlib_end
        return find_insert_position(lines, 'stdlib')
    
    return len(lines)


def cmd_add(filepath, import_stmt):
    """Add an import statement with proper placement."""
    content = read_file(filepath)
    
    # Check if import already exists
    import_lines = [l for l in content.split('\n') 
                    if l.strip().startswith('import ') or l.strip().startswith('from ')]
    normalized_new = import_stmt.strip()
    for il in import_lines:
        if il.strip() == normalized_new:
            print(f"Import already exists: {normalized_new}")
            return True
    
    # Check if importing a name that's already imported from another module
    new_from = re.match(r'^\s*from\s+(\S+)\s+import\s+(.+)$', normalized_new)
    if new_from:
        new_module = new_from.group(1)
        new_names = [n.strip() for n in new_from.group(2).split(',')]
        for il in import_lines:
            il_stripped = il.strip()
            m = re.match(r'^\s*from\s+(\S+)\s+import\s+(.+)$', il_stripped)
            if m and m.group(1) != new_module:
                existing_names = [n.strip() for n in m.group(2).split(',')]
                for n in new_names:
                    if n in existing_names:
                        print(f"WARNING: '{n}' is already imported from '{m.group(1)}' (not from '{new_module}')")
    
    lines = content.split('\n')
    category = classify_import(import_stmt)
    
    # Handle __future__ imports - must go at top before any other imports
    if 'from __future__' in import_stmt:
        insert_pos = 0
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped and not stripped.startswith('#') and not stripped.startswith('"') and not stripped.startswith("'"):
                if stripped.startswith('from __future__') or stripped.startswith('import __future__'):
                    insert_pos = i + 1
                else:
                    break
        lines.insert(insert_pos, import_stmt.rstrip())
        write_file(filepath, '\n'.join(lines))
        print(f"Added __future__ import at line {insert_pos + 1}: {import_stmt.strip()}")
        return True
    
    pos = find_insert_position(lines, category)
    
    # Add blank line separator between import groups if needed
    if pos > 0 and pos < len(lines):
        prev_line = lines[pos - 1].strip()
        if prev_line and not prev_line.startswith('#'):
            if prev_line != '':
                lines.insert(pos, '')
                pos += 1
    
    lines.insert(pos, import_stmt.rstrip())
    write_file(filepath, '\n'.join(lines))
    print(f"Added import at line {pos + 1}: {import_stmt.strip()}")
    return True


def cmd_remove(filepath, import_stmt, force=False):
    """Remove an import statement. If not force, checks usage first."""
    content = read_file(filepath)
    normalized = import_stmt.strip()
    
    # Parse what names this import provides
    imported_names = []
    m = re.match(r'^\s*import\s+(\S+)', normalized)
    if m:
        module = m.group(1)
        imported_names.append(module.split('.')[0])
    
    m = re.match(r'^\s*from\s+(\S+)\s+import\s+(.+)$', normalized)
    if m:
        names_part = m.group(2)
        for part in re.split(r',\s*', names_part):
            part = part.strip()
            if part.startswith('('):
                continue
            imported_names.append(part.split(' as ')[0].strip())
    
    if not force and imported_names:
        for name in imported_names:
            if is_name_used(content, name, import_stmt):
                print(f"ERROR: '{name}' is still referenced in the file. Use 'force-remove' to remove anyway, or check-usage for details.")
                return False
    
    # Find and remove the import line(s)
    lines = content.split('\n')
    new_lines = []
    removed = False
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip() == normalized:
            removed = True
            i += 1
            continue
        new_lines.append(line)
        i += 1
    
    if not removed:
        print(f"ERROR: Import not found in file: {normalized}")
        return False
    
    write_file(filepath, '\n'.join(new_lines))
    print(f"Removed import: {normalized}")
    return True


def is_name_used(content, name, import_stmt):
    """Check if a name is actually used in the file content, excluding its own import statement."""
    try:
        tree = ast.parse(content)
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == name:
                # Check if this is in the import statement itself
                if hasattr(node, 'lineno') and hasattr(node, 'col_offset'):
                    # We'll do a simpler check - check if the occurrence is in the import stmt
                    line_text = content.split('\n')[node.lineno - 1] if node.lineno <= len(content.split('\n')) else ''
                    if import_stmt.strip() in line_text:
                        continue
                return True
            # Check attribute access like os.path, os.PathLike
            if isinstance(node, ast.Attribute):
                # Check module.name usage like os.path
                if isinstance(node.value, ast.Name) and node.value.id == name:
                    return True
        return False
    except SyntaxError:
        # Fallback to regex if AST parsing fails
        rest = content.replace(import_stmt, '')
        pattern = re.compile(r'\b' + re.escape(name) + r'\b')
        matches = pattern.findall(rest)
        return len(matches) > 0


def cmd_check_usage(filepath, name):
    """Check if a name is used in the file."""
    content = read_file(filepath)
    
    try:
        tree = ast.parse(content)
        usages = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == name:
                usages.append((node.lineno, node.col_offset, f"Name reference: {name}"))
            if isinstance(node, ast.Attribute):
                if isinstance(node.value, ast.Name) and node.value.id == name:
                    usages.append((node.lineno, node.col_offset, 
                                   f"Attribute access: {name}.{node.attr}"))
        
        if usages:
            print(f"'{name}' is used in {len(usages)} location(s):")
            for lineno, col, desc in sorted(usages):
                lines = content.split('\n')
                context = lines[lineno - 1].strip()
                print(f"  Line {lineno}: {context[:100]}")
            return True
        else:
            print(f"'{name}' does not appear to be used in the file (AST analysis).")
            pattern = re.compile(r'\b' + re.escape(name) + r'\b')
            regex_matches = pattern.findall(content)
            if regex_matches:
                print(f"  (Note: regex found {len(regex_matches)} occurrences, may be in strings/comments)")
            return False
    except SyntaxError as e:
        print(f"Warning: Could not parse file (SyntaxError: {e}). Falling back to regex.")
        pattern = re.compile(r'\b' + re.escape(name) + r'\b')
        matches = pattern.findall(content)
        if matches:
            print(f"'{name}' was found {len(matches)} time(s) via regex search.")
            return True
        else:
            print(f"'{name}' not found in file.")
            return False


def cmd_list(filepath):
    """List all import lines grouped by category."""
    content = read_file(filepath)
    lines = content.split('\n')
    
    imports_by_cat = {'stdlib': [], 'third_party': [], 'local': [], 'unknown': []}
    
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith('import ') or stripped.startswith('from '):
            cat = classify_import(stripped)
            imports_by_cat[cat].append((i + 1, stripped))
    
    for cat in ['stdlib', 'third_party', 'local', 'unknown']:
        items = imports_by_cat[cat]
        if items:
            print(f"\n{cat.upper()} imports:")
            for lineno, text in items:
                print(f"  Line {lineno}: {text}")
    
    total = sum(len(v) for v in imports_by_cat.values())
    print(f"\nTotal: {total} import(s)")


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
    
    if action == 'add':
        if len(sys.argv) < 4:
            print("Usage: importer.py <file> add <import_stmt>")
            sys.exit(1)
        success = cmd_add(filepath, sys.argv[3])
    elif action == 'remove':
        if len(sys.argv) < 4:
            print("Usage: importer.py <file> remove <import_stmt>")
            sys.exit(1)
        success = cmd_remove(filepath, sys.argv[3], force=False)
    elif action == 'force-remove':
        if len(sys.argv) < 4:
            print("Usage: importer.py <file> force-remove <import_stmt>")
            sys.exit(1)
        success = cmd_remove(filepath, sys.argv[3], force=True)
    elif action == 'check-usage':
        if len(sys.argv) < 4:
            print("Usage: importer.py <file> check-usage <name>")
            sys.exit(1)
        success = cmd_check_usage(filepath, sys.argv[3])
        if success:
            sys.exit(0)
        else:
            sys.exit(1)
    elif action == 'list':
        cmd_list(filepath)
        success = True
    else:
        print(f"Unknown action: {action}")
        print(__doc__)
        sys.exit(1)
    
    if not success:
        sys.exit(1)


if __name__ == '__main__':
    main()
