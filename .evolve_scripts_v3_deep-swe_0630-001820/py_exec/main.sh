#!/usr/bin/env bash
set -euo pipefail

# py_exec - Run Python code with auto-venv activation and environment variables.
# Usage: py_exec [options] [--dir=DIR] <code_string> | -f <script.py> [args...]
#        py_exec [--dir=DIR] --module <module> [args...]          - Run python -m <module> <args>
#        py_exec [--dir=DIR] --stdin                              - Read Python code from stdin
#        py_exec [--dir=DIR] --entry-points <group> [--has-entry <name>]  - List/check entry points
#        py_exec [--dir=DIR] --check-import <module>              - Verify a Python module/class can be imported
#        py_exec [--dir=DIR] --find-package <package>             - Find a Python package installation location
# Options:
#   -f <script.py> [args...]    Run a script file
#   --module <name> [args...]    Run a Python module (python -m)
#   --stdin                     Read Python code from stdin (heredoc support)
#   --check, --check-syntax <file.py>  Syntax check only
#   --check-import <module>      Verify a Python module/class can be imported
#   -e, --env=KEY=val           Set env var (repeatable)
#   --dir=DIR                   Working directory to cd into before running
#   --entry-points <group>      List entry points for a Python package group
#     --has-entry <name>        With --entry-points, check if a specific entry point name exists
#   --find-package <package>    Find a Python package's installation directory
#   --stevedore-extensions <group>  Inspect stevedore extension manager for a namespace (lists extensions, plugins, test IDs)

WORKDIR=""
SCRIPT=""
SCRIPT_FILE=""
MODULE=""
CHECK_SYNTAX=false
STDIN_MODE=false
ENVS=()
ENTRY_POINTS_GROUP=""
ENTRY_POINTS_CHECK=""
CHECK_IMPORT=""
CHECK_IMPORT_ARGS=()
FIND_PACKAGE=""
STEVEDORE_GROUP=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dir=*)
            WORKDIR="${1#*=}"
            shift
            ;;
        --dir)
            WORKDIR="$2"
            shift 2
            ;;
        -f)
            SCRIPT_FILE="$2"
            shift 2
            break
            ;;
        --module)
            MODULE="$2"
            shift 2
            break
            ;;
        --stdin)
            STDIN_MODE=true
            shift
            ;;
        --check|--check-syntax)
            CHECK_SYNTAX=true
            shift
            ;;
        --entry-points)
            ENTRY_POINTS_GROUP="$2"
            shift 2
            ;;
        --has-entry)
            ENTRY_POINTS_CHECK="$2"
            shift 2
            ;;
        --has-entry=*)
            ENTRY_POINTS_CHECK="${1#*=}"
            shift
            ;;
        --check-import)
            CHECK_IMPORT="$2"
            shift 2
            ;;
        --check-import=*)
            CHECK_IMPORT="${1#*=}"
            shift
            ;;
        --check-import-args)
            shift
            while [[ $# -gt 0 ]] && ! [[ "$1" =~ ^-- ]]; do
                CHECK_IMPORT_ARGS+=("$1")
                shift
            done
            ;;
        --check-import-args=*)
            CHECK_IMPORT_ARGS+=("${1#*=}")
            shift
            ;;
        -e|--env)
            ENVS+=("$2")
            shift 2
            ;;
        --env=*)
            ENVS+=("${1#*=}")
            shift
            ;;
        --find-package)
            FIND_PACKAGE="$2"
            shift 2
            ;;
        --find-package=*)
            FIND_PACKAGE="${1#*=}"
            shift
            ;;
        --stevedore-extensions)
            STEVEDORE_GROUP="$2"
            shift 2
            ;;
        --stevedore-extensions=*)
            STEVEDORE_GROUP="${1#*=}"
            shift
            ;;
        -*)
            echo "Unknown option: $1" >&2
            exit 1
            ;;
        *)
            SCRIPT="$1"
            shift
            ;;
    esac
done

# Change to working directory if specified
if [[ -n "$WORKDIR" ]]; then
    cd "$WORKDIR" || { echo "Error: Cannot cd to $WORKDIR" >&2; exit 1; }
fi

# Export env vars
for env_var in "${ENVS[@]}"; do
    export "$env_var" 2>/dev/null || true
done

# Activate virtualenv if present
if [[ -f "venv/bin/activate" ]]; then
    source venv/bin/activate 2>/dev/null || true
elif [[ -f ".venv/bin/activate" ]]; then
    source .venv/bin/activate 2>/dev/null || true
fi

# Find package mode: locate a Python package's installation directory
if [[ -n "$FIND_PACKAGE" ]]; then
    python3 -c "
import sys, importlib.util, importlib.metadata as md

pkg = sys.argv[1]
try:
    spec = importlib.util.find_spec(pkg)
    if spec is None or spec.origin is None:
        try:
            dist = md.distribution(pkg)
            if dist:
                loc = dist.locate_file('')
                print(pkg + ' is installed at: ' + str(loc))
                if hasattr(dist, '_path'):
                    print('Package metadata: ' + str(dist._path))
                sys.exit(0)
        except Exception:
            pass
        print('Package not found: ' + pkg, file=sys.stderr)
        sys.exit(1)
    loc = spec.origin
    print(pkg + ' is installed at: ' + str(loc))
    for name in [pkg, pkg.replace('-', '_'), pkg.replace('_', '-')]:
        try:
            dist = md.distribution(name)
            if dist and hasattr(dist, '_path'):
                print('Package metadata: ' + str(dist._path))
                break
        except Exception:
            continue
except Exception as e:
    print('Error finding package: ' + str(e), file=sys.stderr)
    sys.exit(1)
" "$FIND_PACKAGE" 2>&1 || true
    exit 0
fi

# Stevedore extensions mode: inspect stevedore extension manager for a namespace
if [[ -n "$STEVEDORE_GROUP" ]]; then
    python3 -c "
import sys
try:
    from stevedore import extension
    mgr = extension.ExtensionManager(
        namespace=sys.argv[1],
        invoke_on_load=False,
        verify_requirements=False,
    )
    print(f'Total extensions: {len(mgr.extensions)}')
    for ext in mgr:
        print(f'  {ext.name}: {ext.plugin}')
        if hasattr(ext.plugin, '_test_id'):
            print(f'    _test_id: {ext.plugin._test_id}')
        if hasattr(ext.plugin, '_checks'):
            print(f'    _checks: {ext.plugin._checks}')
except ImportError:
    print('stevedore not installed, trying importlib.metadata...', file=sys.stderr)
    try:
        from importlib.metadata import entry_points
        eps = entry_points(group=sys.argv[1])
        print(f'Total entry points (via importlib.metadata): {len(eps)}')
        for ep in sorted(eps, key=lambda e: e.name):
            attr = ep.attr if ep.attr else '?'
            print(f'  {ep.name}: {ep.module}:{attr}')
    except Exception as e2:
        print(f'Error: {e2}', file=sys.stderr)
        sys.exit(1)
except Exception as e:
    print(f'Error: {e}', file=sys.stderr)
    sys.exit(1)
" "$STEVEDORE_GROUP" 2>&1 || true
    exit 0
fi

# Entry points mode: list Python entry points for a given group
if [[ -n "$ENTRY_POINTS_GROUP" ]]; then
    if [[ -n "$ENTRY_POINTS_CHECK" ]]; then
        python3 -c "
import sys
from importlib.metadata import entry_points
group = sys.argv[1]
check_name = sys.argv[2]
eps = entry_points(group=group)
found = [ep for ep in eps if ep.name == check_name]
if found:
    ep = found[0]
    attr = ep.attr if ep.attr else '?'
    print(check_name + ' found: ' + ep.module + ':' + attr)
    sys.exit(0)
else:
    names = sorted(ep.name for ep in eps)
    print(check_name + ' NOT found in group \"' + group + '\"', file=sys.stderr)
    if names:
        print('Available names: ' + ', '.join(names), file=sys.stderr)
    sys.exit(1)
" "$ENTRY_POINTS_GROUP" "$ENTRY_POINTS_CHECK" 2>&1 || true
    else
        python3 -c "
import sys
try:
    from importlib.metadata import entry_points
    eps = entry_points(group=sys.argv[1])
    for ep in sorted(eps, key=lambda e: e.name):
        attr = ep.attr if ep.attr else '?'
        print(ep.name + ': ' + ep.module + ':' + attr)
except Exception as e:
    print('Error: ' + str(e), file=sys.stderr)
    sys.exit(1)
" "$ENTRY_POINTS_GROUP" 2>&1 || true
    fi
    exit 0
fi

# Check import mode: verify that a Python module/class can be imported
if [[ -n "$CHECK_IMPORT" ]]; then
    IMPORT_PATH="$CHECK_IMPORT"
    python3 -c "
import sys
import importlib

IMPORT_PATH = sys.argv[1]

parts = IMPORT_PATH.split('.')
for i in range(len(parts), 0, -1):
    candidate = '.'.join(parts[:i])
    try:
        mod = importlib.import_module(candidate)
        result = mod
        if i < len(parts):
            for attr in parts[i:]:
                result = getattr(result, attr)
        print('Import successful: ' + IMPORT_PATH + ' -> ' + str(result))
        sys.exit(0)
    except (ImportError, AttributeError):
        continue

print('Import failed: ' + IMPORT_PATH + ': No module could be resolved', file=sys.stderr)
sys.exit(1)
" "$IMPORT_PATH" 2>&1 || true
    exit 0
fi

if [[ "$CHECK_SYNTAX" == true ]]; then
    for f in "$@"; do
        python3 -m py_compile "$f" 2>&1 || true
    done
    exit 0
fi

if [[ -n "$MODULE" ]]; then
    python3 -m "$MODULE" "$@" 2>&1 || true
elif [[ -n "$SCRIPT_FILE" ]]; then
    python3 "$SCRIPT_FILE" "$@" 2>&1 || true
elif [[ "$STDIN_MODE" == true ]]; then
    python3 - 2>&1 || true
elif [[ -n "$SCRIPT" ]]; then
    python3 -c "$SCRIPT" 2>&1 || true
else
    echo "Usage: py_exec [options] [--dir=DIR] <code_string> | -f <script.py> [args...]" >&2
    echo "       py_exec [--dir=DIR] --module <module> [args...]" >&2
    echo "       py_exec [--dir=DIR] --stdin (read code from stdin)" >&2
    echo "       py_exec [--dir=DIR] --entry-points <group> [--has-entry <name>]" >&2
    echo "       py_exec [--dir=DIR] --check-import <module>" >&2
    echo "       py_exec [--dir=DIR] --find-package <package>"
    echo "       py_exec [--dir=DIR] --stevedore-extensions <group>" >&2
    exit 1
fi
