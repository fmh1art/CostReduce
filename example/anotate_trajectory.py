import argparse
import ast
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import logging
import re
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.tools.llm import LLM

logger = logging.getLogger(__name__)


class DependencyParseError(ValueError):
    """Raised when an LLM dependency response cannot be parsed safely."""


def setup_logging(log_file=None):
    handlers = [logging.StreamHandler()]
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=handlers,
        force=True,
    )


def find_trajectory_files(result_dir, task=None):
    files = sorted(Path(result_dir).glob("**/agent/trajectory.json"))
    return [p for p in files if not task or task in str(p)]


def step_text(step):
    action = step.get("tool_calls") or step.get("action") or step.get("message") or ""
    observation = step.get("observation", "")
    return json.dumps({"action": action, "observation": observation}, ensure_ascii=False, default=str)


def extract_action_steps(trajectory):
    steps = trajectory.get("steps", [])
    return [s for s in steps if s.get("tool_calls") or "observation" in s or s.get("action")]


def is_annotated(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        dependencies = data.get("dependencies")
        return isinstance(dependencies, dict) and bool(dependencies)
    except Exception:
        return False


def parse_dependency_list(text, max_index):
    text = (text or "").strip()
    if not text:
        raise DependencyParseError("empty LLM dependency output")

    # Prefer a JSON/Python-style list if the model included extra prose, code
    # fences, or fragments like "dependencies=[]".  If no list is found, try
    # parsing the whole response so clear malformed outputs still get reported.
    m = re.search(r"\[[\s\S]*?\]", text)
    candidate = m.group(0) if m else text
    try:
        values = ast.literal_eval(candidate)
    except (SyntaxError, ValueError) as exc:
        raise DependencyParseError(f"invalid LLM dependency output {text!r}: {exc}") from exc
    if not isinstance(values, list):
        raise DependencyParseError(f"LLM dependency output is not a list: {text!r}")
    deps = []
    for x in values:
        try:
            i = int(x)
        except (TypeError, ValueError):
            raise DependencyParseError(f"non-integer dependency value {x!r} in output {text!r}")
        if 0 <= i <= max_index and i not in deps:
            deps.append(i)
    return deps


def dependency_system_prompt():
    return (
        "You annotate dependency relations between trajectory steps. "
        "Step 0 is the initial state before any action. For current step i, "
        "return every previous step index j that the action depends on: if the "
        "action needs information from step j's action/observation, or could only "
        "be generated correctly after seeing step j. Output only a JSON list of integers."
    )


def build_step_inputs(action_steps):
    step_texts = [step_text(step) for step in action_steps]
    history = "Step 0: initial state before any action.\n"
    step_inputs = []
    for i, text in enumerate(step_texts, start=1):
        step_inputs.append((i, text, history))
        history += f"\nStep {i}:\n{text}\n"
    return step_inputs


def annotate_step(path, i, total, current_step_text, history, llm, system_prompt):
    logger.info("annotating %s step %d/%d", path, i, total)
    user_prompt = (
        f"Current step {i}:\n{current_step_text}\n\n"
        f"Which previous steps does step {i} depend on? Output only a JSON list."
    )
    raw = llm.query(system_prompt, history, user_prompt)
    try:
        deps = parse_dependency_list(raw, i - 1)
    except DependencyParseError as exc:
        logger.error("failed to parse dependencies for %s step %d, raw=%r: %s", path, i, raw, exc)
        raise
    logger.info("annotated %s step %d dependencies=%s", path, i, deps)
    return i, deps


def annotate_trajectory(path, llm, config=None, step_workers=1):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    action_steps = extract_action_steps(data)
    step_workers = max(1, int(step_workers or 1))
    logger.info("start annotating %s, action_steps=%d, step_workers=%d", path, len(action_steps), step_workers)
    system_prompt = dependency_system_prompt()
    step_inputs = build_step_inputs(action_steps)

    dependencies = {"0": []}
    if step_workers <= 1 or len(step_inputs) <= 1:
        for i, current_step_text, history in step_inputs:
            _, deps = annotate_step(path, i, len(action_steps), current_step_text, history, llm, system_prompt)
            dependencies[str(i)] = deps
    else:
        thread_state = threading.local()

        def get_llm():
            if config is None:
                return llm
            if not hasattr(thread_state, "llm"):
                thread_state.llm = LLM(config)
            return thread_state.llm

        def annotate_step_with_thread_llm(i, current_step_text, history):
            return annotate_step(path, i, len(action_steps), current_step_text, history, get_llm(), system_prompt)

        step_results = {}
        errors = []
        max_workers = min(step_workers, len(step_inputs))
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(annotate_step_with_thread_llm, i, current_step_text, history): i
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
            raise RuntimeError(f"failed to annotate {path} step {first_step}") from first_error
        for i in range(1, len(action_steps) + 1):
            dependencies[str(i)] = step_results[i]

    data["dependencies"] = dependencies
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    logger.info("finished annotating %s", path)


def annotate_path(path, config, step_workers=1):
    if is_annotated(path):
        logger.info("skip annotated %s", path)
        return
    logger.info("annotating %s", path)
    annotate_trajectory(path, LLM(config), config=config, step_workers=step_workers)


def run_annotation_batch(paths, config, workers):
    failures = []
    pending_paths = []
    for path in paths:
        if is_annotated(path):
            logger.info("skip annotated %s", path)
        else:
            pending_paths.append(path)

    if not pending_paths:
        return failures

    workers = max(1, int(workers or 1))
    file_workers = min(workers, len(pending_paths))
    step_workers = max(1, workers // file_workers)
    logger.info(
        "annotation parallelism: total_workers=%d, file_workers=%d, step_workers=%d, pending_files=%d",
        workers,
        file_workers,
        step_workers,
        len(pending_paths),
    )

    if file_workers <= 1:
        llm = LLM(config)
        for path in pending_paths:
            try:
                logger.info("annotating %s", path)
                annotate_trajectory(path, llm, config=config, step_workers=step_workers)
            except Exception as exc:
                failures.append(path)
                logger.exception("failed to annotate trajectory %s: %s", path, exc)
        return failures

    with ThreadPoolExecutor(max_workers=file_workers) as pool:
        futures = {pool.submit(annotate_path, path, config, step_workers): path for path in pending_paths}
        for future in as_completed(futures):
            path = futures[future]
            try:
                future.result()
            except Exception as exc:
                failures.append(path)
                logger.exception("failed to annotate trajectory %s: %s", path, exc)
    return failures


def main():
    parser = argparse.ArgumentParser(description="Annotate trajectory step dependencies with an LLM.")
    parser.add_argument("result_dir", help="result/run directory containing */agent/trajectory.json")
    parser.add_argument("--config", default="/home/fanmeihao/projects/CostReduce/_config/deepseekv4_flash.yaml")
    parser.add_argument("--task", help="optional task id/name substring filter")
    parser.add_argument("--workers", type=int, default=1, help="total parallel LLM calls across trajectory files and steps")
    parser.add_argument("--retry-failed", type=int, default=1, help="retry failed trajectory files after the first pass")
    parser.add_argument("--log-file", help="optional log file path")
    args = parser.parse_args()

    setup_logging(args.log_file)
    paths = find_trajectory_files(args.result_dir, args.task)
    logger.info("found %d trajectory files", len(paths))
    failures = run_annotation_batch(paths, args.config, args.workers)
    for attempt in range(1, args.retry_failed + 1):
        if not failures:
            break
        logger.warning(
            "retrying %d failed trajectory file(s), retry attempt %d/%d",
            len(failures),
            attempt,
            args.retry_failed,
        )
        failures = run_annotation_batch(failures, args.config, args.workers)
    if failures:
        raise RuntimeError(f"failed to annotate {len(failures)} trajectory file(s) after retry: {failures}")


if __name__ == "__main__":
    main()
