#!/bin/bash
# find_repo_root - Find the root directory of a Git repository
# Usage: find_repo_root [starting_directory]
#
# Searches for the root of a Git repository by looking for a .git directory.
# Checks common locations first, then searches upward from the starting directory.
# If no starting directory is given, checks common workspace directories.
# Saves steps by replacing manual loops like:
#   for d in /workspace /app /project /src /repo; do [ -d "$d/.git" ] && echo "$d"; done
# or:
#   find / -maxdepth 4 -name .git -type d 2>/dev/null | head -1
#
# Examples:
#   find_repo_root
#   find_repo_root /workspace/subdir
#   find_repo_root .

if [ $# -ge 1 ]; then
    if [ "$1" = "--help" ] || [ "$1" = "-h" ]; then
        echo "Usage: find_repo_root [starting_directory]"
        echo ""
        echo "Finds the root directory of a Git repository by locating the .git directory."
        echo "Searches upward from the given directory, or checks common locations if none given."
        echo ""
        echo "Examples:"
        echo "  find_repo_root"
        echo "  find_repo_root /workspace/some/deep/path"
        echo "  find_repo_root ."
        exit 0
    fi
    START_DIR="$1"
else
    # Try common workspace directories
    for d in /workspace /app /project /src /repo /home /go /opt; do
        if [ -d "$d" ]; then
            if [ -d "$d/.git" ]; then
                echo "$d"
                exit 0
            fi
            # Check one level deeper
            for sub in "$d"/*/; do
                if [ -d "${sub}.git" ] 2>/dev/null; then
                    echo "${sub%/}"
                    exit 0
                fi
            done
        fi
    done
    # Search upward from current directory
    START_DIR="$(pwd)"
fi

# Search upward from the given directory
DIR="$START_DIR"
# Resolve to absolute path
DIR="$(cd "$DIR" 2>/dev/null && pwd || echo "$DIR")"

while [ -n "$DIR" ]; do
    if [ -d "$DIR/.git" ]; then
        echo "$DIR"
        exit 0
    fi
    # Go up one level
    PARENT="$(dirname "$DIR")"
    if [ "$PARENT" = "$DIR" ]; then
        break
    fi
    DIR="$PARENT"
done

# Last resort: search broadly but shallowly
if command -v find &>/dev/null; then
    RESULT=$(find / -maxdepth 4 -name .git -type d 2>/dev/null | head -1)
    if [ -n "$RESULT" ]; then
        echo "$(dirname "$RESULT")"
        exit 0
    fi
fi

echo "Error: Could not find repository root (no .git directory found)"
exit 1
