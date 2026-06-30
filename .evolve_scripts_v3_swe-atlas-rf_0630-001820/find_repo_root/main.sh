#!/usr/bin/env bash
# find_repo_root - Find repository root by locating .git directory.

set -euo pipefail

usage() {
  echo "Usage: $0 [--max-depth=N] [starting_dir]"
  echo "Find the repository root directory by searching for .git."
  echo "Default max-depth: 4. Default starting dir: search common locations then cwd."
  exit 1
}

MAX_DEPTH=4
SEARCH_DIR=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --max-depth=*)
      MAX_DEPTH="${1#*=}"
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
      SEARCH_DIR="$1"
      shift
      ;;
  esac
done

search_upward() {
  local dir="$1"
  local max="$2"
  local count=0
  while [[ $count -lt $max ]]; do
    if [[ -d "$dir/.git" ]]; then
      echo "$dir"
      return 0
    fi
    local parent="$(dirname "$dir")"
    if [[ "$parent" == "$dir" ]]; then
      break
    fi
    dir="$parent"
    count=$((count + 1))
  done
  return 1
}

if [[ -n "$SEARCH_DIR" ]]; then
  search_upward "$SEARCH_DIR" "$MAX_DEPTH" && exit 0
  exit 1
fi

# Try common locations first (observed in agent trajectories)
for d in /workspace /app /code /grafana /testbed /src /repo /opt/netdata.git /go/src/go.k6.io/k6 /home/circleci/wp-calypso /app/source /app/suricata /src/suricata /calypso; do
  if [[ -d "$d/.git" ]]; then
    echo "$d"
    exit 0
  fi
done

# Fall back to find (redirect stderr, handle SIGPIPE)
found=$(find / -maxdepth "$MAX_DEPTH" -name .git -type d 2>/dev/null | head -1) || true
if [[ -n "$found" ]]; then
  dirname "$found"
  exit 0
fi

# Try cwd upward
search_upward "$(pwd)" 10 && exit 0

echo "Error: no .git directory found" >&2
exit 1
