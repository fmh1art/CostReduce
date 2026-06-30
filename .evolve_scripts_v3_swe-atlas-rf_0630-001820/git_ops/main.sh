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
  echo "  show <ref:path> [--head=N] [--grep=PATTERN] [--lines=N-M] - git show <ref:path> with optional filtering"
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
    # Parse optional filters: --head=N, --grep=PATTERN, --lines=N-M
    SHOW_HEAD=
    SHOW_GREP=
    SHOW_LINES=
    SHOW_ARGS=()
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --head=*)
          SHOW_HEAD="${1#*=}"
          shift
          ;;
        --grep=*)
          SHOW_GREP="${1#*=}"
          shift
          ;;
        --lines=*)
          SHOW_LINES="${1#*=}"
          shift
          ;;
        -*)
          echo "Unknown option: $1" >&2
          exit 1
          ;;
        *)
          SHOW_ARGS+=("$1")
          shift
          ;;
      esac
    done
    if [[ ${#SHOW_ARGS[@]} -lt 1 ]]; then
      echo "Error: show requires a ref:path argument (e.g. HEAD:file.go)" >&2
      exit 1
    fi
    output=$(git show "${SHOW_ARGS[@]}" 2>/dev/null) || true
    if [[ -z "$output" ]]; then
      exit 0
    fi
    # Apply --grep filter
    if [[ -n "$SHOW_GREP" ]]; then
      output=$(echo "$output" | grep -E "$SHOW_GREP" 2>/dev/null || true)
    fi
    # Apply --lines filter
    if [[ -n "$SHOW_LINES" ]]; then
      if [[ "$SHOW_LINES" =~ ^([0-9]+)-([0-9]+)$ ]]; then
        start="${BASH_REMATCH[1]}"
        end="${BASH_REMATCH[2]}"
        output=$(echo "$output" | sed -n "${start},${end}p" 2>/dev/null || true)
      fi
    fi
    # Apply --head filter
    if [[ -n "$SHOW_HEAD" ]]; then
      output=$(echo "$output" | head -n "$SHOW_HEAD" 2>/dev/null || true)
    fi
    if [[ -n "$output" ]]; then
      echo "$output"
    fi
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
