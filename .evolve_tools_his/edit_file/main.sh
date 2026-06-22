#!/bin/bash
# edit_file - Edit a file by finding and replacing text
# Usage: main.sh <filepath> <old_text> <new_text>
#   filepath: path to the file to edit
#   old_text: the exact text to find
#   new_text: the replacement text
# Options:
#   --all      : replace ALL occurrences (default: only first)
#   --old <f>  : read old_text from file (can be used multiple times for batch edits)
#   --new <f>  : read new_text from file (can be used multiple times for batch edits)
#
# For multiple replacements in a single call, use --old/--new pairs:
#   main.sh file.py --old "text1" --new "repl1" --old "text2" --new "repl2"
#   main.sh file.py --old old1.txt --new new1.txt --old old2.txt --new new2.txt
#
# Shows a diff summary of changes.
#
# Examples:
#   main.sh file.go "func oldName" "func newName"
#   main.sh file.go --old old.txt --new new.txt
#   main.sh file.go "pattern" "replacement" --all
#   main.sh file.py --old "import os" --new "import os\nimport sys" --old "DEBUG=False" --new "DEBUG=True"

FILEPATH=""
declare -a OLD_TEXTS=()
declare -a NEW_TEXTS=()
REPLACE_ALL=false
CURRENT_FLAG=""

# Parse arguments
while [ $# -gt 0 ]; do
    case "$1" in
        --all)
            REPLACE_ALL=true
            shift
            ;;
        --old)
            CURRENT_FLAG="old"
            shift
            # Check if next arg is another flag (missing value)
            if [ $# -eq 0 ] || [ "${1#--}" != "$1" ]; then
                echo "ERROR: --old requires a value (file path or inline text)"
                exit 1
            fi
            ;;
        --new)
            CURRENT_FLAG="new"
            shift
            if [ $# -eq 0 ] || [ "${1#--}" != "$1" ]; then
                echo "ERROR: --new requires a value (file path or inline text)"
                exit 1
            fi
            ;;
        *)
            case "$CURRENT_FLAG" in
                old)
                    if [ -f "$1" ]; then
                        OLD_TEXTS+=("$(cat "$1")")
                    else
                        OLD_TEXTS+=("$1")
                    fi
                    CURRENT_FLAG=""
                    shift
                    ;;
                new)
                    if [ -f "$1" ]; then
                        NEW_TEXTS+=("$(cat "$1")")
                    else
                        NEW_TEXTS+=("$1")
                    fi
                    CURRENT_FLAG=""
                    shift
                    ;;
                *)
                    if [ -z "$FILEPATH" ]; then
                        FILEPATH="$1"
                    elif [ ${#OLD_TEXTS[@]} -eq 0 ]; then
                        OLD_TEXTS+=("$1")
                    elif [ ${#NEW_TEXTS[@]} -eq 0 ]; then
                        NEW_TEXTS+=("$1")
                    else
                        # Additional positional args after old/new are treated as more pairs
                        if [ ${#OLD_TEXTS[@]} -gt ${#NEW_TEXTS[@]} ]; then
                            NEW_TEXTS+=("$1")
                        else
                            OLD_TEXTS+=("$1")
                        fi
                    fi
                    shift
                    ;;
            esac
            ;;
    esac
done

if [ -z "$FILEPATH" ]; then
    echo "ERROR: No filepath provided"
    echo "Usage: main.sh <filepath> <old_text> <new_text> [--all]"
    echo "   or: main.sh <filepath> --old <old_file> --new <new_file> [--all]"
    echo "   or: main.sh <filepath> --old <t1> --new <t2> --old <t3> --new <t4> [--all]"
    exit 1
fi

if [ ! -f "$FILEPATH" ]; then
    echo "ERROR: File not found: $FILEPATH"
    exit 1
fi

if [ ${#OLD_TEXTS[@]} -eq 0 ]; then
    echo "ERROR: No old_text provided (text to find)"
    exit 1
fi

if [ ${#NEW_TEXTS[@]} -ne ${#OLD_TEXTS[@]} ]; then
    echo "ERROR: Number of old_text entries (${#OLD_TEXTS[@]}) doesn't match new_text entries (${#NEW_TEXTS[@]})"
    exit 1
fi

# Read original file content
ORIGINAL="$(cat "$FILEPATH"; echo x)"
ORIGINAL="${ORIGINAL%x}"

CURRENT="$ORIGINAL"
ALL_FOUND=true

# Process each replacement
for i in "${!OLD_TEXTS[@]}"; do
    OLD="${OLD_TEXTS[$i]}"
    NEW="${NEW_TEXTS[$i]}"
    
    # Check if old_text exists
    if [[ "$CURRENT" != *"$OLD"* ]]; then
        echo "ERROR: Pattern not found in file (replacement #$((i+1))): $FILEPATH"
        echo "Searching for: $(echo "$OLD" | head -c 200)"
        echo ""
        echo "Tip: Make sure old_text matches exactly (including whitespace/indentation)"
        ALL_FOUND=false
        continue
    fi
    
    # Perform replacement
    if [ "$REPLACE_ALL" = true ]; then
        CURRENT="${CURRENT//"$OLD"/"$NEW"}"
    else
        CURRENT="${CURRENT/"$OLD"/"$NEW"}"
    fi
done

if [ "$ALL_FOUND" = false ]; then
    exit 1
fi

# Write the modified content back
printf '%s' "$CURRENT" > "$FILEPATH"
EXIT_CODE=$?

if [ $EXIT_CODE -ne 0 ]; then
    echo "ERROR: Failed to write to $FILEPATH"
    exit $EXIT_CODE
fi

NEW_LINES=$(wc -l < "$FILEPATH" 2>/dev/null || echo 0)
NEW_SIZE=$(wc -c < "$FILEPATH" 2>/dev/null || echo 0)

echo "=== File edited: $FILEPATH ==="
echo "Size: $NEW_SIZE bytes, $NEW_LINES lines"
echo "Replacements applied: ${#OLD_TEXTS[@]}"

# Show diff
echo ""
echo "--- Changes (diff -u) ---"
diff -u <(printf '%s' "$ORIGINAL") "$FILEPATH" 2>/dev/null || true

exit 0
