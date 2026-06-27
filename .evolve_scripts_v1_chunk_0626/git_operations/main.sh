#!/bin/bash
# git_operations - Run common git operations in a project directory
# Usage: git_operations <project_root> <action> [args...]
#
# Actions:
#   status              - Show git status
#   branch              - Show current branch and all branches
#   checkout <branch>   - Create and switch to new branch, or switch to existing
#   add <files...>      - Stage files (comma-separated or space-separated)
#   commit <message>    - Commit staged changes with a message
#   add-commit <message> - Stage all files and commit in one step
#   diff [file]         - Show diff of unstaged changes, optionally for a specific file
#   log [N]             - Show last N commits (default: 5)
#   config <key> <val>  - Set git config (e.g., user.email, user.name)
#   stash [message]     - Stash working directory changes (with optional message)
#   stash pop           - Restore most recent stash
#   stash list          - List all stashes
#   show <ref:path>     - Show file content from a specific revision (e.g., HEAD:path)

PROJECT_ROOT="$1"
ACTION="$2"
shift 2 2>/dev/null || shift $#

if [ -z "$PROJECT_ROOT" ] || [ -z "$ACTION" ]; then
    echo "Usage: git_operations <project_root> <action> [args...]"
    echo ""
    echo "Actions:"
    echo "  status                   Show git status"
    echo "  branch                   Show current branch and all branches"
    echo "  checkout -b <branch>     Create and switch to new branch"
    echo "  checkout <branch>        Switch to existing branch"
    echo "  add <files...>           Stage files (comma or space separated)"
    echo "  commit <message>         Commit staged changes"
    echo "  add-commit <message>     Stage all and commit in one step"
    echo "  diff [file]              Show diff of unstaged changes"
    echo "  log [N]                  Show last N commits (default: 5)"
    echo "  config <key> <val>       Set git config"
    echo "  stash [message]          Stash working directory changes"
    echo "  stash pop                Restore most recent stash"
    echo "  stash list               List all stashes"
    echo "  show <ref:path>          Show file from a revision"
    exit 1
fi

if [ ! -d "$PROJECT_ROOT" ]; then
    echo "Error: Directory '$PROJECT_ROOT' does not exist"
    exit 1
fi

cd "$PROJECT_ROOT" || exit 1

# Auto-configure git identity helper
auto_configure_git() {
    if ! git config user.email >/dev/null 2>&1; then
        git config user.email "dev@project.dev"
        echo "Auto-configured user.email"
    fi
    if ! git config user.name >/dev/null 2>&1; then
        git config user.name "Project Developer"
        echo "Auto-configured user.name"
    fi
}

case "$ACTION" in
    status)
        git status
        ;;
    branch)
        echo "=== Current branch ==="
        git branch --show-current
        echo ""
        echo "=== All branches ==="
        git branch -a
        ;;
    checkout)
        if [ "$1" = "-b" ]; then
            shift
            BRANCH="$1"
            echo "Creating and switching to new branch: $BRANCH"
            git checkout -b "$BRANCH"
        else
            BRANCH="$1"
            echo "Switching to branch: $BRANCH"
            git checkout "$BRANCH"
        fi
        ;;
    add)
        FILES="$*"
        FILES=$(echo "$FILES" | tr ',' ' ')
        for file in $FILES; do
            if [ -n "$file" ]; then
                git add "$file"
                echo "Staged: $file"
            fi
        done
        ;;
    add-commit)
        COMMIT_MSG="$1"
        git add -A
        echo "Staged all changes"
        auto_configure_git
        git commit -m "$COMMIT_MSG"
        ;;
    commit)
        COMMIT_MSG="$1"
        auto_configure_git
        git commit -m "$COMMIT_MSG"
        ;;
    diff)
        git diff "$@"
        ;;
    log)
        N="${1:-5}"
        git log --oneline -n "$N"
        ;;
    config)
        KEY="$1"
        VALUE="$2"
        if [ -z "$KEY" ] || [ -z "$VALUE" ]; then
            echo "Usage: git_operations <project_root> config <key> <value>"
            exit 1
        fi
        git config "$KEY" "$VALUE"
        echo "Set git config: $KEY = $VALUE"
        ;;
    stash)
        SUB_ACTION="${1:-push}"
        shift 2>/dev/null || true
        if [ "$SUB_ACTION" = "push" ]; then
            STASH_MSG="$*"
            if [ -n "$STASH_MSG" ]; then
                git stash push -m "$STASH_MSG"
                echo "Stashed with message: $STASH_MSG"
            else
                git stash push
                echo "Stashed working directory changes"
            fi
        elif [ "$SUB_ACTION" = "pop" ]; then
            git stash pop
            echo "Restored most recent stash"
        elif [ "$SUB_ACTION" = "list" ]; then
            git stash list
        else
            echo "Unknown stash sub-action: $SUB_ACTION"
            echo "Valid: stash [message], stash pop, stash list"
            exit 1
        fi
        ;;
    show)
        REF_PATH="$1"
        if [ -z "$REF_PATH" ]; then
            echo "Usage: git_operations <project_root> show <ref:path>"
            echo "  ref:path  e.g. HEAD:evaluator/functions.go"
            exit 1
        fi
        if [[ "$REF_PATH" != *":"* ]]; then
            git show "$REF_PATH" --stat
            exit $?
        fi
        if ! git show "$REF_PATH" >/dev/null 2>&1; then
            echo "Error: '$REF_PATH' not found in repository"
            exit 1
        fi
        echo "=== $REF_PATH ==="
        echo ""
        git show "$REF_PATH" 2>/dev/null | nl -ba | head -200
        ;;
    *)
        echo "Unknown action: $ACTION"
        exit 1
        ;;
esac
