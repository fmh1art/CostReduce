#!/usr/bin/env bash
# batch_rm - Remove files or directories in one step.

set -euo pipefail

RECURSIVE=false
TARGETS=()

usage() {
  cat >&2 << 'EOF'
Usage: batch_rm [--recursive|-r] <target1> [target2...]

Remove files or directories. With --recursive/-r, removes directories
and their contents recursively (like rm -rf).

Examples:
  /app/.preinstalled_scripts/batch_rm/main.sh file.bak
  /app/.preinstalled_scripts/batch_rm/main.sh --recursive /tmp/build_dir
  /app/.preinstalled_scripts/batch_rm/main.sh a.tmp b.tmp c.tmp
EOF
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --recursive|-r|-rf)
      RECURSIVE=true
      shift
      ;;
    --help|-h)
      usage
      ;;
    -*)
      echo "Unknown option: $1" >&2
      usage
      ;;
    *)
      TARGETS+=("$1")
      shift
      ;;
  esac
done

if [[ ${#TARGETS[@]} -eq 0 ]]; then
  usage
fi

for target in "${TARGETS[@]}"; do
  if [[ ! -e "$target" ]]; then
    echo "Skipped (not found): $target"
    continue
  fi

  if [[ "$RECURSIVE" == "true" ]]; then
    rm -rf "$target"
    echo "Removed: $target"
  elif [[ -d "$target" ]]; then
    echo "Error: $target is a directory. Use --recursive to remove directories." >&2
    exit 1
  else
    rm -f "$target"
    echo "Removed: $target"
  fi
done
