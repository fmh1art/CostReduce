#!/usr/bin/env bash
# git-helper: Common git operations in one step.
# Usage: git-helper <action> [args...]
#        git-helper add-commit <message> [directory]

set -euo pipefail

show_help() {
    cat << 'EOF'
Usage: git-helper [--cd=DIR] <action> [args...]

Options:
  --cd=DIR, -C DIR      Change to directory before running (replaces cd + git pattern)

Actions:
  root [start_dir]              Show git repository root path (comprehensive search via git, upward .git, project markers, and fallback find)
  status                         Show branch + status (git branch -a && git status)
  log [N]                        Show last N commits (default 5)
  diff [--head=N|--tail=N] [file...]  Show diff for file(s), optionally limited to first/last N lines
  show [--grep=PATTERN] [-A=N] [-B=N] [-C=N] [--head=N|--tail=N] <revision>:<path>
                                 Show file content at a specific revision, optionally grep-filtered with context
  add-commit <message> [dir]     Stage all changes in [dir] and commit
  stash                          Stash changes
  stash-pop                      Pop stash
  branch [name]                  List branches or create new branch
  checkout <branch>              Switch to existing branch
  checkout-b <name>              Create and switch to new branch
  checkout-file <file...>        Revert file(s) to last committed state
  log-diff [N]                  Show last N commits + diff --stat (combined)
  config <key> <value>           Set git config key=value
EOF
    exit 0
}

# Parse --cd option and --help
CD_DIR=""
if [[ $# -ge 1 ]] && [[ "$1" == --help || "$1" == -h ]]; then
    show_help
fi
if [[ $# -ge 1 ]] && [[ "$1" == --cd=* ]]; then
    CD_DIR="${1#*=}"
    shift
elif [[ $# -ge 2 ]] && [[ "$1" == -C || "$1" == --cd ]]; then
    CD_DIR="$2"
    shift 2
fi

# Check for --help/-h after --cd parsing
if [[ $# -ge 1 ]] && [[ "$1" == --help || "$1" == -h ]]; then
    show_help
fi

[[ $# -lt 1 ]] && show_help

ACTION="$1"
shift

# Change directory if --cd was specified
if [[ -n "$CD_DIR" ]]; then
    cd "$CD_DIR" || { echo "Error: Cannot cd to $CD_DIR" >&2; exit 1; }
fi

case "$ACTION" in
    root)
        # Comprehensive repo root search:
        # 1. Try git rev-parse
        ROOT_RESULT="$(git rev-parse --show-toplevel 2>/dev/null)" || true
        if [[ -n "$ROOT_RESULT" ]]; then
            echo "$ROOT_RESULT"
            exit 0
        fi
        # 2. Check common mount points for .git or project markers
        ROOT_START="${1:-}"
        ROOT_DIRS=()
        if [[ -n "$ROOT_START" ]]; then
            ROOT_DIRS+=("$ROOT_START")
        fi
        ROOT_DIRS+=(/app /workspace /project "$PWD")
        for rdir in "${ROOT_DIRS[@]}"; do
            if [[ ! -d "$rdir" ]]; then continue; fi
            # Upward .git search
            RCUR="$(cd "$rdir" 2>/dev/null && pwd)" || continue
            while [[ "$RCUR" != "/" ]]; do
                if [[ -d "$RCUR/.git" ]]; then
                    echo "$RCUR"
                    exit 0
                fi
                RCUR="$(dirname "$RCUR")"
            done
            # Check for project markers
            for rmarker in go.mod package.json Cargo.toml setup.py pyproject.toml build.gradle.kts build.gradle Gemfile; do
                if [[ -f "$rdir/$rmarker" ]]; then
                    echo "$rdir"
                    exit 0
                fi
            done
        done
        # 3. Fallback: find .git deeper
        for rdir in /app /workspace /project /home /opt; do
            if [[ -d "$rdir" ]]; then
                RFOUND="$(find "$rdir" -maxdepth 4 -name '.git' -type d 2>/dev/null | head -1)" || true
                if [[ -n "$RFOUND" ]]; then
                    echo "$(dirname "$RFOUND")"
                    exit 0
                fi
            fi
        done
        echo "Error: Could not find repo root" >&2
        exit 1
        ;;
    
    status)
        echo "=== Branches ==="
        git branch -a
        echo ""
        echo "=== Status ==="
        git status
        ;;
    log)
        N="${1:-5}"
        git log --oneline -"$N"
        ;;
    diff)
        DIFF_HEAD=""
        DIFF_TAIL=""
        DIFF_ARGS=()
        while [[ $# -gt 0 ]]; do
            case "$1" in
                --head=*) DIFF_HEAD="${1#*=}" ;;
                --tail=*) DIFF_TAIL="${1#*=}" ;;
                *) DIFF_ARGS+=("$1") ;;
            esac
            shift
        done
        if [[ ${#DIFF_ARGS[@]} -eq 0 ]]; then
            if [[ -n "$DIFF_HEAD" ]]; then
                git diff | head -n "$DIFF_HEAD"
            elif [[ -n "$DIFF_TAIL" ]]; then
                git diff | tail -n "$DIFF_TAIL"
            else
                git diff
            fi
        else
            if [[ -n "$DIFF_HEAD" ]]; then
                git diff "${DIFF_ARGS[@]}" | head -n "$DIFF_HEAD"
            elif [[ -n "$DIFF_TAIL" ]]; then
                git diff "${DIFF_ARGS[@]}" | tail -n "$DIFF_TAIL"
            else
                git diff "${DIFF_ARGS[@]}"
            fi
        fi
        ;;
    show)
        # Parse optional flags: --grep=PATTERN, -A=N, -B=N, -C=N, --head=N, --tail=N
        SHOW_GREP=""
        SHOW_AFTER=""
        SHOW_BEFORE=""
        SHOW_CONTEXT=""
        SHOW_HEAD=""
        SHOW_TAIL=""
        SHOW_REV=""
        while [[ $# -gt 0 ]]; do
            case "$1" in
                --grep=*) SHOW_GREP="${1#*=}" ;;
                -A=*) SHOW_AFTER="${1#*=}" ;;
                -A) shift; SHOW_AFTER="$1" ;;
                -B=*) SHOW_BEFORE="${1#*=}" ;;
                -B) shift; SHOW_BEFORE="$1" ;;
                -C=*) SHOW_CONTEXT="${1#*=}" ;;
                -C) shift; SHOW_CONTEXT="$1" ;;
                --head=*) SHOW_HEAD="${1#*=}" ;;
                --tail=*) SHOW_TAIL="${1#*=}" ;;
                *) SHOW_REV="$1" ;;
            esac
            shift
        done
        [[ -z "$SHOW_REV" ]] && { echo "Error: show needs <revision>:<path>" >&2; exit 1; }
        
        # Build grep context flags
        GREP_FLAGS=()
        if [[ -n "$SHOW_AFTER" ]]; then
            GREP_FLAGS+=(-A "$SHOW_AFTER")
        fi
        if [[ -n "$SHOW_BEFORE" ]]; then
            GREP_FLAGS+=(-B "$SHOW_BEFORE")
        fi
        if [[ -n "$SHOW_CONTEXT" ]]; then
            GREP_FLAGS+=(-C "$SHOW_CONTEXT")
        fi
        
        if [[ -n "$SHOW_GREP" ]]; then
            # Show with grep filtering
            if [[ ${#GREP_FLAGS[@]} -gt 0 ]]; then
                OUTPUT="$(git show "$SHOW_REV" | grep -n "$SHOW_GREP" "${GREP_FLAGS[@]}")"
            else
                OUTPUT="$(git show "$SHOW_REV" | grep -n "$SHOW_GREP")"
            fi
            if [[ -n "$SHOW_HEAD" ]]; then
                echo "$OUTPUT" | head -n "$SHOW_HEAD"
            elif [[ -n "$SHOW_TAIL" ]]; then
                echo "$OUTPUT" | tail -n "$SHOW_TAIL"
            else
                echo "$OUTPUT"
            fi
        else
            # No grep, just show with optional head/tail
            if [[ -n "$SHOW_HEAD" ]]; then
                git show "$SHOW_REV" | head -n "$SHOW_HEAD"
            elif [[ -n "$SHOW_TAIL" ]]; then
                git show "$SHOW_REV" | tail -n "$SHOW_TAIL"
            else
                git show "$SHOW_REV"
            fi
        fi
        ;;
    add-commit)
        [[ $# -lt 1 ]] && { echo "Error: add-commit needs a commit message" >&2; exit 1; }
        MSG="$1"
        shift
        # Optional directory parameter
        if [[ $# -ge 1 ]]; then
            TARGET_DIR="$1"
            shift
            cd "$TARGET_DIR" || { echo "Error: Cannot cd to $TARGET_DIR" >&2; exit 1; }
            git add -A
            git commit -m "$MSG"
        else
            git add -A
            git commit -m "$MSG"
        fi
        ;;
    stash)
        git stash
        ;;
    stash-pop)
        git stash pop
        ;;
    branch)
        if [[ $# -ge 1 ]]; then
            git branch "$1"
        else
            git branch -a
        fi
        ;;
    checkout)
        [[ $# -lt 1 ]] && { echo "Error: checkout needs branch name" >&2; exit 1; }
        git checkout "$1"
        ;;
    checkout-b)
        [[ $# -lt 1 ]] && { echo "Error: checkout-b needs branch name" >&2; exit 1; }
        git checkout -b "$1"
        ;;
    log-diff)
        N="${1:-5}"
        git log --oneline -"$N"
        echo "---"
        git diff --stat HEAD~"$N"
        ;;
    config)
        [[ $# -lt 2 ]] && { echo "Error: config needs <key> <value>" >&2; exit 1; }
        git config "$1" "$2"
        ;;
    checkout-file)
        [[ $# -lt 1 ]] && { echo "Error: checkout-file needs file path(s)" >&2; exit 1; }
        git checkout "$@"
        ;;
    
    *)
        echo "Error: Unknown action '$ACTION'" >&2
        show_help
        ;;
esac
