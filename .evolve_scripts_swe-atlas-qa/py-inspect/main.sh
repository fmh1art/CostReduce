#!/bin/bash
# py-inspect - Inspect Python module/class internals: list attributes, print docstrings, call functions, all within a venv
# Usage: py-inspect [--dir=DIR] [--venv=PATH] [--env KEY=val ...] [--attr=ATTR] [--call=FUNC] [--doc] [--code=CODE] module_path

set -euo pipefail

DIR=""
VENV_PATH=""
ATTRS=()
CALL=""
SHOW_DOC=false
EXTRA_CODE=""
ENV_VARS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dir=*)
            DIR="${1#*=}"
            shift
            ;;
        --venv=*)
            VENV_PATH="${1#*=}"
            shift
            ;;
        --env=*)
            ENV_VARS+=("${1#*=}")
            shift
            ;;
        --env)
            shift
            ENV_VARS+=("$1")
            shift
            ;;
        -e)
            shift
            ENV_VARS+=("$1")
            shift
            ;;
        --attr=*)
            ATTRS+=("${1#*=}")
            shift
            ;;
        --call=*)
            CALL="${1#*=}"
            shift
            ;;
        --doc)
            SHOW_DOC=true
            shift
            ;;
        --code=*)
            EXTRA_CODE="${1#*=}"
            shift
            ;;
        --help|-h)
            echo "Usage: $0 [--dir=DIR] [--venv=PATH] [--env KEY=val ...] [--attr=ATTR] [--call=FUNC] [--doc] [--code=CODE] module_path" >&2
            echo ""
            echo "  module_path           Python module/class path to inspect (e.g. limits, flask.Flask)"
            echo "  --attr=ATTR           Print specific attribute(s) (repeatable)"
            echo "  --call=FUNC           Call a function and print its result"
            echo "  --doc                 Show module/class docstring"
            echo "  --code=CODE           Additional Python code to run after import"
            echo "  --dir=DIR             Change to DIR before running"
            echo "  --venv=PATH           Path to venv activate script (default: venv/bin/activate)"
            echo "  --env KEY=val         Set environment variable (repeatable, or -e KEY=val)"
            exit 0
            ;;
        *)
            MODULE_PATH="$1"
            shift
            ;;
    esac
done

if [ -z "${MODULE_PATH:-}" ]; then
    echo "Usage: $0 [--dir=DIR] [--venv=PATH] [--attr=ATTR] ... module_path" >&2
    exit 1
fi

if [ -n "$DIR" ]; then
    cd "$DIR"
fi

# Source venv if it exists
if [ -z "$VENV_PATH" ]; then
    if [ -f "venv/bin/activate" ]; then
        VENV_PATH="venv/bin/activate"
    fi
fi

# Build the Python script
PY_CODE=$(cat << 'PYEOF'
import importlib
import sys
import inspect

module_path = sys.argv[1]
attrs = sys.argv[2].split(",") if sys.argv[2] else []
call_func = sys.argv[3] if sys.argv[3] else None
show_doc = sys.argv[4] == "1"
extra_code = sys.argv[5] if len(sys.argv) > 5 else None

try:
    # Try importing the module
    obj = importlib.import_module(module_path)
except ImportError:
    # Try as a class/attr inside a module
    parts = module_path.split(".")
    for i in range(len(parts), 0, -1):
        mod_name = ".".join(parts[:i])
        try:
            obj = importlib.import_module(mod_name)
            for attr_name in parts[i:]:
                obj = getattr(obj, attr_name)
            break
        except (ImportError, AttributeError):
            continue
    else:
        print(f"Error: Cannot import '{module_path}'")
        sys.exit(1)

result_lines = []

if show_doc:
    doc = inspect.getdoc(obj)
    if doc:
        result_lines.append(f"=== DOCSTRING ===\n{doc}")
    else:
        result_lines.append("(no docstring)")

if not attrs and not call_func and not extra_code:
    # Default: show dir()
    all_attrs = [a for a in dir(obj) if not a.startswith("_")]
    result_lines.append(f"=== {module_path} public attributes ===")
    for a in all_attrs:
        try:
            val = getattr(obj, a)
            if inspect.isclass(val) or inspect.ismodule(val) or inspect.isfunction(val):
                typ = type(val).__name__
                result_lines.append(f"  {a} ({typ})")
            else:
                result_lines.append(f"  {a} = {val!r}")
        except Exception:
            result_lines.append(f"  {a} (unavailable)")
elif attrs:
    result_lines.append(f"=== {module_path} selected attributes ===")
    for a in attrs:
        try:
            val = getattr(obj, a)
            result_lines.append(f"  {a}:")
            if inspect.ismodule(val):
                sub = [x for x in dir(val) if not x.startswith("_")]
                result_lines.append(f"    (module with {len(sub)} public attrs: {', '.join(sub[:10])}{"..." if len(sub) > 10 else ""})")
            elif inspect.isclass(val):
                sub = [x for x in dir(val) if not x.startswith("_")]
                result_lines.append(f"    (class with {len(sub)} public attrs: {', '.join(sub[:10])}{"..." if len(sub) > 10 else ""})")
            else:
                result_lines.append(f"    {val!r}")
        except Exception as e:
            result_lines.append(f"  {a}: ERROR {e}")

if call_func:
    result_lines.append(f"=== {module_path}.{call_func}() ===")
    try:
        func = getattr(obj, call_func)
        if callable(func):
            result = func()
            result_lines.append(f"  {result!r}")
        else:
            result_lines.append(f"  (not callable: {func!r})")
    except Exception as e:
        result_lines.append(f"  ERROR: {e}")

if extra_code:
    result_lines.append(f"=== extra code ===")
    try:
        exec(extra_code, {"mod": obj, "obj": obj})
    except Exception as e:
        result_lines.append(f"  ERROR: {e}")

print("\n".join(result_lines))
PYEOF
)

# Create a temp script
TMPFILE="$(mktemp /tmp/py_inspect_XXXXXX.py)"
printf '%s\n' "$PY_CODE" > "$TMPFILE"

# Build args for the temp script
ATTRS_JOINED=$(IFS=,; echo "${ATTRS[*]}")
CALL_FLAG="${CALL:-}"
DOC_FLAG="0"
[ "$SHOW_DOC" = true ] && DOC_FLAG="1"

# Prepare execution
if [ -n "$VENV_PATH" ] && [ -f "$VENV_PATH" ]; then
    # shellcheck disable=SC1090
    source "$VENV_PATH"
fi

for ev in "${ENV_VARS[@]}"; do
    export "$ev"
done

python "$TMPFILE" "$MODULE_PATH" "$ATTRS_JOINED" "$CALL_FLAG" "$DOC_FLAG" "$EXTRA_CODE"

# Cleanup
rm -f "$TMPFILE"
