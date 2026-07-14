"""Convert evolved bash scripts into native function tools.

The evolve agent edits ``<scripts_dir>/<tool>/{main.sh, intro.json}`` on disk.
This module turns that directory into the three things mini-swe-agent needs to
expose the scripts as real OpenAI-style function tools (no more "call
``bash /app/.preinstalled_scripts/<name>/main.sh``" pseudo-tool prompt):

1. **manifest** — ``<scripts_dir>/.tools_manifest.json``: one entry per tool with
   its JSON-schema ``parameters`` plus a ``param_specs`` list that maps a
   structured function-call argument back to ``main.sh`` CLI tokens. Consumed at
   runtime by ``minisweagent.extra.evolve_tools.registry``.
2. **runtime package** — ``<scripts_dir>/.runtime/evolve_tools/``: a copy of the
   generic agent-side package (``EvolveToolsAgent`` + two Model subclasses). Put
   on ``PYTHONPATH`` in the rollout container so ``import evolve_tools`` works
   even though mini-swe-agent there comes from PyPI (no ``extra/evolve_tools``).
3. **config yaml** — ``<scripts_dir>/.evolve_tools_config.yaml``: sets
   ``model.model_class`` / ``agent.agent_class`` to the evolve_tools classes (and
   ``max_completion_tokens`` for responses-API configs). Passed to mini-swe-agent
   via pier/harbor ``--ak config_file=``.

intro.json parameter ``name`` → ``param_spec`` mapping (the core rule)::

    "--numbered"            → bool_flag       (emit --numbered when truthy)
    "--head=N"              → value_flag_eq   (emit --head=<value>)
    "-c code" / "--x N"     → value_flag_space(emit -c then <value>)
    "file" (no leading -)   → positional      (emit <value>, shell-split)

``main_sh`` paths are stored RELATIVE to the scripts dir (e.g.
``read-lines/main.sh``); the runtime resolves them against
``EVOLVE_TOOLS_SCRIPTS_DIR`` (container ``/app/.preinstalled_scripts``), so the
same manifest works on host and in the container.

CLI::

    python -m src.evolve.native_tools deploy --scripts-dir <dir> --api-type chat|responses
    python -m src.evolve.native_tools build-manifest --scripts-dir <dir>
    python -m src.evolve.native_tools deploy-runtime --scripts-dir <dir>
    python -m src.evolve.native_tools write-config --scripts-dir <dir> --api-type responses
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
CANONICAL_RUNTIME = ROOT / "agent" / "mini-swe-agent" / "src" / "minisweagent" / "extra" / "evolve_tools"

MANIFEST_NAME = ".tools_manifest.json"
RUNTIME_DIRNAME = ".runtime"
CONFIG_NAME = ".evolve_tools_config.yaml"

# Host model/agent class paths (mini-swe-agent editable-installed from this repo
# → evolve_tools lives under minisweagent.extra). Used when running on the host.
HOST_MODEL_CLASS_CHAT = "minisweagent.extra.evolve_tools.model.LitellmModelWithEvolveTools"
HOST_MODEL_CLASS_RESPONSES = "minisweagent.extra.evolve_tools.model.LitellmResponseModelWithEvolveTools"
HOST_AGENT_CLASS = "minisweagent.extra.evolve_tools.agent.EvolveToolsAgent"

# Container model/agent class paths (evolve_tools copied to <scripts>/.runtime
# and put on PYTHONPATH — mini-swe-agent there is from PyPI, no extra/evolve_tools).
CONTAINER_MODEL_CLASS_CHAT = "evolve_tools.model.LitellmModelWithEvolveTools"
CONTAINER_MODEL_CLASS_RESPONSES = "evolve_tools.model.LitellmResponseModelWithEvolveTools"
CONTAINER_AGENT_CLASS = "evolve_tools.agent.EvolveToolsAgent"

# intro.json type → JSON-schema type
_TYPE_MAP = {
    "string": "string",
    "str": "string",
    "bool": "boolean",
    "boolean": "boolean",
    "int": "integer",
    "integer": "integer",
    "number": "number",
    "float": "number",
}


# === intro.json → param_spec =================================================


def _clean_name(flag: str) -> str:
    """``--head`` → ``head``; ``-c`` → ``c``; ``--max-depth`` → ``max_depth``."""
    return flag.lstrip("-").replace("-", "_")


def _parse_param(raw_name: str, ptype: str, positional_idx: int) -> dict[str, Any]:
    """Classify one intro.json parameter into a ``param_spec``.

    ``raw_name`` is the literal ``name`` from intro.json, e.g. ``--head=N``,
    ``-c code``, ``--context=N or -C N``, ``--numbered``, or a bare positional
    like ``file``. We take the first alternative before `` or `` and classify by
    shape (leading dash, ``=``, embedded space).
    """
    name = (raw_name or "").split(" or ")[0].strip()
    if not name:
        name = raw_name or "arg"
    if name.startswith("-"):
        if "=" in name:
            flag = name.split("=", 1)[0].strip()  # --head
            return {"clean": _clean_name(flag), "kind": "value_flag_eq", "flag": flag, "position": None}
        if " " in name:
            flag = name.split(None, 1)[0].strip()  # -c
            return {"clean": _clean_name(flag), "kind": "value_flag_space", "flag": flag, "position": None}
        # bare --flag
        kind = "bool_flag" if ptype == "bool" else "value_flag_space"
        return {"clean": _clean_name(name), "kind": kind, "flag": name, "position": None}
    # positional
    return {"clean": _clean_name(name), "kind": "positional", "flag": None, "position": positional_idx}


def _build_param_specs(intro_params: list[dict]) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    pos_idx = 0
    for p in intro_params or []:
        ptype = (p.get("type") or "string").strip().lower()
        spec = _parse_param(p.get("name", ""), ptype, pos_idx)
        if spec["kind"] == "positional":
            pos_idx += 1
        # Stash the original intro description on the spec for schema building.
        spec["description"] = p.get("description", "")
        spec["intro_type"] = ptype
        specs.append(spec)
    return specs


def _build_parameters_schema(specs: list[dict[str, Any]]) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    # intro.json `parameters` may carry an explicit `required` per-param; we also
    # accept it as a top-level list. Here we derive `required` from per-param
    # `required: true` (the shape every current intro.json uses).
    for spec in specs:
        clean = spec["clean"]
        json_type = _TYPE_MAP.get(spec.get("intro_type", "string"), "string")
        prop: dict[str, Any] = {"type": json_type, "description": spec.get("description", "")}
        properties[clean] = prop
    return {"type": "object", "properties": properties, "required": required}


def _mark_required_from_intro(schema: dict, intro_params: list[dict], specs: list[dict]) -> None:
    """Clear ``schema["required"]`` so evolved tools never expose hard-required
    fields to the model.

    A hard-required field that the model omits triggers FormatError; a few in a
    row trigger RepeatedFormatError and the case exits. Emptying ``required``
    lets a missing arg flow to ``main.sh`` (which returns an error observation
    the model can self-correct on) instead of aborting the case. The runtime
    parser (evolve_tools.registry) no longer enforces required either.
    """
    schema["required"] = []


# === one tool entry =========================================================


def build_tool_entry(tool_dir: Path) -> dict[str, Any] | None:
    """Build one manifest entry from ``<tool_dir>/intro.json``.

    Returns ``None`` (with a warning) if intro.json is missing/invalid — the
    caller skips it so one bad tool doesn't abort the whole manifest.
    """
    intro_path = tool_dir / "intro.json"
    if not intro_path.exists():
        logger.warning("skip %s: no intro.json", tool_dir.name)
        return None
    try:
        intro = json.loads(intro_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        logger.warning("skip %s: invalid intro.json: %s", tool_dir.name, exc)
        return None
    if not isinstance(intro, dict) or not intro.get("name"):
        logger.warning("skip %s: intro.json has no 'name'", tool_dir.name)
        return None

    intro_params = intro.get("parameters") or []
    if not isinstance(intro_params, list):
        intro_params = []

    specs = _build_param_specs(intro_params)
    schema = _build_parameters_schema(specs)
    _mark_required_from_intro(schema, intro_params, specs)

    entrypoint = intro.get("entrypoint") or "main.sh"
    description = intro.get("description", "")
    when_to_use = intro.get("when_to_use")
    if when_to_use:
        description = f"{description}\n\nWhen to use: {when_to_use}".strip()

    # Strip per-spec helper fields before storing (keep the manifest lean).
    lean_specs = [
        {k: v for k, v in spec.items() if k in ("clean", "kind", "flag", "position")}
        for spec in specs
    ]
    return {
        "name": intro["name"],
        "description": description,
        "main_sh": f"{tool_dir.name}/{entrypoint}",
        "parameters": schema,
        "param_specs": lean_specs,
    }


# === manifest ===============================================================


def build_manifest(scripts_dir: Path | str) -> dict[str, Any]:
    """Scan ``<scripts_dir>/*/intro.json`` and build the manifest dict.

    Does NOT write to disk — call :func:`write_manifest` (or the CLI) to persist.
    Tools are sorted by name for deterministic output.
    """
    scripts_dir = Path(scripts_dir)
    tools: list[dict[str, Any]] = []
    if scripts_dir.is_dir():
        for tool_dir in sorted(scripts_dir.iterdir(), key=lambda p: p.name):
            if not tool_dir.is_dir() or tool_dir.name.startswith(".") or tool_dir.name == RUNTIME_DIRNAME:
                continue
            entry = build_tool_entry(tool_dir)
            if entry is not None:
                tools.append(entry)
    return {"version": 1, "scripts_dir_note": "main_sh paths are relative to EVOLVE_TOOLS_SCRIPTS_DIR", "tools": tools}


def write_manifest(scripts_dir: Path | str) -> Path:
    """Build and write the manifest to ``<scripts_dir>/.tools_manifest.json``."""
    scripts_dir = Path(scripts_dir)
    scripts_dir.mkdir(parents=True, exist_ok=True)
    manifest = build_manifest(scripts_dir)
    out = scripts_dir / MANIFEST_NAME
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    logger.info("wrote manifest: %s (%d tools)", out, len(manifest["tools"]))
    return out


# === runtime package deployment =============================================


def deploy_runtime(scripts_dir: Path | str) -> Path:
    """Copy the canonical evolve_tools package into ``<scripts_dir>/.runtime/``.

    Idempotent: replaces any prior copy. Skips ``__pycache__`` / ``*.pyc``. The
    container bind-mounts ``<scripts_dir>`` → ``/app/.preinstalled_scripts`` and
    puts ``/app/.preinstalled_scripts/.runtime`` on PYTHONPATH, so
    ``import evolve_tools`` resolves to this copy.
    """
    scripts_dir = Path(scripts_dir)
    if not CANONICAL_RUNTIME.is_dir():
        raise FileNotFoundError(
            f"canonical evolve_tools runtime not found at {CANONICAL_RUNTIME}; "
            "expected agent/mini-swe-agent/src/minisweagent/extra/evolve_tools/"
        )
    dst_root = scripts_dir / RUNTIME_DIRNAME
    dst = dst_root / "evolve_tools"
    if dst.exists():
        shutil.rmtree(dst)
    dst_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        CANONICAL_RUNTIME,
        dst,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )
    # Sentinel so callers can sanity-check the deploy without importing.
    (dst_root / ".deployed").write_text(
        f"evolve_tools runtime deployed from {CANONICAL_RUNTIME}\n", encoding="utf-8"
    )
    logger.info("deployed evolve_tools runtime -> %s", dst)
    return dst


# === mini-swe-agent config yaml =============================================


def config_yaml_text(
    api_type: str = "chat",
    *,
    max_completion_tokens: int | None = None,
    container: bool = True,
) -> str:
    """Render the mini-swe-agent config yaml that wires evolve_tools classes.

    ``container=True`` (default) uses the ``evolve_tools.*`` import paths (the
    rollout container resolves them via PYTHONPATH). ``container=False`` uses
    ``minisweagent.extra.evolve_tools.*`` (host-side, editable install).
    """
    api = (api_type or "chat").strip().lower()
    if container:
        model_class = CONTAINER_MODEL_CLASS_RESPONSES if api == "responses" else CONTAINER_MODEL_CLASS_CHAT
        agent_class = CONTAINER_AGENT_CLASS
    else:
        model_class = HOST_MODEL_CLASS_RESPONSES if api == "responses" else HOST_MODEL_CLASS_CHAT
        agent_class = HOST_AGENT_CLASS

    lines = [
        "# Auto-generated by src/evolve/native_tools.py — wires evolved scripts",
        "# as native function tools (model + agent subclasses in evolve_tools).",
        "model:",
        f"  model_class: {model_class}",
    ]
    if max_completion_tokens:
        lines += ["  model_kwargs:", f"    max_completion_tokens: {max_completion_tokens}"]
    lines += ["agent:", f"  agent_class: {agent_class}"]
    return "\n".join(lines) + "\n"


def write_config(
    scripts_dir: Path | str,
    api_type: str = "chat",
    *,
    max_completion_tokens: int | None = None,
    container: bool = True,
    out_path: Path | str | None = None,
) -> Path:
    """Write the config yaml to ``<scripts_dir>/.evolve_tools_config.yaml``."""
    scripts_dir = Path(scripts_dir)
    scripts_dir.mkdir(parents=True, exist_ok=True)
    out = Path(out_path) if out_path else scripts_dir / CONFIG_NAME
    out.write_text(
        config_yaml_text(api_type, max_completion_tokens=max_completion_tokens, container=container),
        encoding="utf-8",
    )
    logger.info("wrote evolve_tools config -> %s (api_type=%s)", out, api_type)
    return out


# === all-in-one deploy ======================================================


def deploy(
    scripts_dir: Path | str,
    api_type: str = "chat",
    *,
    max_completion_tokens: int | None = None,
    container: bool = True,
) -> dict[str, Path]:
    """Build manifest + deploy runtime + write config in one shot.

    Returns a dict with ``manifest``, ``runtime``, ``config`` paths (host paths
    that the shell wiring in ``scripts/_bench_common.sh`` passes to pier/harbor).
    """
    scripts_dir = Path(scripts_dir)
    manifest = write_manifest(scripts_dir)
    runtime = deploy_runtime(scripts_dir)
    config = write_config(
        scripts_dir,
        api_type,
        max_completion_tokens=max_completion_tokens,
        container=container,
    )
    return {"manifest": manifest, "runtime": runtime, "config": config}


# === CLI ====================================================================


def _main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(description="Convert evolved scripts into native function tools.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    def add_scripts(p):
        p.add_argument("--scripts-dir", required=True, help="evolved scripts directory (host path)")

    p_deploy = sub.add_parser("deploy", help="build manifest + deploy runtime + write config")
    add_scripts(p_deploy)
    p_deploy.add_argument("--api-type", default="chat", choices=["chat", "responses"])
    p_deploy.add_argument("--max-completion-tokens", type=int, default=None)
    p_deploy.add_argument("--host", action="store_true", help="use minisweagent.extra.evolve_tools.* paths (host-side)")

    p_manifest = sub.add_parser("build-manifest", help="write .tools_manifest.json only")
    add_scripts(p_manifest)

    p_runtime = sub.add_parser("deploy-runtime", help="copy evolve_tools pkg to .runtime/ only")
    add_scripts(p_runtime)

    p_cfg = sub.add_parser("write-config", help="write .evolve_tools_config.yaml only")
    add_scripts(p_cfg)
    p_cfg.add_argument("--api-type", default="chat", choices=["chat", "responses"])
    p_cfg.add_argument("--max-completion-tokens", type=int, default=None)
    p_cfg.add_argument("--host", action="store_true")
    p_cfg.add_argument("--out", default=None)

    args = parser.parse_args()
    if args.cmd == "deploy":
        paths = deploy(
            args.scripts_dir,
            api_type=args.api_type,
            max_completion_tokens=args.max_completion_tokens,
            container=not args.host,
        )
        print(json.dumps({k: str(v) for k, v in paths.items()}, indent=2))
    elif args.cmd == "build-manifest":
        print(str(write_manifest(args.scripts_dir)))
    elif args.cmd == "deploy-runtime":
        print(str(deploy_runtime(args.scripts_dir)))
    elif args.cmd == "write-config":
        print(
            str(
                write_config(
                    args.scripts_dir,
                    api_type=args.api_type,
                    max_completion_tokens=args.max_completion_tokens,
                    container=not args.host,
                    out_path=args.out,
                )
            )
        )


if __name__ == "__main__":
    _main()
