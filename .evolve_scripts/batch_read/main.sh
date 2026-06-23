#!/bin/bash
# batch_read - Read multiple files or specific line ranges in a single tool call
# Usage: batch_read [options] <file1> [file2] [file3] ...
#        batch_read <file:start-end> [file2:start-end] ...
#        batch_read --lines=start-end <file1> [file2] ...
#        batch_read --head=N <file1> [file2] ...
#        batch_read --tail=N <file1> [file2] ...
#        batch_read --number <file1> [file2] ...
#        batch_read -n <file1> [file2] ...
#        batch_read /path/to/*.py /path/to/*.go    (glob patterns)
#        batch_read /path/to/dir/                  (read all files in directory)
#        batch_read --dir=/path/to --include="*.py" --include="*.go"
#
# Reads all specified files and prints their contents with headers.
# Supports glob patterns (shell wildcards) - the shell expands them automatically.
# Supports reading all files in a directory with optional include/exclude filters.
# Supports reading specific line ranges with --lines or :start-end suffix.
# Supports --head=N to read first N lines, --tail=N to read last N lines.
# Use --number (-n) to show line numbers even for full file reads.
# For ranges, use --lines=start-end (e.g., --lines=10-30) before filenames,
# or append :start-end to filenames (e.g., file.py:10-30).
# Saves steps by combining multiple reads into one action.
#
# Examples:
#   batch_read file1.py file2.py file3.py
#   batch_read file1.py:10-30 file2.py:50-100
#   batch_read --lines=10-30 file1.py file2.py
#   batch_read --head=20 file1.py file2.py
#   batch_read --tail=30 file1.py file2.py
#   batch_read --number file1.py file2.py
#   batch_read -n file1.py file2.py
#   batch_read path/to/files/*.log      # Read all .eml files (glob)
#   batch_read path/to/files/           # Read all files in directory
#   batch_read --dir=path/to/dir --include="*.eml"  # Read all .eml files in dir

if [ $# -eq 0 ]; then
    echo "Usage: batch_read [options] <file1> [file2] [file3] ..."
    echo "       batch_read <file.py:10-30> <file2.py:50-100>"
    echo "       batch_read /path/to/*.py /path/to/*.go    (glob patterns)"
    echo "       batch_read /path/to/dir/                  (read all files in dir)"
    echo "       batch_read --dir=/path --include=\"*.py\"   (read files by extension)"
    echo ""
    echo "Options:"
    echo "  --lines=start-end     Read specific line range for subsequent files"
    echo "  --head=N              Read first N lines of subsequent files"
    echo "  --tail=N              Read last N lines of subsequent files"
    echo "  --number, -n          Show line numbers for full file reads"
    echo "  --dir=PATH            Read all files in a directory"
    echo "  --include=GLOB        Only read files matching this glob (with --dir)"
    echo "  --exclude=GLOB        Skip files matching this glob (with --dir)"
    echo "  --help                Show this help message"
    echo ""
    echo "Examples:"
    echo "  batch_read file1.py file2.py file3.py"
    echo "  batch_read file1.py:10-30 file2.py:50-100"
    echo "  batch_read --lines=10-30 file1.py file2.py"
    echo "  batch_read --head=20 file1.py file2.py"
    echo "  batch_read --tail=30 file1.py file2.py"
    echo "  batch_read --number file1.py file2.py"
    echo "  batch_read -n file1.py file2.py"
    echo "  batch_read path/to/files/*.log"
    echo "  batch_read path/to/files/"
    echo "  batch_read --dir=path/to/dir --include=\"*.eml\""
    exit 0
fi

CURRENT_LINES=""
CURRENT_HEAD=""
CURRENT_TAIL=""
SHOW_NUMBERS=false
DIR_MODE=false
DIR_PATH=""
INCLUDE_GLOBS=()
EXCLUDE_GLOBS=()
FILE_ARGS=()

# First pass: parse options and collect file arguments
for arg in "$@"; do
    case "$arg" in
        --help)
            echo "Usage: batch_read [options] <file1> [file2] ..."
            echo "  --lines=start-end  Read specific line range"
            echo "  --head=N           Read first N lines"
            echo "  --tail=N           Read last N lines"
            echo "  --number, -n       Show line numbers for full file reads"
            echo "  --dir=PATH         Read all files in a directory"
            echo "  --include=GLOB     Only read files matching this glob (with --dir)"
            echo "  --exclude=GLOB     Skip files matching this glob (with --dir)"
            echo "  Append :start-end to filename for per-file ranges"
            exit 0
            ;;
        --number|-n)
            SHOW_NUMBERS=true
            ;;
        --lines=*)
            CURRENT_LINES="${arg#*=}"
            CURRENT_HEAD=""
            CURRENT_TAIL=""
            ;;
        --head=*)
            CURRENT_HEAD="${arg#*=}"
            CURRENT_LINES=""
            CURRENT_TAIL=""
            ;;
        --tail=*)
            CURRENT_TAIL="${arg#*=}"
            CURRENT_LINES=""
            CURRENT_HEAD=""
            ;;
        --dir=*)
            DIR_MODE=true
            DIR_PATH="${arg#*=}"
            ;;
        --include=*)
            INCLUDE_GLOBS+=("${arg#*=}")
            ;;
        --exclude=*)
            EXCLUDE_GLOBS+=("${arg#*=}")
            ;;
        *)
            FILE_ARGS+=("$arg")
            ;;
    esac
done

# If --dir is specified, scan the directory for files
if [ "$DIR_MODE" = true ] && [ -n "$DIR_PATH" ]; then
    if [ ! -d "$DIR_PATH" ]; then
        echo "===== Directory not found: $DIR_PATH ====="
        exit 1
    fi
    
    # Build find command for the directory
    FIND_CMD="find \"$DIR_PATH\" -maxdepth 1 -type f"
    
    # Add include filters (if any)
    if [ ${#INCLUDE_GLOBS[@]} -gt 0 ]; then
        FIND_CMD="$FIND_CMD \\( -false"
        for g in "${INCLUDE_GLOBS[@]}"; do
            FIND_CMD="$FIND_CMD -o -name \"$g\""
        done
        FIND_CMD="$FIND_CMD \\)"
    fi
    
    # Add exclude filters
    for g in "${EXCLUDE_GLOBS[@]}"; do
        FIND_CMD="$FIND_CMD -not -name \"$g\""
    done
    
    # Sort files and add to FILE_ARGS
    while IFS= read -r f; do
        FILE_ARGS+=("$f")
    done < <(eval "$FIND_CMD" 2>/dev/null | sort)
fi


# Expand directories: if any FILE_ARG is a directory, list its files
EXPANDED_ARGS=()
for arg in "${FILE_ARGS[@]}"; do
    if [ -d "$arg" ]; then
        while IFS= read -r f; do
            EXPANDED_ARGS+=("$f")
        done < <(find "$arg" -maxdepth 1 -type f 2>/dev/null | sort)
    else
        EXPANDED_ARGS+=("$arg")
    fi
done
FILE_ARGS=("${EXPANDED_ARGS[@]}")
if [ ${#FILE_ARGS[@]} -eq 0 ]; then
    echo "No files specified."
    exit 0
fi

# Second pass: read each file
for arg in "${FILE_ARGS[@]}"; do
    # Extract filepath and line range
    if echo "$arg" | grep -qE ':[0-9]+-[0-9]+$'; then
        FILEPATH="${arg%:*}"
        LINES="${arg##*:}"
        HEAD_N=""
        TAIL_N=""
    elif echo "$arg" | grep -qE ':HEAD=[0-9]+$'; then
        FILEPATH="${arg%:*}"
        HEAD_N="${arg##*=}"
        LINES=""
        TAIL_N=""
    elif echo "$arg" | grep -qE ':TAIL=[0-9]+$'; then
        FILEPATH="${arg%:*}"
        TAIL_N="${arg##*=}"
        LINES=""
        HEAD_N=""
    else
        FILEPATH="$arg"
        # Use global options as fallback if no per-file suffix
        LINES="$CURRENT_LINES"
        HEAD_N="$CURRENT_HEAD"
        TAIL_N="$CURRENT_TAIL"
    fi

    if [ ! -f "$FILEPATH" ]; then
        echo "===== $FILEPATH (FILE NOT FOUND) ====="
        continue
    fi

    if [ -n "$LINES" ]; then
        START=$(echo "$LINES" | cut -d'-' -f1)
        END=$(echo "$LINES" | cut -d'-' -f2)
        TOTAL_LINES=$(wc -l < "$FILEPATH")
        echo "===== $FILEPATH (lines $START-$END, total $TOTAL_LINES lines) ====="
        nl -ba "$FILEPATH" | sed -n "${START},${END}p"
        echo ""
    elif [ -n "$HEAD_N" ]; then
        TOTAL_LINES=$(wc -l < "$FILEPATH")
        echo "===== $FILEPATH (first $HEAD_N lines, total $TOTAL_LINES lines) ====="
        if [ "$SHOW_NUMBERS" = true ]; then
            nl -ba "$FILEPATH" | head -n "$HEAD_N"
        else
            head -n "$HEAD_N" "$FILEPATH"
        fi
        echo ""
    elif [ -n "$TAIL_N" ]; then
        TOTAL_LINES=$(wc -l < "$FILEPATH")
        echo "===== $FILEPATH (last $TAIL_N lines, total $TOTAL_LINES lines) ====="
        if [ "$SHOW_NUMBERS" = true ]; then
            nl -ba "$FILEPATH" | tail -n "$TAIL_N"
        else
            tail -n "$TAIL_N" "$FILEPATH"
        fi
        echo ""
    else
        TOTAL_LINES=$(wc -l < "$FILEPATH")
        echo "===== $FILEPATH ($TOTAL_LINES lines) ====="
        if [ "$SHOW_NUMBERS" = true ]; then
            nl -ba "$FILEPATH"
        else
            cat "$FILEPATH"
        fi
        echo ""
    fi
done
