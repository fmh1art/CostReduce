#!/bin/bash
# smart_source_reader - Read source files intelligently
# Usage: smart_source_reader <target> [action] [pattern_or_line_start] [line_end]

TARGET="$1"
ACTION="${2:-all}"
ARG3="$3"
ARG4="$4"

if [ -z "$TARGET" ]; then
    echo "Usage: smart_source_reader <target> [action] [pattern_or_line_start] [line_end]"
    echo "  target: file path, or comma-sep file:range specs (batch), or glob (batch)"
    echo "  action: grep, lines, functions, all (default), batch"
    exit 1
fi

show_lines() {
    local file="$1"
    local start="$2"
    local end="$3"
    if [ ! -f "$file" ]; then
        echo "Error: File '$file' not found" >&2
        return 1
    fi
    if [ -z "$end" ]; then
        end=$(wc -l < "$file")
    fi
    nl -ba "$file" 2>/dev/null | sed -n "${start},${end}p"
}

show_functions() {
    local file="$1"
    if [ ! -f "$file" ]; then
        echo "Error: File '$file' not found" >&2
        return 1
    fi
    local ext="${file##*.}"
    case "$ext" in
        py)
            grep -n "^def \|^class \|^    def \|^    class \|^async def \|^    async def \|^@\|^\s*@" "$file" 2>/dev/null
            ;;
        ts|tsx|js|jsx)
            grep -n "^export function\|^function\|^export class\|^class\|^export const\|^export interface\|^interface\|^export type\|^export default\|^const .* = \|^export enum\|^enum " "$file" 2>/dev/null
            ;;
        go)
            grep -n "^func \|^type \|^struct {\|^func (\|^type .* struct\|^type .* interface" "$file" 2>/dev/null
            ;;
        rs)
            # Rust: support pub(crate) fn, pub fn, async fn, struct, enum, trait, impl, type, const, static, mod, macro_rules!
            grep -n "^pub\|^fn\|^struct\|^enum\|^trait\|^impl\|^type\|^const\|^static\|^mod\|^async\|^macro_rules!\|^use\|^#\[" "$file" 2>/dev/null | grep -v "^.*#\[cfg(test)\]" | head -80
            ;;
        c|cpp|h|hpp)
            grep -n "^int \|^void \|^char \|^static\|^struct\|^class \|^unsigned\|^long\|^float\|^double\|^size_t\|^const\|^#define\|^typedef\|^enum " "$file" 2>/dev/null
            ;;
        *)
            grep -n "^def \|^function\|^class \|^fn \|^func \|^pub\|^struct\|^enum\|^trait\|^impl\|^type " "$file" 2>/dev/null
            ;;
    esac
}

case "$ACTION" in
    grep)
        PATTERN="${ARG3:-def }"
        if [ -f "$TARGET" ]; then
            grep -n "$PATTERN" "$TARGET" 2>/dev/null
        else
            grep -rn "$PATTERN" "$TARGET" 2>/dev/null
        fi
        ;;
    lines)
        START="${ARG3:-1}"
        END="$ARG4"
        show_lines "$TARGET" "$START" "$END"
        ;;
    functions)
        show_functions "$TARGET"
        ;;
    all)
        echo "=== File: $TARGET ==="
        echo "--- Definitions ---"
        show_functions "$TARGET"
        echo ""
        echo "--- Content (first 200 lines) ---"
        show_lines "$TARGET" 1 200
        ;;
    batch)
        IFS=',' read -ra SPECS <<< "$TARGET"
        for spec in "${SPECS[@]}"; do
            if [[ "$spec" == *":"* ]]; then
                file="${spec%%:*}"
                range="${spec#*:}"
                start="${range%-*}"
                end="${range#*-}"
                echo "=== $file (lines $start-$end) ==="
                show_lines "$file" "$start" "$end"
            elif [[ "$spec" == *"*"* ]]; then
                for f in $spec; do
                    if [ -f "$f" ]; then
                        echo "=== $f (first 200 lines) ==="
                        show_lines "$f" 1 200
                    fi
                done
            else
                echo "=== $spec (first 200 lines) ==="
                show_lines "$spec" 1 200
            fi
            echo ""
        done
        ;;
    *)
        echo "Unknown action: $ACTION"
        exit 1
        ;;
esac
