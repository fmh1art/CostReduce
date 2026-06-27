#!/bin/bash
# multi-file-edit: Apply structured edits (replace, insert, delete, append, prepend, write) to one or more files matching a glob pattern,
#                  or apply a Python transform script via --transform / --transform-file / --transform-stdin.
# Usage:
#   multi-file-edit/main.sh <file_or_glob> --replace <old> <new> [old2 new2 ...]
#   multi-file-edit/main.sh <file_or_glob> --insert-before <pattern> <text>
#   multi-file-edit/main.sh <file_or_glob> --insert-after <pattern> <text>
#   multi-file-edit/main.sh <file_or_glob> --delete-matching <pattern>
#   multi-file-edit/main.sh <file_or_glob> --append <text>
#   multi-file-edit/main.sh <file_or_glob> --prepend <text>
#   multi-file-edit/main.sh <file_or_glob> --write [content]
#   echo <content> | multi-file-edit/main.sh <file_or_glob> --write
#   multi-file-edit/main.sh <file_or_glob> --write --append [content]
#   multi-file-edit/main.sh <file_or_glob> --transform '<python_code>'
#   multi-file-edit/main.sh <file_or_glob> --transform-file <path.py>
#   echo '<python_code>' | multi-file-edit/main.sh <file_or_glob> --transform-stdin
#   cat <<'PYEOF' | multi-file-edit/main.sh <file_or_glob> --transform-stdin
#   ...python code...
#   PYEOF
# Options:
#   --dry-run         Show matching files without editing
#   --append          Used with --write to append instead of overwrite
#   -h, --help        Show usage

set -euo pipefail

target=""
mode=""
mode_args=()
transform_script=""
transform_file=""
use_stdin_transform=false
dry_run=false
write_append=false

show_usage() {
  echo "Usage: $0 <file_or_glob> --<mode> [args...]"
  echo ""
  echo "Modes:"
  echo "  --replace <old> <new> [old2 new2 ...]"
  echo "      Replace first occurrence of each old string with new string (pairs)."
  echo "  --insert-before <pattern> <text>"
  echo "      Insert text before lines matching pattern."
  echo "  --insert-after <pattern> <text>"
  echo "      Insert text after lines matching pattern."
  echo "  --delete-matching <pattern>"
  echo "      Delete lines matching pattern."
  echo "  --append <text>"
  echo "      Append text to end of file."
  echo "  --prepend <text>"
  echo "      Prepend text to beginning of file."
  echo "  --write [content]"
  echo "      Write content to file (creates parent dirs). Reads stdin if no content arg."
  echo "      Use --append with --write to append instead of overwrite."
  echo "  --transform '<inline_python_code>'"
  echo "      Python code as argument that receives variable 'content' (file text)."
  echo "      The script receives 'content' as a local variable and should modify it."
  echo "  --transform-file <path.py>"
  echo "      Read Python transform script from a file."
  echo "  --transform-stdin"
  echo "      Read Python transform script from stdin (pipe/heredoc)."
  echo "      The script receives 'content' as a local variable."
  echo ""
  echo "Options:"
  echo "  --dry-run     Show matching files without editing"
  echo "  --append      Used with --write to append"
  echo "  -h, --help    Show this help"
  echo ""
  echo "Examples:"
  echo "  $0 file.go --replace old_str new_str"
  echo "  $0 file.go --transform 'content = content.replace("foo", "bar")'"
  echo "  $0 file.go --transform-file my_transform.py"
  echo "  cat <<'PYEOF' | $0 file.go --transform-stdin"
  echo "  import re"
  echo "  content = re.sub(r'pattern', 'replacement', content)"
  echo "  PYEOF"
  exit 0
}

while [ $# -gt 0 ]; do
  case "$1" in
    --replace|--insert-before|--insert-after|--delete-matching|--append|--prepend|--write)
      mode="${1#--}"
      shift
      while [ $# -gt 0 ]; do
        case "$1" in
          --dry-run) dry_run=true; shift ;;
          --append) write_append=true; shift ;;
          -h|--help) show_usage ;;
          --*) break ;;
          *) mode_args+=("$1"); shift ;;
        esac
      done
      ;;
    --transform)
      mode="transform"
      shift
      if [ $# -lt 1 ]; then
        echo "ERROR: --transform requires Python code as argument" >&2
        exit 1
      fi
      transform_script="$1"
      shift
      while [ $# -gt 0 ]; do
        case "$1" in
          --dry-run) dry_run=true; shift ;;
          -h|--help) show_usage ;;
          --) break ;;
          --*) echo "ERROR: Unknown option after --transform: $1" >&2; exit 1 ;;
          *) break ;;
        esac
      done
      ;;
    --transform-file)
      mode="transform"
      shift
      if [ $# -lt 1 ]; then
        echo "ERROR: --transform-file requires a file path" >&2
        exit 1
      fi
      transform_file="$1"
      shift
      while [ $# -gt 0 ]; do
        case "$1" in
          --dry-run) dry_run=true; shift ;;
          -h|--help) show_usage ;;
          --) break ;;
          --*) echo "ERROR: Unknown option after --transform-file: $1" >&2; exit 1 ;;
          *) break ;;
        esac
      done
      ;;
    --transform-stdin)
      mode="transform"
      use_stdin_transform=true
      shift
      while [ $# -gt 0 ]; do
        case "$1" in
          --dry-run) dry_run=true; shift ;;
          -h|--help) show_usage ;;
          --) break ;;
          --*) echo "ERROR: Unknown option after --transform-stdin: $1" >&2; exit 1 ;;
          *) break ;;
        esac
      done
      ;;
    --dry-run)
      dry_run=true
      shift
      ;;
    --append)
      write_append=true
      shift
      ;;
    -h|--help)
      show_usage
      ;;
    -*)
      echo "ERROR: Unknown option: $1" >&2
      exit 1
      ;;
    *)
      if [ -z "$target" ]; then
        target="$1"
      fi
      shift
      ;;
  esac
done

if [ -z "$target" ] || [ -z "$mode" ]; then
  echo "ERROR: Missing file/glob or mode" >&2
  show_usage
fi

# Resolve files
files=()
if [ -f "$target" ]; then
  files=("$target")
else
  # Treat as glob pattern. Enable globstar so ** recurses into subdirs
  # (e.g. **/*.ts matches nested dirs); nullglob so no-match yields empty list.
  shopt -s globstar nullglob
  for f in $target; do
    [ -f "$f" ] && files+=("$f")
  done
  shopt -u globstar nullglob
  # Fallback: treat target as a find -path pattern (relative to cwd)
  if [ ${#files[@]} -eq 0 ]; then
    while IFS= read -r -d '' f; do
      files+=("$f")
    done < <(find . -path "$target" -type f -print0 2>/dev/null || true)
  fi
fi

if [ ${#files[@]} -eq 0 ]; then
  echo "ERROR: No files matching: $target" >&2
  exit 1
fi

if [ "$dry_run" = true ]; then
  echo "Would edit ${#files[@]} file(s) matching '$target':"
  for f in "${files[@]}"; do
    echo "  $f"
  done
  exit 0
fi

modified=0
for file in "${files[@]}"; do
  case "$mode" in
    replace)
      if [ $(( ${#mode_args[@]} % 2 )) -ne 0 ]; then
        echo "ERROR: --replace requires pairs of old/new arguments" >&2
        exit 1
      fi
      python3 -c "
import sys
filepath = sys.argv[1]
args = sys.argv[2:]
with open(filepath, 'r') as f:
    content = f.read()
count = 0
for i in range(0, len(args), 2):
    old, new = args[i], args[i+1]
    idx = content.find(old)
    if idx >= 0:
        content = content[:idx] + new + content[idx + len(old):]
        count += 1
with open(filepath, 'w') as f:
    f.write(content)
print(f'Applied {count} replacement(s) to {filepath}')
" "$file" "${mode_args[@]}"
      modified=$((modified + 1))
      ;;

    insert-before)
      if [ ${#mode_args[@]} -lt 2 ]; then
        echo "ERROR: --insert-before requires <pattern> <text>" >&2
        exit 1
      fi
      pattern="${mode_args[0]}"
      text="${mode_args[1]}"
      awk -v pat="$pattern" -v txt="$text" '
        $0 ~ pat { print txt }
        { print }
      ' "$file" > "${file}.tmp" && mv "${file}.tmp" "$file"
      modified=$((modified + 1))
      ;;

    insert-after)
      if [ ${#mode_args[@]} -lt 2 ]; then
        echo "ERROR: --insert-after requires <pattern> <text>" >&2
        exit 1
      fi
      pattern="${mode_args[0]}"
      text="${mode_args[1]}"
      awk -v pat="$pattern" -v txt="$text" '
        { print; if ($0 ~ pat) { print txt } }
      ' "$file" > "${file}.tmp" && mv "${file}.tmp" "$file"
      modified=$((modified + 1))
      ;;

    delete-matching)
      if [ ${#mode_args[@]} -lt 1 ]; then
        echo "ERROR: --delete-matching requires <pattern>" >&2
        exit 1
      fi
      pattern="${mode_args[0]}"
      grep -v "$pattern" "$file" > "${file}.tmp" && mv "${file}.tmp" "$file"
      modified=$((modified + 1))
      ;;

    append)
      text="${mode_args[*]}"
      printf '%s\n' "$text" >> "$file"
      modified=$((modified + 1))
      ;;

    prepend)
      if [ ${#mode_args[@]} -lt 1 ]; then
        echo "ERROR: --prepend requires <text>" >&2
        exit 1
      fi
      text="${mode_args[*]}"
      { printf '%s\n' "$text"; cat "$file"; } > "${file}.tmp" && mv "${file}.tmp" "$file"
      modified=$((modified + 1))
      ;;

    write)
      content="${mode_args[*]}"
      if [ -z "$content" ]; then
        if [ ! -t 0 ]; then
          content=$(cat)
        else
          echo "ERROR: No content provided for --write. Pass as argument or pipe via stdin." >&2
          exit 1
        fi
      fi
      mkdir -p "$(dirname "$file")"
      if [ "$write_append" = true ]; then
        printf '%s\n' "$content" >> "$file"
        echo "Appended ${#content} bytes to $file"
      else
        printf '%s\n' "$content" > "$file"
        echo "Wrote ${#content} bytes to $file"
      fi
      modified=$((modified + 1))
      ;;

    transform)
      # Determine transform script: priority: --transform > --transform-file > --transform-stdin (stdin)
      script_content="$transform_script"
      if [ -n "$transform_file" ]; then
        if [ ! -f "$transform_file" ]; then
          echo "ERROR: Transform file not found: $transform_file" >&2
          exit 1
        fi
        script_content=$(cat "$transform_file")
      elif [ "$use_stdin_transform" = true ]; then
        if [ -t 0 ]; then
          echo "ERROR: --transform-stdin requires piped input" >&2
          exit 1
        fi
        script_content=$(cat)
      fi
      if [ -z "$script_content" ]; then
        echo "ERROR: No transform script provided. Use --transform, --transform-file, or --transform-stdin (pipe)." >&2
        exit 1
      fi
      python3 -c "
import sys
filepath = sys.argv[1]
with open(filepath, 'r') as f:
    content = f.read()
# Execute the transform script - it receives 'content' variable and should modify it
exec(compile(sys.argv[2], '<transform>', 'exec'))
with open(filepath, 'w') as f:
    f.write(content)
print(f'Applied transform to {filepath}')
" "$file" "$script_content"
      modified=$((modified + 1))
      ;;

    *)
      echo "ERROR: Unknown mode: $mode" >&2
      exit 1
      ;;
  esac
done

if [ ${#files[@]} -eq 1 ]; then
  echo "Modified ${files[0]}"
else
  echo "Modified $modified file(s)"
fi
