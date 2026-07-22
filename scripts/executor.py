"""Evolved tool executor (v6.1).

The evolve agent rewrites this file. ``run_tool`` dispatches by ``action["tool"]``
and returns ``{"output", "returncode", "exception_info"}`` (same shape as
``env.execute`` so the default observation template renders it unchanged).

Use stdlib only (subprocess / os / json / re / ...). Keep tools minimal & robust.
"""
import json
import os
import subprocess
import fnmatch
import glob
import re

_MAX_OBSERVATION_CHARS = 3000

_SKIP_DIRS = {'.git', 'node_modules', '__pycache__', 'venv', '.venv',
              'dist', 'build', '.tox', '.eggs', '*.egg-info'}


def _should_skip_dir(dirname):
    """Check if a directory should be excluded from traversal."""
    if dirname in _SKIP_DIRS:
        return True
    for pat in _SKIP_DIRS:
        if '*' in pat and fnmatch.fnmatch(dirname, pat):
            return True
    return False


def _truncate_output(output, max_chars=_MAX_OBSERVATION_CHARS):
    """Truncate output to max_chars, appending a truncation notice."""
    if len(output) <= max_chars:
        return output
    return output[:max_chars] + f"\n... (output truncated from {len(output)} to {max_chars} chars)"


def _find_django_runtests(start):
    """Return the path to Django's ``runtests.py`` if ``start`` looks like a
    Django checkout, else ``None``. Django's own test-suite is driven by
    ``tests/runtests.py`` (not pytest), so detecting it avoids collection errors."""
    start = os.path.abspath(start or ".")
    for cand in (os.path.join(start, "tests", "runtests.py"),
                 os.path.join(start, "runtests.py")):
        if os.path.isfile(cand):
            return cand
    return None


def _find_go_project(start):
    """Return the path to ``go.mod`` if ``start`` is a Go project, else ``None``."""
    start = os.path.abspath(start or ".")
    cand = os.path.join(start, "go.mod")
    if os.path.isfile(cand):
        return cand
    return None


def _check_pytest_available():
    """Check if pytest is importable without running a subprocess."""
    try:
        import importlib
        return importlib.util.find_spec("pytest") is not None
    except Exception:
        return False



def _find_node_project(start):
    """Walk up from *start* looking for ``package.json``. Returns the directory or None."""
    path = os.path.abspath(start or ".")
    for _ in range(20):
        if os.path.isfile(os.path.join(path, "package.json")):
            return path
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    return None


def _detect_node_runner(project_dir):
    """Return (cmd_prefix, runner_args) for a Node.js test runner, or None.

    Checks package.json scripts.test and devDependencies/dependencies for
    jest, vitest, or mocha, then falls back to config files on disk.
    """
    pkg_path = os.path.join(project_dir, "package.json")
    try:
        with open(pkg_path, "r") as f:
            pkg = json.load(f)
    except Exception:
        return None

    scripts = pkg.get("scripts", {}) or {}
    test_script = (scripts.get("test") or "").lower()

    deps = {}
    deps.update(pkg.get("devDependencies", {}) or {})
    deps.update(pkg.get("dependencies", {}) or {})

    # Jest
    if "jest" in test_script or "jest" in deps:
        return (["npx", "jest"], "jest")
    # Vitest
    if "vitest" in test_script or "vitest" in deps:
        return (["npx", "vitest", "run"], "vitest")
    # Mocha
    if "mocha" in test_script or "mocha" in deps:
        return (["npx", "mocha"], "mocha")

    # Config files on disk
    try:
        for fn in os.listdir(project_dir):
            if fn.startswith("jest.config"):
                return (["npx", "jest"], "jest")
            if fn.startswith("vitest.config"):
                return (["npx", "vitest", "run"], "vitest")
    except OSError:
        pass

    # Fallback: if package.json has any test script, use npm test
    if scripts.get("test"):
        return (["npm", "test", "--"], "npm-test")

    return None

def _read_single_file(filepath, head, tail, lines, start_line, end_line, number):
    """Read a single file and return (output, error_message).

    Supports:
    - head: int, first N lines (use -1 or 0 for all lines)
    - tail: int, last N lines
    - lines: str, 'start-end' format (1-indexed inclusive)
    - start_line/end_line: int, alternative to lines string
    - number: bool, prefix lines with line numbers

    Returns (content, None) on success or (None, error_message) on failure.
    """
    if not os.path.isfile(filepath):
        return None, f"file not found: {filepath}"

    try:
        with open(filepath, 'r') as f:
            all_lines = f.readlines()
    except (IOError, OSError) as e:
        return None, f"cannot read {filepath}: {e}"

    total_lines = len(all_lines)

    # Determine which lines to select
    if lines:
        # Parse 'start-end' string format
        parts = lines.split('-')
        if len(parts) != 2:
            return None, f"invalid lines format '{lines}' — use 'start-end' (e.g. '100-130')"
        try:
            s = int(parts[0])
            e = int(parts[1])
        except ValueError:
            return None, f"invalid lines format '{lines}' — start and end must be integers"
        if s < 1:
            s = 1
        if e > total_lines:
            e = total_lines
        selected = all_lines[s - 1:e]
    elif head is not None:
        if head <= 0:
            selected = all_lines[:]      # -1 or 0 means read all lines
        else:
            selected = all_lines[:head]
    elif tail is not None and tail >= 0:
        t = min(tail, total_lines)
        selected = all_lines[-t:] if t > 0 else []
    elif start_line is not None:
        s = max(1, start_line) - 1
        e = end_line if end_line is not None else total_lines
        selected = all_lines[s:e]
    else:
        # Default: first 50 lines
        selected = all_lines[:50]

    # Apply line numbering if requested
    if number:
        # Compute the starting line number
        if lines:
            start_num = int(lines.split('-')[0])
        elif head is not None:
            start_num = 1
        elif tail is not None:
            start_num = max(1, total_lines - tail + 1)
        elif start_line is not None:
            start_num = max(1, start_line)
        else:
            start_num = 1
        width = len(str(total_lines))
        numbered = [f"{start_num + i:>{width}}\t{line}" for i, line in enumerate(selected)]
        content = "".join(numbered)
    else:
        content = "".join(selected)

    if not content.rstrip():
        return "(empty file)", None
    return content, None


def _resolve_single_file_spec(entry, top_level_params):
    """Resolve a single entry from the 'files' array into (filepath, params_dict).

    ``entry`` can be a string (file path only) or a dict with 'file' key
    and optional per-file overrides.  ``top_level_params`` is a dict of the
    shared top-level parameters (head, tail, lines, start, end, number).
    Returns (filepath, merged_params) where merged_params starts from
    top_level_params and is overridden by any keys in the entry dict.

    When a per-file override sets a range parameter, conflicting top-level
    range params are cleared so the override takes full effect.
    """
    if isinstance(entry, str):
        return entry, dict(top_level_params)
    if isinstance(entry, dict):
        filepath = entry.get("file", "")
        merged = dict(top_level_params)
        for key in ("head", "tail", "lines", "start", "end", "number"):
            if key in entry:
                merged[key] = entry[key]
        # Mutually-exclusive range params: if override sets any range param,
        # clear conflicting top-level ones so the override takes effect.
        # Priority order in _read_single_file: lines > head > tail > start/end.
        has_override = set(entry.keys()) & {"lines", "head", "tail", "start", "end"}
        if has_override:
            if "lines" in entry:
                for k in ("head", "tail", "start", "end"):
                    merged.pop(k, None)
            elif "head" in entry:
                for k in ("tail", "lines", "start", "end"):
                    merged.pop(k, None)
            elif "tail" in entry:
                for k in ("head", "lines", "start", "end"):
                    merged.pop(k, None)
            elif "start" in entry or "end" in entry:
                for k in ("head", "tail", "lines"):
                    merged.pop(k, None)
        return filepath, merged
    # Fallback: treat as string
    return str(entry), dict(top_level_params)



def _find_query_db_script(cwd):
    """Search for ``query_db.py`` in *cwd* and its ancestors."""
    path = os.path.abspath(cwd or ".")
    for _ in range(10):
        cand = os.path.join(path, "query_db.py")
        if os.path.isfile(cand):
            return cand
        cand2 = os.path.join(path, "dab", "query_db.py")
        if os.path.isfile(cand2):
            return cand2
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    return None


def _exec_query_db_action(action_type, db_name, sql, table_name, script, cwd, timeout):
    """Execute a single query-db action. Returns (output_str, returncode_int)."""
    if not action_type:
        return "query-db: missing required parameter 'action' (one of: dbs, tables, schema, query)", 1
    if action_type not in ("dbs", "tables", "schema", "query"):
        return f"query-db: unknown action '{action_type}'. Use: dbs, tables, schema, query", 1
    if action_type in ("tables", "schema", "query") and not db_name:
        return f"query-db: action '{action_type}' requires 'database' parameter", 1
    if action_type == "query" and not sql:
        return "query-db: action 'query' requires 'query' parameter (SQL string)", 1
    if action_type == "schema" and not table_name:
        return "query-db: action 'schema' requires 'table' parameter", 1

    if action_type == "dbs":
        if script:
            r = subprocess.run(["python3", script, "dbs"], cwd=cwd,
                               capture_output=True, text=True, timeout=timeout)
            return (r.stdout or "") + (r.stderr or ""), r.returncode
        else:
            return "query-db: query_db.py not found in working directory tree", 1

    elif action_type == "tables":
        if script:
            r = subprocess.run(["python3", script, "tables", db_name], cwd=cwd,
                               capture_output=True, text=True, timeout=timeout)
            return (r.stdout or "") + (r.stderr or ""), r.returncode
        else:
            return "query-db: query_db.py not found in working directory tree", 1

    elif action_type == "schema":
        if script:
            strategies = [
                f"PRAGMA table_info({table_name})",
                f"SELECT column_name, data_type, is_nullable FROM information_schema.columns WHERE table_name = '{table_name}' ORDER BY ordinal_position",
                f"DESCRIBE {table_name}",
                f"PRAGMA table_info('{table_name}')",
            ]
            last_err = ""
            for strat in strategies:
                r = subprocess.run(
                    ["python3", script, "query", db_name, strat],
                    cwd=cwd, capture_output=True, text=True, timeout=timeout)
                if r.returncode == 0 and (r.stdout or "").strip():
                    return r.stdout, 0
                last_err = r.stderr or ""
            return f"query-db: could not determine schema for {db_name}.{table_name}. Last error: {last_err}", 1
        else:
            return "query-db: query_db.py not found in working directory tree", 1

    elif action_type == "query":
        if script:
            r = subprocess.run(
                ["python3", script, "query", db_name, sql],
                cwd=cwd, capture_output=True, text=True, timeout=timeout)
            return (r.stdout or "") + (r.stderr or ""), r.returncode
        else:
            # Fallback: try stdlib sqlite3 for .db/.sqlite files
            import sqlite3 as _sqlite3
            cand_path = os.path.join(cwd or ".", db_name) if not os.path.isabs(db_name) else db_name
            for ext in ("", ".db", ".sqlite", ".sqlite3"):
                try_path = cand_path + ext
                if os.path.isfile(try_path):
                    try:
                        conn = _sqlite3.connect(try_path)
                        cur = conn.execute(sql)
                        cols = [d[0] for d in cur.description] if cur.description else []
                        rows = [dict(zip(cols, row)) for row in cur.fetchall()]
                        conn.close()
                        import json as _json
                        return _json.dumps(rows, default=str), 0
                    except Exception as e:
                        return f"query-db: sqlite3 fallback failed for {try_path}: {e}", 1
            return "query-db: query_db.py not found and no sqlite3 file matched the database name", 1

    return f"query-db: unhandled action '{action_type}'", 1

def run_tool(action, cwd=None, timeout=120):
    """Dispatch one evolved-tool call. Override / extend the branches below."""
    name = action.get("tool")

    # ------------------------------------------------------------------ #
    # run-tests
    # ------------------------------------------------------------------ #
    if name == "run-tests":
        try:
            run_cwd = action.get("cwd") or cwd or "."
            tests = (action.get("tests") or "").strip()
            extra = (action.get("extra_args") or "").strip()

            # Check for Go project first
            go_mod = _find_go_project(run_cwd)
            if go_mod is not None:
                cmd = ["go", "test"]
                if tests:
                    cmd += tests.split()
                else:
                    cmd.append("./...")
                if extra:
                    cmd += extra.split()
                r = subprocess.run(cmd, cwd=run_cwd, capture_output=True,
                                   text=True, timeout=timeout)
                return {"output": (r.stdout or "") + (r.stderr or ""),
                        "returncode": r.returncode, "exception_info": ""}

            # Check for Django test runner
            runtests = _find_django_runtests(run_cwd)
            if runtests is not None:
                rt_dir = os.path.dirname(runtests)
                cmd = ["python", os.path.basename(runtests)]
                if tests:
                    cmd += tests.split()
                if extra:
                    cmd += extra.split()
                r = subprocess.run(cmd, cwd=rt_dir, capture_output=True,
                                   text=True, timeout=timeout)
                return {"output": (r.stdout or "") + (r.stderr or ""),
                        "returncode": r.returncode, "exception_info": ""}

            # Check for Node.js project (package.json)
            node_root = _find_node_project(run_cwd)
            if node_root is not None:
                runner_info = _detect_node_runner(node_root)
                if runner_info is not None:
                    cmd_prefix, _kind = runner_info
                    cmd = list(cmd_prefix)
                    if tests:
                        cmd += tests.split()
                    if extra:
                        cmd += extra.split()
                    r = subprocess.run(cmd, cwd=node_root, capture_output=True,
                                       text=True, timeout=timeout)
                    return {"output": (r.stdout or "") + (r.stderr or ""),
                            "returncode": r.returncode, "exception_info": ""}

            # Check pytest availability before running
            if not _check_pytest_available():
                return {
                    "output": "pytest is not installed. Run 'pip install pytest' first, or use inline Python for verification.",
                    "returncode": 1,
                    "exception_info": "pytest not found"
                }

            # Default: pytest
            cmd = ["python", "-m", "pytest", "-q"]
            if tests:
                cmd += tests.split()
            if extra:
                cmd += extra.split()
            r = subprocess.run(cmd, cwd=run_cwd, capture_output=True,
                               text=True, timeout=timeout)
            return {"output": (r.stdout or "") + (r.stderr or ""),
                    "returncode": r.returncode, "exception_info": ""}
        except Exception as exc:  # noqa: BLE001
            return {"output": f"run-tests failed: {exc}",
                    "returncode": 1, "exception_info": repr(exc)}

    # ------------------------------------------------------------------ #
    # ------------------------------------------------------------------ #
    # query-db
    # ------------------------------------------------------------------ #
    elif name == "query-db":
        try:
            batch = action.get("batch")
            if batch and isinstance(batch, list) and len(batch) > 0:
                script = _find_query_db_script(cwd)
                results = []
                overall_rc = 0
                for i, entry in enumerate(batch):
                    if not isinstance(entry, dict):
                        results.append(f"[batch {i}] invalid entry (not an object)")
                        overall_rc = 1
                        continue
                    sub_action = (entry.get("action") or "").strip()
                    sub_db = (entry.get("database") or "").strip()
                    sub_sql = (entry.get("query") or "").strip()
                    sub_table = (entry.get("table") or "").strip()
                    out, rc = _exec_query_db_action(sub_action, sub_db, sub_sql, sub_table, script, cwd, timeout)
                    label = f"[{sub_action}"
                    if sub_db:
                        label += f" {sub_db}"
                    if sub_table:
                        label += f" {sub_table}"
                    label += "]"
                    results.append(f"{label}\n{out}")
                    if rc != 0:
                        overall_rc = 1
                return {"output": "\n\n".join(results),
                        "returncode": overall_rc, "exception_info": ""}
            else:
                action_type = (action.get("action") or "").strip()
                db_name = (action.get("database") or "").strip()
                sql = (action.get("query") or "").strip()
                table_name = (action.get("table") or "").strip()
                script = _find_query_db_script(cwd)
                out, rc = _exec_query_db_action(action_type, db_name, sql, table_name, script, cwd, timeout)
                return {"output": out, "returncode": rc, "exception_info": ""}
        except Exception as exc:
            return {"output": f"query-db failed: {exc}",
                    "returncode": 1, "exception_info": repr(exc)}

    # read-lines
    # ------------------------------------------------------------------ #
    elif name == "read-lines":
        try:
            # Collect file paths: 'files' array or single 'file' string
            files_param = action.get("files")
            file_param = action.get("file")
            sections_param = action.get("sections")

            if files_param and isinstance(files_param, list):
                entries = files_param
            elif file_param:
                entries = [file_param]
            else:
                return {"output": "read-lines: provide 'file' (string) or 'files' (array of strings/objects)",
                        "returncode": 1, "exception_info": "missing file/files"}

            # Expand sections: if 'sections' is provided with a single 'file',
            # convert each section to a (file, {"lines": section}) entry.
            # sections overrides top-level range params for that file.
            if (sections_param and isinstance(sections_param, list)
                    and len(sections_param) > 0 and file_param
                    and not files_param):
                entries = []
                for sec in sections_param:
                    if isinstance(sec, str) and sec.strip():
                        entries.append({"file": file_param, "lines": sec.strip()})

            # Top-level shared parameters
            top_params = {}
            for key in ("head", "tail", "lines", "start", "end", "number"):
                val = action.get(key)
                if val is not None:
                    top_params[key] = val

            # Expand glob patterns first, then build the final entry list
            import glob as _glob
            expanded_entries = []
            for entry in entries:
                fpath, params = _resolve_single_file_spec(entry, top_params)
                if not fpath or not isinstance(fpath, str):
                    expanded_entries.append((fpath, params, True))  # error entry
                    continue
                # Check for glob characters
                if any(c in fpath for c in '*?['):
                    resolved_glob = os.path.join(cwd or ".", fpath) if not os.path.isabs(fpath) else fpath
                    matches = sorted(_glob.glob(resolved_glob))
                    if not matches:
                        expanded_entries.append((fpath, params, True))  # will report error
                    else:
                        for m in matches:
                            expanded_entries.append((m, dict(params), False))
                else:
                    expanded_entries.append((fpath, params, False))

            results = []
            had_errors = False
            multi = len(expanded_entries) > 1
            for fpath, params, is_error in expanded_entries:
                if is_error:
                    results.append(f"=== {fpath} ===\n<skipped: invalid path or no glob matches>")
                    had_errors = True
                    continue
                # Resolve relative paths against cwd
                resolved = os.path.join(cwd or ".", fpath) if not os.path.isabs(fpath) else fpath
                file_content, err = _read_single_file(
                    resolved,
                    params.get("head"),
                    params.get("tail"),
                    params.get("lines"),
                    params.get("start"),
                    params.get("end"),
                    params.get("number", False),
                )
                if err:
                    results.append(f"=== {fpath} ===\n{err}")
                    had_errors = True
                elif multi:
                    results.append(f"=== {resolved} ===\n{file_content}")
                else:
                    results.append(file_content)

            output = "\n\n".join(results)
            rc = 1 if had_errors else 0
            if output.rstrip():
                return {"output": _truncate_output(output), "returncode": rc, "exception_info": ""}
            else:
                return {"output": "(empty)", "returncode": rc, "exception_info": ""}
        except Exception as exc:
            return {"output": f"read-lines failed: {exc}",
                    "returncode": 1, "exception_info": repr(exc)}

    # ------------------------------------------------------------------ #
    # write-file
    # ------------------------------------------------------------------ #
    elif name == "write-file":
        try:
            filepath = action.get("file")
            file_content = action.get("content")
            if not filepath:
                return {"output": "missing required parameter: file",
                        "returncode": 1, "exception_info": "missing file"}
            if file_content is None:
                return {"output": "missing required parameter: content",
                        "returncode": 1, "exception_info": "missing content"}
            if cwd and not os.path.isabs(filepath):
                filepath = os.path.join(cwd, filepath)
            # Create parent directories if needed
            parent = os.path.dirname(filepath)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(filepath, 'w') as f:
                f.write(file_content)
            return {"output": f"Wrote {len(file_content)} bytes to {filepath}",
                    "returncode": 0, "exception_info": ""}
        except Exception as exc:
            return {"output": f"write-file failed: {exc}",
                    "returncode": 1, "exception_info": repr(exc)}

    # ------------------------------------------------------------------ #
    # search-code
    # ------------------------------------------------------------------ #
    elif name == "search-code":
        try:
            pattern = action.get("pattern", "")
            if not pattern:
                return {"output": "missing required parameter: pattern",
                        "returncode": 1, "exception_info": "missing pattern"}
            root = action.get("path") or cwd or "."
            if cwd and not os.path.isabs(root):
                root = os.path.join(cwd, root)
            if not os.path.isdir(root):
                return {"output": f"directory not found: {root}",
                        "returncode": 1, "exception_info": f"no such directory: {root}"}
            include = action.get("include", "")
            max_results = action.get("max_results", 50)
            files_with_matches = action.get("files_with_matches", False)

            try:
                regex = re.compile(pattern)
            except re.error as e:
                return {"output": f"invalid regex pattern: {e}",
                        "returncode": 1, "exception_info": str(e)}

            matches = []
            matched_files = set()

            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if not _should_skip_dir(d)]
                for f in filenames:
                    filepath = os.path.join(dirpath, f)
                    if include:
                        relpath = os.path.relpath(filepath, root)
                        if not fnmatch.fnmatch(relpath, include) and not fnmatch.fnmatch(f, include):
                            continue
                    try:
                        with open(filepath, 'r', errors='replace') as fh:
                            for line_no, line in enumerate(fh, 1):
                                if regex.search(line):
                                    relpath = os.path.relpath(filepath, root)
                                    if files_with_matches:
                                        matched_files.add(relpath)
                                    else:
                                        matches.append(f"{relpath}:{line_no}:{line.rstrip()}")
                                        if max_results > 0 and len(matches) >= max_results:
                                            break
                    except (IOError, OSError):
                        pass
                    if not files_with_matches and max_results > 0 and len(matches) >= max_results:
                        break
                if not files_with_matches and max_results > 0 and len(matches) >= max_results:
                    break

            if files_with_matches:
                output = "\n".join(sorted(matched_files)) if matched_files else "(no matches)"
            else:
                output = "\n".join(matches) if matches else "(no matches)"
            return {"output": _truncate_output(output), "returncode": 0, "exception_info": ""}
        except Exception as exc:
            return {"output": f"search-code failed: {exc}",
                    "returncode": 1, "exception_info": repr(exc)}

    # ------------------------------------------------------------------ #
    # find-files
    # ------------------------------------------------------------------ #
    elif name == "find-files":
        try:
            pattern = action.get("pattern", "*")
            if not pattern:
                pattern = "*"
            root = action.get("path") or cwd or "."
            if cwd and not os.path.isabs(root):
                root = os.path.join(cwd, root)
            if not os.path.isdir(root):
                return {"output": f"directory not found: {root}",
                        "returncode": 1, "exception_info": f"no such directory: {root}"}
            search_type = action.get("type", "files")
            max_results = action.get("max_results", 50)
            max_depth = action.get("max_depth")

            # Normalize type aliases
            type_map = {"f": "files", "d": "dirs", "files": "files", "dirs": "dirs", "both": "both"}
            search_type = type_map.get(search_type, "files")

            matches = []
            root_abs = os.path.abspath(root)
            # os.walk with depth control: track depth by counting os.sep in relpath
            for dirpath, dirnames, filenames in os.walk(root_abs):
                dirnames[:] = [d for d in dirnames if not _should_skip_dir(d)]
                rel_dir = os.path.relpath(dirpath, root_abs)
                depth = 0 if rel_dir == "." else rel_dir.count(os.sep) + 1
                # Save dirnames before potentially clearing for depth control
                current_dirs = list(dirnames)
                current_files = list(filenames)
                # When we hit max_depth, process current level but stop recursion
                if max_depth is not None and depth >= max_depth:
                    dirnames.clear()
                if max_depth is not None and depth > max_depth:
                    continue

                if search_type in ("dirs", "both"):
                    for d in current_dirs:
                        fullpath = os.path.join(dirpath, d)
                        relpath = os.path.relpath(fullpath, root_abs)
                        if fnmatch.fnmatch(relpath, pattern) or fnmatch.fnmatch(d, pattern):
                            matches.append(fullpath + "/")
                if search_type in ("files", "both"):
                    for f in current_files:
                        fullpath = os.path.join(dirpath, f)
                        relpath = os.path.relpath(fullpath, root_abs)
                        if fnmatch.fnmatch(relpath, pattern) or fnmatch.fnmatch(f, pattern):
                            matches.append(fullpath)

            matches.sort()
            n = len(matches)
            if max_results is not None and max_results > 0 and n > max_results:
                matches = matches[:max_results]
                output = "\n".join(matches) + f"\n... ({n - max_results} more matches truncated)"
            else:
                output = "\n".join(matches) if matches else "(no matches)"
            return {"output": _truncate_output(output), "returncode": 0, "exception_info": ""}
        except Exception as exc:
            return {"output": f"find-files failed: {exc}",
                    "returncode": 1, "exception_info": repr(exc)}

    # ------------------------------------------------------------------ #
    # edit-file
    # ------------------------------------------------------------------ #
    elif name == "edit-file":
        try:
            filepath = action.get("file")
            old_string = action.get("old_string")
            new_string = action.get("new_string")

            if not filepath or old_string is None or new_string is None:
                missing = []
                if not filepath:
                    missing.append("file")
                if old_string is None:
                    missing.append("old_string")
                if new_string is None:
                    missing.append("new_string")
                return {"output": f"missing required parameter(s): {', '.join(missing)}",
                        "returncode": 1, "exception_info": "missing parameters"}

            if cwd:
                filepath = os.path.join(cwd, filepath) if not os.path.isabs(filepath) else filepath

            if not os.path.isfile(filepath):
                return {"output": f"file not found: {filepath}",
                        "returncode": 1, "exception_info": "file not found"}

            with open(filepath, 'r') as f:
                content = f.read()

            if old_string not in content:
                return {"output": f"old_string not found in {filepath}",
                        "returncode": 1, "exception_info": "string not found"}

            new_content = content.replace(old_string, new_string, 1)

            with open(filepath, 'w') as f:
                f.write(new_content)

            return {"output": f"Replaced 1 occurrence in {filepath}",
                    "returncode": 0, "exception_info": ""}
        except Exception as exc:
            return {"output": f"edit-file failed: {exc}",
                    "returncode": 1, "exception_info": repr(exc)}

    # ------------------------------------------------------------------ #
    # run-python
    # ------------------------------------------------------------------ #
    elif name == "run-python":
        try:
            code = action.get("code", "")
            if not code:
                return {"output": "no code provided",
                        "returncode": 1, "exception_info": "missing required parameter: code"}

            # Write code to a temp file for robust execution
            import tempfile
            tmp_fd, tmp_path = tempfile.mkstemp(suffix=".py", prefix="run_python_")
            try:
                with os.fdopen(tmp_fd, "w") as f:
                    f.write(code)

                import sys
                cmd = [sys.executable, tmp_path]
                args = action.get("args", "").strip()
                if args:
                    cmd += args.split()

                run_cwd = action.get("cwd") or cwd
                r = subprocess.run(cmd, cwd=run_cwd, capture_output=True,
                                   text=True, timeout=timeout)
                return {"output": _truncate_output((r.stdout or "") + (r.stderr or "")),
                        "returncode": r.returncode, "exception_info": ""}
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
        except Exception as exc:
            return {"output": f"run-python failed: {exc}",
                    "returncode": 1, "exception_info": repr(exc)}

    return {
        "output": f"executor has no branch for tool {name!r} yet — add it.",
        "returncode": 1,
        "exception_info": f"unhandled tool {name!r}",
    }
