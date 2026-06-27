"""Strip boilerplate (package/import) from source files."""
import sys

def strip_go(lines):
    out = []
    skip_import = False
    idepth = 0
    in_pkg = True
    for line in lines:
        if in_pkg and line.startswith('package '):
            in_pkg = False
            continue
        in_pkg = False
        s = line.strip()
        if s == 'import (' or s == 'import(':
            skip_import = True
            idepth = 1
            continue
        if skip_import:
            if s == ')':
                idepth -= 1
                if idepth <= 0:
                    skip_import = False
                continue
            for ch in s:
                if ch == '(':
                    idepth += 1
                elif ch == ')':
                    idepth -= 1
            if idepth <= 0:
                skip_import = False
            continue
        if s.startswith('import '):
            continue
        out.append(line)
    return out

def strip_python(lines):
    out = []
    found_code = False
    in_docstring = False
    docstring_marker = None
    for line in lines:
        s = line.strip()
        if not found_code:
            if s.startswith('#!') or s.startswith('# -*-'):
                continue
            if not in_docstring and (s.startswith('"""') or s.startswith("'''")):
                in_docstring = True
                docstring_marker = s[:3]
                if docstring_marker and s.endswith(docstring_marker) and len(s) > 3:
                    in_docstring = False
                out.append(line)
                continue
            if in_docstring:
                out.append(line)
                if docstring_marker and docstring_marker in s[len(docstring_marker):]:
                    in_docstring = False
                continue
            if s == '' or s.startswith('#') or s.startswith('import ') or s.startswith('from '):
                continue
            found_code = True
        out.append(line)
    return out

if __name__ == '__main__':
    filepath = sys.argv[1]
    ext = filepath.rsplit('.', 1)[-1].lower() if '.' in filepath else ''
    with open(filepath) as f:
        lines = f.readlines()
    if ext == 'go':
        out = strip_go(lines)
    elif ext == 'py':
        out = strip_python(lines)
    else:
        out = lines
    sys.stdout.write(''.join(out))
