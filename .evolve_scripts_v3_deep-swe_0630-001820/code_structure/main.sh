#!/usr/bin/env bash
set -euo pipefail

# code_structure - List functions, structs, classes, interfaces, traits, enums in source files.
# Usage: code_structure [--dir=DIR] [--summary|-s] [--grep=PATTERN] file1 [file2...]

WORKDIR=""
SHOW_SUMMARY=false
GREP_FILTER=""
FILES=()

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
        --summary|-s)
            SHOW_SUMMARY=true
            shift
            ;;
        --grep=*)
            GREP_FILTER="${1#*=}"
            shift
            ;;
        --grep)
            GREP_FILTER="$2"
            shift 2
            ;;
        -*)
            echo "Unknown option: $1" >&2
            exit 1
            ;;
        *)
            FILES+=("$1")
            shift
            ;;
    esac
done

# Change to working directory if specified
if [[ -n "$WORKDIR" ]]; then
    cd "$WORKDIR" || { echo "Error: Cannot cd to $WORKDIR" >&2; exit 1; }
fi

if [[ ${#FILES[@]} -eq 0 ]]; then
    echo "Usage: code_structure [--dir=DIR] [--summary|-s] [--grep=PATTERN] file1 [file2...]" >&2
    exit 1
fi

# Helper: count grep matches safely (grep -c outputs "0" even on exit 1)
count_matches() {
    local pat="$1"
    local file="$2"
    local result
    result=$(grep -cE "$pat" "$file" 2>/dev/null || true)
    echo "$result"
}

# Detect language from file extension
classify_file() {
    local file="$1"
    case "$file" in
        *.go) echo "go" ;;
        *.py) echo "py" ;;
        *.js|*.mjs) echo "js" ;;
        *.ts) echo "ts" ;;
        *.tsx) echo "tsx" ;;
        *.jsx) echo "jsx" ;;
        *.rs) echo "rs" ;;
        *.java) echo "java" ;;
        *.kt|*.kts) echo "kt" ;;
        *.c|*.h) echo "c" ;;
        *.cpp|*.hpp|*.cc|*.cxx) echo "cpp" ;;
        *.rb) echo "rb" ;;
        *.swift) echo "swift" ;;
        *) echo "unknown" ;;
    esac
}

# Run on each file
for file in "${FILES[@]}"; do
    if [[ ! -f "$file" ]]; then
        echo "Error: File not found: $file" >&2
        continue
    fi

    lang=$(classify_file "$file")

    # Determine grep pattern based on language
    case "$lang" in
        go)
            PATTERN='^func |^type .*( struct| interface| =)'
            ;;
        py)
            PATTERN='^class |^def |^    def |^    async def |^async def '
            ;;
        js|ts|jsx|tsx)
            PATTERN='^(export )?(function|class|interface|type |enum) |^(export )?(default )?(function|class) |^    (function|class) '
            ;;
        rs)
            PATTERN='^(pub )?(fn |struct |enum |trait |impl |type |const |macro_rules!) '
            ;;
        java|kt)
            PATTERN='^[[:space:]]*(public|private|protected)?[[:space:]]*(static)?[[:space:]]*(class|interface|enum|@interface|fun) '
            ;;
        c|cpp|h|hpp)
            PATTERN='^(class |struct |enum |typedef |using namespace |#define |template |static inline )'
            ;;
        rb)
            PATTERN='^(class |module |def )'
            ;;
        swift)
            PATTERN='^(public |private |internal |fileprivate |open )?(func |class |struct |enum |protocol |extension |typealias )'
            ;;
        *)
            PATTERN='^(func|function|def |class |struct |interface|enum |type |trait |fn |const |let |var |async def|async fn) '
            ;;
    esac

    if [[ "$SHOW_SUMMARY" == true ]]; then
        # Count occurrences per category using safe helper
        funcs=$(count_matches '^func |^def |^    def |^    async def |^async def |^fn ' "$file")
        types=$(count_matches '^type ' "$file")
        classes=$(count_matches '^class ' "$file")
        structs=$(count_matches '^type .* struct|^struct ' "$file")
        interfaces=$(count_matches '^type .* interface|^interface |^protocol ' "$file")
        enums=$(count_matches '^enum |^type .* enum' "$file")

        components=()
        [[ "$funcs" -gt 0 ]] 2>/dev/null && components+=("functions: $funcs")
        [[ "$types" -gt 0 ]] 2>/dev/null && components+=("types: $types")
        [[ "$classes" -gt 0 ]] 2>/dev/null && components+=("classes: $classes")
        [[ "$structs" -gt 0 ]] 2>/dev/null && components+=("structs: $structs")
        [[ "$interfaces" -gt 0 ]] 2>/dev/null && components+=("interfaces: $interfaces")
        [[ "$enums" -gt 0 ]] 2>/dev/null && components+=("enums: $enums")

        if [[ ${#FILES[@]} -gt 1 ]]; then
            echo "$file: ${components[*]}"
        else
            echo "${components[*]}"
        fi
    else
        # Detailed output: show actual definitions with line numbers
        if [[ ${#FILES[@]} -gt 1 ]]; then
            echo "===== $file ====="
        fi

        if [[ -n "$GREP_FILTER" ]]; then
            grep -nE "$PATTERN" "$file" 2>/dev/null | grep -i "$GREP_FILTER" || true
        else
            grep -nE "$PATTERN" "$file" 2>/dev/null || true
        fi

        if [[ ${#FILES[@]} -gt 1 ]]; then
            echo ""
        fi
    fi
done
