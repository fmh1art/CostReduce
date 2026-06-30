"""Stage 1: annotate trajectory step dependencies with an LLM."""

from __future__ import annotations

import ast
import json
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from src.tools.llm import LLM

logger = logging.getLogger(__name__)


class DependencyParseError(ValueError):
    """Raised when an LLM dependency response cannot be parsed safely."""


class TrajectoryAnnotator:
    """Annotate `agent/trajectory.json` files with a `dependencies` field.

    For each action step `i`, the LLM is asked to list every previous step
    index `j` that step `i` depends on. The result is written back into the
    trajectory file in-place. Here, "depends on" means that if the agent see all previous step j, it can generate the step i.

    V3 merges a step-type (``op_type``) question into the *same* LLM call, so
    both ``dependencies[i]`` and ``step_meta.op_type`` come from one query per
    step — no extra round-trip. op_type is best-effort (falls back to the v2
    rule classifier on parse failure); dependencies stay correctness-critical.
    """

    name = "annotate"

    # op_type label set mirrors classify_op_type exactly (4 classes), so the
    # contrastive/phase/anchor consumers downstream stay on the same vocabulary.
    OP_TYPE_LABELS = ("read", "write", "verify", "explore")

    SYSTEM_PROMPT = (
        "You annotate dependency relations AND the operation type of trajectory steps. "
        "Step 0 is the initial state before any action. For current step i, "
        "return every previous step index j that the action depends on: as long as the "
        "agent sees all previous step j, it can generate the step i. "
        "Step 0 (initial state) is almost always required — include 0 in the list "
        "unless step i truly needs no prior context. "
        "ALSO classify step i's operation type into exactly one label:\n"
        "  read    — inspecting/searching code/data WITHOUT modifying it "
        "(grep, rg, find, cat, head, ls, sed WITHOUT -i, git status/log/diff/show).\n"
        "  write   — creating/editing/deleting/moving files, or changing repo state "
        "(sed -i, cat >, rm, mv, cp, apply patch, git checkout/commit/add/reset/apply).\n"
        "  verify  — running tests/build/lint/type-check to validate a change "
        "(pytest, make, go test/build, npm run, tsc, ruff, python -m pytest).\n"
        "  explore — none of the above: exploratory shells, env inspection, retries, "
        "setup, or anything ambiguous (pwd, env, echo, cd, which).\n"
        'Output ONLY a JSON object: {"dependencies": [int, ...], "op_type": "label"}. '
        "dependencies may be empty only if step i needs no prior context."
    )

    MAX_OBSERVATION_CHARS = 800

    def __init__(self, config_path, workers: int = 1, retry_failed: int = 1):
        self.config_path = str(config_path)
        self.workers = max(1, int(workers or 1))
        self.retry_failed = max(0, int(retry_failed or 0))

    # ---------- public API ----------

    def find_trajectory_files(self, result_dir, task=None):
        files = sorted(Path(result_dir).glob("**/agent/trajectory.json"))
        return [p for p in files if not task or task in str(p)]

    def annotate_dir(self, result_dir, task=None):
        paths = self.find_trajectory_files(result_dir, task)
        logger.info("found %d trajectory files", len(paths))
        failures = self._run_batch(paths)
        for attempt in range(1, self.retry_failed + 1):
            if not failures:
                break
            logger.warning(
                "retrying %d failed trajectory file(s), retry attempt %d/%d",
                len(failures),
                attempt,
                self.retry_failed,
            )
            failures = self._run_batch(failures)
        if failures:
            raise RuntimeError(
                f"failed to annotate {len(failures)} trajectory file(s) after retry: {failures}"
            )
        return paths

    # Stage interface
    run = annotate_dir

    def annotate_file(self, path, llm=None, step_workers: int = 1):
        llm = llm or LLM(self.config_path)
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        action_steps = self._extract_action_steps(data)
        step_workers = max(1, int(step_workers or 1))
        logger.info(
            "start annotating %s, action_steps=%d, step_workers=%d",
            path,
            len(action_steps),
            step_workers,
        )
        step_inputs = self._build_step_inputs(action_steps)

        dependencies = {"0": []}
        op_types: dict = {}
        if step_workers <= 1 or len(step_inputs) <= 1:
            for i, current_step_text, history in step_inputs:
                _, deps, op_type = self._annotate_step(
                    path, i, len(action_steps), current_step_text, history, llm
                )
                dependencies[str(i)] = deps
                op_types[str(i)] = op_type
        else:
            par_deps, par_ops = self._annotate_steps_parallel(
                path, action_steps, step_inputs, step_workers, llm=llm)
            dependencies.update(par_deps)
            op_types.update(par_ops)

        data["dependencies"] = dependencies
        self._write_step_meta(action_steps, op_types)
        Path(path).write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        logger.info("finished annotating %s", path)

    # ---------- helpers ----------

    @staticmethod
    def is_annotated(path) -> bool:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            dependencies = data.get("dependencies")
            if not isinstance(dependencies, dict) or not dependencies:
                return False
            expected = len(TrajectoryAnnotator._extract_action_steps(data))
            # action step indices are 1..expected; every one must be present
            return all(str(i) in dependencies for i in range(1, expected + 1))
        except Exception:
            return False

    @staticmethod
    def _extract_action_steps(trajectory):
        steps = trajectory.get("steps", [])
        return [
            s for s in steps if s.get("tool_calls") or "observation" in s or s.get("action")
        ]

    @staticmethod
    def _step_text(step) -> str:
        action = step.get("tool_calls") or step.get("action") or step.get("message") or ""
        observation = step.get("observation", "")
        return json.dumps(
            {"action": action, "observation": TrajectoryAnnotator._clip_observation(observation)},
            ensure_ascii=False,
            default=str,
        )

    @staticmethod
    def _clip_observation(observation) -> str:
        """Render observation as a compact string capped at MAX_OBSERVATION_CHARS."""
        if isinstance(observation, dict) and isinstance(observation.get("results"), list):
            parts = []
            for item in observation["results"]:
                content = item.get("content", item) if isinstance(item, dict) else item
                if isinstance(content, str):
                    try:
                        parsed = json.loads(content)
                        if isinstance(parsed, dict):
                            bits = []
                            if "returncode" in parsed:
                                bits.append(f"returncode: {parsed.get('returncode')}")
                            if parsed.get("output"):
                                bits.append(f"output: {parsed.get('output')}")
                            if parsed.get("exception_info"):
                                bits.append(f"exception_info: {parsed.get('exception_info')}")
                            content = "\n".join(bits) if bits else content
                    except json.JSONDecodeError:
                        pass
                parts.append(content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, default=str))
            text = "\n".join(parts)
        elif isinstance(observation, str):
            text = observation
        else:
            text = json.dumps(observation, ensure_ascii=False, default=str)
        max_chars = TrajectoryAnnotator.MAX_OBSERVATION_CHARS
        return text if len(text) <= max_chars else text[:max_chars] + "...<truncated>"

    def _build_step_inputs(self, action_steps):
        step_texts = [self._step_text(step) for step in action_steps]
        history = "Step 0: initial state before any action.\n"
        step_inputs = []
        for i, text in enumerate(step_texts, start=1):
            step_inputs.append((i, text, history))
            history += f"\nStep {i}:\n{text}\n"
        return step_inputs

    @staticmethod
    def _parse_dependency_list(text, max_index):
        text = (text or "").strip()
        if not text:
            raise DependencyParseError("empty LLM dependency output")
        # Take the LAST [...] in the text — LLMs sometimes emit reasoning
        # before the final answer, and earlier brackets are usually not the answer.
        matches = re.findall(r"\[[\s\S]*?\]", text)
        candidate = matches[-1] if matches else text
        try:
            values = ast.literal_eval(candidate)
        except (SyntaxError, ValueError) as exc:
            raise DependencyParseError(
                f"invalid LLM dependency output {text!r}: {exc}"
            ) from exc
        if not isinstance(values, list):
            raise DependencyParseError(f"LLM dependency output is not a list: {text!r}")
        deps = []
        for x in values:
            try:
                i = int(x)
            except (TypeError, ValueError):
                raise DependencyParseError(
                    f"non-integer dependency value {x!r} in output {text!r}"
                )
            if 0 <= i <= max_index and i not in deps:
                deps.append(i)
        # step 0 (initial state) is almost always required for action steps
        if 0 not in deps and max_index >= 0:
            deps.append(0)
        return deps

    def _annotate_step(self, path, i, total, current_step_text, history, llm):
        """One LLM call producing both dependencies and op_type for step i.

        Returns ``(i, deps, op_type)``. deps is correctness-critical and raises
        ``DependencyParseError`` on parse failure (as before). op_type is
        best-effort: ``None`` on parse failure, left for the v2 rule classifier
        to fill (marked ``op_type_source="rule_fallback"`` there).
        """
        logger.info("annotating %s step %d/%d", path, i, total)
        user_prompt = (
            f"Current step {i}:\n{current_step_text}\n\n"
            f"Return the JSON object with step {i}'s dependencies AND op_type."
        )
        raw = llm.query(self.SYSTEM_PROMPT, history, user_prompt)
        try:
            deps, op_type = self._parse_dependency_and_op_type(raw, i - 1)
        except DependencyParseError as exc:
            logger.error(
                "failed to parse dependencies for %s step %d, raw=%r: %s",
                path,
                i,
                raw,
                exc,
            )
            raise
        logger.info("annotated %s step %d dependencies=%s op_type=%s", path, i, deps, op_type)
        return i, deps, op_type

    @classmethod
    def _parse_dependency_and_op_type(cls, text, max_index):
        """Parse a merged ``{"dependencies":[...], "op_type":"..."}`` response.

        Tolerant of two LLM habits:
        - Emits a JSON object as instructed → take its ``dependencies`` (fall
          back to a bare list value) and ``op_type``.
        - Ignores the object shape and emits just ``[int, ...]`` → parse it via
          the legacy ``_parse_dependency_list`` and return op_type=None (rule
          fallback downstream).

        deps are validated strictly (raise DependencyParseError); op_type is
        only accepted if it's one of the 4 known labels, else None.
        """
        text = text or ""
        obj = None
        obj_matches = re.findall(r"\{[\s\S]*\}", text)
        if obj_matches:
            try:
                obj = json.loads(obj_matches[-1])
            except json.JSONDecodeError:
                obj = None
        if isinstance(obj, dict):
            dep_raw = obj.get("dependencies")
            if dep_raw is None:
                # object present but no "dependencies" key → maybe {"op_type":...} only
                dep_raw = obj.get("deps")
            if dep_raw is None:
                # Model emitted an object but omitted dependencies entirely.
                # Treat as "depends on nothing explicit" → just the initial
                # state (step 0), which _parse_dependency_list would add anyway.
                deps = [0] if max_index >= 0 else []
            else:
                deps = cls._parse_dependency_list(json.dumps(dep_raw), max_index)
            op_raw = obj.get("op_type", obj.get("operation_type"))
            op_type = op_raw.strip().lower() if isinstance(op_raw, str) else None
            if op_type not in cls.OP_TYPE_LABELS:
                op_type = None
            return deps, op_type
        # No JSON object — fall back to the legacy bare-list parser.
        deps = cls._parse_dependency_list(text, max_index)
        return deps, None

    @staticmethod
    def _write_step_meta(action_steps, op_types) -> None:
        """Stamp LLM op_type into each action step's ``step_meta``.

        Only writes when the LLM produced a label for that step (op_type is
        non-None); steps without one are left for the v2 rule classifier, which
        fills ``step_meta`` and marks ``op_type_source="rule_fallback"``.
        Leaves any pre-existing ``step_meta`` fields untouched.
        """
        for i, step in enumerate(action_steps, start=1):
            op_type = op_types.get(str(i))
            if not op_type:
                continue
            meta = step.get("step_meta")
            if not isinstance(meta, dict):
                meta = {}
                step["step_meta"] = meta
            meta["op_type"] = op_type
            meta["op_type_source"] = "llm"

    def _annotate_steps_parallel(self, path, action_steps, step_inputs, step_workers, llm=None):
        thread_state = threading.local()

        def get_llm():
            # Prefer an injected (shared) LLM — its query is stateless, so it's
            # safe to share across worker threads. Fall back to a per-thread
            # LLM only when none was injected.
            if llm is not None:
                return llm
            if not hasattr(thread_state, "llm"):
                thread_state.llm = LLM(self.config_path)
            return thread_state.llm

        def work(i, current_step_text, history):
            return self._annotate_step(
                path, i, len(action_steps), current_step_text, history, get_llm()
            )

        step_results = {}
        op_results = {}
        errors = []
        max_workers = min(step_workers, len(step_inputs))
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(work, i, current_step_text, history): i
                for i, current_step_text, history in step_inputs
            }
            for future in as_completed(futures):
                i = futures[future]
                try:
                    _, deps, op_type = future.result()
                    step_results[i] = deps
                    op_results[i] = op_type
                except Exception as exc:
                    errors.append((i, exc))
                    logger.exception("failed to annotate %s step %d: %s", path, i, exc)
        if errors:
            first_step, first_error = sorted(errors, key=lambda x: x[0])[0]
            raise RuntimeError(
                f"failed to annotate {path} step {first_step}"
            ) from first_error
        deps = {str(i): step_results[i] for i in range(1, len(action_steps) + 1)}
        ops = {str(i): op_results.get(i) for i in range(1, len(action_steps) + 1)}
        return deps, ops

    def _run_batch(self, paths):
        pending = [p for p in paths if not self.is_annotated(p)]
        for p in paths:
            if self.is_annotated(p):
                logger.info("skip annotated %s", p)
        if not pending:
            return []

        file_workers = min(self.workers, len(pending))
        # ceil division so workers don't go idle when pending > workers
        step_workers = max(1, -(-self.workers // file_workers))
        logger.info(
            "annotation parallelism: total_workers=%d, file_workers=%d, step_workers=%d, pending_files=%d",
            self.workers,
            file_workers,
            step_workers,
            len(pending),
        )

        failures = []
        if file_workers <= 1:
            llm = LLM(self.config_path)
            for path in pending:
                try:
                    self.annotate_file(path, llm=llm, step_workers=step_workers)
                except Exception as exc:
                    failures.append(path)
                    logger.exception("failed to annotate trajectory %s: %s", path, exc)
            return failures

        def annotate_one(path):
            self.annotate_file(path, llm=LLM(self.config_path), step_workers=step_workers)

        with ThreadPoolExecutor(max_workers=file_workers) as pool:
            futures = {pool.submit(annotate_one, path): path for path in pending}
            for future in as_completed(futures):
                path = futures[future]
                try:
                    future.result()
                except Exception as exc:
                    failures.append(path)
                    logger.exception("failed to annotate trajectory %s: %s", path, exc)
        return failures
