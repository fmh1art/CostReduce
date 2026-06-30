#!/usr/bin/env bash
set -euo pipefail

# code_structure - List functions, structs, classes, interfaces, methods from source files
# Usage: code_structure [--summary] [--context=N] [--py-inspect] [--cd=DIR] file1 [file2 ...]
#
# Supports: Go, TypeScript/JavaScript, Python, Rust, Java, C/C++, Ruby, PHP
# --summary: Compact one-line-per-definition format
# --context=N or -A N: Show N lines of body after each definition (like grep -A N)
#   When --context is used, --summary is automatically enabled.
# --py-inspect: Use Python's inspect.getsource() to extract function/class source code
#   (more accurate than grep -A N; handles decorators, multi-line signatures).
#   Usage: code_structure --py-inspect [--cd=DIR] file.py [name1 name2 ...]
#   If names are given, only those functions/classes are shown (full source).
#   If no names, all top-level definitions are shown.
#   --cd=DIR changes to DIR first; module path is computed relative to DIR.
# --cd=DIR: Change to DIR before processing (for correct Python imports)

SHOW_SUMMARY=false
CONTEXT_LINES=0
PY_INSPECT=false
CD_DIR=""
FILES=()
PY_INSPECT_NAMES=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --summary|-s)
            SHOW_SUMMARY=true
            shift
            ;;
        --context=*)
            CONTEXT_LINES="${1#*=}"
            SHOW_SUMMARY=true
            shift
            ;;
        -A)
            CONTEXT_LINES="$2"
            SHOW_SUMMARY=true
            shift 2
            ;;
        --py-inspect)
            PY_INSPECT=true
            shift
            ;;
        --cd=*)
            CD_DIR="${1#*=}"
            shift
            ;;
        -*)
            echo "Unknown option: $1" >&2
            exit 1
            ;;
        *)
            if $PY_INSPECT && [[ ${#FILES[@]} -ge 1 ]] && [[ ! -f "$1" ]]; then
                PY_INSPECT_NAMES+=("$1")
            else
                FILES+=("$1")
            fi
            shift
            ;;
    esac
done

if [[ ${#FILES[@]} -eq 0 ]]; then
    echo "Usage: $0 [--summary] [--context=N] [--py-inspect] [--cd=DIR] file1 [file2 ...]" >&2
    exit 1
fi

# Handle --py-inspect mode
if $PY_INSPECT; then
    # Build a Python script that uses inspect.getsource() on each file
    PY_SCRIPT=$(mktemp)
    cat > "$PY_SCRIPT" << 'PYEOF'
import sys
import os
import importlib
import inspect

files = os.environ.get('CS_FILES', '').split(':')
names_str = os.environ.get('CS_NAMES', '')
cd_dir = os.environ.get('CS_CD_DIR', '')

# Parse requested names
requested_names = []
if names_str:
    requested_names = [n.strip() for n in names_str.split(',') if n.strip()]

# Resolve cd_dir
if cd_dir:
    abs_cd = os.path.abspath(cd_dir)
    if abs_cd not in sys.path:
        sys.path.insert(0, abs_cd)
    os.chdir(abs_cd)
else:
    abs_cd = os.getcwd()

for filepath in files:
    if not os.path.isfile(filepath):
        print(f"=== {filepath} (file not found) ===")
        continue
    
    print(f"=== {filepath} ===")
    
    # Compute module name - try multiple strategies
    abs_file = os.path.abspath(filepath)
    file_dir = os.path.dirname(abs_file)
    basename = os.path.basename(filepath)
    basename_noext = basename[:-3] if basename.endswith('.py') else basename
    
    modname = None
    
    # Strategy 1: Compute relative path from cd_dir (for package modules)
    try:
        rel_path = os.path.relpath(abs_file, abs_cd)
        # Only use rel_path if it doesn't start with '..' (i.e., file is under cd_dir)
        if not rel_path.startswith('..'):
            if rel_path.endswith('.py'):
                rel_path = rel_path[:-3]
            candidate = rel_path.replace('/', '.').replace('\\', '.').strip('.')
            if candidate:
                modname = candidate
    except (ValueError, OSError):
        pass
    
    # Strategy 2: Use basename only (for standalone files not under cd_dir)
    if modname is None:
        modname = basename_noext
        if file_dir not in sys.path:
            sys.path.insert(0, file_dir)
    
    try:
        # Try to import the module
        mod = importlib.import_module(modname)
        
        # Get all members: functions and classes defined in this module
        all_members = []
        for name in dir(mod):
            obj = getattr(mod, name)
            if inspect.isfunction(obj) or inspect.isclass(obj):
                try:
                    mod_name = getattr(obj, '__module__', None)
                    if mod_name == modname or mod_name is None:
                        all_members.append((name, obj))
                except:
                    pass
        
        # Filter by requested names if any
        if requested_names:
            resolved = []
            for rname in requested_names:
                if '.' in rname:
                    # Dotted name like Calculator.add
                    parts = rname.split('.')
                    obj = mod
                    found = True
                    for part in parts:
                        if hasattr(obj, part):
                            obj = getattr(obj, part)
                        else:
                            found = False
                            break
                    if found:
                        resolved.append((rname, obj))
                else:
                    for n, o in all_members:
                        if n == rname:
                            resolved.append((n, o))
                            break
            all_members = resolved
        
        if not all_members:
            print("  (no functions/classes found)")
            continue
        
        for name, obj in all_members:
            try:
                source = inspect.getsource(obj)
                print(source)
                if not source.endswith('\n'):
                    print()
            except (TypeError, OSError):
                print(f"# {name}: (could not retrieve source)")
                continue
    except Exception as e:
        print(f"  (error loading module '{modname}': {e})")
        continue
PYEOF

    # Prepare env vars
    CS_FILES=""
    sep=""
    for f in "${FILES[@]}"; do
        CS_FILES="${CS_FILES}${sep}${f}"
        sep=":"
    done
    export CS_FILES
    
    if [[ ${#PY_INSPECT_NAMES[@]} -gt 0 ]]; then
        sep=""
        CS_NAMES=""
        for n in "${PY_INSPECT_NAMES[@]}"; do
            CS_NAMES="${CS_NAMES}${sep}${n}"
            sep=","
        done
        export CS_NAMES
    else
        export CS_NAMES=""
    fi
    
    export CS_CD_DIR="$CD_DIR"
    
    python3 "$PY_SCRIPT"
    rm -f "$PY_SCRIPT"
    exit 0
fi

# Build grep patterns per file type
show_structure() {
    local file="$1"
    local ext="${file##*.}"
    local context_flag=""
    [[ "$CONTEXT_LINES" -gt 0 ]] && context_flag="-A $CONTEXT_LINES"

    if ! $SHOW_SUMMARY && [[ "$CONTEXT_LINES" -eq 0 ]]; then
        # Non-summary mode (original behavior)
        case "$ext" in
            go)
                echo "--- Functions ---"
                grep -n '^func ' "$file" 2>/dev/null || echo "(none)"
                echo "--- Types/Structs/Interfaces ---"
                grep -n '^type ' "$file" 2>/dev/null || echo "(none)"
                echo "--- Variables/Constants ---"
                grep -n '^var \|^const ' "$file" 2>/dev/null || echo "(none)"
                ;;
            ts|tsx|js|jsx|mjs|cjs)
                echo "--- Exports ---"
                grep -n '^export ' "$file" 2>/dev/null || echo "(none)"
                echo "--- Functions ---"
                grep -n '^function \|^const \|^let \|^var ' "$file" 2>/dev/null || echo "(none)"
                echo "--- Classes/Interfaces ---"
                grep -n '^class \|^interface ' "$file" 2>/dev/null || echo "(none)"
                ;;
            py)
                echo "--- Functions ---"
                grep -n '^def \|^async def ' "$file" 2>/dev/null || echo "(none)"
                echo "--- Classes ---"
                grep -n '^class ' "$file" 2>/dev/null || echo "(none)"
                ;;
            rs)
                echo "--- Functions ---"
                grep -n '^fn \|^pub fn ' "$file" 2>/dev/null || echo "(none)"
                echo "--- Structs/Enums/Traits ---"
                grep -n '^struct \|^enum \|^trait ' "$file" 2>/dev/null || echo "(none)"
                ;;
            java|kt|scala)
                echo "--- Classes/Interfaces ---"
                grep -n '^class \|^interface \|^enum ' "$file" 2>/dev/null || echo "(none)"
                echo "--- Methods ---"
                grep -n 'public \|private \|protected ' "$file" 2>/dev/null || echo "(none)"
                ;;
            c|cpp|h|hpp)
                echo "--- Structs/Classes ---"
                grep -n '^struct \|^class \|^enum ' "$file" 2>/dev/null || echo "(none)"
                echo "--- Functions/Defines ---"
                grep -n '^#define \|^typedef ' "$file" 2>/dev/null || echo "(none)"
                ;;
            rb)
                echo "--- Methods ---"
                grep -n '^def ' "$file" 2>/dev/null || echo "(none)"
                echo "--- Classes/Modules ---"
                grep -n '^class \|^module ' "$file" 2>/dev/null || echo "(none)"
                ;;
            php)
                echo "--- Functions ---"
                grep -n '^function ' "$file" 2>/dev/null || echo "(none)"
                echo "--- Classes/Interfaces/Traits ---"
                grep -n '^class \|^interface \|^trait ' "$file" 2>/dev/null || echo "(none)"
                ;;
            *)
                grep -n '^func \|^def \|^class \|^function \|^type \|^struct \|^interface \|^pub ' "$file" 2>/dev/null || echo "(no recognizable definitions found)"
                ;;
        esac
    else
        # Summary mode (with optional context)
        local pattern
        case "$ext" in
            go)
                pattern='^func \|^type \|^struct \|^interface \|^var \|^const '
                ;;
            ts|tsx|js|jsx|mjs|cjs)
                pattern='^export \(default \)\?\(function\|class\|interface\|type\|enum\|const\|let\|var\) \|^function \|^class \|^interface '
                ;;
            py)
                pattern='^def \|^class \|^async def '
                ;;
            rs)
                pattern='^fn \|^struct \|^enum \|^trait \|^impl \|^pub \(fn\|struct\|enum\|trait\|impl\)'
                ;;
            java|kt|scala)
                pattern='^public \|^private \|^protected \|^class \|^interface \|^enum '
                ;;
            c|cpp|h|hpp)
                pattern='^#define \|^#include \|^struct \|^class \|^enum \|^typedef '
                ;;
            rb)
                pattern='^def \|^class \|^module '
                ;;
            php)
                pattern='^function \|^class \|^interface \|^trait '
                ;;
            *)
                pattern='^func \|^def \|^class \|^function \|^type \|^struct \|^interface \|^pub '
                ;;
        esac

        if [[ "$CONTEXT_LINES" -gt 0 ]]; then
            # Show definitions with context lines after each match
            grep -n $context_flag "$pattern" "$file" 2>/dev/null || echo "(no definitions found)"
        else
            grep -n "$pattern" "$file" 2>/dev/null || echo "(no definitions found)"
        fi
    fi
}

for file in "${FILES[@]}"; do
    if [[ ! -f "$file" ]]; then
        echo "=== $file (file not found) ===" >&2
        continue
    fi

    echo "=== $file ==="
    show_structure "$file"
    echo
done
