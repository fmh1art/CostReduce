#!/bin/bash
# git_commit - Stage all changes and commit in one step
# Usage: git_commit <message> [directory]
#   message   : commit message (required)
#   directory : optional, repository directory (default: current dir)
#
# Stages all changes (git add -A) and creates a commit.
# Auto-configures a default git identity if not set.
# Shows the resulting commit on success.
# Saves steps by combining git config + git add + git commit.
#
# Examples:
#   git_commit "fix: resolve type error"
#   git_commit "feat: add new feature" /workspace/repo

if [ "$1" = "--help" ] || [ "$1" = "-h" ] || [ $# -eq 0 ]; then
    echo "Usage: git_commit <message> [directory]"
    echo ""
    echo "Stages all changes and commits with the given message."
    echo "Auto-configures git identity if not set."
    echo ""
    echo "Arguments:"
    echo "  message    Commit message (required)"
    echo "  directory  Repository directory (default: current dir)"
    echo ""
    echo "Examples:"
    echo '  git_commit "fix: resolve type error"'
    echo '  git_commit "feat: add new feature" /workspace/repo'
    exit 0
fi

MESSAGE="$1"
DIR="${2:-.}"

if [ -z "$MESSAGE" ]; then
    echo "ERROR: No commit message provided"
    echo "Usage: git_commit <message> [directory]"
    exit 1
fi

if [ ! -d "$DIR" ]; then
    echo "ERROR: Directory not found: $DIR"
    exit 1
fi

cd "$DIR" || exit 1

# Configure git identity if not set
if ! git config user.email >/dev/null 2>&1; then
    git config user.email "developer@example.com"
    git config user.name "Developer"
    echo "(Configured default git identity)"
fi

# Stage and commit
git add -A 2>&1
if git diff --cached --quiet 2>/dev/null; then
    echo "No changes to commit"
    exit 0
fi

git commit -m "$MESSAGE" 2>&1

echo ""
echo "--- Last commit ---"
git log --oneline -1 2>/dev/null
