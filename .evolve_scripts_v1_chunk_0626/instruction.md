# Cost-saving instructions

High-level guidance for reducing future agent cost.

## When exploring a project

- **Use `explore_project` to understand project layout** at the start of a task. Replaces 5-10 separate find/ls/wc calls with a single overview showing source files, directory structure, config files, and file counts. Auto-detects language (Python, Go, C, Rust, TypeScript). Excludes build artifacts automatically.
- **Use `explore_project --subdir <path> to focus on a specific module** when you only need to explore a particular subdirectory (e.g., `explore_project /app ts 50 --subdir src/schema`). Replaces the common pattern of running `find <subdir> -type f -name "*.ts"` to discover files in a specific area.
- **Use `code_section` to read specific functions/structs/enums/traits/classes** by name instead of grepping for line numbers then reading ranges. Saves 2-3 separate read/grep steps. Supports Rust (`fn`, `pub fn`, `pub(crate) fn`, `async fn`, `struct`, `enum`, `trait`, `impl`, `type`, `const`, `static`, `mod`), Go (`func`, `type`, `var`, `const`), Python (`def`, `class`), TypeScript (`function`, `class`, `interface`, `type`, `enum`), and C/C++ patterns.
- **Use `code_section` for brace-delimited blocks** (functions, structs, interfaces, enums, traits, impl blocks, var/const blocks) across Go, Rust, TypeScript, C, and other brace-delimited languages.
- **For Python files, `code_section` uses indentation-based block detection** instead of brace matching. This correctly captures the full body of Python classes and functions (including nested methods) by tracking indentation levels. For example:
  - `code_section /app/app/models.py User` reads the entire User class definition from a SQLAlchemy models file
  - `code_section /app/email_handler.py handle_forward` reads the complete handle_forward function body
  - Replaces the pattern `grep -n "class User" file.py | head -5` + `nl -ba file.py | sed -n '336,450p'` with a single call
- **Use `smart_source_reader` to inspect source code** with line numbers, function definitions, line ranges, or grep in a single call. Use `lines` to read specific line ranges, `functions` to list all definitions, `grep` to search for patterns, or `batch` mode to read multiple file:range specs in one call (e.g., `smart_source_reader 'file1.rs,file2.rs:10-50' batch`).
  - Replace `sed -n '73,400p' file.ts` with `smart_source_reader file.ts lines 73 400`
  - Replace `cat file.ts | head -200` with `smart_source_reader file.ts lines 1 200`
  - Replace `grep -n 'class MyClass' file.ts` with `smart_source_reader file.ts grep 'class MyClass'`
  - Batch mode: `smart_source_reader 'file1.ts:1-200,file2.ts:50-150' batch` reads both files in one call
- **Use `batch_read` to inspect multiple files at once** instead of chaining separate `cat` commands. Saves 2-5+ tool calls when you need to read several related files (e.g., source modules, test files, config files). Use `--head` or `--tail` to limit output per file and avoid token overflow.
- **Use `multi_grep` to search for several patterns at once** instead of running separate grep calls. Automatically excludes noise directories (node_modules, __pycache__, .git, vendor). Use `--include` to filter by file extension, `--no-tests` to skip test files, and `--no-dts` to skip type declarations.
  - To **search inside `node_modules` for library type definitions** (e.g., finding `FindOptions` interface in a dependency), add `--no-exclude --include "*.d.ts"` to override the automatic exclusions: `multi_grep "interface FindOptions" node_modules/@mikro-orm/ --include "*.d.ts" --no-exclude --max 10`
  - To **find type definition files in node_modules**: `multi_grep "definitions|interfaces" . --include "*.d.ts" --no-exclude --file-only`
- **Use `git_operations show HEAD:path` to view committed files** without stashing. When you need to compare a modified file with its original committed version, use `git_operations show HEAD:path` instead of the save-test-restore workflow (stash, view, stash pop). This gives you the original content directly without modifying your working tree, saving 2 tool calls.

## Recommended workflow for tackling a task

1. **Explore the project** with `explore_project /app` to understand the layout, find source files, and identify key modules. Use `explore_project /app ts 50 --subdir src/schema` if you need a focused view of a specific module.
2. **Read relevant files** with `batch_read` to inspect multiple source files in one call. Use `--head N` to limit large files (e.g., `batch_read --head 200 file1.ts file2.ts`).
3. **Search for patterns** with `multi_grep` to find references across the codebase (e.g., `multi_grep "pattern" . --include "*.ts" --no-tests --no-dts`).
4. **Read specific line ranges** with `smart_source_reader lines` instead of `sed -n 'X,Yp'`.
5. **Find definitions by name** with `code_section` instead of grepping for line numbers then reading ranges (e.g., `code_section select.ts PgSelectBase`).
6. **Modify files** with `file_patch` (single file) or `batch_patch` (same edit to multiple files matching a glob pattern) instead of `sed -i` (use `insert-after`, `replace`, `replace-range`, `replace-line`, `replace-block`, `replace-pyblock`, `stdin-replace`, or `stdin-replace-pyblock` with pattern matching). For multiline replacements with complex code blocks, use `stdin-replace` which reads old and new content from stdin via heredoc, avoiding shell escaping issues entirely. For Python files, use `replace-pyblock` to replace an entire function/method/class body by finding its boundaries via indentation rules. For complex Python function replacements with quotes and indentation, use `stdin-replace-pyblock` which reads the new function body from stdin via heredoc, avoiding shell escaping issues entirely (e.g., `cat << EOF | file_patch api.py stdin-replace-pyblock "def format_string"`).
7. **Create new files** with `create_file` instead of heredocs (e.g., `create_file path/to/new.ts 'line1\nline2\nline3'`). Use `\n` for newlines inside the content string.
8. **Verify changes** with runner scripts (`go_runner`, `cargo_runner`, `ts_runner`, `python_runner`, `pytest_runner`) instead of raw build/test commands. These handle output limiting, timeout, and working directory management automatically.

## When modifying files in Go projects

- **Use `file_patch insert-after` to add a new field to a Go struct.** Find a unique pattern in the line before your insertion point (e.g., an existing field declaration), then insert after it. Use `\n` for newlines and `\t` for tabs inside the inserted text:

  file_patch event.go insert-after "WALDeleted func(WALDeleteInfo)" "\t// BatchDurable is invoked after a sync commit completes its WAL sync.\n\t// It fires exactly once per Sync commit, even on failure.\n\tBatchDurable func(BatchDurableInfo)"

- **Use `file_patch insert-before` to add a new type definition before an existing one.** For example, to insert a new `BatchDurableInfo` struct before `WriteStallBeginInfo`:

  file_patch event.go insert-before "// WriteStallBeginInfo contains" "// BatchDurableInfo contains the info for a batch durable event.\n// It fires after a sync commit completes the WAL sync, even on failure.\ntype BatchDurableInfo struct {\n\t// JobID is the ID of the job.\n\tJobID int\n\t// SeqNum is the sequence number of the batch.\n\tSeqNum base.SeqNum\n\t// ...\n}"

- **Use `file_patch insert-after` to add a new field to a Go struct by matching the field definition text.** For Go structs, match on the exact field line like `"WALDeleted func(WALDeleteInfo)"` (including the tab prefix). The Python-based patching handles tabs literally so `\t` in the pattern is not needed - just match the visible field text.

- **Use `file_patch insert-after` to add nil-check initialization blocks.** After adding a new callback field to an EventListener-style struct, you typically need to add an `if l.NewField == nil { l.NewField = func(...) {} }` block in the initialization function. Find the existing nil-check block with `smart_source_reader event.go grep "if l.WALCreated == nil"` or `code_section` to read the initialization function, then use `file_patch insert-after` to add the new check:

  file_patch event.go insert-after "if l.WALDeleted == nil {" "\tif l.BatchDurable == nil {\n\t\tl.BatchDurable = func(info BatchDurableInfo) {}\n\t}"

- **Use `file_patch insert-after` to add entries to the TeeEventListener forwarding function** when mirroring a new callback across all event listener combinators. Find the pattern for the preceding field's forwarding entry and insert after it.

- **Use `go_runner` to run Go tests** with specific test name filters and output limiting:
  - `go_runner /app test ./... --run "TestName|TestOther" -v --tail 30` — runs specific tests and shows only the last 30 lines.
  - `go_runner /app test ./... --clean-cache` — clears test cache before running.
  - `go_runner /app test ./... --stash --run TestExisting` — stash changes, run tests on clean code, restore.

- **Use `create_file` to create Go test files** instead of heredocs. Pass content with `\n` for newlines:
  `create_file path/to/test.go 'package pebble\n\nimport "testing"\n\nfunc TestX(t *testing.T) {\n\tt.Log("hello")\n}'`

## When modifying files with multiline code replacements

This pattern occurs frequently when implementing new features: you need to replace an existing function body, add a new method with multi-line content, or update a block of code across one or more files.

**Use `file_patch stdin-replace` for single-file multiline replacements.** Instead of writing a Python heredoc script:

```bash
# DON'T: Write inline Python scripts
python3 << 'PYEOF'
with open('file.ts', 'r') as f:
    content = f.read()
old = """..."""
new = """..."""
content = content.replace(old, new)
with open('file.ts', 'w') as f:
    f.write(content)
print("Done")
PYEOF

# DO: Use file_patch with stdin-replace
file_patch file.ts stdin-replace << 'EOF'
...old content...
=====REPLACE=====
...new content...
EOF
```

The `stdin-replace` action reads old text (before the delimiter) and new text (after the delimiter) from stdin, performs a replacement, and writes the file. No escaping needed for multiline content with special characters like `$`, `&`, backticks, or quotes.

**Use `batch_patch stdin-replace` for the same multiline replacement across multiple files.** When the same code block needs to be replaced in several files (e.g., updating a function body in all dialect implementations), pipe the old/new content through stdin once:

```bash
batch_patch "src/**/modifiers/*.ts" stdin-replace << 'EOF'
old function body
=====REPLACE=====
new function body
EOF
```

**Use `file_patch stdin-replace-pyblock` for replacing Python function bodies.** Instead of writing inline Python heredoc scripts:

```bash
# DON'T: Write inline Python scripts to replace a Python function body
python3 << 'PYEOF'
with open('src/api.py', 'r') as f:
    content = f.read()
start = content.find('def format_string')
# ... complex logic ...
new_func = 'def format_string(...):
    ...
'
content = content[:start] + new_func + content[end:]
with open('src/api.py', 'w') as f:
    f.write(content)
print("Done")
PYEOF

# DO: Use file_patch with stdin-replace-pyblock
cat << 'PYEOF' | file_patch src/api.py stdin-replace-pyblock "def format_string"
def format_string(source_string: str, mode: Mode) -> str:
    """Formats SQL string."""
    ddl_result = _format_ddl_string(source_string, mode.line_length)
    if ddl_result is not None:
        return ddl_result
    analyzer = mode.dialect.initialize_analyzer(line_length=mode.line_length)
    return analyzer.format(source_string)
PYEOF
```

The `stdin-replace-pyblock` action takes the function name pattern from command-line args and reads the new function body from stdin via heredoc. It finds the Python function/method/class by name, determines its boundaries using Python indentation rules (next line at same or lesser indentation marks the end), and replaces the entire block. No escaping needed for the function body - quotes, indentation, and special characters all work naturally.


This replaces 3-10+ separate `file_patch` calls or inline Python scripts with a single command. The script handles all files matching the glob pattern, reporting which were modified and which were skipped.

## When adding a new variant to a discriminated union type (TypeScript)

This is a common pattern when extending type systems. Instead of manually touching each file:

1. **First, find all files that reference the union type** with `multi_grep` (e.g., `multi_grep "switch.*schema\\.type|fromSchemaDTO|getSchemaDTO" . --include "*.ts"`).
2. **Then decide which files need actual modifications** — not all files that reference the union type need changes (e.g., reference-type files may not need updates).
3. **Use `batch_patch` for systematic changes:**
   - Add imports: `batch_patch "src/**/*.ts" insert-after "import { XxxSchema } from" "import { NewVariant } from '~/schema/newVariant/index.js'"`
   - Add to type union: `batch_patch "src/schema/types/schema.ts" insert-before "export type Schema_" "import type { NewVariant, NewVariant_ } from '../newVariant/index.js'\n"`
   - Add switch case: `batch_patch "src/schema/actions/**/schema.ts" insert-before "case 'existingVariant':" "case 'newVariant':\n  return handleNewVariant(schema)\n"`
   - Add function parameter: `batch_patch "src/schema/actions/dto/getSchemaDTO/*.ts" replace-range "): " "=> {" "  newParam?: Type\n): "`
   - **Replace function body (with brace matching):** `batch_patch "src/**/modifiers/added.ts" replace-block "return <T extends TraitOrRelation[]>(" "{\n    const traits: Trait[] = [];\n    for (const input of inputs) {\n        if (isAspect(input)) {\n            for (const t of input.traits) {\n                traits.push(t);\n            }\n        } else {\n            traits.push(input as Trait);\n        }\n    }\n    return createModifier(...);\n}"`
4. **Only create new files for the variant itself** using `create_file` — one call per file, not one heredoc per file.

## When formatting code

- **Use `prettier` to check and fix code formatting** instead of raw `npx prettier` commands. The `prettier` script handles output limiting, timeout, and grep filtering automatically.
  - Use `prettier <project_root> list-unformatted '<pattern>'` to check which files are unformatted (like `npx prettier --check ... 2>&1 | grep -E "unformatted|warn" | head -10`). Returns exit code 0 if all files are formatted.
  - Use `prettier <project_root> write '<pattern>'` to format files in place (like `npx prettier --write ...`).
  - Use `prettier <project_root> check '<pattern>'` to check formatting and list unformatted files.

## When reading files

- **Use `smart_source_reader` for reading specific line ranges** — replaces `sed -n 'X,Yp' file`.
- **Use `smart_source_reader functions` to list all function/class/struct definitions** in a source file — replaces grepping for `func`, `def`, `class`, `fn`, `function` keywords.
- **Use `smart_source_reader grep` to search within a single file** — replaces `grep -n 'pattern' file`.
- **Use `batch_read` to read multiple files at once** with clear separators and built-in output limiting (--head/--tail). Replaces chaining multiple `cat file` or `sed -n` commands.

## When working with language-specific runners

- **Use `go_runner` for Go projects** — build, test, run, vet, fmt, fmtcheck, fmtdiff, syncheck, gofmt, mod. All actions support --head/--tail output limiting, timeout, and --stash.
- **Use `cargo_runner` for Rust projects** — build, test, check, clippy, run. Supports package targeting, --errors-only, --stash, and output limiting.
- **Use `ts_runner` for TypeScript projects** — run, eval, test, vitest, vitest-paths, vitest-file, tsc. Handles tsx runner setup, vitest config, and output limiting.
- **Use `python_runner` for Python scripts** — run, eval, test. Handles timeout and output limiting.
- **Use `pytest_runner` for Python test suites** — supports file paths, -k filters, --max-lines, --timeout, --no-header.
- **Use `python_import_manager` to safely manage Python imports** — add, remove, check, list. Uses AST-based analysis to add imports in the correct location (stdlib, third-party, local) and avoid duplicates.

## "Don't" rules — patterns to avoid

- Don't grep for patterns without excluding noise directories (`node_modules`, `__pycache__`, `.git`, `vendor`, `.venv`, `dist`, `build`) — `multi_grep` handles these exclusions automatically.
- **Don't use `grep -rn ... --include ... -not -path ...`** — Use `multi_grep` instead. It handles the exclusion logic, output limiting, and supports searching multiple patterns simultaneously with pipe-separated notation.
- **Don't use `cd /some/path && ...` to explore the project** — Use `explore_project /some/path` instead. It auto-detects language, counts files, shows directory structure, and lists source files — all in one call.
- **Don't run `cat file | head -N` to read files** — Use `smart_source_reader file lines 1 N` (with line numbers) or `batch_read --head N file` (without line numbers but with file separators). Both are single calls with cleaner output.
- **Don't run `nl -ba file | sed -n 'X,Yp'` to read line ranges** — Use `smart_source_reader file lines X Y` instead. It shows line numbers, works across all file types, and is a single call versus two chained commands.
- **Don't debug sed escaping issues with `cat -A`, `xxd`, or `od -c`** — If `sed -i` produced incorrect output (e.g., `u0026` instead of `&`), the root cause is using `sed -i` in the first place. The fix is to use `file_patch` instead. If you need to inspect whitespace/indentation, use `show_whitespace file.go tabs` instead of `cat -A` — it shows tabs as → with line numbers, making it much easier to see tab depth.
- **Don't use `sed -i` with line-based insertions (`LINEa\`, `LINEi\`, `LINEc\`)** — These break when any other edit shifts line numbers. Use `file_patch insert-after` or `insert-before` which match on text patterns, not line numbers.
- **Don't assume the working directory** — Always check with `pwd` first, then use `explore_project` with the detected root. If you try `cd /workspace` and it fails, use `pwd` to discover the actual working directory. The `explore_project` script does NOT change directory — it takes an absolute path.
- **Don't write temporary Go test files with heredocs then fix compilation errors in a second step** — Use `go_runner run /tmp/test.go` to compile and run in one call. For permanent test files, use `create_file` to avoid heredoc escaping issues.
- **Don't use the stash-test-pop workflow to verify clean Go builds** — Use `go_runner test --stash` or `go_runner build --stash` which combines all three steps (stash, run command, pop) into a single call. This saves 2 tool calls each time you need to verify the original code compiles before applying edits.
- **Don't run full test suites when only specific tests are relevant** — Use `go_runner test --run "TestName"` to run only matching tests. This is faster and produces less output, reducing token usage. Combine with `--count=1` to bypass stale test cache, and use `--tail 50` to see only the test summary at the end.
- **Use `go_runner fmtcheck <file>` to check Go formatting on a specific file** instead of `gofmt -e file.go | head -N`. The `fmtcheck` action runs `gofmt -e` on a specific file and displays the formatted output without modifying the file. Use this when you need to check if a file is properly formatted or see what gofmt would change. This replaces the pattern of running `cd <project> && gofmt -e path/to/file.go 2>&1 | head -5`.
- **Use `go_runner fmtdiff <file>` to see formatting diffs** instead of `gofmt -d file.go`. Shows what gofmt would change without modifying the file. Exit code 0 means no formatting changes needed.
- **Use `go_runner syncheck <file>` to check Go syntax silently** instead of `gofmt -e file.go > /dev/null 2>&1 && echo "Syntax OK" || echo "Syntax errors"`. The `syncheck` action runs `gofmt -e` on a specific file, discards the formatted output, and only reports whether the syntax is valid ("Syntax OK: <file>") or has errors ("Syntax errors found: <file>"). Exit code is 0 for valid syntax, 1 for errors. Use this after making file modifications to quickly verify no syntax issues were introduced. Replaces the common two-step pattern of running gofmt then checking the exit code.

## When using git

- **Use `git_operations` for all git actions** — status, branch creation, checkout, add, commit, add-commit, diff, log, stash, show. The auto-configure feature prevents the "Author identity unknown" error that costs a round trip.
- **Create branches with `git_operations <project> checkout -b <branch>` instead of raw `git checkout -b`** — This ensures the git identity is configured and the command runs in the right directory. Saves 1 tool call.
- **Use `git_operations show HEAD:path` to view committed files** without stashing changes. This is non-destructive and saves 2 tool calls compared to the stash-view-pop workflow.

## Available scripts reference

- `explore_project` — Explore project structure with auto-detected language. Use at start of any task.
- `code_section` — Read a named code section (function/struct/interface/etc.) by name. Supports Go, Rust, Python (with indentation-based block detection), TypeScript, C/C++.
- `smart_source_reader` — Read source files with line numbers, search patterns, list functions, batch read multiple files.
- `batch_read` — Read multiple files in one call with clear separators and --head/--tail limiting.
- `multi_grep` — Search for pipe-separated patterns with automatic noise exclusion.
- `file_patch` — Patch a single file with actions: replace, stdin-replace, insert-before, insert-after, delete-matching, append, prepend, replace-range, replace-line, replace-block, replace-pyblock, stdin-replace-pyblock.
- `batch_patch` — Apply the same edit to multiple files matching a glob pattern. Same actions as `file_patch`.
- `create_file` — Create a new file with content, auto-creating parent directories. Use `\n` for newlines in content strings.
- `go_runner` — Run Go commands (build, test, run, vet, fmt, fmtcheck, fmtdiff, mod) with timeout and output limiting.
- `cargo_runner` — Run Cargo commands (build, test, check, clippy, run) with package targeting and output limiting.
- `ts_runner` — Run TypeScript files, inline expressions, vitest tests, and tsc type checking.
- `python_runner` — Run Python files or inline expressions with timeout and output limiting.
- `pytest_runner` — Run pytest with file and test name filtering, output limiting.
- `python_import_manager` — Safely manage Python imports (add/remove/check/list) with AST-based analysis.
- `git_operations` — Common git operations with auto-configured identity.
- `prettier` — Check and fix code formatting with prettier.
- `show_whitespace` — Display file content with visible whitespace (tabs as →, trailing spaces as ·).

## When running Go tests with data-driven frameworks

- **Use `go_runner test --rewrite` for data-driven test frameworks** (e.g., `datadriven` in CockroachDB/Pebble). The `--rewrite` flag passes `-rewrite` to `go test`, telling the framework to auto-update test data files based on actual output. This replaces the pattern `go test -v -run "TestName$" -rewrite -timeout 60s`:

  `go_runner /app test ./... --run "TestEventListener$" --rewrite -v --go-timeout 60s`

- **Use `go_runner test --go-timeout D` for Go's native `-timeout` flag** (e.g., `--go-timeout 60s`, `--go-timeout 5m`). This passes `-timeout` to `go test` directly, separate from the bash timeout wrapper (`--timeout`). Use `--go-timeout` when the Go test framework needs its own timeout, and `--timeout` for the bash-level timeout wrapper that prevents the entire command from hanging. Replaces the pattern of manually adding `-timeout 60s` to the test command.

- **Combine `--rewrite`, `--go-timeout`, `--run`, and `-v`** for complex test scenarios:
  `go_runner /app test ./... --run "TestBatchDurable|TestEventListener" -v --rewrite --go-timeout 60s --tail 50`

## When running inline Python scripts for file analysis/transformation

- **Use `python_runner script` to run Python code from stdin via heredoc** instead of the raw `python3 << 'PYEOF'` pattern. The `script` action reads Python code from stdin and executes it in the project root directory, with automatic timeout and output limiting. Replace:

  ```bash
  # DON'T: Raw heredoc
  cd /app && python3 << 'PYEOF'
  ...multi-line Python code...
  PYEOF

  # DO: Use python_runner script
  python_runner /app script << 'PYEOF'
  ...multi-line Python code...
  PYEOF
  ```

  The `script` action is ideal for:
  - **Regex-based file transformations** (incrementing numbers, updating patterns):
    ```bash
    python_runner /app script << 'PYEOF'
    import re
    with open('testdata/event_listener') as f:
        content = f.read()
    content = re.sub(r'\[JOB (\d+)\]',
        lambda m: f'[JOB {int(m.group(1))+1}]' if int(m.group(1)) >= 2 else m.group(0),
        content)
    with open('testdata/event_listener', 'w') as f:
        f.write(content)
    print("Updated job IDs")
    PYEOF
    ```
  - **Counting pattern occurrences in files**:
    ```bash
    python_runner /app script "Count JOB IDs" << 'PYEOF'
    import re
    with open('testdata/event_listener') as f:
        content = f.read()
    matches = re.findall(r'\[JOB (\d+)\]', content)
    for m in sorted(set(matches), key=int):
        print(f"  JOB {m}: {matches.count(m)} occurrences")
    PYEOF
    ```
  - **Complex code modifications** that need conditional logic or regex-based replacements.
  - The optional description argument (e.g., `"Count JOB IDs"`) helps identify the script in output.

- **Use `python_runner eval` for short one-liners** that fit on a single line. For multi-line code with complex logic, always prefer `python_runner script` with a heredoc to avoid escaping issues.

## Available scripts reference (continued)

- `go_runner` now supports `--rewrite` (for data-driven test frameworks) and `--go-timeout` (for Go native -timeout flag).
- `python_runner` now supports `script` action (read Python code from stdin via heredoc), `--env-file` option for safe .env loading, and auto-detection of virtualenv and .env files.

## When running Python code in project context (with virtualenv and .env files)

- **Use `python_runner` instead of raw `python3` commands** when working with Python projects that have virtual environments or .env files. The script auto-detects and activates the virtualenv, and safely loads environment variables from .env files. This eliminates two common failure patterns:

  **1. Virtual environment activation failures:**
  ```bash
  # DON'T: Use 'source' (bashism) - fails with '/bin/sh: source: not found'
  cd /app && source /app/venv/bin/activate && python3 -c "..."

  # DON'T: Try to run without venv - gets ModuleNotFoundError
  cd /app && python3 -c "from app import config; ..."  # Fails: no module 'dotenv'

  # DO: Use python_runner - auto-detects and activates venv
  python_runner /app eval "from app import config; print(config.DB_URI)"
  ```

  **2. .env file loading failures:**
  ```bash
  # DON'T: Use 'export $(grep ... | xargs)' - breaks on values with spaces/special chars
  # Example: FLASK_DEBUG=True\nEMAIL_URL=postgresql://user:pass@host/db?param=value
  #          --> "export: email.hostname.)]: bad variable name"
  cd /app && export $(grep -v '^#' example.env | xargs) && python3 -c "..."

  # DO: Use python_runner with --env-file (uses Python-based safe parsing)
  python_runner /app eval "from app import config; print(config.DB_URI)" --env-file example.env

  # Or let it auto-detect .env, env, .env.example, or example.env in the project root
  python_runner /app eval "from app import config; print(config.DB_URI)"
  ```

  The `python_runner` script:
  - **Auto-detects virtualenv** by checking for `venv/`, `.venv/`, `env/`, `virtualenv/` in the project root
  - **Activates venv using `.` (POSIX-compatible)** instead of `source` (bashism), avoiding the "source: not found" error
  - **Auto-detects .env files** (`.env`, `env`, `.env.example`, `example.env`) and loads them via Python-based parsing
  - **Supports `--env-file <path>`** for custom env file paths
  - **Uses Python's built-in `os.environ`** to set env vars safely, avoiding shell export issues with special characters

- **When you need to inspect config values or test imports** from a Python project, use:
  ```bash
  python_runner /app eval "from app import config; print('DB_URI:', config.DB_URI[:50])"
  ```
  This handles venv activation and .env loading automatically, replacing the trial-and-error pattern of: run without venv → ModuleNotFoundError → try source → source not found → try . → KeyError → try export $(grep ...) → bad variable name.

- **For running test files or test directories** in a Python project with venv:
  ```bash
  python_runner /app test tests/unit_tests/ --max-lines 80
  ```
  Auto-activates venv and loads .env before running pytest.

## Available scripts reference (continued)

- `python_runner` now supports `--env-file` option, auto-detection of virtualenv (venv/, .venv/, env/, virtualenv/), and auto-detection/safe loading of .env files.
