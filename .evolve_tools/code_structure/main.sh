#!/bin/bash
# code_structure - List function/class/struct/interface/type definitions in source files
# Usage: code_structure <file1> [file2 ...]
#        code_structure --summary <file1> [file2 ...]
#        code_structure -s <file1> [file2 ...]
#
# Scans source files and lists all top-level definitions (functions, classes,
# structs, interfaces, traits, enums, etc.) organized by type with line numbers.
# Supports Python, Go, Rust, TypeScript, JavaScript, Java, C/C++, Kotlin, Ruby, PHP.
# Falls back to generic pattern matching for unsupported languages.
#
# Use --summary or -s for a compact one-line summary per file.
# Saves steps by replacing multiple grep -n "^func\|^type\|^class\|^def" commands.
#
# Examples:
#   code_structure main.go                     # List func/types in a Go file
#   code_structure utils.py handler.py          # List defs/classes in Python files
#   code_structure *.ts                         # List all TypeScript symbols
#   code_structure --summary lib/*.go           # Compact summary of Go files
#   code_structure -s src/**/*.rs               # Summary of Rust files

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 "$DIR/analyze.py" "$@"
