#!/usr/bin/env python3
"""
code_structure - List function/class/struct/interface/trait/method definitions
in source code files, organized by language.

Usage:
  python3 analyze.py <file1> [file2 ...]
  python3 analyze.py --summary <file1> [file2 ...]   # Compact one-line summary

Detects language from file extension and prints definitions with line numbers.
"""

import sys
import os
import re
from collections import OrderedDict


def detect_language(filepath):
    ext = os.path.splitext(filepath)[1].lower()
    lang_map = {
        '.py': 'python',
        '.go': 'go',
        '.rs': 'rust',
        '.ts': 'typescript',
        '.tsx': 'typescript',
        '.js': 'javascript',
        '.jsx': 'javascript',
        '.java': 'java',
        '.c': 'c',
        '.h': 'c',
        '.cpp': 'cpp',
        '.cc': 'cpp',
        '.cxx': 'cpp',
        '.hpp': 'cpp',
        '.hh': 'cpp',
        '.kt': 'kotlin',
        '.kts': 'kotlin',
        '.swift': 'swift',
        '.rb': 'ruby',
        '.php': 'php',
        '.sh': 'bash',
        '.bash': 'bash',
        '.zsh': 'bash',
        '.m': 'objectivec',
        '.mm': 'objectivec',
        '.scala': 'scala',
        '.ex': 'elixir',
        '.exs': 'elixir',
    }
    return lang_map.get(ext, None)


def list_python_symbols(lines, filepath):
    results = []
    func_pattern = re.compile(r'^(\s*)def\s+(\w+)\s*\(')
    class_pattern = re.compile(r'^(\s*)class\s+(\w+)')
    async_pattern = re.compile(r'^(\s*)async\s+def\s+(\w+)\s*\(')

    for i, line in enumerate(lines, 1):
        m = async_pattern.match(line)
        if m:
            indent = len(m.group(1))
            kind = "async def" if indent == 0 else "  async def"
            results.append((i, kind, m.group(2)))
            continue
        m = func_pattern.match(line)
        if m:
            indent = len(m.group(1))
            kind = "def" if indent == 0 else "  def"
            results.append((i, kind, m.group(2)))
            continue
        m = class_pattern.match(line)
        if m:
            indent = len(m.group(1))
            kind = "class" if indent == 0 else "  class"
            results.append((i, kind, m.group(2)))
            continue
    return results


def list_go_symbols(lines, filepath):
    results = []
    func_pattern = re.compile(r'^func\s+(\w+)')
    method_pattern = re.compile(r'^func\s+\([^)]+\)\s+(\w+)')
    struct_pattern = re.compile(r'^type\s+(\w+)\s+struct\b')
    interface_pattern = re.compile(r'^type\s+(\w+)\s+interface\b')
    type_pattern = re.compile(r'^type\s+(\w+)\s+')

    for i, line in enumerate(lines, 1):
        m = struct_pattern.match(line)
        if m:
            results.append((i, "struct", m.group(1)))
            continue
        m = interface_pattern.match(line)
        if m:
            results.append((i, "interface", m.group(1)))
            continue
        m = type_pattern.match(line)
        if m:
            results.append((i, "type", m.group(1)))
            continue
        m = func_pattern.match(line)
        if m:
            results.append((i, "func", m.group(1)))
            continue
        m = method_pattern.match(line)
        if m:
            results.append((i, "method", m.group(1)))
            continue
    return results


def list_rust_symbols(lines, filepath):
    results = []
    fn_pattern = re.compile(r'^(\s*)fn\s+(\w+)')
    struct_pattern = re.compile(r'^(\s*)struct\s+(\w+)')
    enum_pattern = re.compile(r'^(\s*)enum\s+(\w+)')
    trait_pattern = re.compile(r'^(\s*)trait\s+(\w+)')
    impl_pattern = re.compile(r'^(\s*)impl\s+')
    type_pattern = re.compile(r'^(\s*)type\s+(\w+)')
    unsafe_fn_pattern = re.compile(r'^(\s*)unsafe\s+fn\s+(\w+)')

    for i, line in enumerate(lines, 1):
        m = unsafe_fn_pattern.match(line)
        if m:
            indent = len(m.group(1))
            kind = "unsafe fn" if indent == 0 else "  unsafe fn"
            results.append((i, kind, m.group(2)))
            continue
        m = fn_pattern.match(line)
        if m:
            indent = len(m.group(1))
            kind = "fn" if indent == 0 else "  fn"
            results.append((i, kind, m.group(2)))
            continue
        m = struct_pattern.match(line)
        if m:
            indent = len(m.group(1))
            kind = "struct" if indent == 0 else "  struct"
            results.append((i, kind, m.group(2)))
            continue
        m = enum_pattern.match(line)
        if m:
            indent = len(m.group(1))
            kind = "enum" if indent == 0 else "  enum"
            results.append((i, kind, m.group(2)))
            continue
        m = trait_pattern.match(line)
        if m:
            indent = len(m.group(1))
            kind = "trait" if indent == 0 else "  trait"
            results.append((i, kind, m.group(2)))
            continue
        m = impl_pattern.match(line)
        if m:
            indent = len(m.group(1))
            rest = line[m.end():].strip()
            impl_target = rest.split()[0] if rest else ""
            kind = "impl" if indent == 0 else "  impl"
            results.append((i, kind, impl_target.rstrip('{')))
            continue
        m = type_pattern.match(line)
        if m:
            indent = len(m.group(1))
            kind = "type" if indent == 0 else "  type"
            results.append((i, kind, m.group(2)))
            continue
    return results


def list_typescript_symbols(lines, filepath):
    results = []
    func_pattern = re.compile(r'^(export\s+)?(async\s+)?function\s+(\*?\s*)?(\w+)')
    class_pattern = re.compile(r'^(export\s+)?(abstract\s+)?class\s+(\w+)')
    interface_pattern = re.compile(r'^(export\s+)?interface\s+(\w+)')
    type_alias = re.compile(r'^(export\s+)?type\s+(\w+)')
    enum_pattern = re.compile(r'^(export\s+)?enum\s+(\w+)')
    const_func = re.compile(r'^(export\s+)?(const|let|var)\s+(\w+)\s*[:=]\s*\([^)]*\)\s*=>')
    module_pattern = re.compile(r'^(export\s+)?(declare\s+)?module\s+[\'\"](.+?)[\'\"]')
    namespace_pattern = re.compile(r'^(export\s+)?namespace\s+(\w+)')

    for i, line in enumerate(lines, 1):
        m = func_pattern.match(line)
        if m:
            name = m.group(4) if m.group(4) else m.group(3) if m.group(3) else ""
            results.append((i, "function", name))
            continue
        m = class_pattern.match(line)
        if m:
            results.append((i, "class", m.group(3)))
            continue
        m = interface_pattern.match(line)
        if m:
            results.append((i, "interface", m.group(2)))
            continue
        m = type_alias.match(line)
        if m:
            results.append((i, "type", m.group(2)))
            continue
        m = enum_pattern.match(line)
        if m:
            results.append((i, "enum", m.group(2)))
            continue
        m = const_func.match(line)
        if m:
            results.append((i, "const fn", m.group(3)))
            continue
        m = module_pattern.match(line)
        if m:
            results.append((i, "module", m.group(3)))
            continue
        m = namespace_pattern.match(line)
        if m:
            results.append((i, "namespace", m.group(2)))
            continue
    return results


def list_javascript_symbols(lines, filepath):
    return list_typescript_symbols(lines, filepath)


def list_java_symbols(lines, filepath):
    results = []
    class_pattern = re.compile(r'^(public\s+|private\s+|protected\s+)?(abstract\s+|final\s+)?(static\s+)?class\s+(\w+)')
    interface_pattern = re.compile(r'^(public\s+)?interface\s+(\w+)')
    enum_pattern = re.compile(r'^(public\s+)?enum\s+(\w+)')
    annotation_pattern = re.compile(r'^(public\s+)?@interface\s+(\w+)')
    method_pattern = re.compile(r'^(public|private|protected)\s+(static\s+)?[\w<>[\]]+\s+(\w+)\s*\(')
    record_pattern = re.compile(r'^(public\s+)?record\s+(\w+)')

    for i, line in enumerate(lines, 1):
        m = class_pattern.match(line)
        if m:
            results.append((i, "class", m.group(4)))
            continue
        m = interface_pattern.match(line)
        if m:
            results.append((i, "interface", m.group(2)))
            continue
        m = enum_pattern.match(line)
        if m:
            results.append((i, "enum", m.group(2)))
            continue
        m = annotation_pattern.match(line)
        if m:
            results.append((i, "@interface", m.group(2)))
            continue
        m = record_pattern.match(line)
        if m:
            results.append((i, "record", m.group(2)))
            continue
        m = method_pattern.match(line)
        if m:
            results.append((i, "method", m.group(3)))
            continue
    return results


def list_c_symbols(lines, filepath):
    results = []
    func_pattern = re.compile(r'^(static\s+|inline\s+|extern\s+)?[\w\s\*]+\s+(\w+)\s*\([^)]*\)\s*\{')
    struct_pattern = re.compile(r'^(typedef\s+)?struct\s+(\w+)')
    enum_pattern = re.compile(r'^(typedef\s+)?enum\s+(\w+)')
    union_pattern = re.compile(r'^(typedef\s+)?union\s+(\w+)')
    macro_pattern = re.compile(r'^#define\s+(\w+)')

    for i, line in enumerate(lines, 1):
        m = struct_pattern.match(line)
        if m:
            results.append((i, "struct", m.group(2)))
            continue
        m = enum_pattern.match(line)
        if m:
            results.append((i, "enum", m.group(2)))
            continue
        m = union_pattern.match(line)
        if m:
            results.append((i, "union", m.group(2)))
            continue
        m = macro_pattern.match(line)
        if m:
            results.append((i, "#define", m.group(1)))
            continue
        m = func_pattern.match(line)
        if m:
            results.append((i, "function", m.group(2)))
            continue
    return results


def list_cpp_symbols(lines, filepath):
    results = list_c_symbols(lines, filepath)
    class_pattern = re.compile(r'^(class|struct)\s+(\w+)')
    namespace_pattern = re.compile(r'^namespace\s+(\w+)')
    
    # Remove struct results that are actually class/struct (re-add with proper type)
    results = [r for r in results if not (r[1] == 'struct' and re.match(r'^(class|struct)\s+', lines[r[0]-1]))]
    
    for i, line in enumerate(lines, 1):
        m = class_pattern.match(line)
        if m:
            results.append((i, m.group(1), m.group(2)))
            continue
        m = namespace_pattern.match(line)
        if m:
            results.append((i, "namespace", m.group(1)))
            continue
    return sorted(results, key=lambda x: x[0])


def list_kotlin_symbols(lines, filepath):
    results = []
    fun_pattern = re.compile(r'^(override\s+|open\s+|inline\s+)?(suspend\s+)?fun\s+(\w+)')
    class_pattern = re.compile(r'^(data\s+|sealed\s+|open\s+|abstract\s+)?class\s+(\w+)')
    interface_pattern = re.compile(r'^interface\s+(\w+)')
    object_pattern = re.compile(r'^(data\s+)?object\s+(\w+)')
    enum_pattern = re.compile(r'^enum\s+class\s+(\w+)')

    for i, line in enumerate(lines, 1):
        m = fun_pattern.match(line)
        if m:
            results.append((i, "fun", m.group(3)))
            continue
        m = class_pattern.match(line)
        if m:
            results.append((i, "class", m.group(2)))
            continue
        m = interface_pattern.match(line)
        if m:
            results.append((i, "interface", m.group(1)))
            continue
        m = object_pattern.match(line)
        if m:
            results.append((i, "object", m.group(2)))
            continue
        m = enum_pattern.match(line)
        if m:
            results.append((i, "enum", m.group(1)))
            continue
    return results


def list_ruby_symbols(lines, filepath):
    results = []
    def_pattern = re.compile(r'^(def|def self\.)\s+(\w+)')
    class_pattern = re.compile(r'^class\s+(\w+)')
    module_pattern = re.compile(r'^module\s+(\w+)')

    for i, line in enumerate(lines, 1):
        m = def_pattern.match(line)
        if m:
            results.append((i, "def", m.group(2)))
            continue
        m = class_pattern.match(line)
        if m:
            results.append((i, "class", m.group(1)))
            continue
        m = module_pattern.match(line)
        if m:
            results.append((i, "module", m.group(1)))
            continue
    return results


def list_php_symbols(lines, filepath):
    results = []
    func_pattern = re.compile(r'^(public\s+|private\s+|protected\s+)?(static\s+)?function\s+(\w+)')
    class_pattern = re.compile(r'^(abstract\s+|final\s+)?class\s+(\w+)')
    interface_pattern = re.compile(r'^interface\s+(\w+)')
    trait_pattern = re.compile(r'^trait\s+(\w+)')
    enum_pattern = re.compile(r'^enum\s+(\w+)')

    for i, line in enumerate(lines, 1):
        m = func_pattern.match(line)
        if m:
            results.append((i, "function", m.group(3)))
            continue
        m = class_pattern.match(line)
        if m:
            results.append((i, "class", m.group(2)))
            continue
        m = interface_pattern.match(line)
        if m:
            results.append((i, "interface", m.group(1)))
            continue
        m = trait_pattern.match(line)
        if m:
            results.append((i, "trait", m.group(1)))
            continue
        m = enum_pattern.match(line)
        if m:
            results.append((i, "enum", m.group(1)))
            continue
    return results


def list_bash_symbols(lines, filepath):
    """List bash functions: function name { or name() {"""
    results = []
    func_keyword = re.compile(r'^\s*function\s+(\w+)')
    func_parens = re.compile(r'^\s*(\w+)\s*\(\s*\)\s*(\{|$|#)')

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        m = func_keyword.match(stripped)
        if m:
            results.append((i, "function", m.group(1)))
            continue
        m = func_parens.match(stripped)
        if m and not stripped.startswith('#'):
            # Skip common bash keywords that might look like functions
            skip_keywords = {'if', 'then', 'else', 'elif', 'fi', 'for', 'while', 'do', 'done',
                           'case', 'esac', 'in', 'return', 'exit', 'break', 'continue',
                           'local', 'export', 'declare', 'typeset', 'unset', 'readonly',
                           'trap', 'source', '.', 'eval', 'exec', 'let', 'set', 'unset',
                           'shift', 'select', 'until', 'function', 'time'}
            name = m.group(1)
            if name not in skip_keywords:
                results.append((i, "function", name))
            continue
    return results


LANGUAGE_PARSERS = {
    'python': list_python_symbols,
    'go': list_go_symbols,
    'rust': list_rust_symbols,
    'typescript': list_typescript_symbols,
    'javascript': list_javascript_symbols,
    'java': list_java_symbols,
    'c': list_c_symbols,
    'cpp': list_cpp_symbols,
    'kotlin': list_kotlin_symbols,
    'ruby': list_ruby_symbols,
    'php': list_php_symbols,
    'bash': list_bash_symbols,
}


def list_generic_symbols(lines, filepath):
    """Fallback: look for common patterns across many languages."""
    results = []
    patterns = [
        (r'^\s*(?:public\s+|private\s+|protected\s+)?(?:static\s+|virtual\s+|override\s+)?(?:async\s+)?(?:function|def|fun|fn|sub)\s+(\w+)', 'function/def'),
        (r'^\s*(?:public\s+|private\s+|protected\s+|abstract\s+|sealed\s+|data\s+)?(?:class|struct|interface|trait|enum|object|record|module)\s+(\w+)', 'type'),
        (r'^\s*type\s+(\w+)', 'type alias'),
        (r'^\s*#\s*define\s+(\w+)', 'macro'),
    ]
    for i, line in enumerate(lines, 1):
        for pattern, kind in patterns:
            m = re.match(pattern, line)
            if m:
                results.append((i, kind, m.group(1)))
                break
    return results


def process_file(filepath, show_summary=False):
    if not os.path.isfile(filepath):
        print(f"=== {filepath} (FILE NOT FOUND) ===")
        return

    try:
        with open(filepath, 'r') as f:
            lines = f.readlines()
    except Exception as e:
        print(f"=== {filepath} (ERROR: {e}) ===")
        return

    lang = detect_language(filepath)
    parser = LANGUAGE_PARSERS.get(lang, list_generic_symbols)
    symbols = parser(lines, filepath)
    
    total_lines = len(lines)
    
    if show_summary:
        counts = {}
        for _, kind, _ in symbols:
            base_kind = kind.strip().split()[0] if kind.strip() else kind.strip()
            counts[base_kind] = counts.get(base_kind, 0) + 1
        summary_parts = [f"{k}:{v}" for k, v in sorted(counts.items())]
        print(f"=== {filepath} ({total_lines} lines, {sum(counts.values())} defs) ===")
        if summary_parts:
            print(f"  Summary: {', '.join(summary_parts)}")
        else:
            print(f"  No definitions found")
        return

    lang_name = lang if lang else "unknown"
    print(f"=== {filepath} ({total_lines} lines, {lang_name}) ===")
    
    if not symbols:
        print("  (no definitions found)")
        return
    
    grouped = OrderedDict()
    for line_num, kind, name in symbols:
        if kind not in grouped:
            grouped[kind] = []
        grouped[kind].append((line_num, name))
    
    for kind, items in grouped.items():
        label = kind.capitalize() if kind[0].isalpha() else kind
        print(f"\n  {label}s:")
        for line_num, name in items:
            if name:
                print(f"    L{line_num:>6}  {name}")
            else:
                print(f"    L{line_num:>6}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    show_summary = False
    files = []
    
    for arg in sys.argv[1:]:
        if arg == '--summary' or arg == '-s':
            show_summary = True
        elif arg == '--help' or arg == '-h':
            print(__doc__)
            sys.exit(0)
        else:
            files.append(arg)
    
    if not files:
        print("Error: No files specified.")
        print(__doc__)
        sys.exit(1)
    
    for filepath in files:
        process_file(filepath, show_summary)
        print()


if __name__ == '__main__':
    main()
