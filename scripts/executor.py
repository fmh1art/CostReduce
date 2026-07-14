"""Evolved tool executor (v6).

The evolve agent rewrites this file. ``run_tool`` dispatches by ``action["tool"]``
and returns ``{"output", "returncode", "exception_info"}`` (same shape as
``env.execute`` so the default observation template render it unchanged).

Use stdlib only (subprocess / os / json / re / ...). Keep tools minimal & robust.
"""
import os
import subprocess


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


def run_tool(action, cwd=None, timeout=120):
    """Dispatch one evolved-tool call. Override / extend the branches below."""
    name = action.get("tool")
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
                # Django: use its native runner with dotted labels, from the
                # directory that contains runtests.py.
                rt_dir = os.path.dirname(runtests)
                cmd = ["python", os.path.basename(runtests)]
                if tests:
                    cmd += tests.split()
                if extra:
                    cmd += extra.split()
                r = subprocess.run(cmd, cwd=rt_dir, capture_output=True,
                                   text=True, timeout=timeout)
            else:
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
    return {
        "output": f"executor has no branch for tool {name!r} yet \u2014 add it.",
        "returncode": 1,
        "exception_info": f"unhandled tool {name!r}",
    }
