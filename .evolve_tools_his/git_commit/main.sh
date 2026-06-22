#!/bin/bash
# git_commit - Stage changes and commit with proper git config
# Usage: main.sh <commit_message> [directory] [file1 file2 ...]
#   commit_message: required, the commit message
#   directory: optional, git repo directory (default: .)
#   files: optional, specific files to stage (if omitted, stages all changes)
# Automatically sets up git config if missing

MESSAGE="$1"
DIR="${2:-.}"
shift 2 2>/dev/null || true
FILES=("$@")  # Remaining args are specific files to stage

if [ -z "$MESSAGE" ]; then
    echo "ERROR: No commit message provided"
    echo "Usage: main.sh <commit_message> [directory] [file1 file2 ...]"
    exit 1
fi

cd "$DIR" || { echo "ERROR: Cannot cd to $DIR"; exit 1; }

echo "=== Git Commit in $(pwd) ==="

# Check if git is available
if ! git rev-parse --git-dir > /dev/null 2>&1; then
    echo "ERROR: Not a git repository"
    exit 1
fi

# Show current status first
echo ""
echo "--- Git Status ---"
git status --short 2>&1
echo ""

# Check git config, set defaults if missing
NEED_CONFIG=0
if ! git config user.email > /dev/null 2>&1; then
    NEED_CONFIG=1
fi
if ! git config user.name > /dev/null 2>&1; then
    NEED_CONFIG=1
fi

if [ "$NEED_CONFIG" -eq 1 ]; then
    echo "(Setting default git config)"
    git config user.email "dev@example.com"
    git config user.name "Developer"
    echo ""
fi

# Stage files
if [ ${#FILES[@]} -gt 0 ]; then
    echo "Staging specific files..."
    git add "${FILES[@]}" 2>&1
else
    echo "Staging all changes..."
    git add -A 2>&1
fi

# Show what's staged
echo ""
echo "--- Changes staged ---"
git diff --cached --stat 2>&1
echo ""

# Commit
echo "Committing..."
git commit -m "$MESSAGE" 2>&1
EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo ""
    echo "Commit successful!"
    git log --oneline -1 2>&1
else
    echo ""
    echo "Commit failed (exit code: $EXIT_CODE)"
    echo "Note: If no changes were staged, there may be nothing to commit."
fi

exit $EXIT_CODE
