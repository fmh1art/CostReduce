"""Stage 3: evolve scripts/instructions from contrastive samples.

The work is split across small classes so each piece can be swapped or
subclassed independently:

* `TrajectorySerializer` — render a trajectory dict as prompt text.
* `EvolvePromptBuilder` — combine a batch of contrastive samples into
  the full prompt sent to the agent.
* `AgentRunner` / `MiniSweAgentRunner` — actually execute the agent.
* `ScriptEvolver` — pipeline stage that wires the above together.

To experiment with new contrastive-sample formats or prompt strategies,
subclass `TrajectorySerializer` or `EvolvePromptBuilder` and inject the
replacement when constructing `ScriptEvolver`.
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
import sys
import threading
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

from src.tools.llm import LLM

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


class TrajectorySerializer:
    """Render a trajectory dict as a human/agent-readable text block."""

    def __init__(self, max_observation_chars: int = 500, max_action_chars: int = 1000):
        self.max_observation_chars = int(max_observation_chars)
        self.max_action_chars = int(max_action_chars)

    def serialize(self, trajectory: dict) -> str:
        lines: List[str] = []
        step_index = 0
        for step in trajectory.get("steps", []):
            if not self._is_action_step(step):
                continue
            step_index += 1
            # 优先用 step 自带的 _display_index（原始 trajectory T 的 step index），
            # 否则回退到 block 内位置计数（v1/v2 旧行为）。
            display_index = step.get("_display_index", step_index)
            lines += [
                f"\n#### Step {display_index}",
                "Action:",
                self._serialize_action(self._action_of(step)),
                "Observation:",
                self._serialize_observation(step.get("observation", "")),
            ]
        return "\n".join(lines)

    # ----- overridable building blocks -----

    @staticmethod
    def _is_action_step(step) -> bool:
        return bool(step.get("tool_calls") or "observation" in step or step.get("action"))

    @staticmethod
    def _action_of(step):
        return step.get("tool_calls") or step.get("action") or step.get("message") or ""

    def _serialize_action(self, action) -> str:
        if isinstance(action, list):
            lines = []
            for call in action:
                lines.append(f"- tool: {call.get('function_name', '')}")
                for key, value in (call.get("arguments") or {}).items():
                    lines.append(f"  {key}: {self._clip(value, self.max_action_chars)}")
            return "\n".join(lines)
        return self._clip(action, self.max_action_chars)

    def _serialize_observation(self, observation) -> str:
        if isinstance(observation, dict) and isinstance(observation.get("results"), list):
            results = observation["results"]
            # Give each result a fair share of the budget so a long first result
            # doesn't starve the rest.
            per_item = max(80, self.max_observation_chars // max(1, len(results)))
            text = "\n".join(self._format_result(item, per_item) for item in results)
        else:
            text = self._clip(observation, self.max_observation_chars)
        return self._clip(text, self.max_observation_chars)

    def _format_result(self, item, max_chars: Optional[int] = None) -> str:
        budget = max_chars if max_chars is not None else self.max_observation_chars
        content = item.get("content", item) if isinstance(item, dict) else item
        try:
            parsed = json.loads(content) if isinstance(content, str) else content
        except json.JSONDecodeError:
            parsed = content
        if not isinstance(parsed, dict):
            return self._clip(parsed, budget)
        parts = []
        if "returncode" in parsed:
            parts.append(f"returncode: {parsed.get('returncode')}")
        if parsed.get("output"):
            parts.append(f"output:\n{parsed.get('output')}")
        if parsed.get("exception_info"):
            parts.append(f"exception_info: {parsed.get('exception_info')}")
        return self._clip("\n".join(parts), budget)

    @staticmethod
    def _clip(value, max_chars: int) -> str:
        text = (
            value if isinstance(value, str)
            else json.dumps(value, ensure_ascii=False, default=str)
        )
        return text if len(text) <= max_chars else text[:max_chars] + "...<truncated>"


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


class EvolvePromptBuilder:
    """Build the prompt sent to the evolution agent."""

    HEADER = [
        "Here are contrastive execution histories. The original trajectory is high-cost, while the minimal trajectory keeps only dependency-critical steps.",
        "Evolve the scripts and instruction.md in this working directory to help future agents solve similar tasks with fewer steps/tokens while preserving correctness.",
        "Each script should live under ./<script_name>/ and contain a main.sh entrypoint plus an intro.json file.",
        "intro.json must be valid JSON with this schema:",
        "  {",
        "    \"name\": \"<script_name>\",",
        "    \"description\": \"what this script does\",",
        "    \"when_to_use\": \"under what conditions to call this script\",",
        "    \"entrypoint\": \"main.sh\",",
        "    \"parameters\": [",
        "      {\"name\": \"...\", \"type\": \"string|int|bool|...\", \"required\": true, \"description\": \"...\"}",
        "    ],",
        "    \"examples\": [{\"call\": \"main.sh <args>\", \"expected\": \"...\"}],",
        "    \"cost_saving_rationale\": \"why this script reduces cost vs. the baseline trajectory\"",
        "  }",
        "instruction.md is for HIGH-LEVEL cost-saving guidance ONLY (e.g. batching multiple tool calls per step to cut round trips, keeping tool output lean, anti-patterns to avoid). Do NOT list or describe specific scripts in instruction.md — each script's usage lives in its own intro.json.",
        "REMEMBER: instruction.md contains **brief** and **high-level** instructions."
    ]
    FOOTER = (
        "\nYour task is to modify, add, or remove scripts in the current directory, "
        "and update instruction.md together with those scripts. "
        "Keep the implementation minimal. Do not edit the prompt file or contrastive sample files. "
        "For every script you add or modify, write a valid intro.json. "
        "For every script you remove, delete its directory. "
        "Finish after the evolved scripts, their intro.json files, and instruction.md are saved."
    )

    def __init__(self, serializer: Optional[TrajectorySerializer] = None):
        self.serializer = serializer or TrajectorySerializer()

    def build(
        self,
        sample_paths: Iterable[Path],
        cwd_name: str = ".",
        scripts_dir: Optional[Path] = None,
    ) -> str:
        parts: List[str] = [
            line.replace("{cwd}", cwd_name) for line in self.HEADER
        ]
        parts.append(
            f"The current working directory is {cwd_name}; evolve scripts in place here."
        )
        if scripts_dir is not None:
            parts += self._current_scripts_block(Path(scripts_dir))
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

    def _current_scripts_block(self, scripts_dir: Path) -> List[str]:
        """List existing scripts and their intro.json so the evolution agent
        can decide what to add, modify, or remove."""
        lines = ["\n# Current scripts in this directory"]
        if not scripts_dir.exists():
            lines.append("(none yet)")
            return lines
        subdirs = sorted(p for p in scripts_dir.iterdir() if p.is_dir())
        if not subdirs:
            lines.append("(none yet)")
            return lines
        for d in subdirs:
            intro = d / "intro.json"
            lines.append(f"\n## ./{d.name}/")
            if intro.exists():
                try:
                    text = intro.read_text(encoding="utf-8").strip()
                except OSError as exc:
                    lines.append(f"(failed to read intro.json: {exc})")
                    continue
                lines.append(f"intro.json:\n{text}")
            else:
                lines.append(
                    "(no intro.json — create one if you keep this script, "
                    "or delete the directory if obsolete)"
                )
        return lines


# ---------------------------------------------------------------------------
# Agent execution
# ---------------------------------------------------------------------------


class AgentRunner:
    """Run an evolution agent on a prepared prompt. Override for new backends."""

    def run(self, prompt: str, prompt_path: Path, output_path: Path, cwd: Path) -> None:
        raise NotImplementedError


class MiniSweAgentRunner(AgentRunner):
    def __init__(self, mini_swe_agent_dir, llm_config, dry_run: bool = False, timeout: int = 3600):
        self.mini_swe_agent_dir = Path(mini_swe_agent_dir).resolve()
        self.llm_config = str(llm_config)
        self.dry_run = bool(dry_run)
        self.timeout = int(timeout)

    def run(self, prompt: str, prompt_path: Path, output_path: Path, cwd: Path) -> None:
        env, model, temperature, model_class = self._load_llm_env()
        task = (
            f"Read the full evolution instruction from {prompt_path}. "
            "Then modify, add, or remove scripts (each with an intro.json) and update instruction.md in the current working directory as requested. "
            "Do not edit the prompt file or contrastive sample files."
        )
        cmd = [
            "uv", "run", "--directory", str(self.mini_swe_agent_dir),
            "mini",
            "-m", model,
            "--model-class", model_class,
            "--environment-class", "local",
            "-y", "--exit-immediately",
            "--cost-limit", "0",
            "-o", str(output_path),
            "-t", task,
            "-c", "mini.yaml",
        ]
        if temperature is not None:
            cmd += ["-c", f"model.model_kwargs.temperature={temperature}"]
        logger.info("mini-swe-agent cmd: %s", " ".join(shlex.quote(x) for x in cmd))
        logger.info("prompt %s to %s", "would be saved" if self.dry_run else "saved", prompt_path)
        if self.dry_run:
            return
        output_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text(prompt, encoding="utf-8")
        self._run_mini_swe(cmd, cwd, {**os.environ, **env})

    def _run_mini_swe(self, cmd: list, cwd: Path, env: dict) -> None:
        """跑 mini-swe-agent 子进程，stdout/stderr 实时流式回显到父进程 stderr，
        让 evolve 过程有可见进度（而非 capture_output 静默几分钟）。

        - Popen + 后台线程逐行 pump：边读边写 sys.stderr，同时累积用于失败 tail。
        - 主线程 ``proc.wait(timeout)`` 保住超时语义；超时则 kill 并抛 RuntimeError。
        - agent 的完整 trajectory 已由 mini-swe-agent 自己写到 ``--output`` 路径，
          这里流式输出的文本只用于实时进度 + 失败时的 tail 诊断。
        """
        logger.info("mini-swe-agent starting (cwd=%s, timeout=%ds)", cwd, self.timeout)
        # PYTHONUNBUFFERED=1：mini-swe-agent 是 Python，stdout 接 PIPE 时默认块缓冲，
        # 会让逐行流式退化成 4KB 一坨；强制 unbuffered 才能真正实时回显进度。
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            env={**env, "PYTHONUNBUFFERED": "1"},
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        captured: list = []

        def _pump() -> None:
            assert proc.stdout is not None
            for line in proc.stdout:
                captured.append(line)
                sys.stderr.write(line)
                sys.stderr.flush()

        reader = threading.Thread(target=_pump, daemon=True)
        reader.start()
        try:
            rc = proc.wait(timeout=self.timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=10)
            except Exception:
                pass
            reader.join(timeout=5)
            logger.error(
                "mini-swe-agent timed out after %ds (cwd=%s)", self.timeout, cwd
            )
            raise RuntimeError(f"mini-swe-agent timed out after {self.timeout}s")
        reader.join(timeout=5)
        out = "".join(captured)
        if rc != 0:
            logger.error(
                "mini-swe-agent failed (rc=%d, cwd=%s); streamed output above. tail:\n%s",
                rc,
                cwd,
                out[-2000:],
            )
            raise RuntimeError(f"mini-swe-agent exited with code {rc}")
        logger.info("mini-swe-agent finished (rc=0, cwd=%s)", cwd)

    def _load_llm_env(self):
        cfg = LLM._load_config(self.llm_config)
        api_type = (cfg.get("api_type") or "chat").strip().lower()
        try:
            temperature = float(cfg.get("temperature", 0))
        except (TypeError, ValueError):
            temperature = None
        name = cfg.get("llm_name") or cfg.get("model")
        if api_type == "responses":
            # 网关只暴露 Responses API：mini-swe-agent 走 litellm_response +
            # litellm.responses(azure/...)，由 AZURE_API_BASE/API_VERSION/API_KEY 路由。
            env = {
                "AZURE_API_KEY": cfg.get("key", ""),
                "AZURE_API_BASE": cfg.get("azure_endpoint", ""),
                "AZURE_API_VERSION": cfg.get("api_version") or "2024-03-01-preview",
                "MSWEA_COST_TRACKING": "ignore_errors",
            }
            return env, f"azure/{name}", temperature, "litellm_response"
        env = {
            "OPENAI_API_KEY": cfg.get("key", ""),
            "OPENAI_BASE_URL": cfg.get("openai_base_url", ""),
            "OPENAI_API_BASE": cfg.get("openai_base_url", ""),
            "MSWEA_COST_TRACKING": "ignore_errors",
        }
        return env, f"openai/{name}", temperature, "litellm"


# ---------------------------------------------------------------------------
# Pipeline stage
# ---------------------------------------------------------------------------


class ScriptEvolver:
    """Pipeline stage: batch contrastive samples and run the evolution agent."""

    name = "evolve"

    DEFAULT_INSTRUCTION = (
        "# Cost-saving instructions\n\n"
        "High-level guidance for reducing future agent cost: e.g. batching multiple "
        "tool calls per step to cut round trips, keeping tool output lean and "
        "on-topic, anti-patterns observed in past trajectories.\n\n"
        "Do NOT describe specific scripts here — each script's usage lives in its "
        "own ./<script_name>/intro.json.\n"
    )

    def __init__(
        self,
        scripts_dir,
        runner: AgentRunner,
        prompt_builder: Optional[EvolvePromptBuilder] = None,
        batch_size: int = 5,
        output_dir: Optional[Path] = None,
        resume: bool = True,
    ):
        self.scripts_dir = Path(scripts_dir).resolve()
        self.runner = runner
        self.prompt_builder = prompt_builder or EvolvePromptBuilder()
        self.batch_size = int(batch_size)
        self.output_dir = Path(output_dir).resolve() if output_dir else None
        self.resume = bool(resume)

    def run(self, result_dir, task: Optional[str] = None) -> Path:
        result_dir = Path(result_dir).resolve()
        self.scripts_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_instruction_file()

        output_dir = self.output_dir or (result_dir / "evolve_logs")
        output_dir.mkdir(parents=True, exist_ok=True)
        samples = self.find_samples(result_dir, task)
        logger.info("found %d contrastive samples", len(samples))
        if not samples:
            logger.warning(
                "no contrastive samples found under %s (task=%r); evolve stage is a no-op",
                result_dir,
                task,
            )
            return output_dir

        failures: List[int] = []
        for batch_id, batch in enumerate(self._batched(samples, self.batch_size), start=1):
            output_path = output_dir / f"evolve_batch_{batch_id}.traj.json"
            prompt_path = output_path.with_suffix(".prompt.md")
            sentinel = output_path.with_suffix(".done")
            if self.resume and sentinel.exists():
                logger.info("batch %d already done (sentinel %s exists), skipping", batch_id, sentinel)
                continue
            try:
                self.runner.run(
                    prompt=self.prompt_builder.build(
                        batch,
                        cwd_name=self.scripts_dir.name,
                        scripts_dir=self.scripts_dir,
                    ),
                    prompt_path=prompt_path,
                    output_path=output_path,
                    cwd=self.scripts_dir,
                )
                sentinel.write_text(
                    json.dumps({"batch_id": batch_id, "samples": [str(p) for p in batch]}),
                    encoding="utf-8",
                )
            except Exception as exc:
                logger.exception("batch %d failed: %s", batch_id, exc)
                failures.append(batch_id)
                continue
        if failures:
            logger.warning("batches failed: %s", failures)
        else:
            logger.info("all batches finished")
        for w in self._validate_intros():
            logger.warning("intro.json: %s", w)
        return output_dir

    def find_samples(self, result_dir, task=None) -> List[Path]:
        files = sorted(Path(result_dir).glob("**/agent/contrastive_sample.json"))
        if not task:
            return files
        # Match task id on a path boundary — bare substring match would catch
        # unrelated directories sharing a fragment.
        matched = []
        for p in files:
            stem = p.parent.parent.name  # the <task_id> dir above agent/
            if stem == task or stem.startswith(f"{task}__") or task in stem.split("__"):
                matched.append(p)
        if not matched:
            # fall back to substring match so short ids still work
            matched = [p for p in files if task in str(p)]
        return matched

    # ---------- helpers ----------

    REQUIRED_INTRO_FIELDS = (
        "name",
        "description",
        "when_to_use",
        "entrypoint",
        "parameters",
        "examples",
        "cost_saving_rationale",
    )

    def _ensure_instruction_file(self) -> None:
        path = self.scripts_dir / "instruction.md"
        if path.exists():
            return
        # O_EXCL-style write to avoid races across concurrent evolve processes
        import os
        fd = None
        try:
            fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(self.DEFAULT_INSTRUCTION)
            fd = None
        except FileExistsError:
            pass
        finally:
            if fd is not None:
                os.close(fd)

    def _validate_intros(self) -> List[str]:
        """Scan <scripts_dir>/*/intro.json and check schema conformance.

        Returns a list of human-readable warning strings; an empty list means
        every script directory has a schema-valid intro.json. This is a soft
        check — it warns, not raises, so a partially-broken evolve run still
        surfaces its problems without aborting.
        """
        warnings: List[str] = []
        if not self.scripts_dir.exists():
            return warnings
        for d in sorted(self.scripts_dir.iterdir()):
            if not d.is_dir():
                continue
            intro = d / "intro.json"
            if not intro.exists():
                warnings.append(f"{d}: missing intro.json")
                continue
            try:
                data = json.loads(intro.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                warnings.append(f"{intro}: invalid JSON: {exc}")
                continue
            if not isinstance(data, dict):
                warnings.append(f"{intro}: top-level is not a JSON object")
                continue
            missing = [f for f in self.REQUIRED_INTRO_FIELDS if f not in data]
            if missing:
                warnings.append(f"{intro}: missing fields: {', '.join(missing)}")
            entrypoint = data.get("entrypoint")
            if isinstance(entrypoint, str) and entrypoint and not (d / entrypoint).exists():
                warnings.append(
                    f"{intro}: entrypoint '{entrypoint}' does not exist in {d}"
                )
            if "parameters" in data and not isinstance(data["parameters"], list):
                warnings.append(f"{intro}: 'parameters' must be a list")
            if "examples" in data and not isinstance(data["examples"], list):
                warnings.append(f"{intro}: 'examples' must be a list")
        return warnings

    @staticmethod
    def _batched(items: Sequence[Path], batch_size: int):
        for i in range(0, len(items), batch_size):
            yield items[i : i + batch_size]
