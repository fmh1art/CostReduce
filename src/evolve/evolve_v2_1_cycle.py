"""Evolve v2.1 cycle - v5's iterative loop around v2's chunk evolve framework,
with a faster (batch-3) annotator.

What v2.1 is
------------
v2.1 = **v5's rollout ↔ evolve cycle** + **v2's chunk evolve framework** + an
**optimized annotator**. Concretely:

* The *cycle* is v5's: ``--n-cycles`` rounds of rollout → annotate → contrastive
  → evolve → bridge-to-native-tools. The rollout agent (reused verbatim from v5)
  runs the benchmark with the *current* toolset; cycle 1 may reuse a
  ``--baseline-dir`` (e.g. the prep no-tools T0) instead of rolling out.
* The *evolve framework* is v2's chunk pipeline: ``ChunkTrajectoryAnnotatorV2``
  (deps + LLM op_type + step_meta + brief_observations), phase-based chunking
  contrastive builder (graph + observation + cost_hotspot), behavior-contract
  prompt builder, cwd-fixed runner, and the chunk script evolver. The evolved
  scripts are ``<name>/{main.sh,intro.json}`` + ``instruction.md`` - same format
  v5 produces - so the same ``native_tools.deploy`` bridge turns them into native
  function tools for the next rollout.
* The *annotator* is new (``ChunkTrajectoryAnnotatorV21``): it batches **3
  consecutive steps per LLM call** instead of one call per step, cutting
  annotation LLM calls ~3×. ``brief_observations`` and ``step_meta`` are
  inherited unchanged from v2.

Annotator optimization (the v2.1 delta)
---------------------------------------
Two ideas, both requested:

1. **Only show steps 1..i-1 when annotating step i.** The base dependency
   annotator already builds a growing history of *prior* steps (0..i-1) and
   never shows future steps - so this property is preserved as-is. v2.1 keeps
   that history mechanism; it does NOT change what context step i sees.

2. **Batch 3 steps per LLM call.** Action steps are grouped into windows of
   ``WINDOW_SIZE`` (default 3): [1,2,3], [4,5,6], ... For window [i, i+1, i+2]
   we send the history of steps 0..i-1 (the minimum context step i needs) plus
   the 3 current steps' full content, and ask the model to return dependencies
   + op_type for all 3 in one JSON object. Step i+1 / i+2 can reference earlier
   steps *within the same window* (they're shown), so intra-window dependencies
   are captured correctly. One call replaces three → ~3× fewer annotation calls.

   Robustness: if a window response fails to parse, v2.1 falls back to the base
   per-step ``_annotate_step`` for just those steps, so annotation correctness
   never regresses below v2's.

One cycle::

    1. rollout      benchmark on N cases with scripts_dir's tools  -> trajectories
    2. annotate     batch-3 LLM-annotate step dependencies + op_type + brief_obs + step_meta
    3. contrastive  phase-based chunk split -> graph + observation + cost_hotspot samples
    4. evolve       v2 chunk evolver rewrites main.sh/intro.json from contrastive samples
    5. bridge       convert evolved scripts -> native function tools (manifest/runtime/config)

Usage::

    python -m src.evolve.evolve_v2_1_cycle run \\
        --benchmark swebench --config _config/deepseekv4_flash.yaml \\
        --eval-cases-file results/.../eval_cases.txt \\
        --baseline-dir results/evolving/swebench/<llm> \\
        --scripts-dir .evolve_scripts_v2_1_swebench \\
        --work-dir results/v2_1_cycle/swebench --n-cycles 4
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from src.tools.llm import LLM

# v5 cycle machinery (benchmark-agnostic rollout + helpers) - reused verbatim.
from .evolve_v5_cycle import (
    BENCHMARKS,
    RolloutAgent,
    RolloutResult,
    _bench_source_task_dir,
    _llm_api_type,
    _max_completion_tokens,
    _results_dir,
    _safe_rmtree,
)
# v2 chunk evolve framework - the evolve side is v2's, unchanged.
from .evolve_v2_chunk import (
    HOTSPOT_MIN_OCCURRENCES_DEFAULT,
    HOTSPOT_MIN_TOTAL_CHARS_DEFAULT,
    ChunkContrastiveSampleBuilderV2,
    ChunkEvolvePromptBuilderV2,
    ChunkScriptEvolverV2,
    ChunkTrajectoryAnnotatorV2,
    MiniSweAgentRunnerV2,
)
from .evolve_v1_chunk import (
    LONG_OBS_THRESHOLD_DEFAULT,
    MIN_REDUCTION_RATIO_DEFAULT,
)
from .annotator import DependencyParseError
from .evolver import TrajectorySerializer
from .native_tools import deploy as deploy_native_tools
from .run_evolve import DEFAULT_MINI_SWE_AGENT, _setup_logging

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCRIPTS_DIR = ROOT / ".evolve_scripts_v2_1"
DEFAULT_WORK_DIR = ROOT / "results" / "v2_1_cycle"
DEFAULT_N_CYCLES = 4
# How many consecutive steps one annotation LLM call covers (the v2.1 delta).
DEFAULT_ANNOTATE_WINDOW_SIZE = 3


# ============================================================================
# Stage 1: V2.1 annotator - batch 3 steps per LLM call
# ============================================================================


class ChunkTrajectoryAnnotatorV21(ChunkTrajectoryAnnotatorV2):
    """v2.1 annotator: batch ``WINDOW_SIZE`` steps per dependency-LLM-call.

    Differences from v2 (which calls the LLM once per step via the base
    ``TrajectoryAnnotator.annotate_file``):

    * ``_annotate_dependencies_batched`` groups action steps into consecutive
      windows of ``WINDOW_SIZE`` (default 3). For window [i, i+1, i+2] it sends
      the history of steps 0..i-1 (only prior steps - the user's "only give
      1..i-1" requirement) plus the window's 3 current steps, and parses a
      single JSON response carrying dependencies + op_type for all 3. This is
      ~3× fewer LLM calls than v2.
    * On window parse failure it falls back to the base per-step
      ``_annotate_step`` for that window's steps, so a flaky window never
      regresses correctness below v2.

    Everything else (``brief_observations`` from v1-chunk, ``step_meta`` rule
    fields from v2, the ``is_annotated`` / ``_run_batch`` skip logic) is
    inherited unchanged.
    """

    name = "annotate_chunk_v21"

    # Number of consecutive steps annotated in one LLM call. Mutable so the
    # cycle / CLI can override (e.g. ``ann.WINDOW_SIZE = 3``).
    WINDOW_SIZE = DEFAULT_ANNOTATE_WINDOW_SIZE

    # System prompt for the batched window call. Mirrors the base
    # ``TrajectoryAnnotator.SYSTEM_PROMPT`` op_type labels (read/write/verify/
    # explore) so downstream phase/anchor consumers stay on the same vocabulary,
    # but asks for a ``{"steps": [...]}`` object covering the whole window.
    WINDOW_SYSTEM_PROMPT = (
        "You annotate dependency relations AND the operation type of trajectory steps. "
        "Step 0 is the initial state before any action. For each current step, "
        "return every previous step index that the action depends on: as long as the "
        "agent sees all those previous steps, it can generate the current step. Make sure that all "
        "necessary dependencies are included. Sometimes, although previous steps are wrong, "
        "they are still necessary for the current step to be generated. "
        "ALSO classify each step's operation type into exactly one label:\n"
        "  read    - inspecting/searching code/data WITHOUT modifying it "
        "(grep, rg, find, cat, head, ls, sed WITHOUT -i, git status/log/diff/show).\n"
        "  write   - creating/editing/deleting/moving files, or changing repo state "
        "(sed -i, cat >, rm, mv, cp, apply patch, git checkout/commit/add/reset/apply).\n"
        "  verify  - running tests/build/lint/type-check to validate a change "
        "(pytest, make, go test/build, npm run, tsc, ruff, python -m pytest).\n"
        "  explore - none of the above: exploratory shells, env inspection, retries, "
        "setup, or anything ambiguous (pwd, env, echo, cd, which).\n"
        "You are given a WINDOW of up to a few consecutive current steps plus the history of "
        "ALL steps BEFORE the window. For EACH current step in the window, its dependencies may "
        "reference any previous step index - including step 0 and earlier steps that appear in "
        "the SAME window. Output ONLY a JSON object:\n"
        '{"steps": [{"index": <int>, "dependencies": [<int>, ...], "op_type": "<label>"}, ...]}\n'
        "Include exactly one entry per current step, with 'index' matching the step numbers shown."
    )

    def annotate_file(self, path, llm=None, step_workers: int = 1):
        # Skip if already fully annotated (matches v2's fast-path).
        if self._has_step_meta(path):
            logger.info("step_meta already present for %s, skipping", path)
            return
        llm = llm or LLM(self.config_path)
        # Stage A: dependencies + op_type via batched windows (replaces the base
        # per-step TrajectoryAnnotator.annotate_file dependency loop).
        self._annotate_dependencies_batched(path, llm, step_workers)
        # Stage B: brief_observations (inherited from ChunkTrajectoryAnnotator).
        self._annotate_brief_observations(path, llm)
        # Stage C: step_meta rule fields (inherited from ChunkTrajectoryAnnotatorV2).
        self._annotate_step_meta(path)

    # ---------- batched dependency + op_type annotation ----------

    def _annotate_dependencies_batched(self, path, llm, step_workers: int) -> None:
        """Write ``dependencies`` + LLM ``step_meta.op_type`` via windowed calls.

        Replaces the base ``TrajectoryAnnotator.annotate_file`` dependency loop
        with windowed batching. After this, the file has ``dependencies`` and
        each action step's ``step_meta.op_type`` (``op_type_source="llm"``);
        ``_annotate_step_meta`` (stage C) fills the remaining rule fields.
        """
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        action_steps = self._extract_action_steps(data)
        n = len(action_steps)
        step_texts = [self._step_text(step) for step in action_steps]

        windows = self._build_windows(step_texts, n)
        logger.info(
            "annotating %s: %d action steps in %d window(s) (window_size=%d)",
            path, n, len(windows), self.WINDOW_SIZE,
        )

        dependencies: Dict[str, List[int]] = {"0": []}
        op_types: Dict[str, Optional[str]] = {}

        if step_workers <= 1 or len(windows) <= 1:
            for indices, texts, history in windows:
                deps, ops = self._annotate_window(
                    path, indices, texts, history, n, llm
                )
                dependencies.update(deps)
                op_types.update(ops)
        else:
            wdeps, wops = self._annotate_windows_parallel(
                path, windows, n, step_workers, llm=llm
            )
            dependencies.update(wdeps)
            op_types.update(wops)

        data["dependencies"] = dependencies
        self._write_step_meta(action_steps, op_types)
        Path(path).write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        logger.info(
            "finished batched dependency annotation for %s (%d steps, %d windows)",
            path, n, len(windows),
        )

    def _build_windows(
        self, step_texts: List[str], n: int
    ) -> List[Tuple[List[int], List[str], str]]:
        """Group action steps into consecutive windows of ``WINDOW_SIZE``.

        Returns ``[(indices_1based, window_texts, history_str), ...]`` where
        ``history_str`` is the steps-0..(first-1) context for that window - i.e.
        only the steps *before* the window, satisfying "annotating step i only
        needs steps 1..i-1" for the window's first step.
        """
        windows: List[Tuple[List[int], List[str], str]] = []
        for start in range(0, n, self.WINDOW_SIZE):
            end = min(start + self.WINDOW_SIZE, n)
            indices = list(range(start + 1, end + 1))
            texts = [step_texts[i - 1] for i in indices]
            history = self._build_window_history(step_texts, start)
            windows.append((indices, texts, history))
        return windows

    @staticmethod
    def _build_window_history(step_texts: List[str], n_before: int) -> str:
        """History string for a window: steps 0..n_before-1 (1-based 1..n_before).

        Mirrors the base ``TrajectoryAnnotator._build_step_inputs`` history
        format (``Step 0: initial state...`` + each prior step's clipped text),
        but only up to the steps before this window.
        """
        history = "Step 0: initial state before any action.\n"
        for i in range(n_before):
            history += f"\nStep {i + 1}:\n{step_texts[i]}\n"
        return history

    def _annotate_window(
        self,
        path,
        indices: List[int],
        texts: List[str],
        history: str,
        total: int,
        llm,
    ) -> Tuple[Dict[str, List[int]], Dict[str, Optional[str]]]:
        """One LLM call for a window of up to ``WINDOW_SIZE`` steps.

        Returns ``({str(idx): deps}, {str(idx): op_type})``. On parse failure
        falls back to per-step ``_annotate_step`` for this window's steps.
        """
        window_desc = ", ".join(str(i) for i in indices)
        logger.info("annotating %s window steps %s/%d", path, window_desc, total)
        user_prompt = self._build_window_user_prompt(indices, texts)
        try:
            raw = llm.query(self.WINDOW_SYSTEM_PROMPT, history, user_prompt)
            return self._parse_window_response(raw, indices)
        except (DependencyParseError, ValueError) as exc:
            logger.warning(
                "window parse failed for %s steps %s (%s); falling back to per-step",
                path, window_desc, exc,
            )
            return self._fallback_per_step(path, indices, texts, history, total, llm)

    @staticmethod
    def _build_window_user_prompt(indices: List[int], texts: List[str]) -> str:
        lines = ["Current steps to annotate (a window of consecutive steps):"]
        for idx, text in zip(indices, texts):
            lines.append(f"\nStep {idx}:")
            lines.append(text)
        lines.append("")
        lines.append(
            "For EACH step above, return its dependencies (previous step indices it "
            "depends on, including step 0 and any earlier steps in this window) and its "
            "op_type. Output ONLY the JSON object: "
            '{"steps": [{"index": <int>, "dependencies": [<int>, ...], "op_type": "<label>"}, ...]}.'
        )
        return "\n".join(lines)

    @classmethod
    def _parse_window_response(
        cls, text: str, indices: List[int]
    ) -> Tuple[Dict[str, List[int]], Dict[str, Optional[str]]]:
        """Parse ``{"steps": [{"index","dependencies","op_type"}, ...]}``.

        deps are validated strictly (raise ``DependencyParseError``); op_type
        is best-effort (None if not a known label). Raises if any window step
        is missing from the response so the caller can fall back.
        """
        text = text or ""
        obj = None
        obj_matches = re.findall(r"\{[\s\S]*\}", text)
        if obj_matches:
            try:
                obj = json.loads(obj_matches[-1])
            except json.JSONDecodeError:
                obj = None
        steps_raw = obj.get("steps") if isinstance(obj, dict) else None
        if not isinstance(steps_raw, list) or not steps_raw:
            raise DependencyParseError(f"no 'steps' list in window response: {text!r}")

        deps: Dict[str, List[int]] = {}
        ops: Dict[str, Optional[str]] = {}
        seen: set = set()
        for entry in steps_raw:
            if not isinstance(entry, dict):
                continue
            try:
                idx = int(entry.get("index"))
            except (TypeError, ValueError):
                continue
            if idx not in indices or idx in seen:
                continue
            seen.add(idx)
            dep_raw = entry.get("dependencies")
            if dep_raw is None:
                dep_raw = entry.get("deps", [])
            if isinstance(dep_raw, str):
                dep_str = dep_raw
            else:
                dep_str = json.dumps(dep_raw)
            # step idx can only depend on prior steps (0..idx-1)
            deps[str(idx)] = cls._parse_dependency_list(dep_str, idx - 1)
            op_raw = entry.get("op_type", entry.get("operation_type"))
            op_type = op_raw.strip().lower() if isinstance(op_raw, str) else None
            ops[str(idx)] = op_type if op_type in cls.OP_TYPE_LABELS else None

        missing = [i for i in indices if str(i) not in deps]
        if missing:
            raise DependencyParseError(
                f"window response missing steps {missing}: {text!r}"
            )
        return deps, ops

    def _fallback_per_step(
        self,
        path,
        indices: List[int],
        texts: List[str],
        window_history: str,
        total: int,
        llm,
    ) -> Tuple[Dict[str, List[int]], Dict[str, Optional[str]]]:
        """Per-step fallback reusing the proven base ``_annotate_step``.

        For step at position ``p`` in the window, its history = the window's
        prior-steps history + the window steps before position ``p`` (so each
        step still sees all of 1..i-1, matching the base annotator).
        """
        deps: Dict[str, List[int]] = {}
        ops: Dict[str, Optional[str]] = {}
        for p, idx in enumerate(indices):
            history = window_history
            for q in range(p):
                prev_idx = indices[q]
                history += f"\nStep {prev_idx}:\n{texts[q]}\n"
            _, d, op_type = self._annotate_step(
                path, idx, total, texts[p], history, llm
            )
            deps[str(idx)] = d
            ops[str(idx)] = op_type
        return deps, ops

    def _annotate_windows_parallel(
        self,
        path,
        windows: List[Tuple[List[int], List[str], str]],
        total: int,
        step_workers: int,
        llm=None,
    ) -> Tuple[Dict[str, List[int]], Dict[str, Optional[str]]]:
        """Parallel-over-windows mirror of the base ``_annotate_steps_parallel``.

        A shared injected ``llm`` is safe across threads (``query`` is stateless);
        otherwise each worker thread gets its own ``LLM``. ``_annotate_window``
        swallows parse errors via per-step fallback, so a future only raises on
        a hard LLM-call failure.
        """
        thread_state = threading.local()

        def get_llm():
            if llm is not None:
                return llm
            if not hasattr(thread_state, "llm"):
                thread_state.llm = LLM(self.config_path)
            return thread_state.llm

        def work(indices, texts, history):
            return self._annotate_window(path, indices, texts, history, total, get_llm())

        window_results: Dict[str, List[int]] = {}
        op_results: Dict[str, Optional[str]] = {}
        errors: List[Tuple[List[int], Exception]] = []
        max_workers = min(step_workers, len(windows))
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(work, idx, txt, hist): idx for idx, txt, hist in windows
            }
            for future in as_completed(futures):
                indices = futures[future]
                try:
                    d, o = future.result()
                    window_results.update(d)
                    op_results.update(o)
                except Exception as exc:  # noqa: BLE001
                    errors.append((indices, exc))
                    logger.exception(
                        "failed to annotate %s window %s: %s", path, indices, exc
                    )
        if errors:
            first_indices, first_error = sorted(errors, key=lambda x: min(x[0]))[0]
            raise RuntimeError(
                f"failed to annotate {path} window {first_indices}"
            ) from first_error
        return window_results, op_results


# ============================================================================
# Evolve agent - v2 chunk framework (annotate V21 + contrastive V2 + evolve V2)
# + native-tools bridge (same as v5, since v2 produces main.sh/intro.json)
# ============================================================================


class EvolveAgentV21:
    """The evolve agent: annotate (batch-3) → contrastive (v2) → evolve (v2),
    then refresh the native-tool registration files (manifest/runtime/config).

    Mirrors v5's ``EvolveAgent`` but swaps in v2's chunk components on the
    evolve side and the v2.1 batch-3 annotator on the annotate side.
    """

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
        long_obs_threshold: int = LONG_OBS_THRESHOLD_DEFAULT,
        hotspot_min_occurrences: int = HOTSPOT_MIN_OCCURRENCES_DEFAULT,
        hotspot_min_total_chars: int = HOTSPOT_MIN_TOTAL_CHARS_DEFAULT,
        min_reduction_ratio: float = MIN_REDUCTION_RATIO_DEFAULT,
        annotate_window_size: int = DEFAULT_ANNOTATE_WINDOW_SIZE,
    ):
        self.scripts_dir = Path(scripts_dir)
        self.config_path = str(config_path)
        self.mini_swe_agent_dir = Path(mini_swe_agent_dir)
        self.batch_size = int(batch_size)
        self.max_observation_chars = int(max_observation_chars)
        self.workers = int(workers)
        self.dry_run = bool(dry_run)
        self.output_dir = Path(output_dir) if output_dir else None
        self.long_obs_threshold = int(long_obs_threshold)
        self.hotspot_min_occurrences = int(hotspot_min_occurrences)
        self.hotspot_min_total_chars = int(hotspot_min_total_chars)
        self.min_reduction_ratio = float(min_reduction_ratio)
        self.annotate_window_size = int(annotate_window_size)

    def annotate(self, run_dir: Path, task: Optional[str] = None) -> None:
        """Stage 1: batch-3 annotate dependencies + op_type + brief_obs + step_meta."""
        logger.info("[v2.1 evolve] annotate %s", run_dir)
        ann = ChunkTrajectoryAnnotatorV21(
            self.config_path,
            workers=self.workers,
            long_obs_threshold=self.long_obs_threshold,
        )
        ann.WINDOW_SIZE = self.annotate_window_size
        ann.run(run_dir, task=task)

    def contrastive(self, run_dir: Path, task: Optional[str] = None) -> None:
        """Stage 2: v2 phase-based chunk contrastive (graph + obs + cost_hotspot)."""
        logger.info("[v2.1 evolve] contrastive %s", run_dir)
        builder = ChunkContrastiveSampleBuilderV2(
            min_reduction_ratio=self.min_reduction_ratio,
            hotspot_min_occurrences=self.hotspot_min_occurrences,
            hotspot_min_total_chars=self.hotspot_min_total_chars,
        )
        builder.run(run_dir, task=task)

    def evolve(self, run_dir: Path, cycle: Optional[int] = None, task: Optional[str] = None) -> Path:
        """Stage 3: v2 chunk evolver rewrites main.sh/intro.json, then bridge.

        ``cycle`` isolates the evolve output (prompts/trajectories/``.done``
        sentinels) into a per-cycle subdir ``evolve_logs/cycle_{N}/``. This is
        essential for the loop to actually iterate: with a shared ``evolve_logs``
        dir + ``resume=True``, cycles 2+ would see cycle 1's
        ``evolve_batch_*.done`` sentinels and skip every batch, so the tools would
        never refine. Per-cycle dirs let each cycle's evolve run fresh while still
        resuming an interrupted cycle.
        """
        out_dir = self.output_dir
        if cycle is not None and out_dir is not None:
            out_dir = out_dir / f"cycle_{cycle}"
        logger.info(
            "[v2.1 evolve] evolve scripts from %s -> %s (cycle=%s, logs=%s)",
            run_dir, self.scripts_dir, cycle, out_dir,
        )
        evolver = ChunkScriptEvolverV2(
            scripts_dir=self.scripts_dir,
            runner=MiniSweAgentRunnerV2(
                mini_swe_agent_dir=self.mini_swe_agent_dir,
                llm_config=self.config_path,
                dry_run=self.dry_run,
            ),
            prompt_builder=ChunkEvolvePromptBuilderV2(
                serializer=TrajectorySerializer(
                    max_observation_chars=self.max_observation_chars
                ),
            ),
            batch_size=self.batch_size,
            output_dir=out_dir,
            resume=True,
        )
        output_dir = evolver.run(run_dir, task=task)
        self.bridge_to_native_tools()
        return output_dir

    def bridge_to_native_tools(self) -> None:
        """Convert evolved scripts -> native function tools (manifest/runtime/config).

        Idempotent. v2 produces the same ``main.sh``/``intro.json`` format as
        v5, so this is identical to v5's bridge. The rollout run script also
        redeploys via ``evolve_scripts_deploy``; doing it here keeps the
        tool-registration files in sync right after each evolve.
        """
        if self.dry_run:
            logger.info("[v2.1 evolve] DRY_RUN - skipping native-tools bridge")
            return
        api_type = _llm_api_type(self.config_path)
        maxtok = _max_completion_tokens()
        paths = deploy_native_tools(
            self.scripts_dir,
            api_type=api_type,
            max_completion_tokens=maxtok,
            container=True,
        )
        logger.info(
            "[v2.1 evolve] native tools bridged: %s",
            {k: str(v) for k, v in paths.items()},
        )


# ============================================================================
# The cycle (mirrors EvolveV5Cycle; only the evolve agent changes)
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
class V21Report:
    benchmark: str
    n_cycles: int
    scripts_dir: str
    cycles: List[CycleReport] = field(default_factory=list)


class EvolveV21Cycle:
    """Orchestrate the N-cycle rollout ↔ evolve loop using v2's chunk framework
    and the v2.1 batch-3 annotator."""

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
        long_obs_threshold: int = LONG_OBS_THRESHOLD_DEFAULT,
        hotspot_min_occurrences: int = HOTSPOT_MIN_OCCURRENCES_DEFAULT,
        hotspot_min_total_chars: int = HOTSPOT_MIN_TOTAL_CHARS_DEFAULT,
        min_reduction_ratio: float = MIN_REDUCTION_RATIO_DEFAULT,
        annotate_window_size: int = DEFAULT_ANNOTATE_WINDOW_SIZE,
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
            benchmark,
            self.config_path,
            n_tasks=n_tasks,
            n_concurrent=n_concurrent,
            n_attempts=n_attempts,
            taskdir_root=self.work_dir / "taskdirs",
        )
        self.evolve_agent = EvolveAgentV21(
            self.scripts_dir,
            self.config_path,
            self.mini_swe_agent_dir,
            batch_size=batch_size,
            max_observation_chars=max_observation_chars,
            workers=workers,
            output_dir=self.work_dir / "evolve_logs",
            dry_run=dry_run,
            long_obs_threshold=long_obs_threshold,
            hotspot_min_occurrences=hotspot_min_occurrences,
            hotspot_min_total_chars=hotspot_min_total_chars,
            min_reduction_ratio=min_reduction_ratio,
            annotate_window_size=annotate_window_size,
        )

    # ----- case ids -----

    def _load_case_ids(self) -> List[str]:
        if self.eval_cases_file and self.eval_cases_file.exists():
            ids = [
                ln.strip()
                for ln in self.eval_cases_file.read_text(encoding="utf-8").splitlines()
                if ln.strip() and not ln.strip().startswith("#")
            ]
            if ids:
                return ids
            logger.warning(
                "[v2.1] eval-cases-file %s is empty; sampling from source",
                self.eval_cases_file,
            )
        src = _bench_source_task_dir(self.benchmark)
        if src and src.exists():
            ids = sorted(p.name for p in src.iterdir() if p.is_dir())[:16]
            logger.info("[v2.1] sampled %d case ids from %s", len(ids), src)
            return ids
        raise ValueError(
            f"no case ids: provide --eval-cases-file or a discoverable source task dir for {self.benchmark}"
        )

    # ----- main loop -----

    def run(self) -> V21Report:
        report = V21Report(
            benchmark=self.benchmark,
            n_cycles=self.n_cycles,
            scripts_dir=str(self.scripts_dir),
        )
        logger.info(
            "[v2.1] start: benchmark=%s cycles=%d cases=%d scripts=%s window=%d",
            self.benchmark,
            self.n_cycles,
            len(self.case_ids),
            self.scripts_dir,
            self.evolve_agent.annotate_window_size,
        )

        for cycle in range(1, self.n_cycles + 1):
            logger.info("[v2.1] === cycle %d/%d ===", cycle, self.n_cycles)
            rollout = self._do_rollout(cycle)
            annotated = self._safe(
                self.evolve_agent.annotate, rollout.run_dir, label="annotate"
            )
            contrastive_built = self._safe(
                self.evolve_agent.contrastive, rollout.run_dir, label="contrastive"
            )
            evolved = self._safe(
                self.evolve_agent.evolve, rollout.run_dir, cycle, label="evolve"
            )
            report.cycles.append(
                CycleReport(
                    cycle=cycle,
                    rollout=rollout,
                    annotated=annotated,
                    contrastive_built=contrastive_built,
                    evolved=evolved,
                )
            )
            self._save_report(report)
            logger.info(
                "[v2.1] cycle %d done: annotate=%s contrastive=%s evolve=%s",
                cycle,
                annotated,
                contrastive_built,
                evolved,
            )

        logger.info(
            "[v2.1] finished %d cycles. scripts=%s", self.n_cycles, self.scripts_dir
        )
        return report

    def _do_rollout(self, cycle: int) -> RolloutResult:
        # Cycle 1 may reuse a pre-existing baseline (e.g. prep no-tools T0)
        # instead of rolling out - lets the loop start from existing trajectories.
        if cycle == 1 and self.baseline_dir and self.baseline_dir.exists():
            logger.info("[v2.1] cycle 1 reusing baseline trajectories: %s", self.baseline_dir)
            return RolloutResult(self.baseline_dir, "baseline", 1, len(self.case_ids))
        run_id = f"v21c{cycle}-{self.benchmark}-{os.getpid()}"
        return self.rollout_agent.rollout(
            self.scripts_dir, self.case_ids, run_id, cycle, dry_run=self.dry_run
        )

    def _safe(self, fn, *args, label: str) -> bool:
        """Run one stage; swallow exceptions so a single failure doesn't kill the loop."""
        try:
            fn(*args)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.exception("[v2.1] %s failed: %s", label, exc)
            return False

    def _save_report(self, report: V21Report) -> None:
        path = self.work_dir / "v21_report.json"
        path.write_text(
            json.dumps(
                {
                    "benchmark": report.benchmark,
                    "n_cycles": report.n_cycles,
                    "scripts_dir": report.scripts_dir,
                    "cycles": [
                        {
                            "cycle": c.cycle,
                            "run_dir": str(c.rollout.run_dir),
                            "run_id": c.rollout.run_id,
                            "n_cases": c.rollout.n_cases,
                            "annotated": c.annotated,
                            "contrastive_built": c.contrastive_built,
                            "evolved": c.evolved,
                            "notes": c.notes,
                        }
                        for c in report.cycles
                    ],
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )


# ============================================================================
# CLI
# ============================================================================


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--benchmark", required=True, choices=list(BENCHMARKS))
    parser.add_argument(
        "--config", default=str(ROOT / "_config" / "deepseekv4_flash.yaml")
    )
    parser.add_argument("--scripts-dir", default=str(DEFAULT_SCRIPTS_DIR))
    parser.add_argument("--work-dir", default=None)
    parser.add_argument("--mini-swe-agent-dir", default=str(DEFAULT_MINI_SWE_AGENT))
    parser.add_argument(
        "--eval-cases-file",
        default=None,
        help="one case id per line; if omitted, sample 16 from the source task dir",
    )
    parser.add_argument(
        "--baseline-dir",
        default=None,
        help="cycle-1 rollout reuse: an existing trajectory run dir (e.g. a no-tools T0)",
    )
    parser.add_argument("--n-cycles", type=int, default=DEFAULT_N_CYCLES)
    parser.add_argument("--n-tasks", type=int, default=1000)
    parser.add_argument("--n-concurrent", type=int, default=8)
    parser.add_argument("--n-attempts", type=int, default=1)
    parser.add_argument(
        "--batch-size", type=int, default=2, help="contrastive samples per evolve prompt"
    )
    parser.add_argument("--max-observation-chars", type=int, default=1000)
    parser.add_argument(
        "--workers", type=int, default=8, help="annotate LLM parallelism"
    )
    parser.add_argument(
        "--annotate-window-size",
        type=int,
        default=DEFAULT_ANNOTATE_WINDOW_SIZE,
        help="consecutive steps annotated in one LLM call (the v2.1 speedup; default %(default)s)",
    )
    parser.add_argument(
        "--long-obs-threshold",
        type=int,
        default=LONG_OBS_THRESHOLD_DEFAULT,
        help="observation chars above which brief_obs is annotated (default %(default)s)",
    )
    parser.add_argument(
        "--hotspot-min-occurrences",
        type=int,
        default=HOTSPOT_MIN_OCCURRENCES_DEFAULT,
        help="min same-verb occurrences in one trajectory for a cost_hotspot (default %(default)s)",
    )
    parser.add_argument(
        "--hotspot-min-total-chars",
        type=int,
        default=HOTSPOT_MIN_TOTAL_CHARS_DEFAULT,
        help="min cumulative observation chars for a cost_hotspot (default %(default)s)",
    )
    parser.add_argument(
        "--min-reduction-ratio",
        type=float,
        default=MIN_REDUCTION_RATIO_DEFAULT,
        help="mini-vs-original chunk step reduction to emit a graph contrastive sample (default %(default)s)",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--log-file", default=None)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(
        description="Evolve v2.1 cycle: v5's rollout ↔ evolve loop around v2's chunk framework + batch-3 annotator."
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="run the full N-cycle loop")
    _add_common(p_run)

    p_bridge = sub.add_parser(
        "refresh", help="(re)build native-tool artifacts from scripts_dir"
    )
    p_bridge.add_argument("--scripts-dir", required=True)
    p_bridge.add_argument(
        "--config", default=str(ROOT / "_config" / "deepseekv4_flash.yaml")
    )
    p_bridge.add_argument("--dry-run", action="store_true")

    args = parser.parse_args(argv)
    _setup_logging(getattr(args, "log_file", None))

    if args.cmd == "run":
        cycle = EvolveV21Cycle(
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
            long_obs_threshold=args.long_obs_threshold,
            hotspot_min_occurrences=args.hotspot_min_occurrences,
            hotspot_min_total_chars=args.hotspot_min_total_chars,
            min_reduction_ratio=args.min_reduction_ratio,
            annotate_window_size=args.annotate_window_size,
        )
        cycle.run()
    elif args.cmd == "refresh":
        EvolveAgentV21(
            scripts_dir=args.scripts_dir,
            config_path=args.config,
            mini_swe_agent_dir=DEFAULT_MINI_SWE_AGENT,
            dry_run=args.dry_run,
        ).bridge_to_native_tools()


if __name__ == "__main__":
    main()
