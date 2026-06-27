#!/bin/bash
# Script: go_mod_graph
# Description: Analyze Go module registration patterns in a project.
# Finds all module.Register() and module.RegisterEndpoint() calls with their
# package locations, module names, and surrounding context. Can also show
# interface definitions and implementation registrations.
# Usage: main.sh <project_root> [action=all|register|endpoints|interfaces|graph] [max_matches=N]

PROJECT_ROOT="${1:-/app}"
ACTION="${2:-all}"
MAX_MATCHES="${3:-80}"

if [ ! -d "$PROJECT_ROOT" ]; then
  echo "ERROR: Project root not found: $PROJECT_ROOT"
  exit 1
fi

echo "=== Go Module Registration Analysis ==="
echo "Project root: $PROJECT_ROOT"
echo "Action: $ACTION"
echo ""
GO_FILES=$(find "$PROJECT_ROOT" -type f -name "*.go" -not -path "*/vendor/*" -not -path "*/testdata/*" 2>/dev/null | head -200)
GO_FILE_COUNT=$(find "$PROJECT_ROOT" -type f -name "*.go" -not -path "*/vendor/*" -not -path "*/testdata/*" 2>/dev/null | wc -l)

case "$ACTION" in
  register|all)
    echo "--- Module.Register() calls ---"
    echo ""
    matches=$(grep -rn 'module\.Register(' "$PROJECT_ROOT" --include="*.go" 2>/dev/null | grep -v vendor | grep -v testdata | head -"$MAX_MATCHES")
    if [ -z "$matches" ]; then
      echo "(none found)"
    else
      echo "$matches" | while IFS=: read -r file line rest; do
        # Extract module name from the call: module.Register("name", ...)
        modname=$(echo "$rest" | grep -oP 'module\.Register\(\s*"([^"]+)"' | sed 's/module.Register("//' | sed 's/"//')
        shortfile="${file#$PROJECT_ROOT/}"
        echo "  $shortfile:$line -> $modname"
      done
    fi
    echo ""
    ;;&
  endpoints|all)
    echo "--- Module.RegisterEndpoint() calls ---"
    echo ""
    matches=$(grep -rn 'module\.RegisterEndpoint(' "$PROJECT_ROOT" --include="*.go" 2>/dev/null | grep -v vendor | grep -v testdata | head -"$MAX_MATCHES")
    if [ -z "$matches" ]; then
      echo "(none found)"
    else
      echo "$matches" | while IFS=: read -r file line rest; do
        modname=$(echo "$rest" | grep -oP 'module\.RegisterEndpoint\(\s*"([^"]+)"' | sed 's/module.RegisterEndpoint("//' | sed 's/"//')
        shortfile="${file#$PROJECT_ROOT/}"
        echo "  $shortfile:$line -> $modname"
      done
    fi
    echo ""
    ;;&
  interfaces|all)
    echo "--- Go Interface Definitions ---"
    echo ""
    matches=$(grep -rn '^type .* interface {' "$PROJECT_ROOT" --include="*.go" 2>/dev/null | grep -v vendor | grep -v testdata | head -"$MAX_MATCHES")
    if [ -z "$matches" ]; then
      echo "(none found)"
    else
      echo "$matches" | while IFS=: read -r file line rest; do
        shortfile="${file#$PROJECT_ROOT/}"
        echo "  $shortfile:$line  $rest"
      done
    fi
    echo ""
    ;;&
  graph|all)
    echo "--- Module Dependency Graph ---"
    echo ""
    echo "Modules registered via module.Register():"
    grep -rn 'module\.Register(' "$PROJECT_ROOT" --include="*.go" 2>/dev/null | grep -v vendor | grep -v testdata | while IFS=: read -r file line rest; do
      modname=$(echo "$rest" | grep -oP 'module\.Register\(\s*"([^"]+)"' | sed 's/module.Register("//' | sed 's/"//')
      shortfile="${file#$PROJECT_ROOT/}"
      echo "  [module] $modname  ($shortfile:$line)"
    done
    echo ""
    echo "Endpoints registered via module.RegisterEndpoint():"
    grep -rn 'module\.RegisterEndpoint(' "$PROJECT_ROOT" --include="*.go" 2>/dev/null | grep -v vendor | grep -v testdata | while IFS=: read -r file line rest; do
      modname=$(echo "$rest" | grep -oP 'module\.RegisterEndpoint\(\s*"([^"]+)"' | sed 's/module.RegisterEndpoint("//' | sed 's/"//')
      shortfile="${file#$PROJECT_ROOT/}"
      echo "  [endpoint] $modname  ($shortfile:$line)"
    done
    echo ""
    echo "Interfaces that registering modules implement:"
    grep -rn '^type .* interface {' "$PROJECT_ROOT" --include="*.go" 2>/dev/null | grep -v vendor | grep -v testdata | while IFS=: read -r file line rest; do
      iface=$(echo "$rest" | grep -oP 'type \K\w+')
      shortfile="${file#$PROJECT_ROOT/}"
      echo "  $iface  ($shortfile:$line)"
    done
    echo ""
    echo "Total Go source files: $GO_FILE_COUNT"
    ;;
esac

echo "=== Analysis complete ==="
