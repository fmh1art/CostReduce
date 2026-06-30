#!/usr/bin/env bash
# batch_cp - Copy files/directories with automatic parent directory creation.

set -euo pipefail

RECURSIVE=false
BACKUP=false
SOURCES=()
DEST=""

usage() {
  cat >&2 << 'EOF'
Usage: batch_cp [--dir|-r] <source> <dest>
       batch_cp [--dir|-r] <source1> [source2...] <dest_dir>/
       batch_cp --backup <source>

Copy files or directories, creating parent directories automatically.
With --dir/-r, copies directories recursively (like cp -r).
With --backup, creates a .bak copy of the source file.
If multiple sources given, the last argument is the destination directory.

Examples:
  /app/.preinstalled_scripts/batch_cp/main.sh file.go /new/dir/file.go
  /app/.preinstalled_scripts/batch_cp/main.sh --dir src_dir /backup/dst_dir
  /app/.preinstalled_scripts/batch_cp/main.sh --backup important.go
  /app/.preinstalled_scripts/batch_cp/main.sh a.ts b.ts /target/dir/
EOF
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir|-r|--recursive)
      RECURSIVE=true
      shift
      ;;
    --backup)
      BACKUP=true
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
      SOURCES+=("$1")
      shift
      ;;
  esac
done

# Handle --backup mode: creates source.bak from source
if [[ "$BACKUP" == "true" ]]; then
  if [[ ${#SOURCES[@]} -ne 1 ]]; then
    echo "Error: --backup requires exactly one source file" >&2
    exit 1
  fi
  src="${SOURCES[0]}"
  if [[ ! -f "$src" ]]; then
    echo "Error: file not found: $src" >&2
    exit 1
  fi
  cp "$src" "${src}.bak"
  echo "Backed up $src to ${src}.bak"
  exit 0
fi

if [[ ${#SOURCES[@]} -lt 2 ]]; then
  usage
fi

# Last argument is destination
DEST="${SOURCES[-1]}"
unset 'SOURCES[-1]'

if [[ "$RECURSIVE" == "true" ]]; then
  mkdir -p "$(dirname "$DEST")"
  cp -r "${SOURCES[0]}" "$DEST"
  echo "Copied directory ${SOURCES[0]} to $DEST"
elif [[ ${#SOURCES[@]} -eq 1 ]]; then
  mkdir -p "$(dirname "$DEST")"
  cp "${SOURCES[0]}" "$DEST"
  echo "Copied ${SOURCES[0]} to $DEST"
else
  # Multiple sources: dest is a directory
  mkdir -p "$DEST"
  for src in "${SOURCES[@]}"; do
    cp "$src" "$DEST/"
    echo "Copied $src to $DEST/"
  done
fi
