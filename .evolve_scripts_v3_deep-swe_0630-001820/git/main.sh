#!/usr/bin/env bash
set -euo pipefail

# git - Unified git operations: branch, checkout, commit, diff, log, status, add, show, and more.
# Usage: git [--dir=DIR] <action> [args...]
# Actions:
#   --branch [name]       List branches or create+switch to new branch
#   --checkout <ref>      Checkout branch/commit
#   --commit <message>    Stage all + commit in one step (auto-configures git identity if missing)
#   --diff [opts]         Show diff (--stat-only, --name-only, --cached, or raw)
#     --head=N            Show first N lines of diff output
#     --tail=N            Show last N lines of diff output
#   --status              Show git status
#   --log [N]             Show last N commits (default: 10)
#   --oneline             Combine with --log for one-line format
#   --short               Quick overview: status --short + log --oneline -5
#   --add [files...]      Stage files (default: -A)
#   --show <ref:path>     Show file content from git ref (e.g. main:file.py)
#     --grep=PATTERN      Filter lines by pattern
#     --head=N            Show first N lines
#     --tail=N            Show last N lines
#     --number,-n         Show line numbers
#     --names-only        List filenames changed in a commit
#   -- <git-cmd> [args]   Run arbitrary git command

DIR="$(pwd)"
ACTION=""
ACTION_ARGS=()
ONELINE=""
SHOW_GREP=""
SHOW_HEAD=""
SHOW_TAIL=""
SHOW_NUMBER=false
SHOW_CONTEXT=""
SHOW_CONTEXT_BEFORE=""
SHOW_CONTEXT_AFTER=""
SHOW_NAMES_ONLY=false
DIFF_HEAD=""
DIFF_TAIL=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dir=*)
            DIR="${1#*=}"
            shift
            ;;
        --dir)
            DIR="$2"
            shift 2
            ;;
        --branch)
            ACTION="branch"
            shift
            ;;
        --checkout)
            ACTION="checkout"
            shift
            ;;
        --commit)
            ACTION="commit"
            shift
            ;;
        --diff)
            ACTION="diff"
            shift
            ;;
        --status)
            ACTION="status"
            shift
            ;;
        --log)
            ACTION="log"
            shift
            ;;
        --add)
            ACTION="add"
            shift
            ;;
        --oneline)
            ONELINE="--oneline"
            shift
            ;;
        --short)
            ACTION="short"
            shift
            ;;
        --show)
            ACTION="show"
            shift
            ;;
        --show=*)
            ACTION="show"
            SHOW_REF="${1#*=}"
            shift
            ;;
        --grep=*)
            SHOW_GREP="${1#*=}"
            shift
            ;;
        --head=*)
            # Used for --show (ref content) and --diff
            if [[ "$ACTION" == "diff" || "$ACTION" == "" ]]; then
                DIFF_HEAD="${1#*=}"
            else
                SHOW_HEAD="${1#*=}"
            fi
            shift
            ;;
        --tail=*)
            if [[ "$ACTION" == "diff" || "$ACTION" == "" ]]; then
                DIFF_TAIL="${1#*=}"
            else
                SHOW_TAIL="${1#*=}"
            fi
            shift
            ;;
        --number|-n)
            SHOW_NUMBER=true
            shift
            ;;
                --after-context=*|-A=*)
            SHOW_CONTEXT_AFTER="${1#*=}"
            shift
            ;;
        --before-context=*|-B=*)
            SHOW_CONTEXT_BEFORE="${1#*=}"
            shift
            ;;
        --context=*|-C=*)
            SHOW_CONTEXT="${1#*=}"
            shift
            ;;
        --after-context|-A)
            SHOW_CONTEXT_AFTER="$2"
            shift 2
            ;;
        --before-context|-B)
            SHOW_CONTEXT_BEFORE="$2"
            shift 2
            ;;
        --context|-C)
            SHOW_CONTEXT="$2"
            shift 2
            ;;

--names-only)
            SHOW_NAMES_ONLY=true
            shift
            ;;
        --stat-only|--stat)
            ACTION="diff"
            ACTION_ARGS+=("--stat-only")
            shift
            ;;
        --name-only)
            ACTION="diff"
            ACTION_ARGS+=("--name-only")
            shift
            ;;
        --cached)
            ACTION="diff"
            ACTION_ARGS+=("--cached")
            shift
            ;;
        --)
            shift
            ACTION="custom"
            ACTION_ARGS=("$@")
            break
            ;;
        -*)
            echo "Unknown option: $1" >&2
            exit 1
            ;;
        *)
            ACTION_ARGS+=("$1")
            shift
            ;;
    esac
done

cd "$DIR" 2>/dev/null || { echo "Error: Cannot change to directory $DIR" >&2; exit 1; }

run_diff() {
    local output
    output=$(git diff "$@" 2>&1) || true
    if [[ -n "$DIFF_HEAD" ]]; then
        echo "$output" | head -n "$DIFF_HEAD"
    elif [[ -n "$DIFF_TAIL" ]]; then
        echo "$output" | tail -n "$DIFF_TAIL"
    else
        echo "$output"
    fi
}

# Helper: ensure git identity is configured for the current repo
ensure_git_identity() {
    if ! git config user.email >/dev/null 2>&1 || ! git config user.name >/dev/null 2>&1; then
        git config user.email "developer@example.com" 2>/dev/null || true
        git config user.name "Developer" 2>/dev/null || true
    fi
}

case "${ACTION:-}" in
    branch)
        if [[ ${#ACTION_ARGS[@]} -eq 0 ]]; then
            git branch -a 2>&1 || true
        else
            git checkout -b "${ACTION_ARGS[0]}" 2>&1 || true
        fi
        ;;
    checkout)
        if [[ ${#ACTION_ARGS[@]} -eq 0 ]]; then
            echo "Usage: git --checkout <ref>" >&2
            exit 1
        fi
        git checkout "${ACTION_ARGS[@]}" 2>&1 || true
        ;;
    commit)
        if [[ ${#ACTION_ARGS[@]} -eq 0 ]]; then
            echo "Usage: git --commit <message>" >&2
            exit 1
        fi
        ensure_git_identity
        git add -A 2>&1 || true
        git commit -m "${ACTION_ARGS[*]}" 2>&1 || true
        ;;
    diff)
        if [[ ${#ACTION_ARGS[@]} -gt 0 ]]; then
            case "${ACTION_ARGS[0]}" in
                --stat-only|--stat)
                    run_diff --stat
                    ;;
                --name-only)
                    run_diff --name-only
                    ;;
                --cached)
                    run_diff --cached
                    ;;
                *)
                    run_diff "${ACTION_ARGS[@]}"
                    ;;
            esac
        else
            if [[ -z "$DIFF_HEAD" && -z "$DIFF_TAIL" ]]; then
                echo "=== git status ==="
                git status 2>&1 || true
                echo ""
                echo "=== git diff ==="
            fi
            run_diff
        fi
        ;;
    status)
        git status 2>&1 || true
        ;;
    log)
        COUNT="${ACTION_ARGS[0]:-10}"
        git log -n "$COUNT" $ONELINE 2>&1 || true
        ;;
    add)
        if [[ ${#ACTION_ARGS[@]} -eq 0 ]]; then
            git add -A 2>&1 || true
        else
            git add "${ACTION_ARGS[@]}" 2>&1 || true
        fi
        ;;
    show)
        SHOW_REF="${SHOW_REF:-}"
        if [[ -z "$SHOW_REF" && ${#ACTION_ARGS[@]} -gt 0 ]]; then
            SHOW_REF="${ACTION_ARGS[0]}"
        fi
        if [[ -z "$SHOW_REF" ]]; then
            echo "Usage: git --show <ref:path> [--grep=PATTERN] [--head=N|--tail=N] [--number] [--names-only]" >&2
            exit 1
        fi

        if [[ "$SHOW_NAMES_ONLY" == true ]]; then
            git show --name-only --format="" "$SHOW_REF" 2>&1 || true
            exit 0
        fi

        CONTENT=$(git show "$SHOW_REF" 2>&1) || { echo "Error: Cannot show $SHOW_REF" >&2; exit 1; }

        if [[ -n "$SHOW_GREP" ]]; then
            GREP_ARGS=()
            if [[ "$SHOW_NUMBER" == true ]]; then
                GREP_ARGS+=("-n")
            fi
            if [[ -n "$SHOW_CONTEXT" ]]; then
                GREP_ARGS+=("-C" "$SHOW_CONTEXT")
            fi
            if [[ -n "$SHOW_CONTEXT_BEFORE" ]]; then
                GREP_ARGS+=("-B" "$SHOW_CONTEXT_BEFORE")
            fi
            if [[ -n "$SHOW_CONTEXT_AFTER" ]]; then
                GREP_ARGS+=("-A" "$SHOW_CONTEXT_AFTER")
            fi
            echo "$CONTENT" | grep "${GREP_ARGS[@]}" "$SHOW_GREP" || true
        elif [[ -n "$SHOW_HEAD" ]]; then
            if [[ "$SHOW_NUMBER" == true ]]; then
                echo "$CONTENT" | head -n "$SHOW_HEAD" | nl -ba || true
            else
                echo "$CONTENT" | head -n "$SHOW_HEAD" || true
            fi
        elif [[ -n "$SHOW_TAIL" ]]; then
            if [[ "$SHOW_NUMBER" == true ]]; then
                echo "$CONTENT" | tail -n "$SHOW_TAIL" | nl -ba || true
            else
                echo "$CONTENT" | tail -n "$SHOW_TAIL" || true
            fi
        elif [[ "$SHOW_NUMBER" == true ]]; then
            echo "$CONTENT" | nl -ba || true
        else
            echo "$CONTENT"
        fi
        ;;
    short)
        echo "=== git status --short ==="
        git status --short 2>&1 || true
        echo ""
        echo "=== git log --oneline -5 ==="
        git log --oneline -5 2>&1 || true
        ;;
    custom)
        git "${ACTION_ARGS[@]}" 2>&1 || true
        ;;
    *)
        echo "Usage: git [--dir=DIR] <action> [args...]" >&2
        echo "Actions: --branch, --checkout, --commit, --diff, --status, --log, --short, --add, --show, -- <cmd>" >&2
        echo "Also: --stat-only, --name-only, --cached (shorthand for --diff)" >&2
        exit 1
        ;;
esac
