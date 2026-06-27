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
    """

    name = "annotate"

    SYSTEM_PROMPT = (
        "You annotate dependency relations between trajectory steps. "
        "Step 0 is the initial state before any action. For current step i, "
        "return every previous step index j that the action depends on: as long as the "
        "agent sees all previous step j, it can generate the step i. "
        "Step 0 (initial state) is almost always required — include 0 in the list "
        "unless step i truly needs no prior context. "
        "Output only a JSON list of integers."
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
        if step_workers <= 1 or len(step_inputs) <= 1:
            for i, current_step_text, history in step_inputs:
                _, deps = self._annotate_step(
                    path, i, len(action_steps), current_step_text, history, llm
                )
                dependencies[str(i)] = deps
        else:
            dependencies.update(
                self._annotate_steps_parallel(path, action_steps, step_inputs, step_workers)
            )

        data["dependencies"] = dependencies
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
        logger.info("annotating %s step %d/%d", path, i, total)
        user_prompt = (
            f"Current step {i}:\n{current_step_text}\n\n"
            f"Which previous steps does step {i} depend on? Output only a JSON list."
        )
        raw = llm.query(self.SYSTEM_PROMPT, history, user_prompt)
        try:
            deps = self._parse_dependency_list(raw, i - 1)
        except DependencyParseError as exc:
            logger.error(
                "failed to parse dependencies for %s step %d, raw=%r: %s",
                path,
                i,
                raw,
                exc,
            )
            raise
        logger.info("annotated %s step %d dependencies=%s", path, i, deps)
        return i, deps

    def _annotate_steps_parallel(self, path, action_steps, step_inputs, step_workers):
        thread_state = threading.local()

        def get_llm():
            if not hasattr(thread_state, "llm"):
                thread_state.llm = LLM(self.config_path)
            return thread_state.llm

        def work(i, current_step_text, history):
            return self._annotate_step(
                path, i, len(action_steps), current_step_text, history, get_llm()
            )

        step_results = {}
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
                    _, deps = future.result()
                    step_results[i] = deps
                except Exception as exc:
                    errors.append((i, exc))
                    logger.exception("failed to annotate %s step %d: %s", path, i, exc)
        if errors:
            first_step, first_error = sorted(errors, key=lambda x: x[0])[0]
            raise RuntimeError(
                f"failed to annotate {path} step {first_step}"
            ) from first_error
        return {str(i): step_results[i] for i in range(1, len(action_steps) + 1)}

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
