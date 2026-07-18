"""Evolve v6.1 — focused DAG slices drive complete harness evolution.

Differs from v5 in what the evolve agent produces:

* v5: evolve agent edits ``<tool>/main.sh`` + ``<tool>/intro.json``; a converter
  then builds a manifest + bash dispatch (``evolve_tools`` reads the manifest).
* v6.1: evolve agent (bash-only mini-swe-agent) writes ``tools.json`` (the tool
  registry), ``executor.py`` (the dispatcher), and ``instruction.md`` (generic
  cost-saving behavior). There is no converter, manifest, or per-tool
  ``main.sh``. The stable ``evolve_tools_v6`` registry runtime loads the first
  two at agent start; rollout wiring injects the instruction file.

One cycle::

    1. rollout     mini-swe-agent with the CURRENT tools.json+executor.py → trajectories
    2. annotate    LLM-annotate dependencies + op_type + op_state, prefix-only
    3. contrastive extract focused DAG signals, with bounded phase fallback
    4. evolve      update tools.json + executor.py + instruction.md in a staged batch
    5. gate        hard-check + LLM-judge the batch diff; promote or exact rollback
    6. deploy      refresh the native registry runtime after all samples are accounted for

Cycle 1 may reuse a ``--baseline-dir`` (e.g. a no-tools T0) instead of rolling
out. The rollout container picks up the complete regenerated harness via
bind-mount (three artifacts + ``.runtime/`` + config yaml).

Usage::

    python -m src.evolve.evolve_v6_1_cycle run \\
        --benchmark swebench --config _config/deepseekv4_flash.yaml \\
        --eval-cases-file <cases.txt> --baseline-dir <prep dir> \\
        --scripts-dir .evolve_scripts_v6_1_swebench --work-dir results/v6_1_cycle/swebench
"""

from __future__ import annotations

import argparse
import copy
import difflib
import hashlib
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from src.tools.llm import LLM

from .evolver import (
    MiniSweAgentRunner,
    ScriptEvolver,
    TrajectorySerializer,
)
from .annotator import TrajectoryAnnotator
from ._chunk_helpers import (
    bash_verb,
    classify_step_meta,
    extract_bash_command,
    identify_phases,
    observation_chars,
)
from .native_tools_v6 import (
    deploy as deploy_v6,
    seed as seed_v6,
    validate as validate_v6,
)
from .run_evolve import DEFAULT_MINI_SWE_AGENT, _setup_logging

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCRIPTS_DIR = ROOT / ".evolve_scripts_v6_1"
DEFAULT_WORK_DIR = ROOT / "results" / "v6_1_cycle"
DEFAULT_N_CYCLES = 4

_FORBIDDEN_ORACLE_ACTION = re.compile(
    # Keep this path-specific. Generic repository tests/ and validate.py files
    # are legitimate SWE evidence; only DAB's answer/private-verifier paths
    # (plus explicitly named ground-truth files) are oracle evidence.
    r"(?:ground[_-]?truth(?:\.csv)?"
    r"|(?:^|[/\\])app[/\\]dab[/\\]query[/\\]validate\.py(?:$|[^A-Za-z0-9_.-])"
    r"|(?:^|[/\\])tests[/\\]dab_query(?:[/\\]|$))",
    re.IGNORECASE,
)


def _step_has_forbidden_oracle_action(step: dict) -> bool:
    """Return whether a trajectory step tries to inspect private evaluation data."""
    for call in step.get("tool_calls") or []:
        if not isinstance(call, dict):
            continue
        arguments = call.get("arguments") or {}
        text = json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str)
        if _FORBIDDEN_ORACLE_ACTION.search(text):
            return True
    return False


def _result_proves_failed_without_output(result: Any) -> bool:
    """Return whether a tool result proves that an oracle probe exposed nothing.

    ATIF stores shell results as a JSON string in ``result.content``.  Stay
    conservative when the result is absent or malformed: only a recorded
    non-zero return code together with empty stdout proves that the attempted
    lookup could not have supplied oracle data.
    """
    if not isinstance(result, Mapping):
        return False
    payload: Any = result
    content = result.get("content")
    if isinstance(content, Mapping):
        payload = content
    elif isinstance(content, str):
        try:
            decoded = json.loads(content)
        except json.JSONDecodeError:
            return False
        if isinstance(decoded, Mapping):
            payload = decoded
        else:
            return False
    if not isinstance(payload, Mapping):
        return False
    returncode = payload.get("returncode")
    if not isinstance(returncode, (int, float)) or returncode == 0:
        return False
    exposed_output = any(
        str(payload.get(key) or "").strip()
        for key in ("output", "stdout", "output_head")
    )
    return not exposed_output


def _step_has_forbidden_oracle_exposure(step: dict) -> bool:
    """Return whether an oracle-seeking action may actually have exposed data.

    Failed, empty probes are not baseline contamination.  They remain oracle
    *attempts*, however, so ``_sample_has_forbidden_oracle_action`` continues to
    discard any focused sample containing them.
    """
    results = (step.get("observation") or {}).get("results") or []
    for index, call in enumerate(step.get("tool_calls") or []):
        if not isinstance(call, dict):
            continue
        arguments = call.get("arguments") or {}
        text = json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str)
        if not _FORBIDDEN_ORACLE_ACTION.search(text):
            continue
        if index < len(results) and _result_proves_failed_without_output(results[index]):
            continue
        return True
    return False


def _sample_has_forbidden_oracle_action(data: dict) -> bool:
    for key in ("negative_sample", "positive_sample"):
        sample = data.get(key) or {}
        if any(_step_has_forbidden_oracle_action(step) for step in sample.get("steps") or []
               if isinstance(step, dict)):
            return True
    return False


def _run_has_forbidden_oracle_action(run_dir: Path) -> bool:
    for path in Path(run_dir).glob("**/agent/trajectory.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if any(_step_has_forbidden_oracle_exposure(step) for step in data.get("steps") or []
               if isinstance(step, dict)):
            logger.error("[v6.1] forbidden oracle exposure found in %s", path)
            return True
    return False


# ============================================================================
# v6.1 annotation — prefix-only dependencies + op_type + op_state
# ============================================================================


class TrajectoryAnnotatorV61(TrajectoryAnnotator):
    """Annotate each action without exposing its current/future trajectory tail."""

    OP_STATE_LABELS = ("success", "fail")
    EXECUTION_MODES = ("exact-global", "legacy")
    CHECKPOINT_SCHEMA = "v6.1-exact-annotation-checkpoint.1"
    METRICS_SCHEMA = "v6.1-exact-annotation-metrics.1"
    EQUIVALENCE_CONTRACT = (
        "all action steps are annotated",
        "each step uses the unchanged v6.1 system/history/user prompt",
        "history for step i contains exactly raw action steps 1..i-1",
        "dependencies, op_type, and op_state are produced by the same LLM call",
        "final trajectory dependencies and step_meta schema are unchanged",
    )

    SYSTEM_PROMPT = (
        "You annotate dependency relations, operation type, AND execution state of "
        "trajectory steps. Step 0 is the initial state. For current step i, return "
        "every previous step index needed to generate it. Include a failed previous "
        "step when observing that failure caused the current action.\n"
        "Classify op_type as exactly one of read, write, verify, explore.\n"
        "Classify op_state as exactly one of:\n"
        "  success — the operator executed as intended and produced a usable result;\n"
        "  fail — the operator failed or produced an unusable result, including a bad "
        "path, missing file/tool/environment, non-zero command result, or failed test/debug.\n"
        'Output ONLY JSON: {"dependencies":[int,...],"op_type":"label",'
        '"op_state":"success|fail"}.'
    )

    def __init__(
        self,
        config_path,
        workers: int = 1,
        retry_failed: int = 1,
        *,
        execution_mode: str = "exact-global",
        checkpoint: bool = True,
        llm_factory: Optional[Callable[[], object]] = None,
    ):
        super().__init__(config_path, workers=workers, retry_failed=retry_failed)
        if execution_mode not in self.EXECUTION_MODES:
            raise ValueError(
                f"unknown v6.1 annotation execution mode {execution_mode!r}; "
                f"expected one of {self.EXECUTION_MODES}"
            )
        self.execution_mode = execution_mode
        self.checkpoint_enabled = bool(checkpoint)
        self._llm_factory = llm_factory
        self.last_run_metrics: dict = {}
        self._metric_step_keys: set = set()

    def annotate_dir(self, result_dir, task=None):
        """Run exact annotation and retain timing/checkpoint metrics.

        ``exact-global`` changes scheduling only.  It deliberately reuses the
        existing per-step prompt builder and parser, so optimized and legacy
        execution submit the same independent LLM request for every action.
        """
        started = time.monotonic()
        self._metric_step_keys = set()
        self.last_run_metrics = {
            "schema_version": self.METRICS_SCHEMA,
            "execution_mode": self.execution_mode,
            "checkpoint_enabled": self.checkpoint_enabled,
            "equivalence_contract": list(self.EQUIVALENCE_CONTRACT),
            "batch_attempts": 0,
            "trajectory_files_seen": 0,
            "trajectory_files_completed": 0,
            "llm_calls": 0,
            "checkpoint_hits": 0,
            "failed_step_attempts": 0,
        }
        try:
            return super().annotate_dir(result_dir, task=task)
        finally:
            self.last_run_metrics["unique_action_steps"] = len(self._metric_step_keys)
            self.last_run_metrics["elapsed_seconds"] = round(
                time.monotonic() - started, 6
            )

    # Stage interface: the base class stores a function alias, so the subclass
    # must rebind it explicitly for the optimized annotate_dir override.
    run = annotate_dir

    def _build_step_inputs(self, action_steps):
        """History for step i contains exactly action steps 1..i-1."""
        texts = [self._step_text(step) for step in action_steps]
        inputs = []
        for i, current in enumerate(texts, start=1):
            previous = [f"Step {j}:\n{texts[j - 1]}" for j in range(1, i)]
            history = (
                f"Previous trajectory steps (only steps 1 through {i - 1}):\n"
                + ("\n\n".join(previous) if previous else "(none)")
            )
            inputs.append((i, current, history))
        return inputs

    @staticmethod
    def _step_user_prompt(i: int, current_step_text: str) -> str:
        """The canonical v6.1 user prompt, shared by both execution modes."""
        return (
            f"Current step {i}:\n{current_step_text}\n\n"
            f"Return step {i}'s dependencies, op_type, and op_state as JSON."
        )

    def _annotate_step(self, path, i, total, current_step_text, history, llm):
        logger.info("[v6.1 annotate] %s step %d/%d", path, i, total)
        user_prompt = self._step_user_prompt(i, current_step_text)
        raw = llm.query(self.SYSTEM_PROMPT, history, user_prompt)
        deps, op_type = self._parse_dependency_and_op_type(raw, i - 1)
        obj = self._last_json_object(raw)
        state_raw = obj.get("op_state") if isinstance(obj, dict) else None
        op_state = state_raw.strip().lower() if isinstance(state_raw, str) else None
        state_source = "llm"
        if op_state not in self.OP_STATE_LABELS:
            op_state = self._fallback_op_state(current_step_text)
            state_source = "rule_fallback"
        return i, deps, {
            "op_type": op_type,
            "op_state": op_state,
            "op_state_source": state_source,
        }

    @staticmethod
    def _last_json_object(text):
        decoder = json.JSONDecoder()
        objects = []
        for match in re.finditer(r"\{", text or ""):
            try:
                value, _ = decoder.raw_decode((text or "")[match.start():])
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                objects.append(value)
        return objects[-1] if objects else None

    @staticmethod
    def _fallback_op_state(current_step_text: str) -> str:
        text = current_step_text or ""
        returncodes = [int(x) for x in re.findall(r"returncode:\s*(-?\d+)", text)]
        if returncodes:
            return "success" if all(code == 0 for code in returncodes) else "fail"
        lowered = text.lower()
        markers = (
            "no such file or directory", "file not found", "command not found",
            "permission denied", "traceback (most recent call last)", "exception_info:",
        )
        return "fail" if any(marker in lowered for marker in markers) else "success"

    @staticmethod
    def _write_step_meta(action_steps, annotations) -> None:
        for i, step in enumerate(action_steps, start=1):
            annotation = annotations.get(str(i)) or {}
            if not isinstance(annotation, dict):
                annotation = {"op_type": annotation}
            rule = classify_step_meta(step)
            meta = step.get("step_meta")
            if not isinstance(meta, dict):
                meta = {}
                step["step_meta"] = meta
            op_type = annotation.get("op_type")
            meta["op_type"] = op_type or rule["op_type"]
            meta["op_type_source"] = "llm" if op_type else "rule_fallback"
            meta["op_state"] = annotation.get("op_state", "success")
            meta["op_state_source"] = annotation.get(
                "op_state_source", "rule_fallback"
            )

    def _run_batch(self, paths):
        """Dispatch one retry round using legacy or exact-global execution."""
        self.last_run_metrics["batch_attempts"] = (
            self.last_run_metrics.get("batch_attempts", 0) + 1
        )
        if self.execution_mode == "legacy":
            return super()._run_batch(paths)
        return self._run_batch_exact_global(paths)

    def _run_batch_exact_global(self, paths):
        """Flatten pending ``(trajectory, step)`` requests into one worker pool.

        The request payload for every step is byte-for-byte the payload used by
        :meth:`annotate_file`.  Successful structured results are appended to a
        per-trajectory checkpoint.  A file-level retry therefore submits only
        missing steps instead of discarding already completed LLM work.
        """
        pending = []
        for path in paths:
            path = Path(path)
            if self.is_annotated(path):
                logger.info("[v6.1 annotate] skip fully annotated %s", path)
                self._remove_checkpoint(path)
            else:
                pending.append(path)
        if not pending:
            return []

        self.last_run_metrics["trajectory_files_seen"] = (
            self.last_run_metrics.get("trajectory_files_seen", 0) + len(pending)
        )
        annotator_fingerprint = self._annotator_fingerprint()
        states = [
            self._prepare_exact_file_state(path, annotator_fingerprint)
            for path in pending
        ]

        jobs = []
        for state in states:
            for i, current, history in state["step_inputs"]:
                key = (str(state["path"].resolve()), state["input_hashes"][i])
                self._metric_step_keys.add(key)
                if i not in state["results"]:
                    jobs.append((i, str(state["path"]), state, current, history))
        # Step-major ordering distributes early/late work across trajectories
        # while the single executor enforces the configured global call cap.
        jobs.sort(key=lambda item: (item[0], item[1]))
        self.last_run_metrics["checkpoint_hits"] = (
            self.last_run_metrics.get("checkpoint_hits", 0)
            + sum(len(state["results"]) for state in states)
        )
        logger.info(
            "[v6.1 annotate] exact-global files=%d unique_steps=%d "
            "checkpoint_hits=%d submitted=%d workers=%d",
            len(states),
            sum(len(state["step_inputs"]) for state in states),
            sum(len(state["results"]) for state in states),
            len(jobs),
            min(self.workers, len(jobs)) if jobs else 0,
        )

        thread_state = threading.local()

        def get_llm():
            if not hasattr(thread_state, "llm"):
                thread_state.llm = (
                    self._llm_factory() if self._llm_factory is not None
                    else LLM(self.config_path)
                )
            return thread_state.llm

        def annotate_job(job):
            i, _path_key, state, current, history = job
            return state, self._annotate_step(
                state["path"], i, len(state["step_inputs"]), current, history, get_llm()
            )

        errors_by_path: Dict[Path, List[Tuple[int, Exception]]] = defaultdict(list)
        if jobs:
            self.last_run_metrics["llm_calls"] = (
                self.last_run_metrics.get("llm_calls", 0) + len(jobs)
            )
            with ThreadPoolExecutor(max_workers=min(self.workers, len(jobs))) as pool:
                futures = {pool.submit(annotate_job, job): job for job in jobs}
                for future in as_completed(futures):
                    job = futures[future]
                    i, _path_key, state, _current, _history = job
                    try:
                        result_state, (_index, deps, annotation) = future.result()
                    except Exception as exc:  # noqa: BLE001
                        errors_by_path[state["path"]].append((i, exc))
                        self.last_run_metrics["failed_step_attempts"] = (
                            self.last_run_metrics.get("failed_step_attempts", 0) + 1
                        )
                        logger.exception(
                            "[v6.1 annotate] failed %s step %d: %s",
                            state["path"], i, exc,
                        )
                        continue
                    record = {
                        "dependencies": deps,
                        "annotation": annotation,
                    }
                    with result_state["lock"]:
                        result_state["results"][i] = record
                        self._append_checkpoint(result_state, i, record)

        failures = []
        for state in states:
            missing = [
                i for i, _current, _history in state["step_inputs"]
                if i not in state["results"]
            ]
            if missing:
                failures.append(state["path"])
                logger.warning(
                    "[v6.1 annotate] %s incomplete; missing steps=%s",
                    state["path"], missing,
                )
                continue
            self._finalize_exact_file_state(state)
            self.last_run_metrics["trajectory_files_completed"] = (
                self.last_run_metrics.get("trajectory_files_completed", 0) + 1
            )
        return failures

    def _prepare_exact_file_state(self, path: Path, annotator_fingerprint: str) -> dict:
        data = json.loads(path.read_text(encoding="utf-8"))
        action_steps = self._extract_action_steps(data)
        step_inputs = self._build_step_inputs(action_steps)
        input_hashes = {
            i: self._input_hash(i, current, history)
            for i, current, history in step_inputs
        }
        state = {
            "path": path,
            "checkpoint_path": self._checkpoint_path(path),
            "annotator_fingerprint": annotator_fingerprint,
            "data": data,
            "action_steps": action_steps,
            "step_inputs": step_inputs,
            "input_hashes": input_hashes,
            "results": {},
            "lock": threading.Lock(),
        }
        if self.checkpoint_enabled:
            state["results"].update(self._load_checkpoint(state))
        return state

    def _annotator_fingerprint(self) -> str:
        safe_config = {}
        try:
            cfg = LLM._load_config(self.config_path)
            safe_config = {
                key: cfg.get(key)
                for key in (
                    "llm_name", "model", "temperature", "api_type",
                    "max_output_tokens",
                )
            }
        except Exception:  # noqa: BLE001 - missing config is valid for injected test LLMs
            safe_config = {"config_unavailable": True}
        payload = {
            "schema": self.CHECKPOINT_SCHEMA,
            "system_prompt": self.SYSTEM_PROMPT,
            "safe_model_config": safe_config,
        }
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()

    def _input_hash(self, i: int, current: str, history: str) -> str:
        payload = [
            self.SYSTEM_PROMPT,
            history,
            self._step_user_prompt(i, current),
        ]
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _checkpoint_path(path: Path) -> Path:
        return path.with_name("annotation_v61.checkpoint.jsonl")

    def _load_checkpoint(self, state: dict) -> Dict[int, dict]:
        checkpoint_path = state["checkpoint_path"]
        if not checkpoint_path.exists():
            return {}
        results: Dict[int, dict] = {}
        try:
            lines = checkpoint_path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            logger.warning("[v6.1 annotate] cannot read checkpoint %s: %s", checkpoint_path, exc)
            return {}
        for line in lines:
            try:
                record = json.loads(line)
                i = int(record.get("step"))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if record.get("schema_version") != self.CHECKPOINT_SCHEMA:
                continue
            if record.get("annotator_fingerprint") != state["annotator_fingerprint"]:
                continue
            if state["input_hashes"].get(i) != record.get("input_hash"):
                continue
            deps = record.get("dependencies")
            annotation = record.get("annotation")
            if not isinstance(deps, list) or not isinstance(annotation, dict):
                continue
            if annotation.get("op_state") not in self.OP_STATE_LABELS:
                continue
            results[i] = {
                "dependencies": deps,
                "annotation": annotation,
            }
        return results

    def _append_checkpoint(self, state: dict, i: int, record: dict) -> None:
        if not self.checkpoint_enabled:
            return
        checkpoint_path = state["checkpoint_path"]
        checkpoint_record = {
            "schema_version": self.CHECKPOINT_SCHEMA,
            "annotator_fingerprint": state["annotator_fingerprint"],
            "step": i,
            "input_hash": state["input_hashes"][i],
            **record,
        }
        try:
            with checkpoint_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(checkpoint_record, ensure_ascii=False) + "\n")
        except OSError as exc:
            # The in-memory result can still complete this run; checkpoint I/O
            # failure must not change annotation semantics.
            logger.warning("[v6.1 annotate] cannot append checkpoint %s: %s", checkpoint_path, exc)

    def _finalize_exact_file_state(self, state: dict) -> None:
        dependencies = {"0": []}
        annotations = {}
        for i in range(1, len(state["action_steps"]) + 1):
            record = state["results"][i]
            dependencies[str(i)] = record["dependencies"]
            annotations[str(i)] = record["annotation"]
        data = state["data"]
        data["dependencies"] = dependencies
        self._write_step_meta(state["action_steps"], annotations)
        self._atomic_write_json(state["path"], data)
        self._remove_checkpoint(state["path"])
        logger.info("[v6.1 annotate] finalized %s", state["path"])

    @staticmethod
    def _atomic_write_json(path: Path, data: dict) -> None:
        tmp = path.with_name(
            f".{path.name}.v61-{os.getpid()}-{threading.get_ident()}.tmp"
        )
        try:
            tmp.write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            try:
                tmp.chmod(path.stat().st_mode & 0o777)
            except OSError:
                pass
            os.replace(tmp, path)
        finally:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass

    def _remove_checkpoint(self, path: Path) -> None:
        if not self.checkpoint_enabled:
            return
        try:
            self._checkpoint_path(path).unlink()
        except FileNotFoundError:
            pass

    def write_metrics(self, path: Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write_json(path, self.last_run_metrics)
        return path

    @classmethod
    def is_annotated(cls, path) -> bool:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            action_steps = cls._extract_action_steps(data)
            deps = data.get("dependencies")
            if not isinstance(deps, dict):
                return False
            if not all(str(i) in deps for i in range(1, len(action_steps) + 1)):
                return False
            return all(
                (step.get("step_meta") or {}).get("op_type")
                in cls.OP_TYPE_LABELS
                and (step.get("step_meta") or {}).get("op_state")
                in cls.OP_STATE_LABELS
                for step in action_steps
            )
        except Exception:
            return False


# ============================================================================
# v6.1 contrastive construction — focused DAG signals + phase fallback
# ============================================================================


class DAGContrastiveSampleBuilderV61:
    """Extract small, optimization-focused slices instead of full trajectory pairs.

    High-confidence DAG signals are preferred: prunable regions and mergeable
    siblings. Behavioral/cost signals expose repeated failure, repeated operator
    families, and long observations. If none exist, bounded op_type phases provide
    coverage. At most three samples are emitted per source trajectory.
    """

    name = "contrastive_v6_1"
    MAX_SIGNALS_PER_TRAJECTORY = 3
    MAX_CONTEXT_BEFORE = 2
    MAX_CONTEXT_AFTER = 1
    MAX_TARGET_STEPS = 5
    MAX_MERGE_GROUP = 4
    MAX_PHASE_STEPS = 12
    LONG_OBSERVATION_CHARS = 4000
    HOTSPOT_MIN_OCCURRENCES = 3
    HOTSPOT_MIN_OBSERVATION_CHARS = 2000

    @staticmethod
    def _is_action_step(step) -> bool:
        return bool(step.get("tool_calls") or "observation" in step or step.get("action"))

    def find_trajectory_files(self, result_dir, task=None):
        files = sorted(Path(result_dir).glob("**/agent/trajectory.json"))
        return [path for path in files if not task or task in str(path)]

    def build_dir(self, result_dir, task=None):
        outputs = []
        for path in self.find_trajectory_files(result_dir, task):
            try:
                outputs.extend(self.build_file(path))
            except Exception as exc:
                logger.exception("[v6.1 contrastive] failed for %s: %s", path, exc)
        return outputs

    run = build_dir

    def build_file(self, path) -> List[Path]:
        path = Path(path)
        trajectory = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(trajectory.get("dependencies"), dict):
            raise ValueError("trajectory has no dependencies; run v6.1 annotate first")
        actions = [
            step for step in trajectory.get("steps", []) if self._is_action_step(step)
        ]
        if not actions:
            return []
        deps = self._normalize_dependencies(trajectory.get("dependencies", {}), len(actions))
        keep = self._trace_global_minimal(deps, len(actions))
        candidates = []
        candidates.extend(self._skippable_candidates(actions, deps, keep))
        candidates.extend(self._mergeable_candidates(actions, deps, keep))
        candidates.extend(self._failure_pivot_candidates(actions, deps))
        candidates.extend(self._long_observation_candidates(actions, deps, keep))
        candidates.extend(self._hotspot_candidates(actions, deps, keep))
        if candidates:
            candidates = self._select_diverse(candidates)
        else:
            candidates = self._phase_fallback_candidates(actions, deps, keep)

        task_description = self._task_description(trajectory)
        for stale in path.parent.glob("contrastive_v61_*.json"):
            stale.unlink()
        outputs = []
        for number, candidate in enumerate(candidates, start=1):
            sample = {
                "schema_version": "v6.1",
                "type": candidate["type"],
                "optimization_target": candidate["optimization_target"],
                "evidence_status": candidate["evidence_status"],
                "source_trajectory": str(path),
                "task_description": task_description,
                "signal": candidate["signal"],
                "negative_sample": candidate["negative_sample"],
                "positive_sample": candidate["positive_sample"],
            }
            out = path.with_name(
                f"contrastive_v61_{number:02d}_{candidate['type'].removeprefix('v61_')}.json"
            )
            out.write_text(
                json.dumps(sample, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            outputs.append(out)
        logger.info(
            "[v6.1 contrastive] %s -> %d focused sample(s), actions=%d minimal=%d",
            path,
            len(outputs),
            len(actions),
            len(keep),
        )
        return outputs

    @staticmethod
    def _normalize_dependencies(raw: dict, n_actions: int) -> Dict[int, List[int]]:
        deps = {0: []}
        for i in range(1, n_actions + 1):
            values = []
            for value in raw.get(str(i), []):
                try:
                    dep = int(value)
                except (TypeError, ValueError):
                    continue
                if 0 <= dep < i and dep not in values:
                    values.append(dep)
            if 0 not in values:
                values.append(0)
            deps[i] = values
        return deps

    @staticmethod
    def _trace_global_minimal(deps: Dict[int, List[int]], n_actions: int) -> set:
        keep = set()
        stack = [n_actions]
        while stack:
            node = stack.pop()
            if node in keep or node < 0 or node > n_actions:
                continue
            keep.add(node)
            stack.extend(deps.get(node, []))
        keep.discard(0)
        return keep

    @staticmethod
    def _step_meta(step: dict) -> dict:
        rule = classify_step_meta(step)
        existing = step.get("step_meta") or {}
        meta = {**rule, **existing}
        if "op_state" not in meta:
            meta["op_state"] = "success" if meta.get("success", True) else "fail"
        extra_files = []
        for call in step.get("tool_calls") or []:
            for key, value in (call.get("arguments") or {}).items():
                if isinstance(value, str) and any(
                    token in key.lower() for token in ("file", "path", "cwd", "dir")
                ):
                    extra_files.append(value)
        meta["files_touched"] = sorted(
            set(meta.get("files_touched", [])) | set(extra_files)
        )
        return meta

    @classmethod
    def _operator_family(cls, step: dict) -> str:
        families = []
        for call in step.get("tool_calls") or []:
            name = str(call.get("function_name") or "unknown")
            if name == "bash":
                verb = bash_verb(extract_bash_command({"tool_calls": [call]}))
                name = f"bash:{verb or 'shell'}"
            families.append(name)
        if not families:
            return "action"
        return "+".join(sorted(set(families)))

    @staticmethod
    def _segments(indices: Sequence[int]) -> List[List[int]]:
        segments: List[List[int]] = []
        for index in sorted(indices):
            if segments and index == segments[-1][-1] + 1:
                segments[-1].append(index)
            else:
                segments.append([index])
        return segments

    @classmethod
    def _representatives(cls, indices: Sequence[int], actions: Sequence[dict]) -> List[int]:
        indices = sorted(set(indices))
        if len(indices) <= cls.MAX_TARGET_STEPS:
            return indices
        chosen = {indices[0], indices[1], indices[-2], indices[-1]}
        ranked = sorted(
            indices,
            key=lambda i: observation_chars(actions[i - 1].get("observation", "")),
            reverse=True,
        )
        for index in ranked:
            chosen.add(index)
            if len(chosen) >= cls.MAX_TARGET_STEPS:
                break
        return sorted(chosen)

    @classmethod
    def _parents(cls, nodes: Sequence[int], deps: Dict[int, List[int]], exclude=()) -> List[int]:
        excluded = set(exclude)
        parents = sorted({
            dep for node in nodes for dep in deps.get(node, [])
            if dep > 0 and dep not in excluded
        })
        return parents[-cls.MAX_CONTEXT_BEFORE:]

    @classmethod
    def _consumers(
        cls,
        nodes: Sequence[int],
        deps: Dict[int, List[int]],
        n_actions: int,
        allowed: Optional[set] = None,
    ) -> List[int]:
        node_set = set(nodes)
        after = max(node_set) if node_set else 0
        consumers = []
        for index in range(after + 1, n_actions + 1):
            if allowed is not None and index not in allowed:
                continue
            if node_set & set(deps.get(index, [])):
                consumers.append(index)
                if len(consumers) >= cls.MAX_CONTEXT_AFTER:
                    break
        return consumers

    @classmethod
    def _trajectory_slice(
        cls, actions: Sequence[dict], indices: Sequence[int], deps: Dict[int, List[int]]
    ) -> dict:
        ordered = sorted(set(i for i in indices if 1 <= i <= len(actions)))
        steps = []
        for index in ordered:
            step = copy.deepcopy(actions[index - 1])
            step["_display_index"] = index
            steps.append(step)
        return {
            "steps": steps,
            "source_step_indices": ordered,
            "dependencies": {str(i): deps.get(i, []) for i in ordered},
        }

    @staticmethod
    def _trajectory_from_pairs(pairs: Sequence[Tuple[object, dict]], deps: dict) -> dict:
        steps = []
        indices = []
        for display_index, original in pairs:
            step = copy.deepcopy(original)
            step["_display_index"] = display_index
            steps.append(step)
            indices.append(display_index)
        return {"steps": steps, "source_step_indices": indices, "dependencies": deps}

    @classmethod
    def _skippable_candidates(cls, actions, deps, keep):
        candidates = []
        pruned = [i for i in range(1, len(actions) + 1) if i not in keep]
        for segment in cls._segments(pruned):
            if len(segment) < 2:
                continue
            shown = cls._representatives(segment, actions)
            before = cls._parents(segment, deps, exclude=segment)
            after = cls._consumers(segment, deps, len(actions), allowed=keep)
            if not after:
                after = [i for i in sorted(keep) if i > segment[-1]][:1]
            negative_indices = sorted(set(before + shown + after))
            positive_indices = sorted(set(before + after))
            saved_chars = sum(
                observation_chars(actions[i - 1].get("observation", "")) for i in segment
            )
            candidates.append({
                "type": "v61_skippable",
                "optimization_target": "instruction",
                "evidence_status": "dependency_validated",
                "score": 120 + 12 * len(segment) + min(saved_chars // 1000, 30),
                "signal": {
                    "reason": "These steps are outside the final action's dependency closure.",
                    "target_step_indices": segment,
                    "shown_target_step_indices": shown,
                    "omitted_target_step_indices": sorted(set(segment) - set(shown)),
                    "estimated_steps_saved": len(segment),
                    "estimated_observation_chars_saved": saved_chars,
                },
                "negative_sample": cls._trajectory_slice(actions, negative_indices, deps),
                "positive_sample": cls._trajectory_slice(actions, positive_indices, deps),
            })
        return candidates

    @classmethod
    def _mergeable_candidates(cls, actions, deps, keep):
        groups = defaultdict(list)
        for index in sorted(keep):
            meta = cls._step_meta(actions[index - 1])
            key = (
                tuple(sorted(set(deps.get(index, [])))),
                meta.get("op_type"),
                meta.get("op_state"),
            )
            groups[key].append(index)
        candidates = []
        for (signature, grouped_op_type, grouped_state), members in groups.items():
            for offset in range(0, len(members), cls.MAX_MERGE_GROUP):
                group = members[offset:offset + cls.MAX_MERGE_GROUP]
                if len(group) < 2:
                    continue
                metas = [cls._step_meta(actions[i - 1]) for i in group]
                if grouped_state != "success":
                    continue
                if not all(cls._structured_mergeable(actions[i - 1]) for i in group):
                    continue
                if grouped_op_type == "write" and not cls._disjoint_known_files(metas):
                    continue
                parents = [i for i in signature if i > 0][-cls.MAX_CONTEXT_BEFORE:]
                consumers = cls._consumers(group, deps, len(actions), allowed=keep)
                negative_indices = sorted(set(parents + group + consumers))
                merged = cls._merge_operator_group(group, actions, deps)
                pairs = [(i, actions[i - 1]) for i in parents]
                pairs.append(("+".join(map(str, group)), merged))
                pairs.extend((i, actions[i - 1]) for i in consumers)
                candidates.append({
                    "type": "v61_mergeable",
                    "optimization_target": "tools",
                    "evidence_status": "dependency_validated",
                    "score": 150 + 25 * (len(group) - 1),
                    "signal": {
                        "reason": "Successful sibling operators have identical direct dependencies.",
                        "target_step_indices": group,
                        "shared_dependencies": list(signature),
                        "operator_family": cls._operator_family(actions[group[0] - 1]),
                        "estimated_steps_saved": len(group) - 1,
                    },
                    "negative_sample": cls._trajectory_slice(actions, negative_indices, deps),
                    "positive_sample": cls._trajectory_from_pairs(
                        pairs, {str(group[0]): list(signature)}
                    ),
                })
        return candidates

    @staticmethod
    def _structured_mergeable(step: dict) -> bool:
        calls = step.get("tool_calls")
        observation = step.get("observation")
        return (
            isinstance(calls, list) and bool(calls)
            and isinstance(observation, dict)
            and isinstance(observation.get("results"), list)
        )

    @staticmethod
    def _disjoint_known_files(metas: Sequence[dict]) -> bool:
        seen = set()
        for meta in metas:
            files = set(meta.get("files_touched", []))
            if not files or seen & files:
                return False
            seen.update(files)
        return True

    @classmethod
    def _merge_operator_group(cls, group, actions, deps):
        steps = [actions[i - 1] for i in group]
        merged = copy.deepcopy(steps[0])
        merged["tool_calls"] = [
            copy.deepcopy(call) for step in steps for call in step.get("tool_calls", [])
        ]
        merged["observation"] = {
            "results": [
                copy.deepcopy(result)
                for step in steps
                for result in step.get("observation", {}).get("results", [])
            ]
        }
        merged["message"] = "\n".join(
            str(step.get("message") or "").strip()
            for step in steps if str(step.get("message") or "").strip()
        )
        merged.pop("metrics", None)
        merged["merged_from_step_indices"] = list(group)
        merged["shared_dependencies"] = deps.get(group[0], [])
        meta = cls._step_meta(merged)
        meta.update({
            "op_state": "success",
            "operator_merged": True,
            "merged_operator_count": len(group),
        })
        merged["step_meta"] = meta
        return merged

    @classmethod
    def _failure_pivot_candidates(cls, actions, deps):
        failed = [
            i for i, step in enumerate(actions, start=1)
            if cls._step_meta(step).get("op_state") == "fail"
        ]
        candidates = []
        for run in cls._segments(failed):
            if len(run) < 2:
                continue
            pivot = run[-1] + 1
            if pivot > len(actions) or cls._step_meta(actions[pivot - 1]).get("op_state") != "success":
                continue
            shown = cls._representatives(run, actions)
            before = cls._parents(run, deps, exclude=run)
            if not before and run[0] > 1:
                before = [run[0] - 1]
            negative_indices = sorted(set(before + shown + [pivot]))
            positive_indices = sorted(set(before + [pivot]))
            candidates.append({
                "type": "v61_failure_pivot",
                "optimization_target": "instruction",
                "evidence_status": "behavioral_diagnostic",
                "score": 140 + 20 * len(run),
                "signal": {
                    "reason": "Repeated failed operators occurred immediately before a successful pivot.",
                    "target_step_indices": run,
                    "shown_target_step_indices": shown,
                    "pivot_step_index": pivot,
                    "failed_operator_families": sorted({
                        cls._operator_family(actions[i - 1]) for i in run
                    }),
                    "minimal_slice_note": "Desired focus view for a give-up/pivot rule; not claimed as a replayed execution.",
                },
                "negative_sample": cls._trajectory_slice(actions, negative_indices, deps),
                "positive_sample": cls._trajectory_slice(actions, positive_indices, deps),
            })
        return candidates

    @classmethod
    def _long_observation_candidates(cls, actions, deps, keep):
        candidates = []
        for index, step in enumerate(actions, start=1):
            if index not in keep:
                continue
            size = observation_chars(step.get("observation", ""))
            if size <= cls.LONG_OBSERVATION_CHARS:
                continue
            before = cls._parents([index], deps)
            after = cls._consumers([index], deps, len(actions), allowed=keep)
            negative_indices = sorted(set(before + [index] + after))
            positive = cls._trajectory_slice(actions, negative_indices, deps)
            state = cls._step_meta(step).get("op_state")
            for rendered in positive["steps"]:
                if rendered.get("_display_index") == index:
                    rendered["observation"] = {
                        "results": [{"content": json.dumps({
                            "returncode": 0 if state == "success" else 1,
                            "output": f"<target observation budget: summarize/retrieve relevant content from {size} chars>",
                            "exception_info": "" if state == "success" else "preserve original failure state",
                        })}]
                    }
            candidates.append({
                "type": "v61_long_observation",
                "optimization_target": "both",
                "evidence_status": "cost_diagnostic",
                "score": 80 + min(size // 1000, 80),
                "signal": {
                    "reason": "A dependency-relevant operator returned an oversized observation.",
                    "target_step_indices": [index],
                    "observation_chars": size,
                    "minimal_slice_note": "Desired output-budget illustration; not a replayed observation.",
                },
                "negative_sample": cls._trajectory_slice(actions, negative_indices, deps),
                "positive_sample": positive,
            })
        return candidates

    @classmethod
    def _hotspot_candidates(cls, actions, deps, keep):
        groups = defaultdict(list)
        for index, step in enumerate(actions, start=1):
            groups[cls._operator_family(step)].append(index)
        candidates = []
        for family, occurrences in groups.items():
            if len(occurrences) < cls.HOTSPOT_MIN_OCCURRENCES:
                continue
            total_chars = sum(
                observation_chars(actions[i - 1].get("observation", ""))
                for i in occurrences
            )
            if total_chars < cls.HOTSPOT_MIN_OBSERVATION_CHARS:
                continue
            shown = cls._representatives(occurrences, actions)
            negative = cls._trajectory_slice(actions, shown, deps)
            positive = cls._trajectory_slice(actions, shown[:1], deps)
            candidates.append({
                "type": "v61_hotspot",
                "optimization_target": "tools",
                "evidence_status": "cost_diagnostic",
                "score": 70 + 8 * len(occurrences) + min(total_chars // 2000, 40),
                "signal": {
                    "reason": "The same operator family recurs often and accumulates material output cost.",
                    "target_step_indices": occurrences,
                    "shown_target_step_indices": shown,
                    "operator_family": family,
                    "occurrence_count": len(occurrences),
                    "total_observation_chars": total_chars,
                    "minimal_slice_note": "One representative expresses the batching target; it is not a replayed replacement.",
                },
                "negative_sample": negative,
                "positive_sample": positive,
            })
        return candidates

    @classmethod
    def _phase_fallback_candidates(cls, actions, deps, keep):
        metas = {i: cls._step_meta(step) for i, step in enumerate(actions, start=1)}
        phases = identify_phases(
            len(actions), metas, min_phase_size=3, max_phase_size=cls.MAX_PHASE_STEPS
        )
        candidates = []
        for start, end, op_type in phases:
            nodes = list(range(start + 1, end + 1))
            before = cls._parents(nodes, deps, exclude=nodes)
            after = cls._consumers(nodes, deps, len(actions))
            negative_indices = sorted(set(before + nodes + after))
            positive_nodes = [i for i in nodes if i in keep]
            positive_indices = sorted(set(before + positive_nodes + after))
            obs_chars = sum(
                observation_chars(actions[i - 1].get("observation", "")) for i in nodes
            )
            candidates.append({
                "type": "v61_phase_fallback",
                "optimization_target": "instruction" if op_type == "explore" else "both",
                "evidence_status": "bounded_context_fallback",
                "score": len(nodes) * 5 + min(obs_chars // 1000, 30),
                "signal": {
                    "reason": "No higher-confidence DAG signal was found; this bounded semantic phase preserves coverage.",
                    "target_step_indices": nodes,
                    "phase_op_type": op_type,
                    "global_minimal_indices_in_phase": positive_nodes,
                },
                "negative_sample": cls._trajectory_slice(actions, negative_indices, deps),
                "positive_sample": cls._trajectory_slice(actions, positive_indices, deps),
            })
        return sorted(candidates, key=lambda c: c["score"], reverse=True)[:cls.MAX_SIGNALS_PER_TRAJECTORY]

    @classmethod
    def _select_diverse(cls, candidates):
        by_type = defaultdict(list)
        for candidate in candidates:
            by_type[candidate["type"]].append(candidate)
        for values in by_type.values():
            values.sort(key=lambda c: c["score"], reverse=True)
        priority = (
            "v61_mergeable", "v61_failure_pivot", "v61_skippable",
            "v61_long_observation", "v61_hotspot",
        )
        selected = []
        for kind in priority:
            if by_type.get(kind):
                selected.append(by_type[kind].pop(0))
                if len(selected) >= cls.MAX_SIGNALS_PER_TRAJECTORY:
                    return selected
        remaining = sorted(
            [candidate for values in by_type.values() for candidate in values],
            key=lambda c: c["score"],
            reverse=True,
        )
        selected.extend(remaining[:cls.MAX_SIGNALS_PER_TRAJECTORY - len(selected)])
        return selected

    @staticmethod
    def _task_description(trajectory: dict) -> str:
        for step in trajectory.get("steps", []):
            if step.get("source") == "user" and step.get("message"):
                text = " ".join(str(step["message"]).split())
                return text[:1000] + ("..." if len(text) > 1000 else "")
        return ""


# ============================================================================
# v6.1 evolve prompt — complete harness, existing Markdown structure retained
# ============================================================================


class PromptBudgetExceededV61(ValueError):
    """The complete batch cannot fit; callers must split it, never drop evidence."""

    def __init__(self, actual_chars: int, max_chars: int, sample_paths: Sequence[Path]):
        self.actual_chars = int(actual_chars)
        self.max_chars = int(max_chars)
        self.sample_paths = tuple(Path(path) for path in sample_paths)
        super().__init__(
            f"v6.1 prompt is {self.actual_chars} chars, above the "
            f"{self.max_chars}-char budget for {len(self.sample_paths)} sample(s)"
        )


class EvolvePromptBuilderV61:
    """Build a bounded Markdown prompt for all three harness artifacts.

    The evolve agent has ONLY a bash tool. It edits three files in its cwd:

    * ``tools.json``  — JSON list of function-tool schemas
      ``[{"name","description","parameters":{"type":"object","properties":{...},"required":[...]}}, ...]``.
    * ``executor.py`` — Python with ``def run_tool(action, cwd=None, timeout=120)``
      returning ``{"output","returncode","exception_info"}``, dispatching by
      ``action["tool"]``.
    * ``instruction.md`` — generic behavioral cost-saving policy.

    These two files must stay in sync (every tool in tools.json has a branch in
    executor.py's run_tool, and vice versa). The rollout mini-swe-agent loads
    them as native function tools via the generic ``evolve_tools_v6`` runtime.
    """

    HEADER = [
        "# Evolve task (v6.1 — write native function tools directly)",
        "",
        "You are evolving the COMPLETE DOWNSTREAM HARNESS for a mini-swe-agent:",
        "tools.json, executor.py, and instruction.md. The rollout agent calls registered",
        "tools BY NAME with structured JSON parameters (and always retains a bash fallback).",
        "Your goal is to lower total steps and tokens while preserving task success by:",
        "1. adding, fixing, merging, or removing GENERIC structured tools that collapse",
        "   repeated operations and control observation size;",
        "2. keeping tools.json schemas and executor.py behavior synchronized and robust;",
        "3. improving instruction.md with reusable rules for batching, tool choice,",
        "   give-up/pivot, early exit, and validation risk; and",
        "4. making no change when evidence is weak or an optimization is already covered.",
        "Optimize the harness as a whole, not merely the number of tools.",
        "",
        "## You edit exactly THREE files in the current working directory",
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
        "- Reference answers, ground-truth files, query-specific validators, verifier tests, and",
        "  their outputs are FORBIDDEN oracle evidence. Never create tools or instructions from",
        "  actions that inspect them; such samples must be discarded rather than optimized.",
        "- A test-running tool (e.g. `run-tests`) MUST accept an optional `cwd` (pass it",
        "  through to subprocess.run) and MUST auto-detect Django: if `runtests.py` exists in",
        "  `cwd` (check `<cwd>/tests/runtests.py` and `<cwd>/runtests.py`), run tests via",
        "  `python runtests.py <labels>` from that dir; otherwise use `python -m pytest`.",
        "  Never assume the current dir is the repo root — always honor `cwd`.",
        "- Do NOT create a build / package / install tool (e.g. `build-package`, `pip install`,",
        "  `setup.py build/develop`). Building to test a change is slow and fails often; run the",
        "  targeted tests directly with `run-tests` instead.",
        "- After editing, VERIFY the registration files:",
        '    python -c "import json; json.load(open(\'tools.json\'))"',
        '    python -c "import ast; ast.parse(open(\'executor.py\').read())"',
        "- Do NOT create main.sh, intro.json, or per-tool directories — v6.1 uses ONLY",
        "  tools.json + executor.py + instruction.md.",
        "",
        "### 3. `instruction.md` — HIGH-LEVEL BEHAVIORAL RULES (≤ 25 short lines)",
        "Write GENERIC, tool-agnostic strategies — NOT tool-specific usage guides.",
        "Focus on FOUR categories:",
        "1. BATCHING: when/how to combine multiple actions into one step.",
        "2. GIVE UP: when to stop retrying a failing approach and pivot.",
        "3. EARLY EXIT: when to commit a best-effort fix without full validation.",
        "4. RISKY MOVES: when to skip environment validation (e.g. tests) and just submit.",
        "Do NOT list tool names, schemas, or parameter details — those live in tools.json.",
        "Update this file when focused DAG evidence reveals a reusable behavioral pattern.",
        "",
        "## How to use the focused DAG evidence",
        "Each Executional History is a LOCAL slice, not a complete task trajectory.",
        "`Optimization target` routes the evidence toward tools, instruction, or both.",
        "`dependency_validated` samples have graph-supported minimal slices; diagnostic",
        "samples are compact design signals and explicitly do not claim replay validation.",
        "Do not infer that omitted trajectory steps never existed; use only the stated signal.",
        "If the samples provide no NEW reusable cost optimization, leave all three artifacts",
        "unchanged. A no-op is preferable to a speculative or case-specific regression.",
    ]

    FOOTER = (
        "\nYour task: evolve tools.json, executor.py, and instruction.md in the current "
        "directory based on the focused DAG samples below. Change only the artifact(s) "
        "supported by each sample's Optimization target; fix broken tools before adding "
        "new ones, merge overlaps, and remove obsolete capabilities. Keep tools.json and "
        "executor.py in sync. Verify registration files and re-read instruction.md. "
        "If there is no new reusable signal, make no changes. "
        "Do not edit the prompt or contrastive-sample files. Finish by saving the files."
    )

    def __init__(
        self,
        serializer: Optional[TrajectorySerializer] = None,
        max_prompt_chars: int = 50000,
    ):
        self.serializer = serializer or TrajectorySerializer()
        self.max_prompt_chars = max(10000, int(max_prompt_chars))

    def build(
        self,
        sample_paths: Iterable[Path],
        cwd_name: str = ".",
        scripts_dir: Optional[Path] = None,
    ) -> str:
        sample_paths = [Path(path) for path in sample_paths]
        parts: List[str] = list(self.HEADER)
        parts.append(f"\nThe current working directory is {cwd_name}; edit tools.json, "
                     f"executor.py, and instruction.md in place here.")
        if scripts_dir is not None:
            parts += self._current_files_block(Path(scripts_dir))
        included = 0
        for path in sample_paths:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            if _sample_has_forbidden_oracle_action(data):
                logger.warning("[v6.1 prompt] discard oracle-contaminated sample %s", path)
                continue
            signal = data.get("signal") or {}
            block = [
                f"\n# Executional History {included + 1}",
                f"Source: {path}",
                f"Signal type: {data.get('type', 'unknown')}",
                f"Optimization target: {data.get('optimization_target', 'both')}",
                f"Evidence status: {data.get('evidence_status', 'unknown')}",
            ]
            if data.get("task_description"):
                block += ["\n## Task Context", data["task_description"]]
            block += [
                "\n## Why This Slice Was Selected",
                json.dumps(signal, ensure_ascii=False, indent=2),
                "\n## Original Trajectory",
                self.serializer.serialize(data["negative_sample"]),
                "\n## Minimal Trajectory Delta",
                self._serialize_minimal_delta(
                    data["negative_sample"], data["positive_sample"]
                ),
            ]
            parts += block
            included += 1
        if not included:
            parts += [
                "\n# Executional Histories",
                "No focused sample fit the prompt budget. Do not change the harness.",
            ]
        parts.append(self.FOOTER)
        prompt = "\n".join(parts)
        if len(prompt) > self.max_prompt_chars:
            raise PromptBudgetExceededV61(
                len(prompt), self.max_prompt_chars, sample_paths
            )
        return prompt

    @staticmethod
    def _action_steps(trajectory: Mapping[str, Any]) -> List[dict]:
        return [
            step for step in trajectory.get("steps", [])
            if isinstance(step, dict) and TrajectorySerializer._is_action_step(step)
        ]

    @staticmethod
    def _display_index(step: Mapping[str, Any], ordinal: int) -> str:
        return str(step.get("_display_index", ordinal))

    def _same_rendered_step(
        self, left: Mapping[str, Any], right: Mapping[str, Any]
    ) -> bool:
        """Compare exactly the action/observation content exposed by the prompt."""
        return self.serializer.serialize({"steps": [left]}) == self.serializer.serialize(
            {"steps": [right]}
        )

    def _serialize_minimal_delta(
        self,
        negative: Mapping[str, Any],
        positive: Mapping[str, Any],
    ) -> str:
        """Render the positive trajectory as a lossless delta over the shown negative.

        Most positive steps are byte-for-byte copies of negative steps. Repeating their
        actions and observations made the old prompt large without adding information.
        The step order plus unchanged/removed/replacement sets below are sufficient to
        reconstruct exactly the action content that the old positive block exposed.
        """
        negative_steps = self._action_steps(negative)
        positive_steps = self._action_steps(positive)
        negative_by_index = {
            self._display_index(step, ordinal): step
            for ordinal, step in enumerate(negative_steps, start=1)
        }
        negative_order = list(negative_by_index)

        minimal_order: List[str] = []
        kept: List[str] = []
        covered_negative: set[str] = set()
        replacements: List[dict] = []
        replacement_labels: List[str] = []

        for ordinal, step in enumerate(positive_steps, start=1):
            index = self._display_index(step, ordinal)
            minimal_order.append(index)
            merged_from = [str(value) for value in step.get("merged_from_step_indices") or []]
            if merged_from:
                covered_negative.update(merged_from)
                replacement_labels.append(
                    f"Step {index} merges original steps {', '.join(merged_from)} into "
                    "one operator; reuse their already-shown actions/observations"
                )
                continue
            original = negative_by_index.get(index)
            if original is not None and self._same_rendered_step(original, step):
                kept.append(index)
                covered_negative.add(index)
                continue
            if original is not None:
                covered_negative.add(index)
                replacement_labels.append(f"Step {index} replaces original step {index}")
            else:
                replacement_labels.append(f"Step {index} is added in the minimal trajectory")
            replacements.append(step)

        removed = [index for index in negative_order if index not in covered_negative]
        lines = [
            "Apply this delta to the Original Trajectory above; unchanged steps are "
            "referenced once by index instead of repeating their actions/observations.",
            f"Original action count: {len(negative_steps)}; minimal action count: "
            f"{len(positive_steps)}.",
            "Minimal step order: " + (", ".join(minimal_order) if minimal_order else "(empty)"),
            "Kept unchanged: " + (", ".join(kept) if kept else "(none)"),
            "Removed from original: " + (", ".join(removed) if removed else "(none)"),
        ]
        if replacement_labels:
            lines += [
                "Transformations:",
                *[f"- {label}" for label in replacement_labels],
            ]
            if replacements:
                lines += [
                    "Changed/added step details:",
                    self.serializer.serialize({"steps": replacements}),
                ]
        else:
            lines.append("Transformations: (none)")
        return "\n".join(lines)

    def _current_files_block(self, scripts_dir: Path) -> List[str]:
        """Show the current complete harness so evolution is incremental."""
        lines = ["\n# Current harness files in this directory"]
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
class RolloutResultV61:
    run_dir: Path
    run_id: str
    cycle: int
    n_cases: int
    returncode: Optional[int] = None


class RolloutAgentV61:
    """Run the benchmark with the current v6 tools (tools.json + executor.py).

    Sets ``EVOLVE_TOOLS_MODE=registry`` so ``scripts/_bench_common.sh:
    evolve_scripts_deploy`` deploys the v6 runtime + config (not the v5 manifest)
    and emits the v6 env vars (``EVOLVE_TOOLS_V6_REGISTRY`` / ``_EXECUTOR``).
    """

    def __init__(self, benchmark: str, config_path, *, n_tasks: int = 1000,
                 n_concurrent: int = 8, n_attempts: int = 1,
                 taskdir_root: Optional[Path] = None,
                 results_root: Optional[Path] = None):
        if benchmark not in BENCHMARKS:
            raise ValueError(f"unknown benchmark: {benchmark} (known: {list(BENCHMARKS)})")
        self.benchmark = benchmark
        self.meta = BENCHMARKS[benchmark]
        self.config_path = str(config_path)
        self.n_tasks = int(n_tasks)
        self.n_concurrent = int(n_concurrent)
        self.n_attempts = int(n_attempts)
        self.taskdir_root = Path(taskdir_root) if taskdir_root else (DEFAULT_WORK_DIR / "taskdirs")
        # The cycle orchestrator points this at a private staging root and then
        # atomically materializes each rollout at ``cycle-N/rollout``.  Keeping
        # the default preserves the standalone RolloutAgent API.
        self.results_root = Path(results_root) if results_root else _results_dir()

    def rollout(self, scripts_dir: Path, case_ids: List[str], run_id: str,
                cycle: int, dry_run: bool = False,
                log_path: Optional[Path] = None) -> RolloutResultV61:
        scripts_dir = Path(scripts_dir)
        env = self._build_env(scripts_dir, case_ids, run_id)
        cmd = ["bash", str(ROOT / "scripts" / self.meta["run_script"])]
        run_dir = self._expected_run_dir(run_id)
        logger.info("[v6.1 rollout] cycle=%d %s run_id=%s cases=%d -> %s",
                    cycle, self.benchmark, run_id, len(case_ids), run_dir)
        if dry_run:
            logger.info("[v6.1 rollout] DRY_RUN — not executing")
            return RolloutResultV61(run_dir, run_id, cycle, len(case_ids), None)
        run_dir.parent.mkdir(parents=True, exist_ok=True)
        if log_path is not None:
            log_path = Path(log_path)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            logger.info("[v6.1 rollout] subprocess output -> %s", log_path)
            with log_path.open("a", encoding="utf-8") as log_handle:
                proc = subprocess.Popen(
                    cmd,
                    env=env,
                    cwd=str(ROOT),
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                returncode = proc.wait()
            try:
                output_tail = log_path.read_text(encoding="utf-8", errors="replace")[-3000:]
            except OSError:
                output_tail = ""
            if output_tail:
                logger.info("[v6.1 rollout] output tail:\n%s", output_tail)
        else:
            proc = subprocess.run(cmd, env=env, cwd=str(ROOT), capture_output=True, text=True)
            returncode = proc.returncode
            if proc.stdout:
                logger.info("[v6.1 rollout] stdout tail:\n%s", proc.stdout[-3000:])
            if proc.stderr:
                logger.info("[v6.1 rollout] stderr tail:\n%s", proc.stderr[-3000:])
        if returncode != 0:
            logger.warning("[v6.1 rollout] run script exited %d (partial failures may be OK)", returncode)
        if not run_dir.exists():
            run_dir = self._resolve_run_dir(run_id) or run_dir
        return RolloutResultV61(run_dir, run_id, cycle, len(case_ids), returncode)

    def _build_env(self, scripts_dir: Path, case_ids: List[str], run_id: str) -> dict:
        env = dict(os.environ)
        env.update({
            "ROOT_DIR": str(ROOT),
            "RESULTS_DIR": str(self.results_root),
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
        if self.benchmark == "dab":
            # Align both the mini-swe bash timeout and native worker deadline to
            # DataAgentBench's original ExecTool limit.
            env.setdefault("EVOLVE_TOOLS_V6_TIMEOUT_SECONDS", "600")
        if self.meta["split"]:
            env["SWE_ATLAS_SPLITS"] = self.meta["split"]
        temp = self._build_temp_task_dir(case_ids, run_id)
        if temp is not None:
            env[self.meta["task_path_env"]] = str(temp)
        return env

    def _build_temp_task_dir(self, case_ids: List[str], run_id: str) -> Optional[Path]:
        src = _bench_source_task_dir(self.benchmark)
        if src is None or not src.exists():
            logger.warning("[v6.1 rollout] no source task dir for %s; running full set", self.benchmark)
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
                logger.warning("[v6.1 rollout] case task dir missing, skip: %s", case_src)
                continue
            try:
                os.symlink(str(case_src.resolve()), str(target_dir / cid))
                n += 1
            except OSError as exc:
                logger.warning("[v6.1 rollout] symlink failed for %s: %s", cid, exc)
        logger.info("[v6.1 rollout] linked %d/%d cases into %s", n, len(case_ids), target_dir)
        return env_value

    def _expected_run_dir(self, run_id: str) -> Path:
        return self.results_root / self.meta["results_subdir"] / run_id

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


V61_HARNESS_FILES = ("tools.json", "executor.py", "instruction.md")


class MiniSweAgentRunnerV61(MiniSweAgentRunner):
    """Pass the complete v6.1 evolve instruction as the actual user prompt.

    The legacy runner asks the agent to ``cat`` a prompt file and mentions the
    obsolete per-tool ``intro.json`` contract.  Besides wasting turns, a long
    shell observation truncates that file.  v6.1 keeps the prompt file only as
    an audit artifact and supplies the exact same text through ``mini -t``.
    """

    def run(self, prompt: str, prompt_path: Path, output_path: Path, cwd: Path) -> None:
        env, model, temperature, model_class = self._load_llm_env()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text(prompt, encoding="utf-8")
        cmd = [
            "uv", "run", "--project", str(self.mini_swe_agent_dir),
            "mini",
            "-m", model,
            "--model-class", model_class,
            "--environment-class", "local",
            "-y", "--exit-immediately",
            "--cost-limit", "0",
            "-o", str(output_path),
            "-t", prompt,
            "-c", "mini.yaml",
        ]
        if temperature is not None:
            cmd += ["-c", f"model.model_kwargs.temperature={temperature}"]
        # Do not dump a 50k prompt (or evidence inside it) into the process log.
        display_cmd = list(cmd)
        display_cmd[display_cmd.index("-t") + 1] = f"<direct-user-prompt:{len(prompt)} chars>"
        logger.info("mini-swe-agent v6.1 cmd: %s", " ".join(
            shlex.quote(value) for value in display_cmd
        ))
        logger.info("saved exact v6.1 user prompt -> %s", prompt_path)
        if self.dry_run:
            return
        self._run_mini_swe(cmd, Path(cwd), {**os.environ, **env})


class LLMJudgeGateV61:
    """Fail-closed semantic promotion gate for one serial evolve modification."""

    SCHEMA_VERSION = "v6.1-llm-judge-gate.1"
    MIN_SCORE = 3
    MAX_DIFF_CHARS = 50000
    SYSTEM_PROMPT = """You are the strict release gate for a reusable agent harness.
The candidate has already been edited in a staging state. Judge only whether its
git-style diff should be promoted. You are not the editor: do not propose a new
patch and do not reward activity by itself. Reject speculative, case-specific,
cost-increasing, contradictory, or correctness-risking changes. Return exactly
one JSON object and no Markdown."""

    RUBRIC = """Evaluate all criteria independently on a 0-4 scale.

1. generality
   - Every promoted behavior must transfer across repositories/tasks.
   - Reject hard-coded project paths, symbols, packages, test names, or a rule
     generalized from one idiosyncratic case.
   - Evidence from one source case is insufficient for a new general tool or
     broad behavioral rule, unless the diff only repairs an objectively broken
     schema/executor contract or removes an existing contradiction.

2. cost_reduction
   - Judge net future API cost, not only shell runtime or raw step count.
   - Account for permanent tool-schema/instruction tokens on every downstream
     turn, output size, retries, and failure recovery.
   - A new tool must collapse a repeated multi-step pattern; reject a one-to-one
     bash wrapper, duplicated capability, or instruction growth without a clear
     reusable saving.

3. correctness
   - tools.json schema and executor.py semantics must agree, including required
     fields, aliases, cwd, timeout, errors, and return shape.
   - Reject unsafe destructive behavior, swallowed failures, hidden oracle use,
     unsupported dependencies, and broad advice to skip necessary verification.

4. consistency
   - Reject overlapping tools, mutually contradictory instructions, stale usage
     guidance, schema/executor mismatch, and rules that conflict with the current
     harness.

5. evidence_alignment_and_minimality
   - Every changed artifact must be supported by the focused evidence and its
     Optimization target.
   - Prefer no-op when the capability already exists or the expected benefit is
     uncertain. Reject unrelated rewrites and unnecessarily large diffs.

Acceptance is fail-closed: decision may be "accept" only if every score is at
least 3 and blocking_issues is empty. Otherwise decision must be "reject".
Return this exact shape:
{
  "decision": "accept|reject",
  "scores": {
    "generality": 0,
    "cost_reduction": 0,
    "correctness": 0,
    "consistency": 0,
    "evidence_alignment_and_minimality": 0
  },
  "blocking_issues": [
    {"criterion": "...", "evidence": "...", "required_fix": "..."}
  ],
  "non_blocking_notes": ["..."],
  "summary": "one concise reason for the promotion decision"
}"""

    SCORE_KEYS = (
        "generality",
        "cost_reduction",
        "correctness",
        "consistency",
        "evidence_alignment_and_minimality",
    )

    def __init__(
        self,
        config_path: str,
        *,
        llm_factory: Optional[Callable[[], Any]] = None,
    ):
        self.config_path = str(config_path)
        self.llm_factory = llm_factory or (lambda: LLM(self.config_path))

    def evaluate(
        self,
        *,
        diff_text: str,
        evidence_digest: str,
        candidate_harness_digest: str,
        source_cases: Sequence[str],
        deterministic_issues: Sequence[str],
        changed_paths: Sequence[str],
    ) -> Tuple[dict, str, str]:
        base = {
            "schema_version": self.SCHEMA_VERSION,
            "source_cases": list(source_cases),
            "source_case_count": len(set(source_cases)),
            "changed_paths": list(changed_paths),
            "diff_chars": len(diff_text),
            "deterministic_issues": list(deterministic_issues),
        }
        if deterministic_issues:
            return ({
                **base,
                "passed": False,
                "decision": "reject",
                "judge_invoked": False,
                "blocking_issues": [
                    {
                        "criterion": "deterministic_validation",
                        "evidence": issue,
                        "required_fix": "repair the candidate before promotion",
                    }
                    for issue in deterministic_issues
                ],
                "summary": "candidate failed deterministic validation",
            }, "", "")
        if not diff_text.strip():
            return ({
                **base,
                "passed": True,
                "decision": "no_change",
                "judge_invoked": False,
                "blocking_issues": [],
                "summary": "candidate made no harness change",
            }, "", "")

        judge_prompt = self._build_prompt(
            diff_text=diff_text,
            evidence_digest=evidence_digest,
            candidate_harness_digest=candidate_harness_digest,
            source_cases=source_cases,
            changed_paths=changed_paths,
        )
        raw_response = ""
        try:
            raw_response = str(self.llm_factory().query(
                self.SYSTEM_PROMPT, "", judge_prompt
            ))
            payload = self._parse_json_object(raw_response)
        except Exception as exc:  # fail closed on transport or malformed output
            logger.exception("[v6.1 gate] judge failed: %s", exc)
            return ({
                **base,
                "passed": False,
                "decision": "reject",
                "judge_invoked": True,
                "blocking_issues": [{
                    "criterion": "judge_protocol",
                    "evidence": str(exc),
                    "required_fix": "rerun the rejected batch with a valid judge response",
                }],
                "summary": "judge call or response parsing failed; fail-closed rollback",
            }, judge_prompt, raw_response)

        scores = payload.get("scores") if isinstance(payload.get("scores"), dict) else {}
        blocking = payload.get("blocking_issues")
        if not isinstance(blocking, list):
            blocking = [{
                "criterion": "judge_protocol",
                "evidence": "blocking_issues is not a JSON list",
                "required_fix": "return the required schema",
            }]
        protocol_issues = []
        normalized_scores = {}
        for key in self.SCORE_KEYS:
            value = scores.get(key)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                protocol_issues.append(f"score {key!r} is missing or non-numeric")
                normalized_scores[key] = 0
            else:
                normalized_scores[key] = value
                if value < 0 or value > 4:
                    protocol_issues.append(f"score {key!r}={value} is outside 0..4")
        for issue in protocol_issues:
            blocking.append({
                "criterion": "judge_protocol",
                "evidence": issue,
                "required_fix": "return the required score schema",
            })
        for key, value in normalized_scores.items():
            if value < self.MIN_SCORE:
                blocking.append({
                    "criterion": key,
                    "evidence": f"judge score {value} is below promotion threshold {self.MIN_SCORE}",
                    "required_fix": "address this criterion in a new candidate; do not promote this diff",
                })
        if payload.get("decision") != "accept" and not blocking:
            blocking.append({
                "criterion": "judge_decision",
                "evidence": payload.get("summary") or "judge declared reject",
                "required_fix": "address the judge's rejection before retrying",
            })
        passed = (
            payload.get("decision") == "accept"
            and not blocking
            and all(normalized_scores[key] >= self.MIN_SCORE for key in self.SCORE_KEYS)
        )
        decision = {
            **base,
            "passed": passed,
            "decision": "accept" if passed else "reject",
            "judge_invoked": True,
            "judge_declared_decision": payload.get("decision"),
            "scores": normalized_scores,
            "blocking_issues": blocking,
            "non_blocking_notes": payload.get("non_blocking_notes", []),
            "summary": payload.get("summary") or (
                "all semantic promotion criteria passed" if passed
                else "one or more semantic promotion criteria failed"
            ),
        }
        return decision, judge_prompt, raw_response

    def _build_prompt(
        self,
        *,
        diff_text: str,
        evidence_digest: str,
        candidate_harness_digest: str,
        source_cases: Sequence[str],
        changed_paths: Sequence[str],
    ) -> str:
        return "\n".join([
            "# Promotion decision",
            self.RUBRIC,
            "",
            "# Batch provenance",
            f"Distinct source cases ({len(set(source_cases))}): "
            + (", ".join(sorted(set(source_cases))) or "(none)"),
            "Changed paths: " + (", ".join(changed_paths) or "(none)"),
            "",
            "# Focused evidence digest",
            evidence_digest,
            "",
            "# Complete candidate harness digest",
            candidate_harness_digest,
            "",
            "# Candidate git diff",
            diff_text,
        ])

    @staticmethod
    def _parse_json_object(text: str) -> dict:
        start = text.find("{")
        if start < 0:
            raise ValueError("judge response contains no JSON object")
        value, _ = json.JSONDecoder().raw_decode(text[start:])
        if not isinstance(value, dict):
            raise ValueError("judge response top level is not a JSON object")
        return value


class ScriptEvolverV61(ScriptEvolver):
    """Run lossless, diversity-ordered v6.1 batches behind a promotion gate."""

    name = "evolve_v6_1"
    SENTINEL_SCHEMA = "v6.1-evolve-batch.2"
    MANIFEST_SCHEMA = "v6.1-evolve-batch-manifest.2"

    def __init__(
        self,
        scripts_dir,
        runner,
        prompt_builder: EvolvePromptBuilderV61,
        gate: LLMJudgeGateV61,
        batch_size: int = 2,
        output_dir: Optional[Path] = None,
        resume: bool = True,
    ):
        super().__init__(
            scripts_dir=scripts_dir,
            runner=runner,
            prompt_builder=prompt_builder,
            batch_size=batch_size,
            output_dir=output_dir,
            resume=resume,
        )
        if self.batch_size < 1:
            raise ValueError("v6.1 batch_size must be positive")
        self.gate = gate

    def find_samples(self, result_dir, task=None) -> List[Path]:
        files = sorted(Path(result_dir).glob("**/agent/contrastive_v61_*.json"))
        if task:
            matched = []
            for path in files:
                case_name = self._case_name(path)
                if case_name == task or case_name.startswith(f"{task}__") or task in case_name:
                    matched.append(path)
            files = matched or [path for path in files if task in str(path)]

        return self.order_samples_for_batches(files)

    @classmethod
    def order_samples_for_batches(cls, files: Sequence[Path]) -> List[Path]:
        """Cluster comparable signals and round-robin independent source cases."""
        by_group_and_case: Dict[Tuple[str, str, str], Dict[str, List[Path]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for path in files:
            by_group_and_case[cls._sample_group_key(path)][cls._case_name(path)].append(path)
        ordered: List[Path] = []
        for group in sorted(by_group_and_case):
            by_case = by_group_and_case[group]
            case_names = sorted(by_case)
            offset = 0
            while True:
                emitted = False
                for case_name in case_names:
                    values = by_case[case_name]
                    if offset < len(values):
                        ordered.append(values[offset])
                        emitted = True
                if not emitted:
                    break
                offset += 1
        return ordered

    def run(self, result_dir, task: Optional[str] = None) -> Path:
        result_dir = Path(result_dir).resolve()
        self.scripts_dir.mkdir(parents=True, exist_ok=True)
        seed_v6(self.scripts_dir)
        output_dir = self.output_dir or (result_dir / "evolve_logs")
        output_dir.mkdir(parents=True, exist_ok=True)
        self._assert_output_outside_scripts(output_dir)

        discovered = self.find_samples(result_dir, task)
        logger.info("[v6.1 evolve] found %d focused samples", len(discovered))
        eligible: List[Path] = []
        discarded_oracle: List[Path] = []
        for path in discovered:
            data = json.loads(path.read_text(encoding="utf-8"))
            if _sample_has_forbidden_oracle_action(data):
                logger.warning("[v6.1 evolve] discard oracle-contaminated sample %s", path)
                discarded_oracle.append(path)
            else:
                eligible.append(path)

        records: List[dict] = []
        cursor = 0
        batch_id = 1
        while cursor < len(eligible):
            sentinel = output_dir / f"evolve_batch_{batch_id}.traj.done"
            resumed = self._resumable_record(sentinel, eligible, cursor)
            if resumed is not None:
                logger.info(
                    "[v6.1 evolve] batch %d already gated (%s); resume",
                    batch_id,
                    resumed.get("decision"),
                )
                records.append(resumed)
                cursor += len(resumed["samples"])
                batch_id += 1
                continue
            if sentinel.exists():
                logger.warning("[v6.1 evolve] stale batch sentinel removed: %s", sentinel)
                sentinel.unlink()

            batch, prompt = self._largest_fitting_batch(eligible, cursor)
            record = self._run_gated_batch(
                batch_id=batch_id,
                batch=batch,
                prompt=prompt,
                output_dir=output_dir,
            )
            self._write_json(sentinel, record)
            records.append(record)
            cursor += len(batch)
            batch_id += 1

        processed = [sample for row in records for sample in row.get("samples", [])]
        expected = [str(path) for path in eligible]
        complete = processed == expected
        manifest = {
            "schema_version": self.MANIFEST_SCHEMA,
            "complete": complete,
            "all_samples_accounted_for": (
                len(processed) + len(discarded_oracle) == len(discovered)
            ),
            "discovered_samples": [str(path) for path in discovered],
            "eligible_samples": expected,
            "processed_samples": processed,
            "discarded_oracle_samples": [str(path) for path in discarded_oracle],
            "batch_size_limit": self.batch_size,
            "batch_count": len(records),
            "accepted_batches": sum(
                row.get("decision") in {"accept", "no_change"} for row in records
            ),
            "rejected_batches": sum(row.get("decision") == "reject" for row in records),
            "batches": records,
        }
        self._write_json(output_dir / "evolve_batch_manifest.json", manifest)
        if not complete:
            raise RuntimeError(
                "v6.1 internal coverage error: not every eligible sample was processed exactly once"
            )
        logger.info(
            "[v6.1 evolve] all %d eligible samples processed in %d gated batches "
            "(%d rejected, %d oracle-discarded)",
            len(eligible), len(records), manifest["rejected_batches"], len(discarded_oracle),
        )
        return output_dir

    def _largest_fitting_batch(
        self, samples: Sequence[Path], cursor: int
    ) -> Tuple[List[Path], str]:
        group = self._sample_group_key(samples[cursor])
        same_group_count = 0
        for path in samples[cursor:cursor + self.batch_size]:
            if self._sample_group_key(path) != group:
                break
            same_group_count += 1
        max_count = max(1, same_group_count)
        last_error: Optional[PromptBudgetExceededV61] = None
        for count in range(max_count, 0, -1):
            batch = list(samples[cursor:cursor + count])
            try:
                prompt = self.prompt_builder.build(
                    batch,
                    cwd_name=self.scripts_dir.name,
                    scripts_dir=self.scripts_dir,
                )
                if count < max_count:
                    logger.info(
                        "[v6.1 evolve] prompt budget split batch at sample %d: %d -> %d; "
                        "remaining evidence is deferred, not dropped",
                        cursor, max_count, count,
                    )
                return batch, prompt
            except PromptBudgetExceededV61 as exc:
                last_error = exc
        assert last_error is not None
        path = samples[cursor]
        raise RuntimeError(
            f"single focused sample cannot fit the prompt budget and was NOT skipped: "
            f"{path} ({last_error.actual_chars}>{last_error.max_chars} chars). "
            "Increase --max-prompt-chars or reduce the bounded serializer caps."
        ) from last_error

    def _run_gated_batch(
        self,
        *,
        batch_id: int,
        batch: Sequence[Path],
        prompt: str,
        output_dir: Path,
    ) -> dict:
        output_path = output_dir / f"evolve_batch_{batch_id}.traj.json"
        prompt_path = output_path.with_suffix(".prompt.md")
        prompt_path.write_text(prompt, encoding="utf-8")
        gate_dir = output_dir / f"evolve_batch_{batch_id}.gate"
        if gate_dir.exists():
            shutil.rmtree(gate_dir)
        gate_dir.mkdir(parents=True)
        before_dir = gate_dir / "harness_before"
        candidate_dir = gate_dir / "harness_candidate"
        self._copy_tree(self.scripts_dir, before_dir)
        before_fingerprint = self._harness_fingerprint(before_dir)

        try:
            self.runner.run(
                prompt=prompt,
                prompt_path=prompt_path,
                output_path=output_path,
                cwd=self.scripts_dir,
            )
        except Exception as exc:
            self._copy_tree(self.scripts_dir, candidate_dir)
            diff_text = self._harness_diff(before_dir, candidate_dir)
            (gate_dir / "harness.diff").write_text(diff_text, encoding="utf-8")
            self._restore_tree(before_dir)
            failure = {
                "schema_version": self.SENTINEL_SCHEMA,
                "batch_id": batch_id,
                "samples": [str(path) for path in batch],
                "prompt_hash": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                "decision": "runner_error",
                "passed": False,
                "error": str(exc),
                "before_fingerprint": before_fingerprint,
                "after_fingerprint": self._harness_fingerprint(self.scripts_dir),
            }
            self._write_json(gate_dir / "decision.json", failure)
            raise

        self._copy_tree(self.scripts_dir, candidate_dir)
        diff_text = self._harness_diff(before_dir, candidate_dir)
        (gate_dir / "harness.diff").write_text(diff_text, encoding="utf-8")
        changed_paths, out_of_scope = self._changed_paths(before_dir, candidate_dir)
        deterministic_issues = self._deterministic_issues(
            candidate_dir, diff_text, out_of_scope
        )
        source_cases = [self._case_name(path) for path in batch]
        evidence_digest = self._evidence_digest(batch)
        decision, judge_prompt, raw_response = self.gate.evaluate(
            diff_text=diff_text,
            evidence_digest=evidence_digest,
            candidate_harness_digest=self._candidate_harness_digest(candidate_dir),
            source_cases=source_cases,
            deterministic_issues=deterministic_issues,
            changed_paths=changed_paths,
        )
        if judge_prompt:
            (gate_dir / "judge.prompt.md").write_text(
                "# System prompt\n\n" + self.gate.SYSTEM_PROMPT
                + "\n\n# User prompt\n\n" + judge_prompt,
                encoding="utf-8",
            )
        if raw_response:
            (gate_dir / "judge.response.txt").write_text(raw_response, encoding="utf-8")

        # Always restore the exact pre-edit tree first. Promotion then overlays
        # only the three allowed artifacts, so caches or accidental extra files
        # can never leak through even when the semantic diff is accepted.
        if decision.get("passed") and decision.get("decision") == "accept":
            self._promote_allowed_files(before_dir, candidate_dir)
        else:
            self._restore_tree(before_dir)
        after_fingerprint = self._harness_fingerprint(self.scripts_dir)
        record = {
            "schema_version": self.SENTINEL_SCHEMA,
            "batch_id": batch_id,
            "samples": [str(path) for path in batch],
            "prompt_hash": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "prompt_chars": len(prompt),
            "source_cases": source_cases,
            "distinct_source_case_count": len(set(source_cases)),
            "decision": decision.get("decision"),
            "passed": bool(decision.get("passed")),
            "gate_decision": str(gate_dir / "decision.json"),
            "before_fingerprint": before_fingerprint,
            "candidate_fingerprint": self._harness_fingerprint(candidate_dir),
            "after_fingerprint": after_fingerprint,
            "changed_paths": changed_paths,
            "summary": decision.get("summary", ""),
            "rejection_reasons": decision.get("blocking_issues", []),
        }
        self._write_json(gate_dir / "decision.json", {**decision, **record})
        if decision.get("decision") == "reject":
            logger.warning(
                "[v6.1 gate] batch %d rejected and rolled back: %s",
                batch_id,
                decision.get("summary"),
            )
        else:
            logger.info(
                "[v6.1 gate] batch %d decision=%s: %s",
                batch_id,
                decision.get("decision"),
                decision.get("summary"),
            )
        return record

    def _resumable_record(
        self, sentinel: Path, samples: Sequence[Path], cursor: int
    ) -> Optional[dict]:
        if not self.resume or not sentinel.exists():
            return None
        try:
            saved = json.loads(sentinel.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        saved_samples = saved.get("samples")
        if (
            saved.get("schema_version") != self.SENTINEL_SCHEMA
            or not isinstance(saved_samples, list)
            or not 1 <= len(saved_samples) <= self.batch_size
            or saved.get("decision") not in {"accept", "reject", "no_change"}
        ):
            return None
        expected = [str(path) for path in samples[cursor:cursor + len(saved_samples)]]
        return saved if saved_samples == expected else None

    def _deterministic_issues(
        self,
        candidate_dir: Path,
        diff_text: str,
        out_of_scope: Sequence[str],
    ) -> List[str]:
        issues = list(validate_v6(candidate_dir))
        if out_of_scope:
            issues.append(
                "candidate changed files outside tools.json/executor.py/instruction.md: "
                + ", ".join(out_of_scope)
            )
        if len(diff_text) > self.gate.MAX_DIFF_CHARS:
            issues.append(
                f"candidate diff is {len(diff_text)} chars, above the "
                f"{self.gate.MAX_DIFF_CHARS}-char minimal-review limit"
            )
        tools_path = candidate_dir / "tools.json"
        executor_path = candidate_dir / "executor.py"
        try:
            tools = json.loads(tools_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            tools = None
        if isinstance(tools, list):
            names: List[str] = []
            for index, tool in enumerate(tools):
                if not isinstance(tool, dict):
                    issues.append(f"tools.json[{index}] is not an object")
                    continue
                name = tool.get("name")
                if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9-]*", name):
                    issues.append(f"tools.json[{index}] has invalid tool name {name!r}")
                else:
                    names.append(name)
                parameters = tool.get("parameters")
                if isinstance(parameters, dict):
                    properties = parameters.get("properties", {})
                    required = parameters.get("required", [])
                    if not isinstance(properties, dict):
                        issues.append(f"tool {name!r} parameters.properties is not an object")
                    if not isinstance(required, list):
                        issues.append(f"tool {name!r} parameters.required is not a list")
                    elif isinstance(properties, dict):
                        unknown = [value for value in required if value not in properties]
                        if unknown:
                            issues.append(
                                f"tool {name!r} requires undefined properties: {unknown}"
                            )
            duplicates = sorted(name for name in set(names) if names.count(name) > 1)
            if duplicates:
                issues.append("duplicate tool names: " + ", ".join(duplicates))
            try:
                executor = executor_path.read_text(encoding="utf-8")
            except OSError:
                executor = ""
            for name in names:
                if not re.search(rf"['\"]{re.escape(name)}['\"]", executor):
                    issues.append(
                        f"tool {name!r} has no literal dispatch/registration in executor.py"
                    )
        instruction = candidate_dir / "instruction.md"
        try:
            nonempty_lines = [
                line for line in instruction.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        except OSError:
            issues.append("instruction.md is missing or unreadable")
        else:
            if len(nonempty_lines) > 25:
                issues.append(
                    f"instruction.md has {len(nonempty_lines)} non-empty lines; v6.1 limit is 25"
                )
        return list(dict.fromkeys(issues))

    def _evidence_digest(self, batch: Sequence[Path]) -> str:
        parts: List[str] = []
        for number, path in enumerate(batch, start=1):
            data = json.loads(path.read_text(encoding="utf-8"))
            negative = EvolvePromptBuilderV61._action_steps(data.get("negative_sample") or {})
            positive = EvolvePromptBuilderV61._action_steps(data.get("positive_sample") or {})
            signal = json.dumps(data.get("signal") or {}, ensure_ascii=False, sort_keys=True)
            if len(signal) > 2000:
                signal = signal[:2000] + "...<truncated>"
            task_context = " ".join(str(data.get("task_description") or "").split())
            if len(task_context) > 500:
                task_context = task_context[:500] + "...<truncated>"
            parts += [
                f"## Evidence {number}",
                f"source_case: {self._case_name(path)}",
                f"signal_type: {data.get('type', 'unknown')}",
                f"optimization_target: {data.get('optimization_target', 'both')}",
                f"evidence_status: {data.get('evidence_status', 'unknown')}",
                f"task_context: {task_context or '(missing)'}",
                f"signal: {signal}",
                f"original_actions: {len(negative)}; minimal_actions: {len(positive)}",
                "original_operator_summary:",
            ]
            for ordinal, step in enumerate(negative, start=1):
                index = EvolvePromptBuilderV61._display_index(step, ordinal)
                meta = step.get("step_meta") or {}
                calls = []
                for call in step.get("tool_calls") or []:
                    arguments = json.dumps(
                        call.get("arguments") or {}, ensure_ascii=False, sort_keys=True,
                        default=str,
                    )
                    if len(arguments) > 300:
                        arguments = arguments[:300] + "..."
                    calls.append(f"{call.get('function_name', '')}({arguments})")
                parts.append(
                    f"- step {index}: op_type={meta.get('op_type', 'unknown')}, "
                    f"op_state={meta.get('op_state', 'unknown')}, calls={' | '.join(calls)}"
                )
        digest = "\n".join(parts)
        return digest if len(digest) <= 12000 else digest[:12000] + "\n...<digest truncated>"

    @staticmethod
    def _candidate_harness_digest(candidate_dir: Path) -> str:
        """Give the judge enough unchanged context to detect overlap/conflicts."""
        candidate_dir = Path(candidate_dir)
        parts: List[str] = []
        tools_path = candidate_dir / "tools.json"
        try:
            tools = json.loads(tools_path.read_text(encoding="utf-8"))
            tools_text = json.dumps(tools, ensure_ascii=False, indent=2, sort_keys=True)
        except (OSError, json.JSONDecodeError) as exc:
            tools_text = f"<unreadable tools.json: {exc}>"
        if len(tools_text) > 12000:
            tools_text = tools_text[:12000] + "\n...<tools.json digest truncated>"
        parts += ["## tools.json", tools_text]

        instruction_path = candidate_dir / "instruction.md"
        try:
            instruction = instruction_path.read_text(encoding="utf-8")
        except OSError as exc:
            instruction = f"<unreadable instruction.md: {exc}>"
        if len(instruction) > 6000:
            instruction = instruction[:6000] + "\n...<instruction.md digest truncated>"
        parts += ["", "## instruction.md", instruction]

        executor_path = candidate_dir / "executor.py"
        try:
            executor = executor_path.read_text(encoding="utf-8")
            tree = compile(executor, str(executor_path), "exec", flags=0, dont_inherit=True,
                           optimize=0)
            del tree
            function_names = re.findall(r"^def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", executor, re.M)
            executor_summary = (
                f"chars={len(executor)}; functions={function_names}; "
                f"sha256={hashlib.sha256(executor.encode('utf-8')).hexdigest()}"
            )
        except (OSError, SyntaxError) as exc:
            executor_summary = f"<unreadable/invalid executor.py: {exc}>"
        parts += ["", "## executor.py summary", executor_summary]
        return "\n".join(parts)

    @staticmethod
    def _case_name(path: Path) -> str:
        return Path(path).parent.parent.name

    @staticmethod
    def _sample_group_key(path: Path) -> Tuple[str, str, str]:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return (
            str(data.get("optimization_target") or "both"),
            str(data.get("type") or "unknown"),
            str(data.get("evidence_status") or "unknown"),
        )

    def _assert_output_outside_scripts(self, output_dir: Path) -> None:
        scripts = self.scripts_dir.resolve()
        output = Path(output_dir).resolve()
        try:
            output.relative_to(scripts)
        except ValueError:
            return
        raise ValueError(
            f"v6.1 evolve output_dir must be outside scripts_dir to keep gate snapshots "
            f"non-recursive: output={output}, scripts={scripts}"
        )

    def _copy_tree(self, source: Path, destination: Path) -> None:
        source = Path(source)
        destination = Path(destination)
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(source, destination, symlinks=True)

    def _restore_tree(self, snapshot: Path) -> None:
        _safe_rmtree(self.scripts_dir)
        shutil.copytree(snapshot, self.scripts_dir, symlinks=True)

    def _promote_allowed_files(self, before_dir: Path, candidate_dir: Path) -> None:
        self._restore_tree(before_dir)
        for name in V61_HARNESS_FILES:
            source = candidate_dir / name
            destination = self.scripts_dir / name
            if source.exists():
                shutil.copy2(source, destination)
            elif destination.exists():
                destination.unlink()

    @staticmethod
    def _file_state(root: Path) -> Dict[str, str]:
        state: Dict[str, str] = {}
        for path in sorted(Path(root).rglob("*")):
            if not (path.is_file() or path.is_symlink()):
                continue
            relative = str(path.relative_to(root))
            if path.is_symlink():
                value = "symlink:" + os.readlink(path)
                state[relative] = hashlib.sha256(value.encode("utf-8")).hexdigest()
            else:
                state[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        return state

    @classmethod
    def _changed_paths(cls, before: Path, candidate: Path) -> Tuple[List[str], List[str]]:
        before_state = cls._file_state(before)
        after_state = cls._file_state(candidate)
        changed = sorted(
            path for path in set(before_state) | set(after_state)
            if before_state.get(path) != after_state.get(path)
        )
        ignored_parts = {"__pycache__", ".pytest_cache"}
        meaningful = [
            path for path in changed
            if not any(part in ignored_parts for part in Path(path).parts)
        ]
        allowed = [path for path in meaningful if path in V61_HARNESS_FILES]
        out_of_scope = [path for path in meaningful if path not in V61_HARNESS_FILES]
        return allowed, out_of_scope

    @staticmethod
    def _harness_diff(before: Path, candidate: Path) -> str:
        chunks: List[str] = []
        for name in V61_HARNESS_FILES:
            before_path = Path(before) / name
            after_path = Path(candidate) / name
            before_text = (
                before_path.read_text(encoding="utf-8", errors="replace")
                if before_path.exists() else ""
            )
            after_text = (
                after_path.read_text(encoding="utf-8", errors="replace")
                if after_path.exists() else ""
            )
            if before_text == after_text:
                continue
            chunks.extend(difflib.unified_diff(
                before_text.splitlines(keepends=True),
                after_text.splitlines(keepends=True),
                fromfile=f"a/{name}",
                tofile=f"b/{name}",
            ))
        return "".join(chunks)

    @staticmethod
    def _harness_fingerprint(root: Path) -> str:
        digest = hashlib.sha256()
        for name in V61_HARNESS_FILES:
            path = Path(root) / name
            digest.update(name.encode("utf-8"))
            digest.update(path.read_bytes() if path.exists() else b"<missing>")
        return digest.hexdigest()

    @staticmethod
    def _write_json(path: Path, data: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


class EvolveAgentV61:
    """Evolve all three harness artifacts from focused DAG evidence."""

    def __init__(self, scripts_dir: Path, config_path: str, mini_swe_agent_dir: Path, *,
                 batch_size: int = 2, max_observation_chars: int = 1000, workers: int = 8,
                 max_prompt_chars: int = 50000,
                 judge_config_path: Optional[str] = None,
                 judge_llm_factory: Optional[Callable[[], Any]] = None,
                 annotation_execution: str = "exact-global",
                 annotation_checkpoint: bool = True,
                 output_dir: Optional[Path] = None, dry_run: bool = False):
        self.scripts_dir = Path(scripts_dir)
        self.config_path = str(config_path)
        self.mini_swe_agent_dir = Path(mini_swe_agent_dir)
        self.batch_size = int(batch_size)
        self.max_observation_chars = int(max_observation_chars)
        self.workers = int(workers)
        self.max_prompt_chars = int(max_prompt_chars)
        self.judge_config_path = str(judge_config_path or config_path)
        self.judge_llm_factory = judge_llm_factory
        self.annotation_execution = annotation_execution
        self.annotation_checkpoint = bool(annotation_checkpoint)
        self.dry_run = bool(dry_run)

        self.output_dir = Path(output_dir) if output_dir else None

    def annotate(
        self,
        run_dir: Path,
        task: Optional[str] = None,
        metrics_path: Optional[Path] = None,
    ) -> Path:
        logger.info("[v6.1 evolve] annotate %s", run_dir)
        annotator = TrajectoryAnnotatorV61(
            self.config_path,
            workers=self.workers,
            execution_mode=self.annotation_execution,
            checkpoint=self.annotation_checkpoint,
        )
        paths = annotator.run(run_dir, task=task)
        if not paths:
            raise RuntimeError(f"no trajectory.json files found under {run_dir}")
        incomplete = [str(path) for path in paths if not annotator.is_annotated(path)]
        if incomplete:
            raise RuntimeError(
                f"v6.1 annotation did not complete {len(incomplete)} trajectory file(s): "
                f"{incomplete[:5]}"
            )
        metrics_path = Path(metrics_path) if metrics_path else (
            Path(run_dir) / "annotation_v61_metrics.json"
        )
        annotator.last_run_metrics["trajectory_files_discovered"] = len(paths)
        annotator.last_run_metrics["all_trajectories_complete"] = True
        annotator.write_metrics(metrics_path)
        logger.info("[v6.1 evolve] annotation metrics -> %s", metrics_path)
        return metrics_path

    def contrastive(self, run_dir: Path, task: Optional[str] = None) -> List[Path]:
        logger.info("[v6.1 evolve] contrastive %s", run_dir)
        outputs = DAGContrastiveSampleBuilderV61().run(run_dir, task=task)
        if not outputs:
            raise RuntimeError(f"no v6.1 contrastive samples were built under {run_dir}")
        logger.info("[v6.1 evolve] built %d focused contrastive samples", len(outputs))
        return outputs

    def evolve(
        self,
        run_dir: Path,
        task: Optional[str] = None,
        output_dir: Optional[Path] = None,
    ) -> Path:
        logger.info("[v6.1 evolve] evolve complete harness from %s -> %s", run_dir, self.scripts_dir)
        effective_output_dir = Path(output_dir) if output_dir else self.output_dir
        evolver = ScriptEvolverV61(
            scripts_dir=self.scripts_dir,
            runner=MiniSweAgentRunnerV61(
                mini_swe_agent_dir=self.mini_swe_agent_dir,
                llm_config=self.config_path,
                dry_run=self.dry_run,
            ),
            prompt_builder=EvolvePromptBuilderV61(
                serializer=TrajectorySerializer(max_observation_chars=self.max_observation_chars),
                max_prompt_chars=self.max_prompt_chars,
            ),
            gate=LLMJudgeGateV61(
                self.judge_config_path,
                llm_factory=self.judge_llm_factory,
            ),
            batch_size=self.batch_size,
            output_dir=effective_output_dir,
            resume=True,
        )
        result_dir = Path(run_dir).resolve()
        samples = evolver.find_samples(result_dir, task)
        if not samples:
            raise RuntimeError(f"no v6.1 contrastive samples found under {run_dir}")
        output_dir = evolver.run(run_dir, task=task)
        manifest_path = Path(output_dir) / "evolve_batch_manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"v6.1 evolve did not produce a valid coverage manifest: {manifest_path}"
            ) from exc
        if not manifest.get("complete") or not manifest.get("all_samples_accounted_for"):
            raise RuntimeError(
                f"v6.1 evolve left unprocessed samples; inspect {manifest_path} and resume"
            )
        self.refresh_registration()
        return output_dir

    def refresh_registration(self) -> None:
        """Re-deploy the v6 runtime + config (idempotent) and soft-validate
        tools.json + executor.py. The evolve agent owns those two files; this
        only ensures the runtime package + config yaml are present and the files
        parse — matching the v6 contract ("evolve agent updates the registration
        files in real time")."""
        if self.dry_run:
            logger.info("[v6.1 evolve] DRY_RUN — skipping refresh/validate")
            return
        api_type = _llm_api_type(self.config_path)
        paths = deploy_v6(self.scripts_dir, api_type=api_type,
                          max_completion_tokens=_max_completion_tokens(), container=True)
        ws = validate_v6(self.scripts_dir)
        for w in ws:
            logger.warning("v6 validate: %s", w)
        if not ws:
            logger.info("[v6.1 evolve] tools.json + executor.py valid; runtime+config at %s",
                        {k: str(v) for k, v in paths.items()})
        else:
            logger.warning("[v6.1 evolve] %d validation warning(s) — rollout may fall back to bash-only", len(ws))


# ============================================================================
# The cycle
# ============================================================================


@dataclass
class CycleReportV61:
    cycle: int
    rollout: RolloutResultV61
    annotated: bool
    contrastive_built: bool
    evolved: bool
    cycle_dir: str = ""
    annotation_metrics: str = ""
    evolve_logs: str = ""
    harness_snapshot: str = ""
    notes: str = ""


@dataclass
class V61Report:
    benchmark: str
    n_cycles: int
    scripts_dir: str
    cycles: List[CycleReportV61] = field(default_factory=list)


class EvolveV61Cycle:
    """Orchestrate v6.1 rollout, focused DAG analysis, and harness evolution."""

    def __init__(self, benchmark: str, config_path, scripts_dir, *,
                 eval_cases_file: Optional[str] = None, baseline_dir: Optional[str] = None,
                 work_dir: Optional[str] = None, mini_swe_agent_dir: str = str(DEFAULT_MINI_SWE_AGENT),
                 n_cycles: int = DEFAULT_N_CYCLES, n_tasks: int = 1000, n_concurrent: int = 8,
                 n_attempts: int = 1, batch_size: int = 2, max_observation_chars: int = 1000,
                 max_prompt_chars: int = 50000, workers: int = 8,
                 judge_config_path: Optional[str] = None,
                 annotation_execution: str = "exact-global",
                 annotation_checkpoint: bool = True,
                 dry_run: bool = False):
        self.benchmark = benchmark
        self.config_path = str(config_path)
        self.scripts_dir = Path(scripts_dir)
        self.baseline_dir = Path(baseline_dir) if baseline_dir else None
        self.work_dir = Path(work_dir) if work_dir else (DEFAULT_WORK_DIR / benchmark)
        self.mini_swe_agent_dir = Path(mini_swe_agent_dir)
        self.n_cycles = int(n_cycles)
        self.eval_cases_file = Path(eval_cases_file) if eval_cases_file else None
        self.dry_run = bool(dry_run)
        self.n_tasks = int(n_tasks)
        self.n_concurrent = int(n_concurrent)
        self.n_attempts = int(n_attempts)
        self.batch_size = int(batch_size)
        self.max_observation_chars = int(max_observation_chars)
        self.max_prompt_chars = int(max_prompt_chars)
        self.judge_config_path = str(judge_config_path or config_path)
        self.workers = int(workers)
        self.annotation_execution = annotation_execution
        self.annotation_checkpoint = bool(annotation_checkpoint)
        if self.benchmark == "dab":
            # deploy_v6 reads this when producing the mini-swe environment config.
            os.environ.setdefault("EVOLVE_TOOLS_V6_TIMEOUT_SECONDS", "600")

        self.case_ids = self._load_case_ids()
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.scripts_dir.mkdir(parents=True, exist_ok=True)
        # Seed tools.json + executor.py + instruction.md if absent, and deploy the
        # v6 runtime + config once so cycle-1 rollout (if any) has the wiring ready.
        if not self.dry_run:
            seed_v6(self.scripts_dir)
            deploy_v6(self.scripts_dir, api_type=_llm_api_type(self.config_path),
                      max_completion_tokens=_max_completion_tokens(), container=True)

        self.rollout_agent = RolloutAgentV61(
            benchmark, self.config_path, n_tasks=n_tasks, n_concurrent=n_concurrent,
            n_attempts=n_attempts, taskdir_root=self.work_dir / "taskdirs",
            results_root=self.work_dir / ".rollout_staging")
        self.evolve_agent = EvolveAgentV61(
            self.scripts_dir, self.config_path, self.mini_swe_agent_dir,
            batch_size=batch_size, max_observation_chars=max_observation_chars,
            max_prompt_chars=max_prompt_chars,
            judge_config_path=self.judge_config_path,
            annotation_execution=annotation_execution,
            annotation_checkpoint=annotation_checkpoint,
            workers=workers, output_dir=None, dry_run=dry_run)
        self._save_run_manifest()

    def _load_case_ids(self) -> List[str]:
        if self.eval_cases_file and self.eval_cases_file.exists():
            ids = [ln.strip() for ln in self.eval_cases_file.read_text(encoding="utf-8").splitlines()
                   if ln.strip() and not ln.strip().startswith("#")]
            if ids:
                return ids
        src = _bench_source_task_dir(self.benchmark)
        if src and src.exists():
            ids = sorted(p.name for p in src.iterdir() if p.is_dir())[:16]
            logger.info("[v6.1] sampled %d case ids from %s", len(ids), src)
            return ids
        raise ValueError(f"no case ids: provide --eval-cases-file or a source task dir for {self.benchmark}")

    def run(self) -> V61Report:
        if (self.benchmark == "dab" and self.baseline_dir and self.baseline_dir.exists()
                and _run_has_forbidden_oracle_action(self.baseline_dir)):
            raise ValueError(
                "refusing oracle-contaminated DAB baseline; regenerate prep with "
                "dab-harbor.v2-blind"
            )
        report = V61Report(benchmark=self.benchmark, n_cycles=self.n_cycles, scripts_dir=str(self.scripts_dir))
        logger.info("[v6.1] start: benchmark=%s cycles=%d cases=%d scripts=%s",
                    self.benchmark, self.n_cycles, len(self.case_ids), self.scripts_dir)
        for cycle in range(1, self.n_cycles + 1):
            logger.info("[v6.1] === cycle %d/%d ===", cycle, self.n_cycles)
            cycle_dir = self._cycle_dir(cycle)
            cycle_dir.mkdir(parents=True, exist_ok=True)
            metrics_path = cycle_dir / "annotation_v61_metrics.json"
            evolve_logs = cycle_dir / "evolve_logs"
            harness_snapshot = cycle_dir / "harness_after"
            state = self._load_cycle_state(cycle)
            resumed = []

            rollout = self._do_rollout(cycle)
            self._mark_cycle_stage(
                cycle, "rollout", True,
                run_id=rollout.run_id,
                rollout_dir=str(rollout.run_dir),
                trajectory_files=self._trajectory_count(rollout.run_dir),
            )

            annotated = bool(
                state.get("stages", {}).get("annotate")
                and metrics_path.is_file()
                and self._annotation_complete(rollout.run_dir)
            )
            if annotated:
                resumed.append("annotate")
                logger.info("[v6.1] cycle %d annotation already complete; skip", cycle)
            else:
                # Any newly materialized dependency/meta result invalidates all
                # downstream artifacts, even if a stale state file survived.
                state["stages"]["contrastive"] = False
                state["stages"]["evolve"] = False
                self._mark_cycle_stage(cycle, "contrastive", False)
                self._mark_cycle_stage(cycle, "evolve", False)
                annotated = self._safe(
                    self.evolve_agent.annotate,
                    rollout.run_dir,
                    None,
                    metrics_path,
                    label="annotate",
                )
                self._mark_cycle_stage(cycle, "annotate", annotated)
            if not annotated:
                cycle_report = self._cycle_report(
                    cycle, rollout, False, False, False, resumed,
                )
                report.cycles.append(cycle_report)
                self._save_report(report)
                raise RuntimeError(
                    f"v6.1 cycle {cycle} annotation failed; rerun with the same "
                    f"--work-dir to resume exact missing steps"
                )

            contrastive_built = bool(
                state.get("stages", {}).get("contrastive")
                and self._contrastive_complete(rollout.run_dir)
            )
            if contrastive_built:
                resumed.append("contrastive")
                logger.info("[v6.1] cycle %d contrastive samples already complete; skip", cycle)
            else:
                state["stages"]["evolve"] = False
                self._mark_cycle_stage(cycle, "evolve", False)
                contrastive_built = self._safe(
                    self.evolve_agent.contrastive,
                    rollout.run_dir,
                    label="contrastive",
                )
                self._mark_cycle_stage(
                    cycle,
                    "contrastive",
                    contrastive_built,
                    contrastive_samples=len(list(
                        rollout.run_dir.glob("**/agent/contrastive_v61_*.json")
                    )),
                )
            if not contrastive_built:
                cycle_report = self._cycle_report(
                    cycle, rollout, True, False, False, resumed,
                )
                report.cycles.append(cycle_report)
                self._save_report(report)
                raise RuntimeError(
                    f"v6.1 cycle {cycle} contrastive construction failed; "
                    f"rerun with --work-dir {self.work_dir}"
                )

            evolved = bool(
                state.get("stages", {}).get("evolve")
                and harness_snapshot.is_dir()
            )
            if evolved:
                resumed.append("evolve")
                logger.info("[v6.1] cycle %d evolve already complete; skip", cycle)
            else:
                evolved = self._safe(
                    self.evolve_agent.evolve,
                    rollout.run_dir,
                    None,
                    evolve_logs,
                    label="evolve",
                )
                if evolved:
                    self._snapshot_harness(cycle)
                self._mark_cycle_stage(cycle, "evolve", evolved)

            report.cycles.append(self._cycle_report(
                cycle, rollout, annotated, contrastive_built, evolved, resumed,
            ))
            self._save_report(report)
            logger.info("[v6.1] cycle %d done: annotate=%s contrastive=%s evolve=%s",
                        cycle, annotated, contrastive_built, evolved)
            if not evolved:
                raise RuntimeError(
                    f"v6.1 cycle {cycle} evolve has unfinished batches; rerun with "
                    f"--work-dir {self.work_dir} to resume from {evolve_logs}"
                )
        logger.info("[v6.1] finished %d cycles. scripts=%s", self.n_cycles, self.scripts_dir)
        self._validate_output_layout(report)
        return report

    def _do_rollout(self, cycle: int) -> RolloutResultV61:
        cycle_dir = self._cycle_dir(cycle)
        destination = cycle_dir / "rollout"
        state = self._load_cycle_state(cycle)
        saved_run_id = str(state.get("run_id") or "")
        if destination.exists():
            count = self._trajectory_count(destination)
            if count < 1 and not self.dry_run:
                raise RuntimeError(
                    f"existing v6.1 rollout has no trajectories: {destination}"
                )
            run_id = saved_run_id or ("baseline-snapshot" if cycle == 1 else self._run_id(cycle))
            provenance_path = cycle_dir / "rollout_provenance.json"
            if not provenance_path.is_file():
                if cycle == 1 and self.baseline_dir and self.baseline_dir.exists():
                    source = self.baseline_dir.resolve()
                    self._write_rollout_provenance(
                        cycle,
                        run_id,
                        destination,
                        source_type="prep-baseline-snapshot-recovered",
                        source_path=source,
                        source_fingerprint=self._tree_fingerprint(source),
                    )
                else:
                    self._write_rollout_provenance(
                        cycle,
                        run_id,
                        destination,
                        source_type="benchmark-rollout-recovered",
                        source_path=self.rollout_agent._expected_run_dir(run_id),
                    )
            logger.info("[v6.1] cycle %d reusing materialized rollout: %s", cycle, destination)
            return RolloutResultV61(destination, run_id, cycle, len(self.case_ids))

        if cycle == 1 and self.baseline_dir and self.baseline_dir.exists():
            source = self.baseline_dir.resolve()
            logger.info(
                "[v6.1] cycle 1 snapshotting immutable baseline %s -> %s",
                source,
                destination,
            )
            self._atomic_copytree(source, destination, suffix="baseline")
            run_id = "baseline-snapshot"
            self._write_rollout_provenance(
                cycle,
                run_id,
                destination,
                source_type="prep-baseline-snapshot",
                source_path=source,
                source_fingerprint=self._tree_fingerprint(source),
            )
            return RolloutResultV61(destination, run_id, 1, len(self.case_ids))

        run_id = saved_run_id or self._run_id(cycle)
        self._mark_cycle_stage(cycle, "rollout", False, run_id=run_id)
        raw_dir = self.rollout_agent._expected_run_dir(run_id)
        command_marker = cycle_dir / "rollout_command_complete.json"
        marker_matches = False
        try:
            marker_data = json.loads(command_marker.read_text(encoding="utf-8"))
            marker_matches = marker_data.get("run_id") == run_id
        except (OSError, json.JSONDecodeError):
            marker_data = {}
        if raw_dir.exists() and not marker_matches:
            # A raw run without our post-wait marker means the parent process
            # stopped mid-rollout.  It is owned private staging, so discard it
            # and rerun the exact case set instead of accepting partial output.
            logger.warning(
                "[v6.1] removing interrupted staged rollout before retry: %s",
                raw_dir,
            )
            _safe_rmtree(raw_dir)
        if raw_dir.exists() and marker_matches:
            logger.info("[v6.1] cycle %d found completed staged rollout: %s", cycle, raw_dir)
            raw = RolloutResultV61(
                raw_dir,
                run_id,
                cycle,
                len(self.case_ids),
                marker_data.get("returncode"),
            )
        else:
            raw = self.rollout_agent.rollout(
                self.scripts_dir,
                self.case_ids,
                run_id,
                cycle,
                dry_run=self.dry_run,
                log_path=cycle_dir / "rollout_command.log",
            )
            self._atomic_json(command_marker, {
                "schema_version": "v6.1-rollout-command.1",
                "cycle": cycle,
                "run_id": run_id,
                "returncode": raw.returncode,
                "raw_run_dir": str(raw.run_dir),
                "trajectory_file_count": self._trajectory_count(raw.run_dir),
                "completed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            })
        if self.dry_run and not raw.run_dir.exists():
            destination.mkdir(parents=True, exist_ok=True)
        elif not raw.run_dir.exists():
            raise RuntimeError(f"v6.1 rollout directory was not produced: {raw.run_dir}")
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(raw.run_dir, destination)
            self._remove_empty_rollout_staging()
        if self._trajectory_count(destination) < 1 and not self.dry_run:
            raise RuntimeError(f"v6.1 rollout produced no trajectories: {destination}")
        self._write_rollout_provenance(
            cycle,
            run_id,
            destination,
            source_type="benchmark-rollout",
            source_path=raw.run_dir,
        )
        return RolloutResultV61(destination, run_id, cycle, len(self.case_ids))

    def _safe(self, fn, *args, label: str) -> bool:
        try:
            fn(*args)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.exception("[v6.1] %s failed: %s", label, exc)
            return False

    def _cycle_dir(self, cycle: int) -> Path:
        return self.work_dir / f"cycle-{cycle}"

    def _run_id(self, cycle: int) -> str:
        stable = re.sub(r"[^A-Za-z0-9_.-]+", "-", self.work_dir.name).strip("-")
        return f"v61c{cycle}-{self.benchmark}-{stable or 'run'}"

    @staticmethod
    def _trajectory_count(path: Path) -> int:
        return sum(1 for _ in Path(path).glob("**/agent/trajectory.json"))

    @classmethod
    def _annotation_complete(cls, run_dir: Path) -> bool:
        paths = list(Path(run_dir).glob("**/agent/trajectory.json"))
        return bool(paths) and all(TrajectoryAnnotatorV61.is_annotated(path) for path in paths)

    @staticmethod
    def _contrastive_complete(run_dir: Path) -> bool:
        return any(Path(run_dir).glob("**/agent/contrastive_v61_*.json"))

    def _cycle_state_path(self, cycle: int) -> Path:
        return self._cycle_dir(cycle) / "cycle_state.json"

    def _load_cycle_state(self, cycle: int) -> dict:
        path = self._cycle_state_path(cycle)
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            state = {}
        if not isinstance(state, dict):
            state = {}
        state.setdefault("schema_version", "v6.1-cycle-state.1")
        state.setdefault("cycle", cycle)
        state.setdefault("stages", {})
        return state

    def _mark_cycle_stage(self, cycle: int, stage: str, complete: bool, **extra) -> None:
        state = self._load_cycle_state(cycle)
        state["stages"][stage] = bool(complete)
        state.update(extra)
        state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        self._atomic_json(self._cycle_state_path(cycle), state)

    def _cycle_report(
        self,
        cycle: int,
        rollout: RolloutResultV61,
        annotated: bool,
        contrastive_built: bool,
        evolved: bool,
        resumed: Sequence[str],
    ) -> CycleReportV61:
        cycle_dir = self._cycle_dir(cycle)
        return CycleReportV61(
            cycle=cycle,
            rollout=rollout,
            annotated=annotated,
            contrastive_built=contrastive_built,
            evolved=evolved,
            cycle_dir=str(cycle_dir),
            annotation_metrics=str(cycle_dir / "annotation_v61_metrics.json"),
            evolve_logs=str(cycle_dir / "evolve_logs"),
            harness_snapshot=str(cycle_dir / "harness_after"),
            notes=("resumed stages: " + ", ".join(resumed)) if resumed else "",
        )

    @staticmethod
    def _atomic_json(path: Path, data: dict) -> None:
        TrajectoryAnnotatorV61._atomic_write_json(Path(path), data)

    @staticmethod
    def _atomic_copytree(source: Path, destination: Path, *, suffix: str) -> None:
        source = Path(source)
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging = destination.parent / f".{destination.name}.{suffix}.tmp"
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        shutil.copytree(source, staging, symlinks=True)
        if destination.exists():
            shutil.rmtree(destination)
        os.replace(staging, destination)

    @staticmethod
    def _tree_fingerprint(root: Path) -> str:
        digest = hashlib.sha256()
        root = Path(root)
        for path in sorted(root.rglob("*"), key=lambda item: str(item.relative_to(root))):
            relative = str(path.relative_to(root))
            digest.update(relative.encode("utf-8", errors="surrogateescape"))
            if path.is_symlink():
                digest.update(b"L")
                digest.update(os.readlink(path).encode("utf-8", errors="surrogateescape"))
            elif path.is_file():
                digest.update(b"F")
                with path.open("rb") as fh:
                    for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                        digest.update(chunk)
            elif path.is_dir():
                digest.update(b"D")
        return digest.hexdigest()

    def _write_rollout_provenance(
        self,
        cycle: int,
        run_id: str,
        destination: Path,
        *,
        source_type: str,
        source_path: Path,
        source_fingerprint: Optional[str] = None,
    ) -> None:
        self._atomic_json(self._cycle_dir(cycle) / "rollout_provenance.json", {
            "schema_version": "v6.1-rollout-provenance.1",
            "cycle": cycle,
            "run_id": run_id,
            "source_type": source_type,
            "source_path": str(source_path),
            "source_fingerprint": source_fingerprint,
            "materialized_path": str(destination),
            "requested_case_count": len(self.case_ids),
            "trajectory_file_count": self._trajectory_count(destination),
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        })

    def _remove_empty_rollout_staging(self) -> None:
        root = self.rollout_agent.results_root
        if not root.exists():
            return
        for path in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            if path.is_dir():
                try:
                    path.rmdir()
                except OSError:
                    pass
        try:
            root.rmdir()
        except OSError:
            pass

    def _snapshot_harness(self, cycle: int) -> Path:
        destination = self._cycle_dir(cycle) / "harness_after"
        self._atomic_copytree(self.scripts_dir, destination, suffix="harness")
        self._atomic_json(self._cycle_dir(cycle) / "harness_snapshot.json", {
            "schema_version": "v6.1-harness-snapshot.1",
            "cycle": cycle,
            "source": str(self.scripts_dir),
            "snapshot": str(destination),
            "fingerprint": self._tree_fingerprint(destination),
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        })
        return destination

    def _save_run_manifest(self) -> None:
        path = self.work_dir / "v6_1_run_manifest.json"
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            previous = {}
        created_at = previous.get("created_at") or time.strftime("%Y-%m-%dT%H:%M:%S%z")
        self._atomic_json(path, {
            "schema_version": "v6.1-run-manifest.1",
            "version": "v6.1",
            "benchmark": self.benchmark,
            "created_at": created_at,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "work_dir": str(self.work_dir),
            "scripts_dir": str(self.scripts_dir),
            "baseline_source": str(self.baseline_dir.resolve()) if self.baseline_dir else None,
            "eval_cases_file": str(self.eval_cases_file) if self.eval_cases_file else None,
            "case_ids": self.case_ids,
            "layout": {
                "cycle_pattern": "cycle-N",
                "rollout": "cycle-N/rollout",
                "annotation_metrics": "cycle-N/annotation_v61_metrics.json",
                "evolve_logs": "cycle-N/evolve_logs",
                "harness_snapshot": "cycle-N/harness_after",
                "cycle_state": "cycle-N/cycle_state.json",
                "final_harness": str(self.scripts_dir),
            },
            "settings": {
                "n_cycles": self.n_cycles,
                "n_tasks": self.n_tasks,
                "n_concurrent": self.n_concurrent,
                "n_attempts": self.n_attempts,
                "batch_size": self.batch_size,
                "max_observation_chars": self.max_observation_chars,
                "max_prompt_chars": self.max_prompt_chars,
                "annotation_workers": self.workers,
                "annotation_execution": self.annotation_execution,
                "annotation_checkpoint": self.annotation_checkpoint,
                "config_path": self.config_path,
                "judge_config_path": self.judge_config_path,
                "evolve_prompt_delivery": "direct-user-prompt",
                "evolve_gate": LLMJudgeGateV61.SCHEMA_VERSION,
            },
            "annotation_equivalence_contract": list(
                TrajectoryAnnotatorV61.EQUIVALENCE_CONTRACT
            ),
        })

    def _validate_output_layout(self, report: V61Report) -> None:
        missing = []
        root_files = [
            self.work_dir / "v6_1_run_manifest.json",
            self.work_dir / "v6_1_report.json",
        ]
        for path in root_files:
            if not path.is_file():
                missing.append(str(path))
        for cycle_report in report.cycles:
            cycle_dir = self._cycle_dir(cycle_report.cycle)
            required = [
                cycle_dir / "rollout",
                cycle_dir / "rollout_provenance.json",
                cycle_dir / "annotation_v61_metrics.json",
                cycle_dir / "evolve_logs",
                cycle_dir / "harness_after",
                cycle_dir / "harness_snapshot.json",
                cycle_dir / "cycle_state.json",
            ]
            for path in required:
                if not path.exists():
                    missing.append(str(path))
        prep_immutable = True
        prep_check = None
        if self.baseline_dir and report.cycles:
            provenance_path = self._cycle_dir(1) / "rollout_provenance.json"
            try:
                provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
                expected = provenance.get("source_fingerprint")
                actual = self._tree_fingerprint(self.baseline_dir.resolve())
                prep_immutable = bool(expected) and expected == actual
                prep_check = {"expected": expected, "actual": actual}
            except (OSError, json.JSONDecodeError):
                prep_immutable = False
            if not prep_immutable:
                missing.append("cycle-1 prep baseline immutability check failed")
        layout = {
            "schema_version": "v6.1-output-layout.1",
            "valid": not missing,
            "work_dir": str(self.work_dir),
            "prep_baseline_unchanged": prep_immutable,
            "prep_fingerprint_check": prep_check,
            "missing": missing,
        }
        self._atomic_json(self.work_dir / "output_layout.json", layout)
        if missing:
            raise RuntimeError(f"invalid v6.1 output layout: {missing}")

    def _save_report(self, report: V61Report) -> None:
        path = self.work_dir / "v6_1_report.json"
        self._atomic_json(path, {
            "benchmark": report.benchmark, "n_cycles": report.n_cycles, "scripts_dir": report.scripts_dir,
            "cycles": [{"cycle": c.cycle, "run_dir": str(c.rollout.run_dir), "run_id": c.rollout.run_id,
                        "n_cases": c.rollout.n_cases, "rollout_returncode": c.rollout.returncode,
                        "annotated": c.annotated,
                        "contrastive_built": c.contrastive_built, "evolved": c.evolved,
                        "cycle_dir": c.cycle_dir,
                        "annotation_metrics": c.annotation_metrics,
                        "evolve_logs": c.evolve_logs,
                        "harness_snapshot": c.harness_snapshot,
                        "notes": c.notes}
                       for c in report.cycles],
        })


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
    parser.add_argument("--max-prompt-chars", type=int, default=50000)
    parser.add_argument(
        "--judge-config",
        default=None,
        help="LLM-as-Judge config; defaults to --config",
    )
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument(
        "--annotation-execution",
        choices=TrajectoryAnnotatorV61.EXECUTION_MODES,
        default="exact-global",
        help="exact-global preserves every legacy per-step prompt while using one global worker pool",
    )
    parser.add_argument(
        "--no-annotation-checkpoint",
        action="store_false",
        dest="annotation_checkpoint",
        default=True,
        help="disable exact per-step annotation checkpoint/resume",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--log-file", default=None)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(
        description="Evolve v6.1: focused DAG slices evolve tools and instructions."
    )
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
        EvolveV61Cycle(
            benchmark=args.benchmark, config_path=args.config, scripts_dir=args.scripts_dir,
            eval_cases_file=args.eval_cases_file, baseline_dir=args.baseline_dir, work_dir=args.work_dir,
            mini_swe_agent_dir=args.mini_swe_agent_dir, n_cycles=args.n_cycles, n_tasks=args.n_tasks,
            n_concurrent=args.n_concurrent, n_attempts=args.n_attempts, batch_size=args.batch_size,
            max_observation_chars=args.max_observation_chars,
            max_prompt_chars=args.max_prompt_chars,
            judge_config_path=args.judge_config,
            workers=args.workers,
            annotation_execution=args.annotation_execution,
            annotation_checkpoint=args.annotation_checkpoint,
            dry_run=args.dry_run,
        ).run()
    elif args.cmd == "refresh":
        EvolveAgentV61(scripts_dir=args.scripts_dir, config_path=args.config,
                    mini_swe_agent_dir=DEFAULT_MINI_SWE_AGENT, dry_run=args.dry_run).refresh_registration()


if __name__ == "__main__":
    main()
