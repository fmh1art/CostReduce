"""v6: the evolve agent writes the registration files directly (no converter).

In v5 the evolve agent edits ``main.sh``/``intro.json`` and a converter
(:mod:`src.evolve.native_tools`) builds a manifest + bash dispatch. In v6 the
evolve agent writes the **actual tool-registration files** that mini-swe-agent
loads:

* ``tools.json``  — the registry (function-tool schemas).
* ``executor.py`` — the ``run_tool(action, ...)`` Python executor.

So there is nothing to *convert* — those two files ARE the registration. This
module only:

* :func:`seed`        — write initial ``tools.json`` (``[]``) + ``executor.py``
  stub + ``instruction.md`` if absent, so cycle-1 evolve has a starting point.
* :func:`deploy_runtime` — copy the generic ``evolve_tools_v6`` package into
  ``<scripts_dir>/.runtime/`` (bind-mounted + ``PYTHONPATH`` in the rollout
  container; mini-swe-agent there is from PyPI, no ``extra/evolve_tools_v6``).
* :func:`write_config`  — the mini-swe-agent config yaml setting
  ``model_class``/``agent_class`` to the v6 classes (+ ``max_completion_tokens``).
* :func:`validate`      — soft check: tools.json valid JSON, executor.py valid
  Python (``ast.parse``) with a ``run_tool`` callable. Warns, doesn't raise.
* :func:`deploy`        — deploy_runtime + write_config in one shot.

CLI::

    python -m src.evolve.native_tools_v6 seed        --scripts-dir <dir>
    python -m src.evolve.native_tools_v6 deploy       --scripts-dir <dir> --api-type chat|responses
    python -m src.evolve.native_tools_v6 validate     --scripts-dir <dir>
    python -m src.evolve.native_tools_v6 deploy-runtime --scripts-dir <dir>
"""

from __future__ import annotations

import argparse
import ast
import json
import logging
import shutil
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
CANONICAL_RUNTIME = ROOT / "agent" / "mini-swe-agent" / "src" / "minisweagent" / "extra" / "evolve_tools_v6"

RUNTIME_DIRNAME = ".runtime"
CONFIG_NAME = ".evolve_tools_v6_config.yaml"
TOOLS_JSON_NAME = "tools.json"
EXECUTOR_NAME = "executor.py"
INSTRUCTION_NAME = "instruction.md"

# Container import paths (evolve_tools_v6 copied to <scripts>/.runtime/ + PYTHONPATH).
CONTAINER_MODEL_CLASS_CHAT = "evolve_tools_v6.model.LitellmModelWithEvolveToolsV6"
CONTAINER_MODEL_CLASS_RESPONSES = "evolve_tools_v6.model.LitellmResponseModelWithEvolveToolsV6"
CONTAINER_AGENT_CLASS = "evolve_tools_v6.agent.EvolveToolsAgentV6"
# Host import paths (editable install).
HOST_MODEL_CLASS_CHAT = "minisweagent.extra.evolve_tools_v6.model.LitellmModelWithEvolveToolsV6"
HOST_MODEL_CLASS_RESPONSES = "minisweagent.extra.evolve_tools_v6.model.LitellmResponseModelWithEvolveToolsV6"
HOST_AGENT_CLASS = "minisweagent.extra.evolve_tools_v6.agent.EvolveToolsAgentV6"


# === seed ===================================================================


SEED_TOOLS_JSON = '''[
  {
    "name": "run-tests",
    "description": "Run a repo's tests in a given directory, auto-detecting Django (runtests.py) vs pytest.",
    "parameters": {
      "type": "object",
      "properties": {
        "tests": {"type": "string", "description": "Test target(s): pytest path/node-id(s), or for Django a space-separated list of dotted test labels (e.g. 'auth.tests.test_x'). Empty runs the default suite."},
        "cwd": {"type": "string", "description": "Directory to run the tests in (repo root or subdir). Defaults to the agent's current working directory."},
        "extra_args": {"type": "string", "description": "Optional extra CLI args passed through to the underlying runner."}
      },
      "required": []
    }
  }
]
'''

SEED_EXECUTOR_PY = '''"""Evolved tool executor (v6).

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


def run_tool(action, cwd=None, timeout=120):
    """Dispatch one evolved-tool call. Override / extend the branches below."""
    name = action.get("tool")
    if name == "run-tests":
        try:
            run_cwd = action.get("cwd") or cwd or "."
            tests = (action.get("tests") or "").strip()
            extra = (action.get("extra_args") or "").strip()
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
        "output": f"executor has no branch for tool {name!r} yet — add it.",
        "returncode": 1,
        "exception_info": f"unhandled tool {name!r}",
    }
'''

SEED_INSTRUCTION_MD = (
    "# Cost-saving instructions\n\n"
    "## General principles\n"
    "- Batch multiple actions into one step: if multiple tool calls are independent, call them together rather than sequentially.\n"
    "- Prefer one tool call that achieves multiple actions over separate calls for each.\n\n"
    "## When to give up / stop trying\n"
    "- If the same approach fails twice, stop and re-read the original issue before retrying.\n"
    "- If a dependency install fails twice, skip it and work with what is available.\n"
    "- If you have been reading different ranges of the same file across 3+ steps, batch remaining reads into one call.\n\n"
    "## When to exit early\n"
    "- If the issue is clearly infeasible given the current environment (missing deps, wrong Python version, etc.), submit a best-effort fix without full validation.\n"
    "- If tests keep failing for the same root cause after 2 fix attempts, commit the current best-guess fix rather than continuing to iterate.\n\n"
    "## When to take risks / skip validation\n"
    "- If the environment is broken (persistent errors unrelated to your fix) and repeated debug attempts fail, apply the fix and commit without running tests.\n"
    "- If a build/compile step fails repeatedly and is not required for the fix itself, skip it.\n"
    "- Do NOT build/package/install the project just to test a change; run targeted tests directly.\n"
)


def seed(scripts_dir: Path | str) -> dict[str, Path]:
    """Write initial tools.json ([]) + executor.py stub + instruction.md if absent."""
    scripts_dir = Path(scripts_dir)
    scripts_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    for name, content in [
        (TOOLS_JSON_NAME, SEED_TOOLS_JSON),
        (EXECUTOR_NAME, SEED_EXECUTOR_PY),
        (INSTRUCTION_NAME, SEED_INSTRUCTION_MD),
    ]:
        p = scripts_dir / name
        if not p.exists():
            p.write_text(content, encoding="utf-8")
            written[name] = p
            logger.info("seeded %s", p)
    return written


# === runtime deployment =====================================================


def deploy_runtime(scripts_dir: Path | str) -> Path:
    """Copy the canonical evolve_tools_v6 package into ``<scripts_dir>/.runtime/``.

    Idempotent. The rollout container bind-mounts ``<scripts_dir>`` →
    ``/app/.preinstalled_scripts`` and puts ``/app/.preinstalled_scripts/.runtime``
    on PYTHONPATH, so ``import evolve_tools_v6`` resolves to this copy.
    """
    scripts_dir = Path(scripts_dir)
    if not CANONICAL_RUNTIME.is_dir():
        raise FileNotFoundError(
            f"canonical evolve_tools_v6 runtime not found at {CANONICAL_RUNTIME}"
        )
    dst_root = scripts_dir / RUNTIME_DIRNAME
    dst = dst_root / "evolve_tools_v6"
    if dst.exists():
        shutil.rmtree(dst)
    dst_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        CANONICAL_RUNTIME, dst,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )
    (dst_root / ".deployed").write_text(
        f"evolve_tools_v6 runtime deployed from {CANONICAL_RUNTIME}\n", encoding="utf-8"
    )
    logger.info("deployed evolve_tools_v6 runtime -> %s", dst)
    return dst


# === config yaml ============================================================


def config_yaml_text(
    api_type: str = "chat",
    *,
    max_completion_tokens: int | None = None,
    container: bool = True,
) -> str:
    api = (api_type or "chat").strip().lower()
    if container:
        mc = CONTAINER_MODEL_CLASS_RESPONSES if api == "responses" else CONTAINER_MODEL_CLASS_CHAT
        ac = CONTAINER_AGENT_CLASS
    else:
        mc = HOST_MODEL_CLASS_RESPONSES if api == "responses" else HOST_MODEL_CLASS_CHAT
        ac = HOST_AGENT_CLASS
    lines = [
        "# Auto-generated by src/evolve/native_tools_v6.py — wires evolved tools",
        "# (tools.json + executor.py) as native function tools via evolve_tools_v6.",
        "model:", f"  model_class: {mc}",
    ]
    if max_completion_tokens:
        lines += ["  model_kwargs:", f"    max_completion_tokens: {max_completion_tokens}"]
    lines += ["agent:", f"  agent_class: {ac}"]
    return "\n".join(lines) + "\n"


def write_config(
    scripts_dir: Path | str,
    api_type: str = "chat",
    *,
    max_completion_tokens: int | None = None,
    container: bool = True,
    out_path: Path | str | None = None,
) -> Path:
    scripts_dir = Path(scripts_dir)
    scripts_dir.mkdir(parents=True, exist_ok=True)
    out = Path(out_path) if out_path else scripts_dir / CONFIG_NAME
    out.write_text(
        config_yaml_text(api_type, max_completion_tokens=max_completion_tokens, container=container),
        encoding="utf-8",
    )
    logger.info("wrote v6 config -> %s (api_type=%s)", out, api_type)
    return out


# === validate ===============================================================


def validate(scripts_dir: Path | str) -> list[str]:
    """Soft-validate tools.json + executor.py. Returns human-readable warnings."""
    scripts_dir = Path(scripts_dir)
    warnings: list[str] = []

    tj = scripts_dir / TOOLS_JSON_NAME
    if not tj.exists():
        warnings.append(f"{tj}: missing tools.json (run `seed`)")
    else:
        try:
            data = json.loads(tj.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            warnings.append(f"{tj}: invalid JSON: {exc}")
            data = None
        if isinstance(data, list):
            for i, t in enumerate(data):
                if not isinstance(t, dict) or not t.get("name"):
                    warnings.append(f"{tj}[{i}]: tool entry missing 'name'")
                elif not isinstance(t.get("parameters"), dict):
                    warnings.append(f"{tj}[{i}] ({t.get('name')}): 'parameters' must be an object")
        elif data is not None:
            warnings.append(f"{tj}: top-level is not a JSON list")

    ex = scripts_dir / EXECUTOR_NAME
    if not ex.exists():
        warnings.append(f"{ex}: missing executor.py (run `seed`)")
    else:
        src = ex.read_text(encoding="utf-8")
        try:
            tree = ast.parse(src)
        except SyntaxError as exc:
            warnings.append(f"{ex}: Python syntax error: {exc}")
            tree = None
        if tree is not None:
            has_run_tool = any(
                isinstance(node, ast.FunctionDef) and node.name == "run_tool"
                for node in ast.walk(tree)
            )
            if not has_run_tool:
                warnings.append(f"{ex}: no `def run_tool(action, ...)` defined")
    return warnings


# === all-in-one deploy ======================================================


def deploy(
    scripts_dir: Path | str,
    api_type: str = "chat",
    *,
    max_completion_tokens: int | None = None,
    container: bool = True,
) -> dict[str, Path]:
    """deploy_runtime + write_config. (Does NOT seed — the evolve agent owns
    tools.json/executor.py. Call ``seed`` once before cycle 1 if starting empty.)"""
    scripts_dir = Path(scripts_dir)
    runtime = deploy_runtime(scripts_dir)
    config = write_config(
        scripts_dir, api_type,
        max_completion_tokens=max_completion_tokens, container=container,
    )
    return {"runtime": runtime, "config": config}


# === CLI ====================================================================


def _main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(description="v6: deploy/validate evolved tool registration files.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    def add_scripts(p):
        p.add_argument("--scripts-dir", required=True)

    p_seed = sub.add_parser("seed", help="write initial tools.json + executor.py + instruction.md")
    add_scripts(p_seed)

    p_deploy = sub.add_parser("deploy", help="deploy_runtime + write_config")
    add_scripts(p_deploy)
    p_deploy.add_argument("--api-type", default="chat", choices=["chat", "responses"])
    p_deploy.add_argument("--max-completion-tokens", type=int, default=None)
    p_deploy.add_argument("--host", action="store_true")

    p_rt = sub.add_parser("deploy-runtime", help="copy evolve_tools_v6 pkg to .runtime/ only")
    add_scripts(p_rt)

    p_cfg = sub.add_parser("write-config", help="write .evolve_tools_v6_config.yaml only")
    add_scripts(p_cfg)
    p_cfg.add_argument("--api-type", default="chat", choices=["chat", "responses"])
    p_cfg.add_argument("--max-completion-tokens", type=int, default=None)
    p_cfg.add_argument("--host", action="store_true")
    p_cfg.add_argument("--out", default=None)

    p_val = sub.add_parser("validate", help="soft-validate tools.json + executor.py")
    add_scripts(p_val)

    args = parser.parse_args()
    if args.cmd == "seed":
        for name, p in seed(args.scripts_dir).items():
            print(f"seeded {name}: {p}")
    elif args.cmd == "deploy":
        paths = deploy(args.scripts_dir, api_type=args.api_type,
                       max_completion_tokens=args.max_completion_tokens, container=not args.host)
        print(json.dumps({k: str(v) for k, v in paths.items()}, indent=2))
    elif args.cmd == "deploy-runtime":
        print(str(deploy_runtime(args.scripts_dir)))
    elif args.cmd == "write-config":
        print(str(write_config(args.scripts_dir, api_type=args.api_type,
                               max_completion_tokens=args.max_completion_tokens,
                               container=not args.host, out_path=args.out)))
    elif args.cmd == "validate":
        ws = validate(args.scripts_dir)
        if ws:
            print("WARNINGS:")
            for w in ws:
                print(f"  - {w}")
        else:
            print("OK: tools.json + executor.py valid")


if __name__ == "__main__":
    _main()
