#!/usr/bin/env bash
# git_ops - Perform common git operations: diff, diff-tail, status, log, add, commit, checkout, stash, show, summary, cleanup.

set -euo pipefail

usage() {
  echo "Usage: $0 <action> [args...]"
  echo "  diff          - git diff (with --stat, --name-only, --cached, or specific files)"
  echo "  diff-tail [N] - Show last N lines (default 5) of each modified file"
  echo "  status        - git status"
  echo "  log [N] [path] - git log --oneline -N [path]"
  echo "  add <files>   - git add <files> (stage changes)"
  echo "  commit <msg>  - git commit -m <message> (stage all and commit)"
  echo "  checkout <files> - git checkout -- <files>"
  echo "  stash [op]    - git stash [push|pop|list|drop]"
  echo "  show <ref:path> - git show <ref:path> (view file from git history)"
  echo "  summary       - Combined git diff --stat and git status --short"
  echo "  cleanup [files...] - Remove .bak files (or specified files) and show git diff --stat"
  exit 1
}

if [[ $# -lt 1 ]]; then
  usage
fi

ACTION="$1"
shift

case "$ACTION" in
  diff)
    git diff "$@" 2>/dev/null || true
    ;;
  diff-tail)
    n=5
    if [[ $# -ge 1 && "$1" =~ ^[0-9]+$ ]]; then
      n="$1"
      shift
    fi
    # Show modified files (not staged) with tail content
    files=$(git diff --name-only --diff-filter=M 2>/dev/null || true)
    if [[ -z "$files" ]]; then
      # Also check staged changes
      files=$(git diff --name-only --cached 2>/dev/null || true)
    fi
    if [[ -z "$files" ]]; then
      echo "No modified files."
      exit 0
    fi
    while IFS= read -r f; do
      if [[ -f "$f" ]]; then
        echo "=== $f ==="
        tail -"$n" "$f"
        echo ""
      fi
    done <<< "$files"
    ;;
  status)
    git status 2>/dev/null || true
    ;;
  log)
    n=5
    path=""
    if [[ $# -ge 1 && "$1" =~ ^[0-9]+$ ]]; then
      n="$1"
      shift
    fi
    if [[ $# -ge 1 ]]; then
      path="$*"
    fi
    git log --oneline -"$n" $path 2>/dev/null || true
    ;;
  add)
    if [[ $# -eq 0 ]]; then
      echo "Error: add requires file arguments" >&2
      exit 1
    fi
    git add "$@" 2>/dev/null || true
    ;;
  commit)
    if [[ $# -eq 0 ]]; then
      echo "Error: commit requires a message (-m <msg>)" >&2
      exit 1
    fi
    git add -A 2>/dev/null || true
    git commit "$@" 2>/dev/null || true
    ;;
  checkout)
    if [[ $# -eq 0 ]]; then
      echo "Error: checkout requires file arguments" >&2
      exit 1
    fi
    git checkout -- "$@" 2>/dev/null || true
    ;;
  show)
    if [[ $# -lt 1 ]]; then
      echo "Error: show requires a ref:path argument (e.g. HEAD:file.go)" >&2
      exit 1
    fi
    git show "$@" 2>/dev/null || true
    ;;
  summary)
    echo "=== git diff --stat ==="
    git diff --stat 2>/dev/null || true
    echo ""
    echo "=== git status --short ==="
    git status --short 2>/dev/null || true
    ;;

  cleanup)
    # Remove .bak files optionally specified, then show git diff --stat
    TARGETS=()
    while [[ $# -gt 0 ]]; do
      case "$1" in
        -*)
          echo "Unknown option: $1" >&2
          exit 1
          ;;
        *)
          TARGETS+=("$1")
          shift
          ;;
      esac
    done
    if [[ ${#TARGETS[@]} -eq 0 ]]; then
      # Remove all .bak files in the repo
      while IFS= read -r -d '' f; do
        rm -f "$f"
        echo "Removed: $f"
      done < <(find . -name '*.bak' -type f -print0 2>/dev/null || true)
    else
      for target in "${TARGETS[@]}"; do
        if [[ -f "$target" ]]; then
          rm -f "$target"
          echo "Removed: $target"
        else
          echo "Skipped (not found): $target"
        fi
      done
    fi
    echo ""
    echo "=== git diff --stat ==="
    git diff --stat 2>/dev/null || true
    ;;

  stash)
    if [[ $# -eq 0 ]]; then
      git stash list 2>/dev/null || true
    else
      git stash "$@" 2>/dev/null || true
    fi
    ;;
  *)
    echo "Unknown action: $ACTION" >&2
    usage
    ;;
esac
