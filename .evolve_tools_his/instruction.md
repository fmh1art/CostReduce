# Cost-saving instructions

## High-level guidance to reduce future agent cost

### Golden rule: Use evolved tools instead of raw bash
Every evolved tool exists because raw bash patterns are verbose, fragile, or produce too much output.
**If there is an evolved tool for your task, use it.** Raw bash should only be used for truly one-off operations.

### Operations to AVOID (high cost, low value)

#### File discovery & exploration
1. **Avoid sequential `ls` then `find`**: Use `project_structure` tool instead - it combines both in one call and shows file counts per language, directory tree, and config files
2. **Avoid `find . -type f -name "*.go" | head -50`**: Use `project_structure <dir> <ext>` to list files and see counts
3. **Avoid `find src -type f | head -50` then `find src -type f | tail -50`**: `project_structure` shows everything in one call
4. **Avoid `find | xargs grep -l` combinations**: Use `search_code <pattern> <dir> <ext> --files-only` to find matching files

#### Reading files
5. **Avoid `cat <file>` then `cat -n <file>`**: `read_file` already shows line numbers; read once, not twice
6. **Avoid `cat <file> | wc -l`**: `read_file` shows total line count as metadata
7. **Avoid `nl -ba <file> | sed -n '41,120p'`**: Use `read_file <file> <start> <end>` which is more readable
8. **Avoid `sed -n '89,300p' <file>`**: Use `read_file <file> 89 300` with explicit line range
9. **Avoid `cat` on large files**: Use `read_file` with line ranges to inspect only needed sections
10. **Avoid sequential `cat` of multiple files to understand patterns**: Use `read_file` on one representative file, then `search_code` to find similar patterns
11. **Avoid `head -50 <file>` for reading**: Use `read_file <file> 1 50` which shows line numbers

#### Searching code
12. **Avoid `grep -rn "pattern" <dir>`**: Use `search_code <pattern> <dir> <ext>` which excludes noise dirs automatically
13. **Avoid `grep -n "pattern" <file>` for single-file search**: Use `search_code <pattern> <filepath>` to search within a single file directly
14. **Avoid `grep --include=*.go` (or any `--include` flag)**: Many minimal environments use BusyBox grep which does not support `--include`. Use `search_code "pattern" <dir> <ext>` instead - it uses `find` + `xargs grep` which works everywhere.

#### Writing files
16. **Avoid `cat << 'EOF' > file` for creating files**: Use `write_file <filepath> <content>` instead - it's more concise, creates parent directories, shows a summary, and handles content correctly
17. **Avoid `echo "content" > file` for multi-line content**: Use `write_file` with stdin (pipe or heredoc) for proper newline handling
18. **Avoid `cat > file << 'EOF'` for appending**: Use `write_file` to create, then `edit_file` to modify

#### Editing files
19. **Avoid Python scripts or `sed` for find-and-replace in files**: Use `edit_file` which does it in a single tool call - no need for complex Python one-liners or fragile sed commands
20. **Avoid `python3 -c "with open(...) as f: ..."` for file editing**: Use `edit_file <filepath> <old_text> <new_text>` for a clean, simple replacement
21. **Avoid `sed -i 's/old/new/' <file>` for inline edits**: `edit_file` is safer (exact string match, shows diff, reports errors clearly)
22. **Avoid creating Python scripts just to edit files**: Use `edit_file` with the old and new text directly

#### Building & compiling
23. **Avoid `go build ./...` or `go vet ./...` directly**: Use `run_build <dir>` which auto-detects the build system and adds a timeout
24. **Avoid `npx tsc --noEmit` directly**: Use `run_build <dir>` which detects TypeScript and runs the compiler
25. **Avoid `cargo build` directly**: Use `run_build <dir>` which handles timeout and output limiting
26. **Avoid `go build ./pkg/...` for specific packages manually**: Use `run_build /app ./pkg/...` which auto-detects the build system and handles tags/timeout
27. **Avoid `go build -tags kqueue,dev ./...` directly**: Use `run_build /app --tags=kqueue,dev` which passes build tags automatically

#### Running tests
28. **Avoid `npx jest <file> --no-coverage --no-cache` directly**: Use `run_test <dir> <file>` which auto-detects the framework and adds timeout
29. **Avoid `go test ./...` manually**: Use `run_test <dir>` which handles timeout and output limiting
30. **Avoid running tests without timeout**: Use `run_test` with `--timeout=<seconds>` (default: 120s) to prevent hanging
31. **Avoid `npm test` or `npx vitest` directly**: Use `run_test` which auto-detects the test framework
32. **Avoid `go test -v -run "TestXxx" ./...` with build tags**: Use `run_test /app "TestXxx" --tags=kqueue,dev` which passes tags and timeout correctly
33. **Avoid `go test ./pkg/specific/... -tags=kqueue,dev` manually**: Use `run_test /app ./pkg/specific/... --tags=kqueue,dev` for package-specific testing
34. **Avoid creating custom `run_script.sh` to run Go tests**: `run_test` already handles test discovery, file-specific running, and output limiting. Just pass the test file path directly.
35. **Avoid `head -n N file > tmp && cat >> tmp << EOF` to append test functions**: Use `edit_file` with exact string match to insert new test functions at the right location, or use `write_file` to create a new test file.
36. **Avoid attempting `patch` or `git apply` with diff files**: These often fail due to path/strip mismatches. Use `edit_file` for precise string-based replacement instead.

#### Git operations
37. **Avoid separate `git config` + `git add` + `git commit` steps**: Use `git_commit` which handles all in one call
38. **Avoid `git status` before every commit**: `git_commit` shows status automatically before staging
39. **Avoid `git add -A && git commit -m "..."`**: Use `git_commit "message" <dir>` which handles auto-config and staging
40. **Avoid `git status` then `git diff --stat` then `git diff` in separate steps**: Use `git_diff [directory]` which combines status and diff in one call, showing changed files, diff summary, and optionally recent log.
41. **Avoid `git diff --cached` to check staged changes separately from status**: Use `git_diff [directory] --cached` which shows staged changes concisely.

#### Directory context management
42. **Avoid `cd /app && <command>` in every step**: Most tools accept a directory parameter; pass the directory instead of cd-ing
43. **Avoid `cd <dir> && <command> && cd ..`**: Use tool directory arguments (e.g., `run_test /app/src`, `run_build /app`)

#### Finding the project root
44. **Avoid `find / -type d -name ".git"` or `find / -type f -name "go.mod"` to find the project**: Use `project_structure <candidate_dir>` instead - it detects git repos (shows ">>> Git repository detected <<<") and lists config files like go.mod, package.json, Cargo.toml in its overview
45. **Avoid iterating over common directories with a for-loop to find the project root**: Check likely directories with `project_structure <dir>` each. Common candidates: `/workspace`, `/app`, `/go/src`, `/home`, `/src`.
46. **Avoid `ls -la /` then `find .` to understand the project**: One `project_structure` call replaces both

#### Controlling output volume
40. **Avoid unlimited build/test output**: Use `--output-limit=<lines>` with `run_build` or `run_test` (default: 200 for build, 150 for tests). Set to 0 for unlimited.
41. **Avoid `| tail -15` after test runs**: `run_test` already limits output by default. Adjust `--output-limit` if needed.
42. **Avoid `| head -50` after build output**: `run_build` already limits output by default. Use `--output-limit=0` for full output.

#### When to use parallel tool calls in one step
43. **Avoid sequential tool calls when they have no dependencies**: Multiple tool calls in one step run in parallel, saving steps.
44. **Avoid `project_structure` then `search_code` in separate steps**: Run them in parallel - they are independent.
45. **Avoid reading one file at a time**: Use parallel `read_file` calls to read multiple files in one step.

#### Quick verification workflow (check if solution already exists)
Before deep exploration, always check if changes already exist:
46. **Check git log first**: `cd /app && git log --oneline -5` to see recent commits relevant to the task.
47. **Check staged/uncommitted changes**: `cd /app && git diff --cached --stat` or `cd /app && git diff --stat`.
48. **If changes exist, verify with tests**: Run `run_test /app` to verify existing changes work.
49. **Submit immediately if done**: If the solution is already in place, just run `echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`.


### When to combine multiple tool calls
- Use `project_structure <directory>` once per subdirectory to understand the codebase (replaces `ls -la` + `find`)
- Use `project_structure <directory> <extension>` to find specific file types (replaces `find ... -name "*.rs"`)
- Use `search_code <pattern> <directory> <extension>` to find relevant code sections (replaces `grep -rn` + `find | xargs grep`)
- Use `search_code <pattern> <directory> <extension> <context>` to see surrounding code context
- Use `read_file <file> <start> <end>` to inspect specific code sections (replaces `nl -ba | sed -n`, `head -n`, `cat`)
- Use `write_file <filepath> <content>` to create a new file or overwrite an existing one (replaces `cat <<'EOF' > file`, `echo > file`, `printf > file`)
- Use `edit_file <filepath> <old_text> <new_text>` to modify existing files by find-and-replace (replaces Python file-editing scripts and fragile `sed` commands)
- Use `run_test <directory> [test_file_or_pattern] [--timeout=<sec>]` to run tests instead of manually crafting test commands
- Use `run_build <directory> [target]` to compile/build the project (replaces `go build`, `go vet`, `npx tsc`, `cargo build`, etc.)
- Use `git_commit <message> <directory>` to stage and commit with auto-config (replaces `git config` + `git add` + `git commit`)
- Use `git_commit <message> <directory> <file1> <file2> ...` to commit only specific files

### How to inspect files/logs efficiently
1. Start with `project_structure <directory>` to get an overview of the project layout (file counts per language, directory tree, config files)
2. Use `project_structure <directory> <extension>` to focus on a specific file type (e.g., `project_structure . ts`)
3. Use `search_code <pattern> <directory> <extension>` to find relevant code locations
4. Use `read_file <file> <start> <end>` to inspect specific sections (shows metadata: size, lines, type)
5. Use `edit_file <filepath> <old_text> <new_text>` to modify files when changes are needed
6. Use `run_build <directory>` to verify the project compiles after changes
7. Use `run_test <directory> [test_pattern] [--timeout=<sec>]` to validate changes
8. Use `write_file <filepath> <content>` to create/modify files with a single call
9. Use `git_commit <message> <directory>` to commit final changes

### When to use each evolved tool
- **project_structure**: First step when entering a new codebase or subdirectory; use with extension filter to focus on specific file types; shows directory tree, file counts, and config files
- **read_file**: For reading any file with optional line ranges; supports stdin via `--`; shows file metadata (size, line count, type); detects binary files; replaces `cat`, `nl -ba`, `sed -n`, `head`
- **search_code**: For finding patterns across the codebase; supports file extension filter, context lines, and `--files-only` mode; auto-excludes noise dirs; also supports single-file search (pass a file path instead of directory); replaces `grep -rn`, `find | xargs grep`, `grep -n "pattern" /path/to/file`
- **write_file**: For creating or overwriting files; accepts content as argument or from stdin; auto-creates parent directories; shows file size/line summary and preview; replaces `cat <<'EOF' > file`, `echo > file`, `printf > file`
- **edit_file**: For modifying existing files by find-and-replace; use when you need to change specific text in a file (like a method implementation, variable name, or import statement); replaces Python-based file editing scripts and fragile `sed` substitutions; use `--all` flag to replace all occurrences instead of just the first; shows a diff summary
- **run_test**: For running project tests; auto-detects Go, Node/TypeScript (jest, vitest), Python (pytest), Rust, Java/Maven, Gradle; supports running specific test files; has built-in timeout (default 120s); use `--timeout=0` for no timeout; use `--output-limit=<lines>` to control output (default 150); use `--tags=<tags>` for Go build tags (e.g., `--tags=kqueue,dev`); use `--env=<key=val,...>` for environment variables (e.g., `--env=CGO_ENABLED=0,DEBUG=1`)
- **run_build**: For compiling/building projects; auto-detects Go, Node/TypeScript, Rust, Python, Java/Maven, Gradle, Makefile; has built-in timeout (default 120s); use `--timeout=0` for no timeout; use `--output-limit=<lines>` to control output (default 200); use `--tags=<tags>` for Go build tags (e.g., `--tags=kqueue,dev`); use `--env=<key=val,...>` for environment variables (e.g., `--env=CGO_ENABLED=0,DEBUG=1`); replaces `go build ./...`, `npx tsc`, `cargo build`, `mvn compile`, `make`, etc.
- **git_commit**: For staging and committing changes; shows git status before staging; auto-configures git if needed; supports committing specific files; replaces `git status` + `git config` + `git add` + `git commit`
- **git_diff**: For checking project status and diff before committing or reviewing changes; shows git status, diff stat, and file changes in one call; supports --stat-only, --name-only, --cached flags; use after editing files to verify changes are correct; replaces `git status` + `git diff` + `git diff --stat`

### How to avoid common costly mistakes
1. **Don't read the same file twice** (once with `cat`, once with `cat -n`): `read_file` shows line numbers by default
2. **Don't create Python scripts for file editing**: `edit_file` handles find-and-replace with exact text matching
3. **Don't run tests without a timeout**: Some test suites may hang; use `run_test` which has a default 120s timeout
4. **Don't run builds without a timeout**: Use `run_build` which has a default 120s timeout
5. **Don't use `cd` in every step**: Pass the directory to the tool as an argument instead
6. **Don't explore one directory at a time**: Use `project_structure` for a comprehensive view
7. **Don't search across all files without an extension filter**: Narrow down with file extension to reduce output

### Typical cost-optimized workflow (example: TypeScript project - adding a new function)
```
# Step 1: Understand project structure (1 call instead of ls + find)
project_structure . ts

# Step 2: Read existing similar files to understand the pattern (1 call per file)
read_file ./src/functions/color.ts 1 50
read_file ./src/environments/array.ts 89 300

# Step 3: Find related code patterns (1 call instead of find | xargs grep)
search_code "htmlBuilder|mathmlBuilder" ./src ts

# Step 4: Create new files (1 call each instead of cat <<'EOF')
write_file ./src/functions/multicolumn.ts "import defineFunction from '../defineFunction';
import buildCommon from '../buildCommon';
import { MathNode } from '../mathMLTree';

defineFunction({
    type: 'multicolumn',
    names: ['\\\\multicolumn'],
    props: { numArgs: 3, allowedInText: false },
    handler: ({ parser, args }) => {
        const cols = parser.parseStringGroup('r|l|c');
        const body = parser.parseMath();
        return {
            type: 'multicolumn',
            mode: parser.mode,
            cols,
            body: parser.parseMath(),
        };
    },
    htmlBuilder: (group, options) => {
        // ... implementation
    },
    mathmlBuilder: (group, options) => {
        // ... implementation
    },
});"

# Step 5: Edit existing files to register the new module (1 call instead of sed -i)
edit_file ./src/functions.ts "import \"./functions/color\";" "import \"./functions/color\";\nimport \"./functions/multicolumn\";"

# Step 6: Build to check for compilation errors (1 call instead of npx tsc)
run_build .

# Step 7: Run tests related to the change (1 call instead of npx jest)
run_test . multicolumn --timeout=60

# Step 8: Run existing tests to verify no regressions (1 call instead of npx jest)
run_test . katex-spec.ts --timeout=120

# Step 9: Commit all changes (1 call instead of git config + add + commit)
git_commit "feat: add multicolumn support" . src/functions/multicolumn.ts src/functions.ts
```

### Typical cost-optimized workflow (example: Go project - adding a new API field)
```
# Step 1: Understand structure (1 tool call instead of find | head)
project_structure . go

# Step 2: Read the relevant type file (1 call instead of cat + wc -l)
read_file ./api/v1alpha1/kgateway/traffic_policy_types.go 1 50

# Step 3: Search for similar patterns to understand the codebase conventions (1 call instead of find | xargs grep)
search_code "type.*Policy" ./api/v1alpha1/kgateway go

# Step 4: Edit the API types file to add new field (1 call instead of sed -i)
edit_file ./api/v1alpha1/kgateway/traffic_policy_types.go \
  "OAuth2 *OAuth2Policy \`json:\"oauth2,omitempty\"\`" \
  "OAuth2 *OAuth2Policy \`json:\"oauth2,omitempty\"\`\n\tConsistentHash *ConsistentHash \`json:\"consistentHash,omitempty\"\`"

# Step 5: Create new implementation files (1 call each instead of cat <<'EOF')
write_file ./pkg/kgateway/extensions2/plugins/trafficpolicy/consistent_hash.go "package trafficpolicy

type consistentHashIR struct {
    // ... implementation
}"

# Step 6: Build to check for compilation errors (1 call instead of go build ./...)
run_build /app

# Step 7: Run tests (1 call instead of go test ./...)
run_test /app/pkg/kgateway/extensions2/plugins/trafficpolicy/

# Step 8: Commit all changes (1 call instead of git config + add + commit)
git_commit "Add spec.consistentHash to TrafficPolicy" /app
```

### Typical cost-optimized workflow (example: Go project - adding a test function and running with build tags)
```
# Step 1: Find the test file to modify
search_code "func TestToFileLinePathParse" . go

# Step 2: Read the surrounding context to find where to insert
read_file ./pkg/gitparse/gitparse_test.go 725 730

# Step 3: Insert a new test function using edit_file (1 call instead of head + cat + patch)
edit_file ./pkg/gitparse/gitparse_test.go \
  "func TestToFileLinePathParse" \
  "func TestBinaryFileLinePathParse(t *testing.T) {\n\tcases := map[string]struct {\n\t\tpath string\n\t\tok   bool\n\t}{\n\t\t\"Binary files /dev/null and b/file differ\\\\n\": {path: \"file\", ok: true},\n\t}\n\tfor name, tc := range cases {\n\t\tt.Run(name, func(t *testing.T) {\n\t\t\t// test logic\n\t\t})\n\t}\n}\n\nfunc TestToFileLinePathParse"

# Step 4: Build the specific package to check compilation (1 call instead of go build ./pkg/gitparse/)
run_build /app ./pkg/gitparse/

# Step 5: Run the new test with build tags (1 call instead of go test -v -timeout 30s -tags kqueue,dev -run "^TestBinary" ./pkg/gitparse/)
run_test /app "TestBinaryFileLinePathParse" --tags=kqueue,dev --timeout=30

# Step 6: Run all tests in the package to verify no regressions (1 call instead of go test -v -timeout 90s -tags kqueue,dev ./pkg/gitparse/)
run_test /app ./pkg/gitparse/... --tags=kqueue,dev --timeout=90

# Step 7: Commit changes (1 call instead of git config + add + commit)
git_commit "Add TestBinaryFileLinePathParse" /app
```

### Typical cost-optimized workflow (example: Rust project with boa engine)
```
# Step 1: Understand structure (1 tool call instead of ls + find + find)
project_structure . rs

# Step 2: Find relevant code sections (1 tool call instead of find | xargs grep)
search_code "Context" ./core rs

# Step 3: Read specific sections (1 tool call instead of cat entire file)
read_file ./core/engine/src/context/mod.rs 150 350

# Step 4: Search for public API (1 tool call instead of grep -n)
search_code "pub fn" ./core/engine/src/module rs

# Step 5: Build to verify compilation (1 tool call instead of cargo build)
run_build .

# Step 6: Run tests (1 tool call instead of cargo test)
run_test .

# Step 7: Commit changes (1 tool call instead of git config + add + commit)
git_commit "feat: implement feature" .
```

### Typical cost-optimized workflow (example: Python project editing existing code)
```
# Step 1: Understand structure
project_structure . py

# Step 2: Find the code to modify
search_code "def _session_bundle_status" . py

# Step 3: Read the full method
read_file ./IPython/core/magics/session.py 150 180

# Step 4: Edit the method (1 call instead of Python file-editing script)
edit_file ./IPython/core/magics/session.py \
  "        if status[\"recording\"]:\n            print(f'Recording session bundle to: {status[\"path\"]}')\n        else:\n            print(\"No active session bundle recording.\")" \
  "        import json\n        print(json.dumps(status))"

# Step 5: Run tests (1 call instead of manually crafting pytest command)
run_test . test_magic

# Step 6: Commit changes
### Typical cost-optimized workflow (example: Python project adding a new module)
```
# Step 1: Understand structure
project_structure . py

# Step 2: Find the file to modify (e.g., __init__.py for exports)
search_code "def with_config" langchain_core/runnables py

# Step 3: Read the existing code to understand the pattern
read_file langchain_core/runnables/base.py 1644 1668

# Step 4: Create the new module (1 call instead of cat << EOF)
write_file langchain_core/runnables/new_module.py """New module docstring."""
from __future__ import annotations

from typing import Any

__all__ = ["NewClass"]

class NewClass:
    pass
"""

# Step 5: Edit __init__.py to add exports at 3 locations in a single call
#        (instead of 3 separate Python scripts)
cat > /tmp/old_import.txt << 'EOF'
from langchain_core.runnables.utils import (
EOF
cat > /tmp/new_import.txt << 'EOF'
from langchain_core.runnables.new_module import (
    NewClass,
)
from langchain_core.runnables.utils import (
EOF
edit_file langchain_core/runnables/__init__.py \
  --old /tmp/old_import.txt --new /tmp/new_import.txt \
  --old '"RunnableLambda",' '"RunnableLambda",\n    "NewClass",' \
  --old '"run_in_executor": "config",' '"run_in_executor": "config",\n    "NewClass": "new_module",'

# Step 6: Run tests to verify (1 call instead of crafting pytest command)
run_test . test_new_module

# Step 7: Commit all changes (1 call instead of git config + add + commit)
git_commit "Add NewClass module" .
```

### write_file usage examples
```
# Create a file with content as argument
write_file ./src/new-file.ts "export const hello = 'world'"

# Create a file by piping content (for large content, use heredoc)
cat <<'EOF' | write_file ./src/new-file.ts
import { Something } from './something'
export function greet(name: string): string {
  return `Hello, ${name}!`
}
EOF

# Overwrite an existing file
write_file ./src/config.ts "export const DEBUG = true"

# Create file in a new directory (parent dirs auto-created)
write_file ./src/new-module/index.ts "export * from './types.js'"
```

### edit_file usage examples
```
# Replace a simple string (first occurrence)
edit_file ./src/main.py "def old_name():" "def new_name():"

# Replace a multi-line code block (use --old <file> and --new <file>)
edit_file ./src/main.py --old old_code.txt --new new_code.txt

# Replace a method implementation (using exact string match)
edit_file ./src/module.ts \
  "function greet(name: string) {\n  return 'Hello ' + name;\n}" \
  "function greet(name: string) {\n  return \`Hello, \${name}!\`;\n}"

# Replace ALL occurrences of a pattern
edit_file ./src/utils.py "deprecated_func" "new_func" --all

# Make multiple replacements in a single call (--old/--new file pairs)
# This replaces 3 different patterns in __init__.py in one tool call
edit_file ./src/__init__.py \
  --old /tmp/old_import.txt --new /tmp/new_import.txt \
  --old '"RunnableLambda",' '"RunnableLambda",\n    "NewClass",' \
  --old '"run_in_executor": "config",' '"run_in_executor": "config",\n    "NewClass": "new_module",'

# Make multiple simple replacements in one call
edit_file ./src/config.py \
  "DEBUG = False" "DEBUG = True" \
  "LOG_LEVEL = \"INFO\"" "LOG_LEVEL = \"DEBUG\""
```

### run_build usage examples
```
# Build a Go project
run_build /app

# Build specific Go package
run_build /app ./api/v1alpha1/kgateway/

# Build a TypeScript project
run_build . 

# Build with custom timeout
TIMEOUT=60 run_build /app

# Build with output limit (default: 200 lines)
run_build /app --output-limit=50

# Build with unlimited output
run_build /app --output-limit=0

# Build with Go build tags
run_build /app --tags=kqueue,dev

# Build with environment variables (comma-separated KEY=VAL)
run_build /app --env=CGO_ENABLED=0,MINIO_API_REQUESTS_MAX=10000

# Build with both tags and env vars
run_build /app --tags=kqueue,dev --env=CGO_ENABLED=0
```

### run_test usage examples
```
# Run all tests in a Go project
run_test /app

# Run tests with specific pattern
run_test /app "TestConsistentHash"

# Run a specific test file
run_test . test/multicolumn-spec.ts

# Run a specific Go test file (auto-detects test functions and runs in the correct package)
run_test /app pkg/gitparse/gitparse_test.go

# Run a specific Go test file with build tags
run_test /app pkg/gitparse/gitparse_test.go --tags=kqueue,dev --timeout=30
# Run all tests with 60-second timeout
run_test /app --timeout=60

# Run tests with no timeout
run_test /app --timeout=0

# Run Python tests
run_test . test_api.py

# Run tests with custom output limit
run_test /app --output-limit=300

# Run tests with unlimited output (show all output)
run_test /app --output-limit=0

# Run Go tests with build tags
run_test /app --tags=kqueue,dev

# Run Go tests with environment variables
run_test /app --env=CGO_ENABLED=0,MINIO_API_REQUESTS_MAX=10000

# Run specific Go package with build tags and env vars
run_test /app ./js/modules/k6/timers/... --tags=kqueue,dev --env=CGO_ENABLED=0

# Run tests with both tags and env
run_test /app "TestSetTimeout" --tags=kqueue,dev --env=CGO_ENABLED=0
```

### git_diff usage examples
```
# Show git status + diff summary for current directory
git_diff

# Show git status + diff summary for a specific repo
git_diff /workspace

# Quick check of which files changed (no full diff)
git_diff . --stat-only

# Show only changed file names (useful before commit to verify scope)
git_diff . --name-only

# Show staged changes (after git add, before commit)
git_diff . --cached

# Show status, diff, and last 5 commits for context
git_diff . --log=5

# Typical workflow: check changes before committing
git_diff . --stat-only
# Then commit
git_commit "fix: resolve issue" .
```
