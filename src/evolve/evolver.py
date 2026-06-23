"""Stage 3: evolve scripts/instructions from contrastive samples.

Each batch of contrastive samples is rendered into a prompt that asks
mini-swe-agent to evolve the per-script directories and `instruction.md`
inside an `.evolve_scripts/` working directory.
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
from pathlib import Path
from typing import Iterable, List

from src.tools.llm import LLM

logger = logging.getLogger(__name__)


class ScriptEvolver:
    DEFAULT_INSTRUCTION = (
        "# Cost-saving instructions\n\n"
        "Record reusable high-level guidance for reducing future agent cost here.\n"
    )

    def __init__(
        self,
        mini_swe_agent_dir,
        llm_config,
        scripts_dir,
        batch_size: int = 5,
        max_observation_chars: int = 500,
        dry_run: bool = False,
    ):
        self.mini_swe_agent_dir = Path(mini_swe_agent_dir).resolve()
        self.llm_config = str(llm_config)
        self.scripts_dir = Path(scripts_dir).resolve()
        self.batch_size = int(batch_size)
        self.max_observation_chars = int(max_observation_chars)
        self.dry_run = bool(dry_run)

    # ---------- public API ----------

    def find_samples(self, result_dir, task=None):
        files = sorted(Path(result_dir).glob("**/agent/contrastive_sample.json"))
        return [p for p in files if not task or task in str(p)]

    def evolve_dir(self, result_dir, output_dir=None, task=None):
        self.scripts_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_instruction_file()
        output_dir = (
            Path(output_dir).resolve()
            if output_dir
            else Path(result_dir).resolve() / "evolve_logs"
        )
        samples = self.find_samples(result_dir, task)
        logger.info("found %d contrastive samples", len(samples))
        for batch_id, sample_paths in enumerate(self._batched(samples, self.batch_size), start=1):
            self._run_agent(
                self._build_prompt(sample_paths),
                output_dir / f"evolve_batch_{batch_id}.traj.json",
            )
        return output_dir

    # ---------- prompt construction ----------

    def _build_prompt(self, sample_paths: Iterable[Path]) -> str:
        parts = [
            "Here are contrastive execution histories. The original trajectory is high-cost, while the minimal trajectory keeps only dependency-critical steps.",
            "Evolve the scripts and instruction.md in this working directory to help future agents solve similar tasks with fewer steps/tokens while preserving correctness.",
            "The current working directory is .evolve_scripts; each script should live under ./<script_name>/ and may contain Python files plus a main.sh entrypoint.",
            "Also maintain ./instruction.md with high-level cost-saving guidance: operations to avoid, when to combine multiple actions, how to inspect files/logs efficiently, and when an evolved script should be used.",
        ]
        for i, path in enumerate(sample_paths, start=1):
            negative, positive = self._load_sample(path)
            parts += [
                f"\n# Executional History {i}",
                f"Source: {path}",
                "\n## Original Trajectory",
                negative,
                "\n## Minimal Trajectory",
                positive,
            ]
        parts.append(
            "\nYour task is to modify, add, or remove scripts in the current directory, and update instruction.md together with those scripts. "
            "Keep the implementation minimal. Do not edit the prompt file or contrastive sample files. "
            "Finish after the evolved scripts and instruction.md are saved."
        )
        return "\n".join(parts)

    def _load_sample(self, path):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return (
            self._serialize_trajectory(data["negative_sample"]),
            self._serialize_trajectory(data["positive_sample"]),
        )

    def _serialize_trajectory(self, trajectory):
        lines: List[str] = []
        if trajectory.get("minimal_step_indices"):
            lines.append(
                "Minimal step indices: "
                + ", ".join(map(str, trajectory["minimal_step_indices"]))
            )
        dependencies = trajectory.get("dependencies", {})
        step_index = 0
        for i, step in enumerate(trajectory.get("steps", [])):
            if step.get("tool_calls") or "observation" in step or step.get("action"):
                step_index += 1
                lines += [
                    f"\n### Step {step_index} (source_step_id={step.get('step_id', i)})",
                    f"Depends on: {', '.join(map(str, dependencies.get(str(step_index), []))) or 'none'}",
                    "Action:",
                    self._serialize_action(self._action_of(step)),
                    "Observation:",
                    self._serialize_observation(step.get("observation", "")),
                ]
        return "\n".join(lines)

    @staticmethod
    def _action_of(step):
        return step.get("tool_calls") or step.get("action") or step.get("message") or ""

    @classmethod
    def _serialize_action(cls, action):
        if isinstance(action, list):
            lines = []
            for call in action:
                lines.append(f"- tool: {call.get('function_name', '')}")
                for key, value in (call.get("arguments") or {}).items():
                    lines.append(f"  {key}: {cls._clip_text(value, 1000)}")
            return "\n".join(lines)
        return cls._clip_text(action, 1000)

    def _serialize_observation(self, observation):
        max_chars = self.max_observation_chars
        if isinstance(observation, dict) and isinstance(observation.get("results"), list):
            texts = []
            for item in observation["results"]:
                content = item.get("content", item) if isinstance(item, dict) else item
                try:
                    parsed = json.loads(content) if isinstance(content, str) else content
                except json.JSONDecodeError:
                    parsed = content
                if isinstance(parsed, dict):
                    texts.append(
                        "\n".join(
                            part
                            for part in [
                                f"returncode: {parsed.get('returncode')}"
                                if "returncode" in parsed
                                else "",
                                f"output:\n{parsed.get('output')}"
                                if parsed.get("output")
                                else "",
                                f"exception_info: {parsed.get('exception_info')}"
                                if parsed.get("exception_info")
                                else "",
                            ]
                            if part
                        )
                    )
                else:
                    texts.append(self._clip_text(parsed, max_chars))
            text = "\n".join(texts)
        else:
            text = self._clip_text(observation, max_chars)
        return self._clip_text(text, max_chars)

    @staticmethod
    def _clip_text(value, max_chars):
        text = (
            json.dumps(value, ensure_ascii=False, default=str)
            if not isinstance(value, str)
            else value
        )
        return text if len(text) <= max_chars else text[:max_chars] + "...<truncated>"

    @staticmethod
    def _batched(items, batch_size):
        for i in range(0, len(items), batch_size):
            yield items[i : i + batch_size]

    # ---------- agent invocation ----------

    def _ensure_instruction_file(self):
        path = self.scripts_dir / "instruction.md"
        if not path.exists():
            path.write_text(self.DEFAULT_INSTRUCTION, encoding="utf-8")

    def _load_llm_env(self):
        cfg = LLM._load_config(self.llm_config)
        env = {
            "OPENAI_API_KEY": cfg.get("key", ""),
            "OPENAI_BASE_URL": cfg.get("openai_base_url", ""),
            "OPENAI_API_BASE": cfg.get("openai_base_url", ""),
            "MSWEA_COST_TRACKING": "ignore_errors",
        }
        model = f"openai/{cfg.get('llm_name') or cfg.get('model')}"
        return env, model

    def _run_agent(self, prompt: str, output: Path):
        env, model = self._load_llm_env()
        prompt_path = output.with_suffix(".prompt.md")
        task = (
            f"Read the full evolution instruction from {prompt_path}. "
            "Then modify, add, or remove scripts and update instruction.md in the current working directory as requested. "
            "Do not edit the prompt file or contrastive sample files."
        )
        cmd = [
            "uv",
            "run",
            "--directory",
            str(self.mini_swe_agent_dir),
            "mini",
            "-m",
            model,
            "--model-class",
            "litellm",
            "--environment-class",
            "local",
            "-y",
            "--exit-immediately",
            "--cost-limit",
            "0",
            "-o",
            str(output),
            "-t",
            task,
        ]
        print("+", " ".join(shlex.quote(x) for x in cmd))
        print(f"prompt {'would be saved' if self.dry_run else 'saved'} to {prompt_path}")
        if self.dry_run:
            return
        output.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text(prompt, encoding="utf-8")
        subprocess.run(
            cmd, cwd=self.scripts_dir, env={**os.environ, **env}, check=True
        )
