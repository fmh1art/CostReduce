# Cost-Saving Instructions

## Where these tools are installed in the benchmark workspace

The evolved tools from this directory have already been pre-installed into the task workspace as **bind mounts**. Each top-level entry of the host `.evolve_tools/` directory (e.g. `quick_map/`, `batch_read/`, ...) is mounted directly under `/app/.preinstalled_tools/` inside the container, so the task repo at `/app/` is **not** polluted. Use them directly; do **not** waste time searching for, cloning, or reinstalling these tools.

Robust path selection pattern:

```bash
if [ -d /app/.preinstalled_tools/quick_map ]; then
  TOOLS=/app/.preinstalled_tools
elif [ -d /workspace/.preinstalled_tools/quick_map ]; then
  TOOLS=/workspace/.preinstalled_tools
elif [ -d ./.preinstalled_tools/quick_map ]; then
  TOOLS=./.preinstalled_tools
else
  TOOLS=/app/.preinstalled_tools
fi

"$TOOLS/quick_map/main.sh" . 3
"$TOOLS/multi_search/main.sh" . "TODO" "FIXME"
"$TOOLS/batch_read/main.sh" file1.py file2.py
```

Important: these are **not native agent tools**. They are ordinary shell scripts. When using a tool below, always run its `main.sh` script explicitly, e.g. `"$TOOLS/run_tests/main.sh" tests/`. Do **not** write bare commands like `run_tests tests/` or `multi_search . pattern`; those commands may not exist in `PATH`.

Consolidate operations into fewer tool calls. Never check file existence before reading, never mkdir before writing, never verify content after writing — tools handle these automatically.

**Key principles**: Combine independent reads/writes/searches; prefer `"$TOOLS/run_cmd/main.sh"` over `cd && export && command`; prefer `"$TOOLS/multi_search/main.sh"` over separate greps; prefer `"$TOOLS/code_structure/main.sh"` over grepping definitions; prefer `"$TOOLS/py_exec/main.sh"` over `python3 -c`; prefer `"$TOOLS/file_patch/main.sh"` / `"$TOOLS/multi_replace/main.sh"` over fragile `sed`; use `"$TOOLS/quick_map/main.sh"` first to explore layout; use `"$TOOLS/find_repo_root/main.sh"` when unknown.

## Tools

### batch_read
Read multiple files or line ranges in one call.
- `file1 [file2...]` — files to read
- `file:start-end` — read line range
- `--head=N` / `--tail=N` — first/last N lines
- `--lines=start-end` — range for subsequent files
- `--number, -n` — show line numbers
- `--dir=PATH [--include=GLOB] [--exclude=GLOB]` — read dir files filtered by glob
```
"$TOOLS/batch_read/main.sh" file1.py file2.py
"$TOOLS/batch_read/main.sh" file1.py:10-30
"$TOOLS/batch_read/main.sh" --head=20 file1.py
"$TOOLS/batch_read/main.sh" --dir=/project --include="*.py"
```

### build_check
Run build/vet/test for Go, TypeScript (tsc), Python (syntax check).
- `<target>` — file or directory
- `--build-only / --vet-only / --test-only / --compile-only` — limit checks
- `--tags=TAGS` — Go build tags
- `--goos=OS / --goarch=ARCH` — cross-compilation
- `--ts [--filter=PATTERN]` — TypeScript check
- `--python` — Python syntax check
```
"$TOOLS/build_check/main.sh" ./pkg/                          # Go: build+vet+test
"$TOOLS/build_check/main.sh" ./pkg/ --compile-only --tags="kqueue,dev"
"$TOOLS/build_check/main.sh" lib/ --ts --filter=circleci
"$TOOLS/build_check/main.sh" file.py --python
```

### code_structure
List functions, structs, classes, interfaces, traits, enums in source files.
- `file1 [file2...]` — source files
- `--summary, -s` — compact one-line summary
```
"$TOOLS/code_structure/main.sh" main.go
"$TOOLS/code_structure/main.sh" --summary src/*.py
"$TOOLS/code_structure/main.sh" utils.py handler.py
```

### file_patch
Modify files with structured actions (replace, insert, delete, append, prepend).
- `<file>` — file to modify
- `<action>` — `replace`, `insert-before`, `insert-after`, `delete-matching`, `append`, `prepend`
- `[args...]` — action-specific arguments
```
"$TOOLS/file_patch/main.sh" file.go replace "old" "new"
"$TOOLS/file_patch/main.sh" file.py insert-after "def foo():" "    print('bar')"
"$TOOLS/file_patch/main.sh" file.go delete-matching "debugger;"
"$TOOLS/file_patch/main.sh" file.py append "# end of file"
```

### find_files
Find files by name pattern with filtering.
- `[directory]` — search root (default: `.`)
- `-n, --name=PATTERN` — name glob (repeatable)
- `-t, --type=TYPE` — `f` (file) or `d` (dir)
- `-d, --max-depth=N` — max depth
- `-l, --limit=N` — max results (default: 100)
- `-p, --path=PATH` — path glob filter
- `-x, --exclude=PATTERN` — exclude path pattern
- `-i, --case-insensitive` — case-insensitive matching
- `--no-exclude-defaults` — don't auto-exclude `.git`/`node_modules`
```
"$TOOLS/find_files/main.sh" . -n "*.go"
"$TOOLS/find_files/main.sh" . -n "*.rs" -n "*.c" -n "*.h"
"$TOOLS/find_files/main.sh" /project -n "*.go" -d 4 -l 50
"$TOOLS/find_files/main.sh" . -n "*test*" -i
```

### find_repo_root
Find the root directory of a Git repository.
- `[starting_directory]` — search upward from here (default: common locations then current dir)
```
"$TOOLS/find_repo_root/main.sh"
"$TOOLS/find_repo_root/main.sh" /workspace/subdir
```

### git_commit
Stage all changes and commit in one step.
- `<message>` — commit message (required)
- `[directory]` — repo directory (default: current dir)
```
"$TOOLS/git_commit/main.sh" "fix: resolve type error"
"$TOOLS/git_commit/main.sh" "feat: add new feature" /workspace/repo
```

### git_diff
Show git changes summary (status, diff, log) in one view.
- `[directory]` — repo dir (default: `.`)
- `--stat-only / --name-only / --cached / --short` — display modes
- `--log=N` — last N commits
- `--oneline` — one-line commit format
```
"$TOOLS/git_diff/main.sh"
"$TOOLS/git_diff/main.sh" --stat-only
"$TOOLS/git_diff/main.sh" --log=5 --oneline
"$TOOLS/git_diff/main.sh" --short
```

### multi_replace
Perform multiple string replacements in a file in one step.
- `<file> <old1> <new1> [old2 new2 ...]` — replacement pairs
- `--pairs old1 new1 old2 new2 ...` — explicit pairs
- `-f <script.py>` — custom Python transform (receives `content`, `filepath`)
```
"$TOOLS/multi_replace/main.sh" file.go "l.fill(" "l.frame.fill(l.selector, "
"$TOOLS/multi_replace/main.sh" file.go --pairs "old1" "new1" "old2" "new2"
"$TOOLS/multi_replace/main.sh" file.go -f transform.py
```

### multi_search
Search multiple patterns in a single filesystem pass.
- `<directory_or_file> <pattern1> [pattern2...]` — target and patterns
- `--include='*.ext'` — file type filter
- `--names-only` — search file names only
- `-i, --ignore-case` — case-insensitive search
- `-l, --files-with-matches` — list filenames only
- `-v, --exclude-pattern=PATTERN` — exclude matching lines (repeatable)
```
"$TOOLS/multi_search/main.sh" . pattern1 pattern2
"$TOOLS/multi_search/main.sh" . --include='*.py' class1 class2
"$TOOLS/multi_search/main.sh" . --names-only test_* *_test.py
"$TOOLS/multi_search/main.sh" . -i PATTERN
"$TOOLS/multi_search/main.sh" . -l pattern
```

### py_exec
Run Python code with auto-venv activation and environment variables.
- `<code_string>` — inline Python code
- `-f <script.py> [args...]` — run a script file
- `--check, --check-syntax <file.py> [file2...]` — syntax check only
- `--env=KEY=value, -e KEY=value` — set env vars (repeatable)
```
"$TOOLS/py_exec/main.sh" "print('hello world')"
"$TOOLS/py_exec/main.sh" -f test.py arg1 arg2
"$TOOLS/py_exec/main.sh" --check file.py
"$TOOLS/py_exec/main.sh" -e MY_VAR=123 "import os; print(os.environ['MY_VAR'])"
```

### quick_map
Generate a compact tree view of project structure with file sizes and extension stats.
- `[directory]` — dir to map (default: `.`)
- `[max_depth=4]` — max depth
- `--filter=GLOBS, -f GLOBS` — show only specific file types (comma-separated globs)
```
"$TOOLS/quick_map/main.sh" . 3
"$TOOLS/quick_map/main.sh" /project
"$TOOLS/quick_map/main.sh" . --filter="*.py,*.md"
"$TOOLS/quick_map/main.sh" src/ 2 -f "*.go"
```

### run_cmd
Run arbitrary commands in a specified directory with optional environment variables.
- `-C, --dir=DIR` — working directory
- `-e, --env=KEY=val` — set env var (repeatable)
- `--timeout=SECONDS` — timeout
- `<command> [args...]` — command to run
```
"$TOOLS/run_cmd/main.sh" --dir=/app python -m scapy.tools.UTscapy -t test.uts
"$TOOLS/run_cmd/main.sh" --dir=/app/src -e DJANGO_SETTINGS_MODULE=paperless.settings pytest tests/
"$TOOLS/run_cmd/main.sh" --timeout=30 curl http://example.com
```

### run_tests
Run tests for any language/framework. Auto-detects framework.
- `<test_path>` — test file or directory
- `--go <pkg> / --vitest <file> / --jest <file> / --pytest <file>` — force framework
- `--all` — run all available test commands
- `--grep=pattern` — filter tests by name
- `--count=N` — test repetition (Go only)
- `--env=KEY=value, -e KEY=value` — env vars (repeatable)
- `--no-coverage` — disable coverage
- `--tags=TAGS` — Go build tags
- `--timeout=DURATION` — timeout (e.g., `90s`)
- `--verbose, -v` — verbose output
```
"$TOOLS/run_tests/main.sh" tests/                          # Auto-detect
"$TOOLS/run_tests/main.sh" --pytest tests/test_api.py
"$TOOLS/run_tests/main.sh" --go ./pkg/...
"$TOOLS/run_tests/main.sh" --go --tags="kqueue,dev" ./pkg/
"$TOOLS/run_tests/main.sh" --vitest lib/module/
```

### write_file
Write content to a file atomically. Creates parent directories automatically.
- `<filepath>` — path to write
- `<content>` — content to write
- `-` — read content from stdin
```
"$TOOLS/write_file/main.sh" /tmp/out.txt "Hello World"
"$TOOLS/write_file/main.sh" /project/main.py "print('hello')
print('world')"
echo "content" | "$TOOLS/write_file/main.sh" /tmp/out.txt -
```
