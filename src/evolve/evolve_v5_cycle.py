"""Evolve v5 cycle — rollout ↔ evolve closed loop over native function tools.

Two agents alternate in a fixed ``--n-cycles`` loop (default 4):

  * **rollout agent** — mini-swe-agent running the benchmark with the *current*
    toolset. The evolved scripts are exposed to it as **native function tools**
    (built by :mod:`src.evolve.native_tools` from each script's ``intro.json``),
    not as bash pseudo-tools.
  * **evolve agent** — mini-swe-agent that rewrites ``main.sh`` / ``intro.json``
    in ``scripts_dir`` from contrastive samples, then refreshes the native-tool
    registration files (manifest + runtime + config) so the next rollout sees
    the new tools.

One cycle::

    1. rollout      run the benchmark on N cases with scripts_dir's tools  → trajectories
    2. annotate     LLM-annotate step dependencies on each trajectory
    3. contrastive  cut each trajectory to its dependency-critical minimal path
    4. evolve       feed contrastive samples to the evolve agent; it edits scripts
    5. bridge       convert evolved scripts → native tools (manifest/runtime/config)

The next cycle's rollout picks up the regenerated tools automatically. Cycle 1
may reuse a pre-existing ``--baseline-dir`` (e.g. a no-tools T0) instead of
rolling out, so the loop can start from an existing trajectory set.

Usage::

    python -m src.evolve.evolve_v5_cycle run \\
        --benchmark deep-swe \\
        --config _config/deepseekv4_flash.yaml \\
        --eval-cases-file results/evolving/deep-swe/deepseek-v4-flash/eval_cases.txt \\
        --scripts-dir .evolve_scripts_v5_deep-swe \\
        --work-dir results/v5_cycle/deep-swe \\
        --n-cycles 4
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
from typing import List, Optional

from src.tools.llm import LLM

from .annotator import TrajectoryAnnotator
from .contrastive import ContrastiveSampleBuilder
from .evolver import (
    EvolvePromptBuilder,
    MiniSweAgentRunner,
    ScriptEvolver,
    TrajectorySerializer,
)
from .native_tools import deploy as deploy_native_tools
from .run_evolve import DEFAULT_MINI_SWE_AGENT, _setup_logging

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCRIPTS_DIR = ROOT / ".evolve_scripts_v5"
DEFAULT_WORK_DIR = ROOT / "results" / "v5_cycle"
DEFAULT_N_CYCLES = 4


# ============================================================================
# Benchmark metadata — which run script + how to scope it to N cases
# ============================================================================

# temp_layout:
#   "flat"  → <temp>/<case>        symlinks, env=<temp>           (pier -p <temp>)
#   "split" → <temp>/<split>/<case> symlinks, env=<temp>           (harbor -p <temp>/<split>)
BENCHMARKS: dict[str, dict] = {
    "deep-swe": dict(
        run_script="run_deep_swe.sh", results_subdir="deep-swe", split="",
        task_path_env="DEEP_SWE_TASKS_PATH", temp_layout="flat",
    ),
    "swe-atlas-qa": dict(
        run_script="run_swe_atlas.sh", results_subdir="swe-atlas-qa", split="qa",
        task_path_env="SWE_ATLAS_DATA_DIR", temp_layout="split",
    ),
    "swe-atlas-tw": dict(
        run_script="run_swe_atlas.sh", results_subdir="swe-atlas-tw", split="tw",
        task_path_env="SWE_ATLAS_DATA_DIR", temp_layout="split",
    ),
    "swe-atlas-rf": dict(
        run_script="run_swe_atlas.sh", results_subdir="swe-atlas-rf", split="rf",
        task_path_env="SWE_ATLAS_DATA_DIR", temp_layout="split",
    ),
    "swebench": dict(
        run_script="run_swe_bench.sh", results_subdir="swebench-verified", split="",
        task_path_env="SWEBENCH_TASK_PATH", temp_layout="flat",
    ),
    # datamind uses the harbor mini-swe-agent path (run_datamind_harbor.sh), so
    # evolved scripts register as native function tools there just like the others.
    "datamind": dict(
        run_script="run_datamind_harbor.sh", results_subdir="datamind-longds", split="",
        task_path_env="DATAMIND_TASK_PATH", temp_layout="flat",
    ),
    "dab": dict(
        run_script="run_dab_harbor.sh", results_subdir="dab", split="",
        task_path_env="DAB_TASK_PATH", temp_layout="flat",
    ),
}


def _bench_source_task_dir(benchmark: str) -> Optional[Path]:
    """Where the canonical per-case task directories live on this host."""
    if benchmark == "deep-swe":
        return ROOT / "benchmark" / "deep-swe" / "tasks"
    if benchmark.startswith("swe-atlas-"):
        split = benchmark.split("-", 2)[-1]  # qa / tw / rf
        return ROOT / "benchmark" / "SWE-Atlas" / "data" / split
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
# Rollout agent — drive scripts/run_<bench>.sh on a scoped case set
# ============================================================================


@dataclass
class RolloutResult:
    run_dir: Path
    run_id: str
    cycle: int
    n_cases: int


class RolloutAgent:
    """The rollout agent: run the benchmark with the current evolved toolset.

    Scopes the run to ``case_ids`` by symlinking those case dirs into a temp
    directory and pointing the benchmark's ``<TASK_PATH_ENV>`` at it. The run
    script's own ``evolve_scripts_deploy`` call turns ``scripts_dir`` into native
    function tools inside the rollout container.
    """

    def __init__(
        self,
        benchmark: str,
        config_path,
        *,
        n_tasks: int = 1000,
        n_concurrent: int = 8,
        n_attempts: int = 1,
        taskdir_root: Optional[Path] = None,
    ):
        if benchmark not in BENCHMARKS:
            raise ValueError(f"unknown benchmark: {benchmark} (known: {list(BENCHMARKS)})")
        self.benchmark = benchmark
        self.meta = BENCHMARKS[benchmark]
        self.config_path = str(config_path)
        self.n_tasks = int(n_tasks)
        self.n_concurrent = int(n_concurrent)
        self.n_attempts = int(n_attempts)
        self.taskdir_root = Path(taskdir_root) if taskdir_root else (DEFAULT_WORK_DIR / "taskdirs")

    def rollout(
        self,
        scripts_dir: Path,
        case_ids: List[str],
        run_id: str,
        cycle: int,
        dry_run: bool = False,
    ) -> RolloutResult:
        scripts_dir = Path(scripts_dir)
        env = self._build_env(scripts_dir, case_ids, run_id)
        cmd = ["bash", str(ROOT / "scripts" / self.meta["run_script"])]
        run_dir = self._expected_run_dir(run_id)
        logger.info("[v5 rollout] cycle=%d %s run_id=%s cases=%d -> %s",
                    cycle, self.benchmark, run_id, len(case_ids), run_dir)
        logger.info("[v5 rollout] cmd: %s", " ".join(shlex.quote(x) for x in cmd))

        if dry_run:
            logger.info("[v5 rollout] DRY_RUN — not executing")
            return RolloutResult(run_dir, run_id, cycle, len(case_ids))

        run_dir.parent.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(cmd, env=env, cwd=str(ROOT), capture_output=True, text=True)
        if proc.stdout:
            logger.info("[v5 rollout] stdout tail:\n%s", proc.stdout[-3000:])
        if proc.stderr:
            logger.info("[v5 rollout] stderr tail:\n%s", proc.stderr[-3000:])
        if proc.returncode != 0:
            logger.warning("[v5 rollout] run script exited %d (partial failures may be OK)",
                           proc.returncode)
        if not run_dir.exists():
            run_dir = self._resolve_run_dir(run_id) or run_dir
        return RolloutResult(run_dir, run_id, cycle, len(case_ids))

    # ----- env + temp task dir -----

    def _build_env(self, scripts_dir: Path, case_ids: List[str], run_id: str) -> dict:
        env = dict(os.environ)
        env.update({
            "ROOT_DIR": str(ROOT),
            "RESULTS_DIR": str(_results_dir()),
            "LLM_CONFIG": self.config_path,
            # Empty scripts_dir → rollout runs baseline (no tools); otherwise the
            # run script deploys native tools from it.
            "EVOLVE_SCRIPTS_DIR": str(scripts_dir) if scripts_dir.exists() else "",
            "RUN_ID": run_id,
            "N_TASKS": str(self.n_tasks),
            "N_CONCURRENT": str(self.n_concurrent),
            "N_ATTEMPTS": str(self.n_attempts),
            # v5 always (re)validates the full case set — never skip.
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
            logger.warning("[v5 rollout] no source task dir for %s; running full set",
                           self.benchmark)
            return None
        base = self.taskdir_root / run_id
        _safe_rmtree(base)
        base.mkdir(parents=True, exist_ok=True)

        if self.meta["temp_layout"] == "split":
            target_dir = base / self.meta["split"]
            target_dir.mkdir(parents=True, exist_ok=True)
            env_value = base  # harbor -p = ${SWE_ATLAS_DATA_DIR}/${split}
        else:
            target_dir = base
            env_value = base

        n_linked = 0
        for cid in case_ids:
            case_src = src / cid
            if not case_src.exists():
                logger.warning("[v5 rollout] case task dir missing, skip: %s", case_src)
                continue
            try:
                os.symlink(str(case_src.resolve()), str(target_dir / cid))
                n_linked += 1
            except OSError as exc:
                logger.warning("[v5 rollout] symlink failed for %s: %s", cid, exc)
        logger.info("[v5 rollout] linked %d/%d cases into %s", n_linked, len(case_ids), target_dir)
        return env_value

    # ----- run dir resolution -----

    def _expected_run_dir(self, run_id: str) -> Path:
        return _results_dir() / self.meta["results_subdir"] / run_id

    def _resolve_run_dir(self, run_id: str) -> Optional[Path]:
        base = self._expected_run_dir(run_id).parent
        if not base.exists():
            return None
        hits = sorted(base.glob(f"{run_id}*"))
        return hits[0] if hits else None


def _safe_rmtree(path: Path) -> None:
    """Remove a temp symlink dir without following the symlinks it contains."""
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
# Evolve agent — rewrite scripts from contrastive samples, then bridge to tools
# ============================================================================


def _llm_api_type(config_path: str) -> str:
    cfg = LLM._load_config(config_path)
    return (cfg.get("api_type") or "chat").strip().lower()


def _max_completion_tokens() -> Optional[int]:
    """Read max_completion_tokens from MSWEA_MAXTOK_CONFIG if pointed at a yaml."""
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
    """The evolve agent: edit scripts from contrastive samples, then refresh
    the native-tool registration files (manifest + runtime + config)."""

    def __init__(
        self,
        scripts_dir: Path,
        config_path: str,
        mini_swe_agent_dir: Path,
        *,
        batch_size: int = 2,
        max_observation_chars: int = 1000,
        workers: int = 8,
        output_dir: Optional[Path] = None,
        dry_run: bool = False,
    ):
        self.scripts_dir = Path(scripts_dir)
        self.config_path = str(config_path)
        self.mini_swe_agent_dir = Path(mini_swe_agent_dir)
        self.batch_size = int(batch_size)
        self.max_observation_chars = int(max_observation_chars)
        self.workers = int(workers)
        self.dry_run = bool(dry_run)
        self.output_dir = Path(output_dir) if output_dir else None

    def annotate(self, run_dir: Path, task: Optional[str] = None) -> None:
        """Stage 1: LLM-annotate step dependencies on every trajectory.json."""
        logger.info("[v5 evolve] annotate %s", run_dir)
        TrajectoryAnnotator(self.config_path, workers=self.workers).run(run_dir, task=task)

    def contrastive(self, run_dir: Path, task: Optional[str] = None) -> None:
        """Stage 2: build contrastive samples from annotated trajectories."""
        logger.info("[v5 evolve] contrastive %s", run_dir)
        ContrastiveSampleBuilder().run(run_dir, task=task)

    def evolve(self, run_dir: Path, task: Optional[str] = None) -> Path:
        """Stage 3: evolve scripts from contrastive samples + refresh native tools.

        Returns the evolve output dir. The evolve agent edits ``main.sh`` /
        ``intro.json`` in ``scripts_dir``; afterward we (re)build the manifest,
        runtime package, and config yaml so the next rollout sees the new tools.
        """
        logger.info("[v5 evolve] evolve scripts from %s -> %s", run_dir, self.scripts_dir)
        evolver = ScriptEvolver(
            scripts_dir=self.scripts_dir,
            runner=MiniSweAgentRunner(
                mini_swe_agent_dir=self.mini_swe_agent_dir,
                llm_config=self.config_path,
                dry_run=self.dry_run,
            ),
            prompt_builder=EvolvePromptBuilder(
                serializer=TrajectorySerializer(max_observation_chars=self.max_observation_chars),
            ),
            batch_size=self.batch_size,
            output_dir=self.output_dir,
            resume=True,
        )
        output_dir = evolver.run(run_dir, task=task)
        self.bridge_to_native_tools()
        return output_dir

    def bridge_to_native_tools(self) -> None:
        """Convert evolved scripts → native function tools (manifest/runtime/config).

        Idempotent. The rollout run script also does this via ``evolve_scripts_deploy``,
        but doing it here keeps the tool-registration files in sync immediately
        after each evolve, matching the v5 contract ("evolve agent updates the
        tool-registration files in real time").
        """
        if self.dry_run:
            logger.info("[v5 evolve] DRY_RUN — skipping native-tools bridge")
            return
        api_type = _llm_api_type(self.config_path)
        maxtok = _max_completion_tokens()
        paths = deploy_native_tools(
            self.scripts_dir,
            api_type=api_type,
            max_completion_tokens=maxtok,
            container=True,
        )
        logger.info("[v5 evolve] native tools bridged: %s", {k: str(v) for k, v in paths.items()})


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
class V5Report:
    benchmark: str
    n_cycles: int
    scripts_dir: str
    cycles: List[CycleReport] = field(default_factory=list)


class EvolveV5Cycle:
    """Orchestrate the 4-cycle rollout ↔ evolve loop over native function tools."""

    def __init__(
        self,
        benchmark: str,
        config_path,
        scripts_dir,
        *,
        eval_cases_file: Optional[str] = None,
        baseline_dir: Optional[str] = None,
        work_dir: Optional[str] = None,
        mini_swe_agent_dir: str = str(DEFAULT_MINI_SWE_AGENT),
        n_cycles: int = DEFAULT_N_CYCLES,
        n_tasks: int = 1000,
        n_concurrent: int = 8,
        n_attempts: int = 1,
        batch_size: int = 2,
        max_observation_chars: int = 1000,
        workers: int = 8,
        dry_run: bool = False,
    ):
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

        self.rollout_agent = RolloutAgent(
            benchmark, self.config_path,
            n_tasks=n_tasks, n_concurrent=n_concurrent, n_attempts=n_attempts,
            taskdir_root=self.work_dir / "taskdirs",
        )
        self.evolve_agent = EvolveAgent(
            self.scripts_dir, self.config_path, self.mini_swe_agent_dir,
            batch_size=batch_size, max_observation_chars=max_observation_chars,
            workers=workers, output_dir=self.work_dir / "evolve_logs", dry_run=dry_run,
        )

    # ----- case ids -----

    def _load_case_ids(self) -> List[str]:
        if self.eval_cases_file and self.eval_cases_file.exists():
            ids = [ln.strip() for ln in self.eval_cases_file.read_text(encoding="utf-8").splitlines()
                   if ln.strip() and not ln.strip().startswith("#")]
            if ids:
                return ids
            logger.warning("[v5] eval-cases-file %s is empty; sampling from source", self.eval_cases_file)
        src = _bench_source_task_dir(self.benchmark)
        if src and src.exists():
            ids = sorted(p.name for p in src.iterdir() if p.is_dir())[:16]
            logger.info("[v5] sampled %d case ids from %s", len(ids), src)
            return ids
        raise ValueError(
            f"no case ids: provide --eval-cases-file or a discoverable source task dir for {self.benchmark}"
        )

    # ----- main loop -----

    def run(self) -> V5Report:
        report = V5Report(benchmark=self.benchmark, n_cycles=self.n_cycles,
                          scripts_dir=str(self.scripts_dir))
        logger.info("[v5] start: benchmark=%s cycles=%d cases=%d scripts=%s",
                    self.benchmark, self.n_cycles, len(self.case_ids), self.scripts_dir)

        for cycle in range(1, self.n_cycles + 1):
            logger.info("[v5] === cycle %d/%d ===", cycle, self.n_cycles)
            rollout = self._do_rollout(cycle)
            annotated = self._safe(self.evolve_agent.annotate, rollout.run_dir, label="annotate")
            contrastive_built = self._safe(self.evolve_agent.contrastive, rollout.run_dir, label="contrastive")
            evolved = self._safe(self.evolve_agent.evolve, rollout.run_dir, label="evolve")
            report.cycles.append(CycleReport(
                cycle=cycle, rollout=rollout,
                annotated=annotated, contrastive_built=contrastive_built, evolved=evolved,
            ))
            self._save_report(report)
            logger.info("[v5] cycle %d done: annotate=%s contrastive=%s evolve=%s",
                        cycle, annotated, contrastive_built, evolved)

        logger.info("[v5] finished %d cycles. scripts=%s", self.n_cycles, self.scripts_dir)
        return report

    def _do_rollout(self, cycle: int) -> RolloutResult:
        # Cycle 1 may reuse a pre-existing baseline (e.g. a no-tools T0) instead
        # of rolling out — lets the loop start from an existing trajectory set.
        if cycle == 1 and self.baseline_dir and self.baseline_dir.exists():
            logger.info("[v5] cycle 1 reusing baseline trajectories: %s", self.baseline_dir)
            return RolloutResult(self.baseline_dir, "baseline", 1, len(self.case_ids))
        run_id = f"v5c{cycle}-{self.benchmark}-{os.getpid()}"
        return self.rollout_agent.rollout(
            self.scripts_dir, self.case_ids, run_id, cycle, dry_run=self.dry_run,
        )

    def _safe(self, fn, *args, label: str) -> bool:
        """Run one stage; swallow exceptions so a single failure doesn't kill the loop."""
        try:
            fn(*args)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.exception("[v5] %s failed: %s", label, exc)
            return False

    def _save_report(self, report: V5Report) -> None:
        path = self.work_dir / "v5_report.json"
        path.write_text(json.dumps({
            "benchmark": report.benchmark, "n_cycles": report.n_cycles,
            "scripts_dir": report.scripts_dir,
            "cycles": [
                {"cycle": c.cycle, "run_dir": str(c.rollout.run_dir), "run_id": c.rollout.run_id,
                 "n_cases": c.rollout.n_cases, "annotated": c.annotated,
                 "contrastive_built": c.contrastive_built, "evolved": c.evolved, "notes": c.notes}
                for c in report.cycles
            ],
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
    parser.add_argument("--eval-cases-file", default=None,
                        help="one case id per line; if omitted, sample 16 from the source task dir")
    parser.add_argument("--baseline-dir", default=None,
                        help="cycle-1 rollout reuse: an existing trajectory run dir (e.g. a no-tools T0)")
    parser.add_argument("--n-cycles", type=int, default=DEFAULT_N_CYCLES)
    parser.add_argument("--n-tasks", type=int, default=1000)
    parser.add_argument("--n-concurrent", type=int, default=8)
    parser.add_argument("--n-attempts", type=int, default=1)
    parser.add_argument("--batch-size", type=int, default=2,
                        help="contrastive samples per evolve prompt")
    parser.add_argument("--max-observation-chars", type=int, default=1000)
    parser.add_argument("--workers", type=int, default=8, help="annotate LLM parallelism")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--log-file", default=None)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Evolve v5 cycle: rollout ↔ evolve over native function tools.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="run the full N-cycle loop")
    _add_common(p_run)

    # Lightweight one-shot helpers (mirror run_evolve subcommands for debug).
    p_bridge = sub.add_parser("bridge", help="(re)build native-tool artifacts from scripts_dir")
    p_bridge.add_argument("--scripts-dir", required=True)
    p_bridge.add_argument("--config", default=str(ROOT / "_config" / "deepseekv4_flash.yaml"))
    p_bridge.add_argument("--dry-run", action="store_true")

    args = parser.parse_args(argv)
    _setup_logging(getattr(args, "log_file", None))

    if args.cmd == "run":
        cycle = EvolveV5Cycle(
            benchmark=args.benchmark,
            config_path=args.config,
            scripts_dir=args.scripts_dir,
            eval_cases_file=args.eval_cases_file,
            baseline_dir=args.baseline_dir,
            work_dir=args.work_dir,
            mini_swe_agent_dir=args.mini_swe_agent_dir,
            n_cycles=args.n_cycles,
            n_tasks=args.n_tasks,
            n_concurrent=args.n_concurrent,
            n_attempts=args.n_attempts,
            batch_size=args.batch_size,
            max_observation_chars=args.max_observation_chars,
            workers=args.workers,
            dry_run=args.dry_run,
        )
        cycle.run()
    elif args.cmd == "bridge":
        EvolveAgent(
            scripts_dir=args.scripts_dir,
            config_path=args.config,
            mini_swe_agent_dir=DEFAULT_MINI_SWE_AGENT,
            dry_run=args.dry_run,
        ).bridge_to_native_tools()


if __name__ == "__main__":
    main()
