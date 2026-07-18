"""Evolve v6 cycle — rollout ↔ evolve where the evolve agent writes the
**tool-registration files directly** (``tools.json`` + ``executor.py``).

Differs from v5 in what the evolve agent produces:

* v5: evolve agent edits ``<tool>/main.sh`` + ``<tool>/intro.json``; a converter
  then builds a manifest + bash dispatch (``evolve_tools`` reads the manifest).
* v6: evolve agent (bash-only mini-swe-agent) writes ``tools.json`` (the tool
  registry — function-tool schemas the LLM sees) and ``executor.py`` (a single
  ``run_tool(action, cwd, timeout)`` Python dispatcher). These ARE the files
  mini-swe-agent loads to register the tools — no converter, no manifest, no
  per-tool ``main.sh``. The generic ``evolve_tools_v6`` runtime (shipped, stable)
  loads them at agent start.

One cycle (same contrastive construction as v5; only the evolve TARGET changed)::

    1. rollout     mini-swe-agent with the CURRENT tools.json+executor.py → trajectories
    2. annotate    LLM-annotate step dependencies (DAG)
    3. contrastive cut each trajectory to its dependency-critical minimal path
    4. evolve      evolve agent rewrites tools.json + executor.py from contrastive samples
    5. validate    soft-check tools.json (JSON) + executor.py (ast + run_tool)

Cycle 1 may reuse a ``--baseline-dir`` (e.g. a no-tools T0) instead of rolling
out. The rollout container picks up the regenerated tools via bind-mount
(``tools.json`` + ``executor.py`` + ``.runtime/`` + config yaml).

Usage::

    python -m src.evolve.evolve_v6_cycle run \\
        --benchmark swebench --config _config/deepseekv4_flash.yaml \\
        --eval-cases-file <cases.txt> --baseline-dir <prep dir> \\
        --scripts-dir .evolve_scripts_v6_swebench --work-dir results/v6_cycle/swebench
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional

from src.tools.llm import LLM

from .annotator import TrajectoryAnnotator
from .contrastive import ContrastiveSampleBuilder
from .evolver import (
    AgentRunner,
    MiniSweAgentRunner,
    ScriptEvolver,
    TrajectorySerializer,
)
from .native_tools_v6 import (
    deploy as deploy_v6,
    seed as seed_v6,
    validate as validate_v6,
)
from .run_evolve import DEFAULT_MINI_SWE_AGENT, _setup_logging

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCRIPTS_DIR = ROOT / ".evolve_scripts_v6"
DEFAULT_WORK_DIR = ROOT / "results" / "v6_cycle"
DEFAULT_N_CYCLES = 4


# ============================================================================
# v6 evolve prompt — targets tools.json + executor.py (NOT main.sh/intro.json)
# ============================================================================


class EvolvePromptBuilderV6:
    """Build the prompt that tells the evolve agent to write tools.json + executor.py.

    The evolve agent has ONLY a bash tool. It edits two files in its cwd:

    * ``tools.json``  — JSON list of function-tool schemas
      ``[{"name","description","parameters":{"type":"object","properties":{...},"required":[...]}}, ...]``.
    * ``executor.py`` — Python with ``def run_tool(action, cwd=None, timeout=120)``
      returning ``{"output","returncode","exception_info"}``, dispatching by
      ``action["tool"]``.

    These two files must stay in sync (every tool in tools.json has a branch in
    executor.py's run_tool, and vice versa). The rollout mini-swe-agent loads
    them as native function tools via the generic ``evolve_tools_v6`` runtime.
    """

    HEADER = [
        "# Evolve task (v6 — write native function tools directly)",
        "",
        "You are evolving NATIVE FUNCTION TOOLS for a downstream mini-swe-agent. The",
        "rollout agent will call your tools BY NAME with structured JSON parameters",
        "(a `bash` tool is always available too, for ad-hoc commands). Your goal: write",
        "tools that collapse high-frequency, multi-step operations into one structured",
        "call so future agents solve similar tasks with fewer steps/tokens.",
        "",
        "## You edit exactly TWO files in the current working directory",
        "",
        "### 1. `tools.json` — the tool registry",
        "A JSON list of function-tool schemas (what the LLM sees). Each entry:",
        "  {",
        '    "name": "read-lines",            # snake-case, unique',
        '    "description": "ONE sentence: what this tool does.",',
        '    "parameters": {                  # JSON schema',
        '      "type": "object",',
        '      "properties": {"file": {"type":"string","description":"path"},',
        '                      "head": {"type":"integer","description":"first N lines"}},',
        '      "required": ["file"]',
        '    }',
        "  }",
        "Keep names clean (the LLM passes them as JSON keys). Use `integer`/`string`/`boolean`.",
        "",
        "### 2. `executor.py` — the execution logic",
        "Python (STDLIB ONLY: subprocess, os, json, re, shutil, glob, ...). MUST define:",
        "  def run_tool(action, cwd=None, timeout=120):",
        "      name = action.get('tool')",
        "      if name == 'read-lines':",
        "          file = action.get('file'); head = action.get('head', 200)",
        "          r = subprocess.run(['head','-n',str(head),file],",
        "                             cwd=cwd, capture_output=True, text=True, timeout=timeout)",
        "          return {'output': r.stdout, 'returncode': r.returncode, 'exception_info': ''}",
        "      elif name == '...':",
        "          ...",
        "      return {'output': f'unknown tool {name}', 'returncode': 1, 'exception_info': 'unknown'}",
        "Return value MUST be a dict with keys `output` (str), `returncode` (int),",
        "`exception_info` (str) — same shape as the bash tool's result. Catch your own",
        "errors per branch so one bad call doesn't crash the agent.",
        "",
        "## Rules",
        "- tools.json and executor.py MUST stay in sync: every name in tools.json has a",
        "  branch in run_tool, and every branch matches a tools.json entry.",
        "- Don't reimplement bash. Write tools for REPEATED multi-step patterns",
        "  (read/edit/find/search/test/run/git). Fewer, more general tools = lower cost.",
        "- Keep tools GENERIC: remove any tool that is too case-specific (e.g. hard-coded paths,",
        "  project-specific commands) and would not work across different repositories or tasks.",
        "- Retain tools that: (a) reduce step count for common patterns, (b) help quickly locate",
        "  specific code in a codebase (search/navigation), or (c) explore the codebase without",
        "  producing excessively long output.",
        "- If a tool only worked for certain past cases and is not broadly applicable, delete it.",
        "- Stdlib only in executor.py (no third-party imports — they may not be installed).",
        "- Keep tools robust: missing/empty args → returncode=1 + a clear message, not a crash.",
        "- Tool descriptions and executor semantics MUST agree. If a command parameter claims",
        "  Bash syntax support (`cd`, `&&`, pipes, redirects), execute it through `/bin/bash -lc`;",
        "  if using `shlex.split` + `shell=False`, describe it as argv-only and reject shell syntax",
        "  with a clear observation. Never advertise shell-command behavior with an argv-only executor.",
        "- Every subprocess MUST honor the provided `timeout` argument. Do not replace it with a",
        "  smaller hard-coded deadline; the runtime supplies the benchmark-specific safety limit.",
        "- When contrastive observations show timeout, command-not-found, truncation, or non-zero",
        "  failures from an existing tool, repair that tool's contract before adding new tools.",
        "- A test-running tool (e.g. `run-tests`) MUST accept an optional `cwd` (pass it",
        "  through to subprocess.run) and MUST auto-detect Django: if `runtests.py` exists in",
        "  `cwd` (check `<cwd>/tests/runtests.py` and `<cwd>/runtests.py`), run tests via",
        "  `python runtests.py <labels>` from that dir; otherwise use `python -m pytest`.",
        "  Never assume the current dir is the repo root — always honor `cwd`.",
        "- Do NOT create a build / package / install tool (e.g. `build-package`, `pip install`,",
        "  `setup.py build/develop`). Building to test a change is slow and fails often; run the",
        "  targeted tests directly with `run-tests` instead.",
        "- After editing, VERIFY both files:",
        '    python -c "import json; json.load(open(\'tools.json\'))"',
        '    python -c "import ast; ast.parse(open(\'executor.py\').read())"',
        "- Do NOT create main.sh, intro.json, or per-tool directories — v6 uses ONLY",
        "  tools.json + executor.py + instruction.md.",
        "",
        "## instruction.md (HIGH-LEVEL BEHAVIORAL RULES, ≤ 25 short lines)",
        "Write GENERIC, tool-agnostic strategies — NOT tool-specific usage guides.",
        "Focus on FOUR categories:",
        "1. BATCHING: when/how to combine multiple actions into one step.",
        "2. GIVE UP: when to stop retrying a failing approach and pivot.",
        "3. EARLY EXIT: when to commit a best-effort fix without full validation.",
        "4. RISKY MOVES: when to skip environment validation (e.g. tests) and just submit.",
        "Do NOT list tool names, schemas, or parameter details — those live in tools.json.",
        "Update this file when contrastive samples reveal new behavioral patterns.",
    ]

    FOOTER = (
        "\nYour task: modify tools.json and executor.py (and instruction.md) in the current "
        "directory based on the contrastive samples below — add tools for costly repeated "
        "patterns, fix tools that failed, merge overlapping ones, remove unused ones. Keep "
        "tools.json and executor.py in sync. Verify both files parse before finishing. "
        "Do not edit the prompt or contrastive-sample files. Finish by saving the files."
    )

    def __init__(self, serializer: Optional[TrajectorySerializer] = None):
        self.serializer = serializer or TrajectorySerializer()

    def build(
        self,
        sample_paths: Iterable[Path],
        cwd_name: str = ".",
        scripts_dir: Optional[Path] = None,
    ) -> str:
        parts: List[str] = list(self.HEADER)
        parts.append(f"\nThe current working directory is {cwd_name}; edit tools.json, "
                     f"executor.py, and instruction.md in place here.")
        if scripts_dir is not None:
            parts += self._current_files_block(Path(scripts_dir))
        for i, path in enumerate(sample_paths, start=1):
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            parts += [
                f"\n# Executional History {i}",
                f"Source: {path}",
                "\n## Original Trajectory",
                self.serializer.serialize(data["negative_sample"]),
                "\n## Minimal Trajectory",
                self.serializer.serialize(data["positive_sample"]),
            ]
        parts.append(self.FOOTER)
        return "\n".join(parts)

    def _current_files_block(self, scripts_dir: Path) -> List[str]:
        """Show the current tools.json + executor.py so the evolve agent can edit them."""
        lines = ["\n# Current tool-registration files in this directory"]
        for name in ("tools.json", "executor.py", "instruction.md"):
            p = scripts_dir / name
            lines.append(f"\n## ./{name}")
            if not p.exists():
                lines.append("(missing — create it)")
                continue
            try:
                text = p.read_text(encoding="utf-8")
            except OSError as exc:
                lines.append(f"(failed to read: {exc})")
                continue
            # Cap executor.py so the prompt doesn't blow up as it grows across cycles.
            cap = 4000 if name == "executor.py" else 2000
            if len(text) > cap:
                text = text[:cap] + f"\n... <truncated, {len(text)-cap} more chars>"
            lines.append(text)
        return lines


# ============================================================================
# Benchmark metadata (same as v5)
# ============================================================================


BENCHMARKS: dict[str, dict] = {
    "deep-swe": dict(run_script="run_deep_swe.sh", results_subdir="deep-swe", split="",
                     task_path_env="DEEP_SWE_TASKS_PATH", temp_layout="flat"),
    "swe-atlas-qa": dict(run_script="run_swe_atlas.sh", results_subdir="swe-atlas-qa", split="qa",
                         task_path_env="SWE_ATLAS_DATA_DIR", temp_layout="split"),
    "swe-atlas-tw": dict(run_script="run_swe_atlas.sh", results_subdir="swe-atlas-tw", split="tw",
                         task_path_env="SWE_ATLAS_DATA_DIR", temp_layout="split"),
    "swe-atlas-rf": dict(run_script="run_swe_atlas.sh", results_subdir="swe-atlas-rf", split="rf",
                         task_path_env="SWE_ATLAS_DATA_DIR", temp_layout="split"),
    "swebench": dict(run_script="run_swe_bench.sh", results_subdir="swebench-verified", split="",
                     task_path_env="SWEBENCH_TASK_PATH", temp_layout="flat"),
    "datamind": dict(run_script="run_datamind_harbor.sh", results_subdir="datamind-longds", split="",
                     task_path_env="DATAMIND_TASK_PATH", temp_layout="flat"),
    "dab": dict(run_script="run_dab_harbor.sh", results_subdir="dab", split="",
                task_path_env="DAB_TASK_PATH", temp_layout="flat"),
}


def _bench_source_task_dir(benchmark: str) -> Optional[Path]:
    if benchmark == "deep-swe":
        return ROOT / "benchmark" / "deep-swe" / "tasks"
    if benchmark.startswith("swe-atlas-"):
        return ROOT / "benchmark" / "SWE-Atlas" / "data" / benchmark.split("-", 2)[-1]
    if benchmark == "swebench":
        return Path(os.environ.get("SWEBENCH_TASK_PATH") or ROOT / "tmp" / "harbor" / "datasets" / "swebench-verified")
    if benchmark == "datamind":
        return Path(os.environ.get("DATAMIND_TASK_PATH") or ROOT / "tmp" / "harbor" / "datasets" / "longds")
    if benchmark == "dab":
        return Path(os.environ.get("DAB_TASK_PATH") or ROOT / "benchmark" / "DBA-bench" / "harbor" / "datasets" / "dab")
    return None


def _results_dir() -> Path:
    return Path(os.environ.get("RESULTS_DIR", ROOT / "results"))


# ============================================================================
# Rollout agent — sets EVOLVE_TOOLS_MODE=registry so the run script uses v6 wiring
# ============================================================================


@dataclass
class RolloutResult:
    run_dir: Path
    run_id: str
    cycle: int
    n_cases: int


class RolloutAgent:
    """Run the benchmark with the current v6 tools (tools.json + executor.py).

    Sets ``EVOLVE_TOOLS_MODE=registry`` so ``scripts/_bench_common.sh:
    evolve_scripts_deploy`` deploys the v6 runtime + config (not the v5 manifest)
    and emits the v6 env vars (``EVOLVE_TOOLS_V6_REGISTRY`` / ``_EXECUTOR``).
    """

    def __init__(self, benchmark: str, config_path, *, n_tasks: int = 1000,
                 n_concurrent: int = 8, n_attempts: int = 1, taskdir_root: Optional[Path] = None):
        if benchmark not in BENCHMARKS:
            raise ValueError(f"unknown benchmark: {benchmark} (known: {list(BENCHMARKS)})")
        self.benchmark = benchmark
        self.meta = BENCHMARKS[benchmark]
        self.config_path = str(config_path)
        self.n_tasks = int(n_tasks)
        self.n_concurrent = int(n_concurrent)
        self.n_attempts = int(n_attempts)
        self.taskdir_root = Path(taskdir_root) if taskdir_root else (DEFAULT_WORK_DIR / "taskdirs")

    def rollout(self, scripts_dir: Path, case_ids: List[str], run_id: str,
                cycle: int, dry_run: bool = False) -> RolloutResult:
        scripts_dir = Path(scripts_dir)
        env = self._build_env(scripts_dir, case_ids, run_id)
        cmd = ["bash", str(ROOT / "scripts" / self.meta["run_script"])]
        run_dir = self._expected_run_dir(run_id)
        logger.info("[v6 rollout] cycle=%d %s run_id=%s cases=%d -> %s",
                    cycle, self.benchmark, run_id, len(case_ids), run_dir)
        if dry_run:
            logger.info("[v6 rollout] DRY_RUN — not executing")
            return RolloutResult(run_dir, run_id, cycle, len(case_ids))
        run_dir.parent.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(cmd, env=env, cwd=str(ROOT), capture_output=True, text=True)
        if proc.stdout:
            logger.info("[v6 rollout] stdout tail:\n%s", proc.stdout[-3000:])
        if proc.stderr:
            logger.info("[v6 rollout] stderr tail:\n%s", proc.stderr[-3000:])
        if proc.returncode != 0:
            logger.warning("[v6 rollout] run script exited %d (partial failures may be OK)", proc.returncode)
        if not run_dir.exists():
            run_dir = self._resolve_run_dir(run_id) or run_dir
        return RolloutResult(run_dir, run_id, cycle, len(case_ids))

    def _build_env(self, scripts_dir: Path, case_ids: List[str], run_id: str) -> dict:
        env = dict(os.environ)
        env.update({
            "ROOT_DIR": str(ROOT),
            "RESULTS_DIR": str(_results_dir()),
            "LLM_CONFIG": self.config_path,
            "EVOLVE_SCRIPTS_DIR": str(scripts_dir) if scripts_dir.exists() else "",
            # v6: tell _bench_common.sh to deploy the v6 runtime+config (registry mode),
            # not the v5 manifest.
            "EVOLVE_TOOLS_MODE": "registry",
            "RUN_ID": run_id,
            "N_TASKS": str(self.n_tasks),
            "N_CONCURRENT": str(self.n_concurrent),
            "N_ATTEMPTS": str(self.n_attempts),
            "EVOLVE_SKIP_FILE": "",
        })
        if self.meta["split"]:
            env["SWE_ATLAS_SPLITS"] = self.meta["split"]
        temp = self._build_temp_task_dir(case_ids, run_id)
        if temp is not None:
            env[self.meta["task_path_env"]] = str(temp)
        return env

    def _build_temp_task_dir(self, case_ids: List[str], run_id: str) -> Optional[Path]:
        src = _bench_source_task_dir(self.benchmark)
        if src is None or not src.exists():
            logger.warning("[v6 rollout] no source task dir for %s; running full set", self.benchmark)
            return None
        base = self.taskdir_root / run_id
        _safe_rmtree(base)
        base.mkdir(parents=True, exist_ok=True)
        if self.meta["temp_layout"] == "split":
            target_dir = base / self.meta["split"]
            target_dir.mkdir(parents=True, exist_ok=True)
            env_value = base
        else:
            target_dir = base
            env_value = base
        n = 0
        for cid in case_ids:
            case_src = src / cid
            if not case_src.exists():
                logger.warning("[v6 rollout] case task dir missing, skip: %s", case_src)
                continue
            try:
                os.symlink(str(case_src.resolve()), str(target_dir / cid))
                n += 1
            except OSError as exc:
                logger.warning("[v6 rollout] symlink failed for %s: %s", cid, exc)
        logger.info("[v6 rollout] linked %d/%d cases into %s", n, len(case_ids), target_dir)
        return env_value

    def _expected_run_dir(self, run_id: str) -> Path:
        return _results_dir() / self.meta["results_subdir"] / run_id

    def _resolve_run_dir(self, run_id: str) -> Optional[Path]:
        base = self._expected_run_dir(run_id).parent
        if not base.exists():
            return None
        hits = sorted(base.glob(f"{run_id}*"))
        return hits[0] if hits else None


def _safe_rmtree(path: Path) -> None:
    if not path.exists():
        return
    for entry in path.iterdir():
        if entry.is_symlink() or entry.is_file():
            entry.unlink()
        elif entry.is_dir():
            shutil.rmtree(entry, ignore_errors=True)
    try:
        path.rmdir()
    except OSError:
        pass


# ============================================================================
# Evolve agent — writes tools.json + executor.py, then validates
# ============================================================================


def _llm_api_type(config_path: str) -> str:
    cfg = LLM._load_config(config_path)
    return (cfg.get("api_type") or "chat").strip().lower()


def _max_completion_tokens() -> Optional[int]:
    p = os.environ.get("MSWEA_MAXTOK_CONFIG")
    if not p or not Path(p).is_file():
        return None
    try:
        cfg = LLM._load_config(p)
        mk = cfg.get("max_completion_tokens")
        return int(mk) if mk else None
    except (TypeError, ValueError):
        return None


class EvolveAgent:
    """The evolve agent: rewrite tools.json + executor.py from contrastive samples,
    then refresh the v6 runtime/config and soft-validate the result."""

    def __init__(self, scripts_dir: Path, config_path: str, mini_swe_agent_dir: Path, *,
                 batch_size: int = 2, max_observation_chars: int = 1000, workers: int = 8,
                 output_dir: Optional[Path] = None, dry_run: bool = False):
        self.scripts_dir = Path(scripts_dir)
        self.config_path = str(config_path)
        self.mini_swe_agent_dir = Path(mini_swe_agent_dir)
        self.batch_size = int(batch_size)
        self.max_observation_chars = int(max_observation_chars)
        self.workers = int(workers)
        self.dry_run = bool(dry_run)
        self.output_dir = Path(output_dir) if output_dir else None

    def annotate(self, run_dir: Path, task: Optional[str] = None) -> None:
        logger.info("[v6 evolve] annotate %s", run_dir)
        TrajectoryAnnotator(self.config_path, workers=self.workers).run(run_dir, task=task)

    def contrastive(self, run_dir: Path, task: Optional[str] = None) -> None:
        logger.info("[v6 evolve] contrastive %s", run_dir)
        ContrastiveSampleBuilder().run(run_dir, task=task)

    def evolve(self, run_dir: Path, task: Optional[str] = None) -> Path:
        logger.info("[v6 evolve] evolve tools.json+executor.py from %s -> %s", run_dir, self.scripts_dir)
        evolver = ScriptEvolver(
            scripts_dir=self.scripts_dir,
            runner=MiniSweAgentRunner(
                mini_swe_agent_dir=self.mini_swe_agent_dir,
                llm_config=self.config_path,
                dry_run=self.dry_run,
            ),
            prompt_builder=EvolvePromptBuilderV6(
                serializer=TrajectorySerializer(max_observation_chars=self.max_observation_chars),
            ),
            batch_size=self.batch_size,
            output_dir=self.output_dir,
            resume=True,
        )
        output_dir = evolver.run(run_dir, task=task)
        self.refresh_registration()
        return output_dir

    def refresh_registration(self) -> None:
        """Re-deploy the v6 runtime + config (idempotent) and soft-validate
        tools.json + executor.py. The evolve agent owns those two files; this
        only ensures the runtime package + config yaml are present and the files
        parse — matching the v6 contract ("evolve agent updates the registration
        files in real time")."""
        if self.dry_run:
            logger.info("[v6 evolve] DRY_RUN — skipping refresh/validate")
            return
        api_type = _llm_api_type(self.config_path)
        paths = deploy_v6(self.scripts_dir, api_type=api_type,
                          max_completion_tokens=_max_completion_tokens(), container=True)
        ws = validate_v6(self.scripts_dir)
        for w in ws:
            logger.warning("v6 validate: %s", w)
        if not ws:
            logger.info("[v6 evolve] tools.json + executor.py valid; runtime+config at %s",
                        {k: str(v) for k, v in paths.items()})
        else:
            logger.warning("[v6 evolve] %d validation warning(s) — rollout may fall back to bash-only", len(ws))


# ============================================================================
# The cycle
# ============================================================================


@dataclass
class CycleReport:
    cycle: int
    rollout: RolloutResult
    annotated: bool
    contrastive_built: bool
    evolved: bool
    notes: str = ""


@dataclass
class V6Report:
    benchmark: str
    n_cycles: int
    scripts_dir: str
    cycles: List[CycleReport] = field(default_factory=list)


class EvolveV6Cycle:
    """Orchestrate the 4-cycle rollout ↔ evolve loop over v6 native tools
    (tools.json + executor.py written directly by the evolve agent)."""

    def __init__(self, benchmark: str, config_path, scripts_dir, *,
                 eval_cases_file: Optional[str] = None, baseline_dir: Optional[str] = None,
                 work_dir: Optional[str] = None, mini_swe_agent_dir: str = str(DEFAULT_MINI_SWE_AGENT),
                 n_cycles: int = DEFAULT_N_CYCLES, n_tasks: int = 1000, n_concurrent: int = 8,
                 n_attempts: int = 1, batch_size: int = 2, max_observation_chars: int = 1000,
                 workers: int = 8, dry_run: bool = False):
        self.benchmark = benchmark
        self.config_path = str(config_path)
        self.scripts_dir = Path(scripts_dir)
        self.baseline_dir = Path(baseline_dir) if baseline_dir else None
        self.work_dir = Path(work_dir) if work_dir else (DEFAULT_WORK_DIR / benchmark)
        self.mini_swe_agent_dir = Path(mini_swe_agent_dir)
        self.n_cycles = int(n_cycles)
        self.eval_cases_file = Path(eval_cases_file) if eval_cases_file else None
        self.dry_run = bool(dry_run)

        self.case_ids = self._load_case_ids()
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.scripts_dir.mkdir(parents=True, exist_ok=True)
        # Seed tools.json + executor.py + instruction.md if absent, and deploy the
        # v6 runtime + config once so cycle-1 rollout (if any) has the wiring ready.
        if not self.dry_run:
            seed_v6(self.scripts_dir)
            deploy_v6(self.scripts_dir, api_type=_llm_api_type(self.config_path),
                      max_completion_tokens=_max_completion_tokens(), container=True)

        self.rollout_agent = RolloutAgent(
            benchmark, self.config_path, n_tasks=n_tasks, n_concurrent=n_concurrent,
            n_attempts=n_attempts, taskdir_root=self.work_dir / "taskdirs")
        self.evolve_agent = EvolveAgent(
            self.scripts_dir, self.config_path, self.mini_swe_agent_dir,
            batch_size=batch_size, max_observation_chars=max_observation_chars,
            workers=workers, output_dir=self.work_dir / "evolve_logs", dry_run=dry_run)

    def _load_case_ids(self) -> List[str]:
        if self.eval_cases_file and self.eval_cases_file.exists():
            ids = [ln.strip() for ln in self.eval_cases_file.read_text(encoding="utf-8").splitlines()
                   if ln.strip() and not ln.strip().startswith("#")]
            if ids:
                return ids
        src = _bench_source_task_dir(self.benchmark)
        if src and src.exists():
            ids = sorted(p.name for p in src.iterdir() if p.is_dir())[:16]
            logger.info("[v6] sampled %d case ids from %s", len(ids), src)
            return ids
        raise ValueError(f"no case ids: provide --eval-cases-file or a source task dir for {self.benchmark}")

    def run(self) -> V6Report:
        report = V6Report(benchmark=self.benchmark, n_cycles=self.n_cycles, scripts_dir=str(self.scripts_dir))
        logger.info("[v6] start: benchmark=%s cycles=%d cases=%d scripts=%s",
                    self.benchmark, self.n_cycles, len(self.case_ids), self.scripts_dir)
        for cycle in range(1, self.n_cycles + 1):
            logger.info("[v6] === cycle %d/%d ===", cycle, self.n_cycles)
            rollout = self._do_rollout(cycle)
            annotated = self._safe(self.evolve_agent.annotate, rollout.run_dir, label="annotate")
            contrastive_built = self._safe(self.evolve_agent.contrastive, rollout.run_dir, label="contrastive")
            evolved = self._safe(self.evolve_agent.evolve, rollout.run_dir, label="evolve")
            report.cycles.append(CycleReport(cycle=cycle, rollout=rollout, annotated=annotated,
                                             contrastive_built=contrastive_built, evolved=evolved))
            self._save_report(report)
            logger.info("[v6] cycle %d done: annotate=%s contrastive=%s evolve=%s",
                        cycle, annotated, contrastive_built, evolved)
        logger.info("[v6] finished %d cycles. scripts=%s", self.n_cycles, self.scripts_dir)
        return report

    def _do_rollout(self, cycle: int) -> RolloutResult:
        if cycle == 1 and self.baseline_dir and self.baseline_dir.exists():
            logger.info("[v6] cycle 1 reusing baseline trajectories: %s", self.baseline_dir)
            return RolloutResult(self.baseline_dir, "baseline", 1, len(self.case_ids))
        run_id = f"v6c{cycle}-{self.benchmark}-{os.getpid()}"
        return self.rollout_agent.rollout(self.scripts_dir, self.case_ids, run_id, cycle, dry_run=self.dry_run)

    def _safe(self, fn, *args, label: str) -> bool:
        try:
            fn(*args)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.exception("[v6] %s failed: %s", label, exc)
            return False

    def _save_report(self, report: V6Report) -> None:
        path = self.work_dir / "v6_report.json"
        path.write_text(json.dumps({
            "benchmark": report.benchmark, "n_cycles": report.n_cycles, "scripts_dir": report.scripts_dir,
            "cycles": [{"cycle": c.cycle, "run_dir": str(c.rollout.run_dir), "run_id": c.rollout.run_id,
                        "n_cases": c.rollout.n_cases, "annotated": c.annotated,
                        "contrastive_built": c.contrastive_built, "evolved": c.evolved, "notes": c.notes}
                       for c in report.cycles],
        }, indent=2, ensure_ascii=False), encoding="utf-8")


# ============================================================================
# CLI
# ============================================================================


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--benchmark", required=True, choices=list(BENCHMARKS))
    parser.add_argument("--config", default=str(ROOT / "_config" / "deepseekv4_flash.yaml"))
    parser.add_argument("--scripts-dir", default=str(DEFAULT_SCRIPTS_DIR))
    parser.add_argument("--work-dir", default=None)
    parser.add_argument("--mini-swe-agent-dir", default=str(DEFAULT_MINI_SWE_AGENT))
    parser.add_argument("--eval-cases-file", default=None)
    parser.add_argument("--baseline-dir", default=None)
    parser.add_argument("--n-cycles", type=int, default=DEFAULT_N_CYCLES)
    parser.add_argument("--n-tasks", type=int, default=1000)
    parser.add_argument("--n-concurrent", type=int, default=8)
    parser.add_argument("--n-attempts", type=int, default=1)
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--max-observation-chars", type=int, default=1000)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--log-file", default=None)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Evolve v6 cycle: rollout ↔ evolve over tools.json + executor.py.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_run = sub.add_parser("run", help="run the full N-cycle loop")
    _add_common(p_run)
    p_bridge = sub.add_parser("refresh", help="(re)deploy v6 runtime+config + validate tools.json/executor.py")
    p_bridge.add_argument("--scripts-dir", required=True)
    p_bridge.add_argument("--config", default=str(ROOT / "_config" / "deepseekv4_flash.yaml"))
    p_bridge.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    _setup_logging(getattr(args, "log_file", None))
    if args.cmd == "run":
        EvolveV6Cycle(
            benchmark=args.benchmark, config_path=args.config, scripts_dir=args.scripts_dir,
            eval_cases_file=args.eval_cases_file, baseline_dir=args.baseline_dir, work_dir=args.work_dir,
            mini_swe_agent_dir=args.mini_swe_agent_dir, n_cycles=args.n_cycles, n_tasks=args.n_tasks,
            n_concurrent=args.n_concurrent, n_attempts=args.n_attempts, batch_size=args.batch_size,
            max_observation_chars=args.max_observation_chars, workers=args.workers, dry_run=args.dry_run,
        ).run()
    elif args.cmd == "refresh":
        EvolveAgent(scripts_dir=args.scripts_dir, config_path=args.config,
                    mini_swe_agent_dir=DEFAULT_MINI_SWE_AGENT, dry_run=args.dry_run).refresh_registration()


if __name__ == "__main__":
    main()
