"""Evolve v9: cost-aware harness evolution with auditable evidence.

v9 keeps the v6 native registry/runtime contract (``tools.json`` +
``executor.py`` + ``instruction.md``), but replaces the v6 batch prompt with a
cycle-scoped evidence compiler, staging validation, a paired promotion gate,
and rollback feedback.  The module also contains an offline compatibility
command which reconstructs v9 compiler prompts from preserved v6 contrastive
samples without invoking an LLM or changing the historical results.

The implementation intentionally reuses stable transport/runtime components
from v6 and small parsing helpers from v8.  It does *not* impose a tool-type or
tool-name whitelist: candidate workflows are derived from normalized actions,
including previously unseen commands.  Runtime capability is constrained by
validation and budgets rather than by restricting discovery to two tool kinds.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import logging
import math
import os
import random
import re
import shlex
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict, deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

from src.tools.llm import LLM

from .annotator import TrajectoryAnnotator
from .evolve_v6_cycle import (
    BENCHMARKS,
    DEFAULT_MINI_SWE_AGENT,
    RolloutAgent,
    _llm_api_type,
    _max_completion_tokens,
)
from .evolver import MiniSweAgentRunner
from .native_tools_v6 import deploy as deploy_v6
from .run_evolve import _setup_logging

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_VERSION = "cahe.v9.0"
REGISTRY_FILES = ("tools.json", "executor.py", "instruction.md")
DEFAULT_OUTPUT_TOKEN_CAP = 1000
DEFAULT_TOOL_TIMEOUT_SECONDS = 30
DEFAULT_MEMORY_MB = 1024

BASELINE_RULES = (
    "Use the smallest operation that can answer the current question, and narrow scope after a broad or failed result.",
    "Do not repeat an unchanged failed call; adjust its scope or parameters, or use an equivalent bash command.",
    "Preserve correctness checks for destructive, security, data-integrity, public API, schema, and broad changes.",
)


# ---------------------------------------------------------------------------
# Stable IO and data models
# ---------------------------------------------------------------------------


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _stable_hash(value: Any, length: int = 20) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()[:length]


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value.rstrip() + "\n", encoding="utf-8")


def _action_steps(trajectory: Mapping[str, Any]) -> list[dict]:
    return [
        step for step in trajectory.get("steps", [])
        if isinstance(step, Mapping)
        and (step.get("tool_calls") or "observation" in step or step.get("action"))
    ]


def _trajectory_files(run_dir: Path) -> list[Path]:
    direct = sorted(Path(run_dir).glob("**/agent/trajectory.json"))
    return direct or sorted(Path(run_dir).glob("**/trajectory.json"))


def _message_usage_totals(trajectory: Mapping[str, Any]) -> dict[str, int]:
    """Sum persisted per-request usage without trusting provider cost fields."""
    totals = {"prompt_tokens": 0, "cached_tokens": 0,
              "completion_tokens": 0, "api_calls": 0}
    for message in trajectory.get("messages", []):
        if not isinstance(message, Mapping):
            continue
        extra = message.get("extra") if isinstance(message.get("extra"), Mapping) else {}
        response = extra.get("response") if isinstance(extra.get("response"), Mapping) else {}
        usage = response.get("usage") if isinstance(response.get("usage"), Mapping) else None
        if usage is None:
            usage = message.get("usage") if isinstance(message.get("usage"), Mapping) else None
        if usage is None:
            continue
        prompt = int(usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0)
        completion = int(usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0)
        details = usage.get("prompt_tokens_details")
        details = details if isinstance(details, Mapping) else {}
        cached = int(details.get(
            "cached_tokens",
            usage.get("prompt_cache_hit_tokens", usage.get("cache_read_input_tokens", 0)),
        ) or 0)
        prompt = max(0, prompt)
        totals["prompt_tokens"] += prompt
        totals["cached_tokens"] += min(prompt, max(0, cached))
        totals["completion_tokens"] += max(0, completion)
        totals["api_calls"] += 1
    return totals


@dataclass(frozen=True)
class ToolCallNode:
    node_id: str
    task_id: str
    step_id: int
    call_index: int
    tool_call_id: Optional[str]
    tool_name: str
    arguments: dict
    normalized_action: dict
    observation_text: str
    observation_tokens: int
    future_llm_calls: int
    estimated_future_cost: float
    dependency_state: str
    returncode: Optional[int]
    exception_info: str
    source_path: str
    quality_score: float


@dataclass(frozen=True)
class PatternOccurrence:
    task_id: str
    node_ids: tuple[str, ...]
    estimated_benefit: float
    action_signature: str
    source_path: str


@dataclass
class PatternCard:
    candidate_id: str
    pattern_type: str
    support_tasks: list[str]
    occurrences: list[PatternOccurrence]
    total_benefit: float
    median_benefit: float
    evidence_quality: float
    expected_replacement: str
    positive_examples: list[dict]
    negative_controls: list[dict]


@dataclass(frozen=True)
class TaskMetrics:
    task_id: str
    success: bool
    primary_score: float
    api_cost: Optional[float]
    new_input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    llm_calls: int
    native_tool_calls: int
    native_tool_failures: int
    error: Optional[str]
    raw_verifier: dict = field(default_factory=dict)
    cost_source: str = "token_usage_x_configured_price"


@dataclass
class GateDecision:
    promote: bool
    reasons: list[str]
    paired_task_count: int
    success_regressions: list[str]
    success_improvements: list[str]
    mean_cost_delta: float
    cost_saving_ratio: float
    bootstrap_interval: tuple[float, float]


@dataclass(frozen=True)
class PriceModel:
    """Auditable per-million-token prices loaded from the experiment config."""

    uncached_input: float = 1.0
    cached_input: float = 0.1
    completion: float = 1.0
    chars_per_token: float = 4.0

    @classmethod
    def from_config(cls, path: Path | str) -> "PriceModel":
        config = LLM._load_config(str(path))
        nested = config.get("price_yuan_per_million_token") or {}
        if not isinstance(nested, Mapping):
            nested = {}
        prices = {
            **{key: config.get(key) for key in ("input_token", "cached_token", "output_token")
               if config.get(key) not in (None, "")},
            **nested,
        }
        return cls(
            uncached_input=float(prices.get("input_token", 1.0)),
            cached_input=float(prices.get("cached_token", 0.1)),
            completion=float(prices.get("output_token", 1.0)),
        )

    def calculate(self, prompt_tokens: int, cached_tokens: int,
                  completion_tokens: int) -> float:
        """Calculate cost from usage and configured per-million-token prices."""
        prompt = max(0, int(prompt_tokens or 0))
        cached = min(prompt, max(0, int(cached_tokens or 0)))
        completion = max(0, int(completion_tokens or 0))
        return (
            (prompt - cached) * self.uncached_input
            + cached * self.cached_input
            + completion * self.completion
        ) / 1_000_000.0


class CostLedger:
    """Attribute direct API usage and future observation exposure per LLM turn."""

    def __init__(self, prices: PriceModel = PriceModel()):
        self.prices = prices

    def annotate(self, steps: Sequence[Mapping[str, Any]]) -> list[dict]:
        usages = [self._usage(step) for step in steps]
        cache_ratios = [usage["cached_tokens"] / usage["prompt_tokens"]
                        if usage["prompt_tokens"] else 0.0 for usage in usages]
        rows = []
        for index, (step, usage) in enumerate(zip(steps, usages)):
            observation_tokens = math.ceil(
                len(self._observation_text(step.get("observation"))) / self.prices.chars_per_token
            )
            exposure = 0.0
            for offset, cache_ratio in enumerate(cache_ratios[index + 1:], 1):
                price = (self.prices.uncached_input if offset == 1 else
                         cache_ratio * self.prices.cached_input
                         + (1.0 - cache_ratio) * self.prices.uncached_input)
                exposure += observation_tokens * price
            direct = (
                max(0, usage["prompt_tokens"] - usage["cached_tokens"]) * self.prices.uncached_input
                + usage["cached_tokens"] * self.prices.cached_input
                + usage["completion_tokens"] * self.prices.completion
            )
            rows.append({"usage": usage, "observation_tokens": observation_tokens,
                         "direct_cost": direct, "exposure_cost": exposure,
                         "total_cost": direct + exposure})
        return rows

    @staticmethod
    def _usage(step: Mapping[str, Any]) -> dict[str, int]:
        candidates = [step[key] for key in ("usage", "model_usage", "metrics")
                      if isinstance(step.get(key), Mapping)]
        info = step.get("info")
        if isinstance(info, Mapping):
            candidates.append(info)
            candidates.extend(info[key] for key in ("usage", "model_usage", "metrics")
                              if isinstance(info.get(key), Mapping))
        merged: dict[str, Any] = {}
        for candidate in candidates:
            merged.update(candidate)
        prompt = int(merged.get("prompt_tokens", merged.get("input_tokens", 0)) or 0)
        cached = int(merged.get("cached_tokens", merged.get("cache_read_input_tokens", 0)) or 0)
        completion = int(merged.get("completion_tokens", merged.get("output_tokens", 0)) or 0)
        return {"prompt_tokens": max(0, prompt), "cached_tokens": min(max(0, prompt), max(0, cached)),
                "completion_tokens": max(0, completion)}

    @staticmethod
    def _observation_text(observation: Any) -> str:
        if isinstance(observation, Mapping) and isinstance(observation.get("results"), list):
            parts = []
            for item in observation["results"]:
                content = item.get("content", item) if isinstance(item, Mapping) else item
                if isinstance(content, str):
                    try:
                        content = json.loads(content)
                    except json.JSONDecodeError:
                        pass
                if isinstance(content, Mapping):
                    parts.append(str(content.get("output", content)))
                else:
                    parts.append(str(content))
            return "\n".join(parts)
        return str(observation or "")


def run_compile_agent(prompt: str, work_dir: Path, staging_dir: Path, config: str,
                      mini_swe_agent_dir: Path, dry_run: bool = False,
                      output_name: str = "compile_trajectory.json") -> None:
    """Use the v6 mini-swe transport while keeping the v9 prompt and artifacts."""
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = (work_dir / "compile_prompt.md").resolve()
    output_path = (work_dir / output_name).resolve()
    runner = MiniSweAgentRunner(mini_swe_agent_dir, config, dry_run=dry_run)
    env, model, temperature, model_class = runner._load_llm_env()
    task = (
        f"Read {prompt_path} completely. Implement exactly the candidate-harness changes it authorizes "
        "in the current staging directory. Edit only tools.json, executor.py, instruction.md, and "
        "change_manifest.json. Do not create intro.json, main.sh, per-tool directories, or edit evidence."
    )
    command = [
        "mini", "-m", model, "--model-class", model_class, "--environment-class", "local",
        "-y", "--exit-immediately", "--cost-limit", "0", "-o", str(output_path),
        "-t", task, "-c", "mini.yaml",
    ]
    if temperature is not None:
        command += ["-c", f"model.model_kwargs.temperature={temperature}"]
    if dry_run:
        logger.info("dry-run compiler command: %s", " ".join(shlex.quote(item) for item in command))
        return
    prompt_path.write_text(prompt, encoding="utf-8")
    runner._run_mini_swe(command, Path(staging_dir), {**os.environ, **env})


# ---------------------------------------------------------------------------
# Open-ended action normalization
# ---------------------------------------------------------------------------


class ActionNormalizerV9:
    """Normalize observed actions without restricting the candidate vocabulary.

    Known commands receive stable semantic labels.  An unknown executable is
    retained as ``command:<name>`` instead of being rejected; this is what keeps
    discovery open-ended.  Validation later decides whether a generated
    implementation is safe and bounded.
    """

    COMMAND_GROUPS = {
        "search": {"rg", "grep", "egrep", "fgrep", "ag", "ack"},
        "discover": {"find", "fd", "locate", "ls", "tree", "pwd"},
        "read": {"cat", "head", "tail", "sed", "awk", "nl", "wc", "stat", "file"},
        "edit": {"apply_patch", "patch", "tee", "rm", "mv", "cp", "mkdir", "touch", "chmod", "chown", "ln"},
        "verify": {"pytest", "tox", "nosetests", "ruff", "mypy", "eslint", "tsc"},
        "version_control": {"git", "hg", "svn"},
        "build": {"make", "cmake", "ninja", "cargo", "go", "mvn", "gradle", "gradlew"},
        "package": {"pip", "pip3", "npm", "yarn", "pnpm", "poetry", "uv"},
        "interpreter": {"python", "python3", "node", "ruby", "perl", "php"},
        "database": {"sqlite3", "psql", "mysql", "mongosh", "redis-cli"},
        "network": {"curl", "wget", "ssh", "scp", "rsync"},
        "context": {"echo", "printf", "true", "false", "date", "whoami", "which", "type"},
        "submit": {"submit", "final", "complete_task_and_submit_final_output"},
    }
    SHELL_NAMES = {"bash", "shell", "terminal", "exec", "exec_command"}

    def normalize_call(self, call: Mapping[str, Any]) -> dict:
        name = str(call.get("function_name") or call.get("name") or call.get("tool") or "").strip()
        arguments = call.get("arguments") or call.get("args") or {}
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {"command": arguments}
        if not isinstance(arguments, Mapping):
            arguments = {"value": str(arguments)}
        command = str(arguments.get("command") or arguments.get("cmd") or arguments.get("script") or "")
        operations: list[str]
        commands: list[str]
        if name.lower() in self.SHELL_NAMES or (not name and command):
            commands = self._shell_commands(command)
            operations = self._shell_operations(command, commands)
        else:
            commands = [name.lower().replace("_", "-")] if name else []
            operations = [self._semantic_native(name)] if name else ["unknown_action"]
        roles = sorted(self._argument_roles(arguments))
        path_roles = sorted(self._path_roles(arguments))
        operations = operations or ["context_only"]
        return {
            "operations": operations,
            "commands": commands,
            "argument_roles": roles,
            "path_roles": path_roles,
            "signature": "+".join(operations) + "|" + ",".join(roles) + "|" + ",".join(path_roles),
        }

    @classmethod
    def _semantic_command(cls, command: str) -> str:
        value = Path(command).name.lower()
        for group, names in cls.COMMAND_GROUPS.items():
            if value in names:
                return group
        return f"command:{re.sub(r'[^a-z0-9_.-]+', '-', value)[:40] or 'unknown'}"

    @classmethod
    def _semantic_native(cls, name: str) -> str:
        value = name.lower().replace("_", "-")
        for token, semantic in (
            ("search", "search"), ("grep", "search"), ("find", "discover"),
            ("list", "discover"), ("read", "read"), ("show", "read"),
            ("edit", "edit"), ("write", "edit"), ("patch", "edit"),
            ("test", "verify"), ("check", "verify"), ("verify", "verify"),
        ):
            if token in value:
                return semantic
        return f"native:{re.sub(r'[^a-z0-9_.-]+', '-', value)[:40] or 'unknown'}"

    @staticmethod
    def _shell_commands(command: str) -> list[str]:
        heredoc = re.search(r"(?:^|\s)\d*<<-?\s*['\"]?[A-Za-z_][A-Za-z0-9_]*", command)
        if heredoc:
            # The heredoc body is data/code passed to the leading executable,
            # not a sequence of shell commands.  The compiler may still reject
            # arbitrary-code tools; discovery simply records the interpreter.
            command = command[:heredoc.start()]
        # Redirection bookkeeping is not an executable.  Removing it before
        # tokenization prevents ``2>&1`` from creating spurious command:1
        # actions while retaining every substantive pipeline command.
        command = re.sub(r"(?<!\S)\d*(?:>>?|<<?)(?:&\d+)?(?=\s|$)", " ", command)
        command = re.sub(r"(?<!\S)\d*>&\d+(?=\s|$)", " ", command)
        try:
            lexer = shlex.shlex(command, posix=True, punctuation_chars="|;&")
            lexer.whitespace_split = True
            tokens = list(lexer)
        except ValueError:
            return ["unparsed-shell"]
        commands: list[str] = []
        at_start = True
        skip_arg = False
        for token in tokens:
            if token in {"|", ";", "&&", "||", "&"}:
                at_start = True
                skip_arg = False
                continue
            if not at_start:
                continue
            if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", token):
                continue
            value = Path(token).name.lower()
            if value in {"cd", "export", "env", "sudo", "timeout"}:
                skip_arg = value in {"cd", "timeout"}
                at_start = False if skip_arg else True
                continue
            if skip_arg:
                skip_arg = False
                at_start = True
                continue
            commands.append(value)
            at_start = False
        return commands

    @classmethod
    def _shell_operations(cls, raw_command: str, commands: Sequence[str]) -> list[str]:
        operations = [cls._semantic_command(item) for item in commands]
        lowered = raw_command.lower()
        test_patterns = (
            r"\bpython(?:3(?:\.\d+)?)?\s+-m\s+(?:pytest|unittest|nose)",
            r"\b(?:go|cargo)\s+test\b", r"\b(?:npm|yarn|pnpm)\s+(?:run\s+)?test\b",
            r"\b(?:mvn|gradle|gradlew|make)\s+[^;&|]*\btest\b",
        )
        if any(re.search(pattern, lowered) for pattern in test_patterns):
            operations = ["verify" if operation in {"interpreter", "build", "package"}
                          else operation for operation in operations]
        if re.search(r"\bsed\b[^;&|]*\s-i(?:\s|$)", lowered):
            operations = ["edit" if command == "sed" else operation
                          for command, operation in zip(commands, operations)]
        return operations

    @staticmethod
    def _argument_roles(arguments: Mapping[str, Any]) -> set[str]:
        roles: set[str] = set()
        for key in arguments:
            low = str(key).lower()
            if any(x in low for x in ("query", "pattern", "regex", "symbol", "needle")):
                roles.add("query")
            elif any(x in low for x in ("path", "file", "cwd", "dir", "root")):
                roles.add("path")
            elif any(x in low for x in ("line", "start", "end", "head", "tail", "offset", "cursor")):
                roles.add("range")
            elif any(x in low for x in ("test", "check", "target", "label")):
                roles.add("target")
            elif any(x in low for x in ("command", "cmd", "script")):
                roles.add("command")
            else:
                roles.add("option")
        return roles

    @staticmethod
    def _path_roles(arguments: Mapping[str, Any]) -> set[str]:
        text = _stable_json(arguments).lower().replace("\\", "/")
        roles: set[str] = set()
        if "test" in text:
            roles.add("test")
        if any(ext in text for ext in (".py", ".js", ".ts", ".go", ".rs", ".java", ".c", ".h")):
            roles.add("source")
        if any(ext in text for ext in (".json", ".yaml", ".yml", ".toml", ".ini", ".cfg")):
            roles.add("config")
        if any(ext in text for ext in (".md", ".rst", ".txt")):
            roles.add("document")
        return roles or {"repository"}


# ---------------------------------------------------------------------------
# Evidence construction and quality filtering
# ---------------------------------------------------------------------------


class ContrastiveEvidenceBuilderV9:
    def __init__(self, prices: PriceModel = PriceModel(), *, output_token_cap: int = DEFAULT_OUTPUT_TOKEN_CAP):
        self.prices = prices
        self.output_token_cap = output_token_cap
        self.normalizer = ActionNormalizerV9()

    def build_from_samples(self, sample_paths: Sequence[Path]) -> dict:
        nodes: list[ToolCallNode] = []
        tasks: list[dict] = []
        rejected: list[dict] = []
        for path in sorted(Path(item) for item in sample_paths):
            sample = _read_json(path, {}) or {}
            negative = sample.get("negative_sample") or {}
            positive = sample.get("positive_sample") or {}
            task = self._task_id(path, negative)
            success, verifier = self._task_outcome(path)
            minimal = positive.get("minimal_step_indices")
            if not isinstance(minimal, list):
                minimal = self._trace_minimal_indices(negative.get("dependencies") or {})
            parsed_nodes, quality = self._parse_trajectory(
                negative, task, path, minimal_indices=minimal, task_success=success
            )
            nodes.extend(parsed_nodes)
            task_record = {
                "task_id": task,
                "source_path": str(path),
                "task_success": success,
                "raw_verifier": verifier,
                **quality,
            }
            tasks.append(task_record)
            if not quality["usable_for_tool_patterns"]:
                rejected.append(task_record)
        return {
            "schema_version": SCHEMA_VERSION,
            "source_type": "v6_contrastive_samples",
            "sample_count": len(tasks),
            "tasks": sorted(tasks, key=lambda item: item["task_id"]),
            "nodes": [asdict(node) for node in nodes],
            "rejected_samples": sorted(rejected, key=lambda item: item["task_id"]),
        }

    def build_from_run(self, run_dir: Path) -> dict:
        """Build evidence in memory; never writes contrastive files into a run."""
        nodes: list[ToolCallNode] = []
        tasks: list[dict] = []
        rejected: list[dict] = []
        for path in _trajectory_files(Path(run_dir)):
            trajectory = _read_json(path, {}) or {}
            task = self._task_id(path, trajectory)
            success, verifier = self._task_outcome(path)
            minimal = self._trace_minimal_indices(trajectory.get("dependencies") or {})
            parsed_nodes, quality = self._parse_trajectory(
                trajectory, task, path, minimal_indices=minimal, task_success=success
            )
            nodes.extend(parsed_nodes)
            row = {"task_id": task, "source_path": str(path), "task_success": success,
                   "raw_verifier": verifier, **quality}
            tasks.append(row)
            if not quality["usable_for_tool_patterns"]:
                rejected.append(row)
        return {
            "schema_version": SCHEMA_VERSION,
            "source_type": "annotated_run",
            "source_run": str(run_dir),
            "sample_count": len(tasks),
            "tasks": sorted(tasks, key=lambda item: item["task_id"]),
            "nodes": [asdict(node) for node in nodes],
            "rejected_samples": sorted(rejected, key=lambda item: item["task_id"]),
        }

    def _parse_trajectory(self, trajectory: Mapping[str, Any], task_id: str, source_path: Path,
                          *, minimal_indices: Sequence[Any], task_success: bool) -> tuple[list[ToolCallNode], dict]:
        steps = _action_steps(trajectory)
        dependencies = trajectory.get("dependencies") or {}
        minimal = {int(value) for value in minimal_indices if str(value).isdigit()}
        quality = self._quality(steps, dependencies, minimal, task_success)
        step_costs = CostLedger(self.prices).annotate(steps)
        nodes: list[ToolCallNode] = []
        uncertain_steps = 0
        for step_id, (step, step_cost) in enumerate(zip(steps, step_costs), 1):
            calls = self._calls(step)
            observations = self._observation_results(step.get("observation"))
            aligned = len(calls) == len(observations) or (len(calls) == 1 and not observations)
            uncertain_steps += int(not aligned)
            share = max(1, len(calls))
            for call_index, call in enumerate(calls):
                observation = observations[call_index] if call_index < len(observations) else {}
                obs_text = str(observation.get("output") or observation.get("content") or "")
                normalized = self.normalizer.normalize_call(call)
                # Task correctness is deliberately not a mining gate.  Failed
                # trajectories often contain the clearest cost pathologies
                # (unchanged retries, broad reads, repeated verification).  We
                # only mark an individual call uncertain when its call/result
                # alignment cannot be reconstructed.
                if not aligned:
                    dependency_state = "uncertain"
                elif step_id in minimal:
                    dependency_state = "critical"
                else:
                    dependency_state = "low_criticality"
                raw_returncode = observation.get("returncode")
                try:
                    returncode = int(raw_returncode) if raw_returncode is not None else None
                except (TypeError, ValueError):
                    returncode = None
                arguments = call.get("arguments") or call.get("args") or {}
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except json.JSONDecodeError:
                        arguments = {"command": arguments}
                if not isinstance(arguments, dict):
                    arguments = {"value": str(arguments)}
                node_id = f"{task_id}:{step_id}:{call_index}"
                nodes.append(ToolCallNode(
                    node_id=node_id,
                    task_id=task_id,
                    step_id=step_id,
                    call_index=call_index,
                    tool_call_id=(str(call.get("tool_call_id")) if call.get("tool_call_id") else None),
                    tool_name=str(call.get("function_name") or call.get("name") or call.get("tool") or ""),
                    arguments=arguments,
                    normalized_action=normalized,
                    observation_text=obs_text[:4000],
                    observation_tokens=math.ceil(len(obs_text) / self.prices.chars_per_token),
                    future_llm_calls=max(0, len(steps) - step_id),
                    estimated_future_cost=float(step_cost["total_cost"]) / share,
                    dependency_state=dependency_state,
                    returncode=returncode,
                    exception_info=str(observation.get("exception_info") or ""),
                    source_path=str(source_path),
                    quality_score=float(quality["quality_score"]),
                ))
        quality["uncertain_observation_steps"] = uncertain_steps
        quality["tool_call_count"] = len(nodes)
        return nodes, quality

    def _quality(self, steps: Sequence[Mapping[str, Any]], dependencies: Mapping[str, Any],
                 minimal: set[int], task_success: bool) -> dict:
        count = len(steps)
        reasons: list[str] = []
        coverage = count > 0 and all(str(index) in dependencies or index in dependencies
                                     for index in range(1, count + 1))
        if not coverage:
            reasons.append("dependency coverage is not 100%")
        bad_refs: list[tuple[int, Any]] = []
        for index in range(1, count + 1):
            raw = dependencies.get(str(index), dependencies.get(index, []))
            if not isinstance(raw, list):
                bad_refs.append((index, raw))
                continue
            for ref in raw:
                if not str(ref).isdigit() or not 0 <= int(ref) < index:
                    bad_refs.append((index, ref))
        if bad_refs:
            reasons.append(f"invalid dependency references: {bad_refs[:5]}")
        retained = sorted(index for index in minimal if 1 <= index <= count)
        minimum = max(2, math.ceil(0.05 * count))
        if len(retained) < minimum:
            reasons.append(f"degenerate minimal path: retained={len(retained)} required>={minimum}")
        retained_ops = []
        for index in retained:
            for call in self._calls(steps[index - 1]):
                retained_ops.extend(self.normalizer.normalize_call(call)["operations"])
        if not any(operation != "submit" for operation in retained_ops):
            reasons.append("minimal path has no non-submit causal action")
        if task_success and not any(operation in {"edit", "verify"} for operation in retained_ops):
            reasons.append("successful task retained neither a write nor verification anchor")
        if not task_success:
            reasons.append("external task did not pass; retained as cost and behavior evidence")
        # V9 optimizes execution cost.  Verifier failure, a sparse dependency
        # graph, or a degenerate annotated minimal path are useful diagnostics,
        # but none is a reason to discard an otherwise observable action
        # sequence.  Correctness is enforced later by the paired promotion
        # gate.  A trajectory with no actions is not filtered for quality; it
        # simply contains no pattern-mining evidence.
        usable = count > 0
        score = 1.0 if usable else 0.0
        return {
            "action_step_count": count,
            "minimal_action_count": len(retained),
            "minimum_required_actions": minimum,
            "dependency_coverage": coverage,
            "quality_score": score,
            "quality_filter_mode": "cost_only_no_outcome_gate",
            "usable_for_tool_patterns": usable,
            "admission_reason": ("parseable_actions_present" if usable else "no_parseable_actions"),
            "diagnostic_issues": reasons,
            "rejection_reasons": ([] if usable else ["no parseable action evidence"]),
        }

    @staticmethod
    def _calls(step: Mapping[str, Any]) -> list[dict]:
        calls = step.get("tool_calls")
        if isinstance(calls, list):
            return [dict(item) for item in calls if isinstance(item, Mapping)]
        action = step.get("action")
        if isinstance(action, Mapping):
            return [dict(action)]
        if isinstance(action, str):
            return [{"function_name": "bash", "arguments": {"command": action}}]
        return []

    @staticmethod
    def _observation_results(observation: Any) -> list[dict]:
        raw_results = observation.get("results", []) if isinstance(observation, Mapping) else []
        result: list[dict] = []
        for raw in raw_results if isinstance(raw_results, list) else []:
            value = raw.get("content", raw) if isinstance(raw, Mapping) else raw
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except json.JSONDecodeError:
                    value = {"output": value}
            result.append(dict(value) if isinstance(value, Mapping) else {"output": str(value)})
        return result

    @staticmethod
    def _trace_minimal_indices(dependencies: Mapping[str, Any]) -> list[int]:
        valid_keys = sorted(int(key) for key in dependencies if str(key).isdigit() and int(key) > 0)
        if not valid_keys:
            return [0]
        keep = {0}
        stack = [valid_keys[-1]]
        while stack:
            current = stack.pop()
            if current in keep:
                continue
            keep.add(current)
            raw = dependencies.get(str(current), dependencies.get(current, []))
            if isinstance(raw, list):
                stack.extend(int(item) for item in raw if str(item).isdigit() and int(item) >= 0)
        return sorted(keep)

    @staticmethod
    def _task_id(path: Path, trajectory: Mapping[str, Any]) -> str:
        for key in ("task_id", "instance_id", "id"):
            if trajectory.get(key):
                return str(trajectory[key])
        config = _read_json(path.parent.parent / "config.json", {}) or {}
        task = config.get("task") or {}
        if isinstance(task, Mapping) and task.get("path"):
            return Path(str(task["path"])).name
        trial = path.parent.parent.name
        return trial.rsplit("__", 1)[0]

    @staticmethod
    def _task_outcome(path: Path) -> tuple[bool, dict]:
        trial = path.parent.parent
        result = _read_json(trial / "result.json", {}) or {}
        reward = _read_json(trial / "verifier" / "reward.json", {}) or {}
        rewards = ((result.get("verifier_result") or {}).get("rewards") or reward)
        if not isinstance(rewards, Mapping):
            rewards = {}
        raw = rewards.get("overall_pass", rewards.get("resolved", rewards.get("reward", 0)))
        try:
            success = float(raw or 0) > 0
        except (TypeError, ValueError):
            success = bool(raw)
        return success, dict(rewards)


# ---------------------------------------------------------------------------
# Cross-task pattern and instruction candidates
# ---------------------------------------------------------------------------


class PatternMinerV9:
    def __init__(self, *, min_support: int = 2, max_cards: int = 15,
                 min_size: int = 2, max_size: int = 5, schema_tokens: int = 160):
        self.min_support = min_support
        self.max_cards = max_cards
        self.min_size = min_size
        self.max_size = max_size
        self.schema_tokens = schema_tokens

    def mine(self, evidence: Mapping[str, Any]) -> tuple[list[dict], list[dict]]:
        by_task: dict[str, list[dict]] = defaultdict(list)
        for node in evidence.get("nodes", []):
            by_task[str(node["task_id"])].append(dict(node))
        task_outcomes = {str(row["task_id"]): bool(row.get("task_success"))
                         for row in evidence.get("tasks", [])}
        buckets: dict[tuple[str, str], list[dict]] = defaultdict(list)
        controls: dict[str, list[dict]] = defaultdict(list)
        for task_id, raw_nodes in by_task.items():
            nodes = sorted(raw_nodes, key=lambda item: (int(item["step_id"]), int(item["call_index"])))
            # A compound tool may compress either (a) a causal workflow that
            # must be preserved or (b) a low-criticality workflow worth a
            # counterfactual canary.  Only call/result-unaligned nodes are
            # excluded.  Mining low-criticality nodes alone would incorrectly
            # discard the most useful search->read->verify causal sequences.
            eligible = [node for node in nodes
                        if node.get("dependency_state") in {"critical", "low_criticality"}]
            by_step: dict[int, list[dict]] = defaultdict(list)
            for node in eligible:
                by_step[int(node["step_id"])].append(node)
            steps = sorted(by_step)
            blocks: list[list[int]] = []
            for step in steps:
                if not blocks or step != blocks[-1][-1] + 1:
                    blocks.append([step])
                else:
                    blocks[-1].append(step)
            for block in blocks:
                for size in range(self.min_size, self.max_size + 1):
                    for start in range(0, len(block) - size + 1):
                        chosen_steps = block[start:start + size]
                        chosen = [node for step in chosen_steps for node in by_step[step]]
                        self._add_occurrence(buckets, "sequence", task_id, chosen)
            all_by_step: dict[int, list[dict]] = defaultdict(list)
            for node in nodes:
                all_by_step[int(node["step_id"])].append(node)
            all_steps = sorted(all_by_step)
            for size in range(self.min_size, self.max_size + 1):
                for start in range(0, len(all_steps) - size + 1):
                    chosen_steps = all_steps[start:start + size]
                    if any(right != left + 1 for left, right in zip(chosen_steps, chosen_steps[1:])):
                        continue
                    chosen = [node for step in chosen_steps for node in all_by_step[step]]
                    if chosen and any(node.get("dependency_state") == "uncertain" for node in chosen):
                        signature = " -> ".join(
                            " & ".join(str(item["normalized_action"]["signature"])
                                     for item in all_by_step[step])
                            for step in chosen_steps
                        )
                        if not any(row["task_id"] == task_id for row in controls[signature]):
                            controls[signature].append({
                                "task_id": task_id,
                                "node_ids": [str(node["node_id"]) for node in chosen],
                                "source_path": str(chosen[0]["source_path"]),
                                "reason": "the same workflow includes critical or uncertain evidence",
                            })
            # Dependency-connected candidates use the conservative old-v6 step
            # graph.  They are enumerated as short paths, not all subgraphs, to
            # avoid the v8 combinatorial motif explosion.
            task_row = next((row for row in evidence.get("tasks", []) if row["task_id"] == task_id), {})
            source = Path(str(task_row.get("source_path") or ""))
            sample = _read_json(source, {}) or {}
            trajectory = sample.get("negative_sample") or sample
            deps = trajectory.get("dependencies") or {}
            eligible_steps = set(by_step)
            for end in sorted(eligible_steps):
                path = [end]
                current = end
                while len(path) < self.max_size:
                    raw = deps.get(str(current), deps.get(current, []))
                    parents = sorted((int(value) for value in raw if str(value).isdigit()
                                      and int(value) in eligible_steps), reverse=True)
                    if not parents:
                        break
                    current = parents[0]
                    path.append(current)
                    if len(path) >= self.min_size:
                        chosen = [node for step in sorted(path) for node in by_step[step]]
                        self._add_occurrence(buckets, "dependency_subgraph", task_id, chosen)

        pool: list[dict] = []
        for (pattern_type, signature), occurrences in buckets.items():
            support_tasks = sorted({item["task_id"] for item in occurrences})
            if len(support_tasks) < self.min_support:
                continue
            # One task cannot inflate support or total benefit via repeated use.
            best_by_task: dict[str, dict] = {}
            for occurrence in occurrences:
                previous = best_by_task.get(occurrence["task_id"])
                if previous is None or occurrence["estimated_benefit"] > previous["estimated_benefit"]:
                    best_by_task[occurrence["task_id"]] = occurrence
            kept = sorted(best_by_task.values(), key=lambda item: (item["task_id"], item["node_ids"]))
            values = [float(item["estimated_benefit"]) for item in kept]
            quality = statistics.fmean(float(item["quality_score"]) for item in kept)
            passed_support = sum(bool(task_outcomes.get(task)) for task in support_tasks)
            failed_support = len(support_tasks) - passed_support
            if passed_support and failed_support:
                evidence_role = "mixed_outcome_cost_hypothesis"
            elif passed_support:
                evidence_role = "successful_workflow_cost_hypothesis"
            else:
                evidence_role = "failure_only_waste_signal"
            total = sum(values)
            overhead = self.schema_tokens * (1 + 0.1 * statistics.fmean(item["trajectory_turns"] for item in kept))
            score = total * math.log1p(len(support_tasks)) * quality - overhead
            candidate_id = "p-" + _stable_hash({"type": pattern_type, "signature": signature}, 10)
            pool.append({
                "schema_version": SCHEMA_VERSION,
                "candidate_id": candidate_id,
                "pattern_type": pattern_type,
                "action_signature": signature,
                "support": len(support_tasks),
                "support_tasks": support_tasks,
                "outcome_support": {"passed": passed_support, "failed": failed_support},
                "evidence_role": evidence_role,
                "occurrences": kept,
                "total_benefit": total,
                "median_benefit": statistics.median(values),
                "evidence_quality": quality,
                "schema_overhead_estimate": overhead,
                "score": score,
                "expected_replacement": (
                    f"Design one structured workflow which can replace the {len(signature.split(' -> '))}-turn "
                    f"observed sequence `{signature}` while preserving bounded output, errors, and bash fallback. "
                    + ("Because all supporting tasks failed, treat this as a waste/recovery signal: prefer avoiding, "
                       "narrowing, or exiting the repeated behavior; do not automate the failed strategy as if it were "
                       "correct." if not passed_support else
                       "Task success is still not proof that contraction is safe; require paired canary validation.")
                ),
                "positive_examples": [self._example(item) for item in kept[:3]],
                "negative_controls": controls.get(signature, [])[:3],
            })
        pool.sort(key=lambda item: (-item["score"], -item["support"], item["candidate_id"]))

        # Greedy union attribution prevents the same node benefit from being
        # counted in full by several overlapping cards.
        assigned: set[str] = set()
        selected: list[dict] = []
        for candidate in pool:
            marginal = []
            for occurrence in candidate["occurrences"]:
                uncovered = [node for node in occurrence["node_ids"] if node not in assigned]
                if uncovered:
                    fraction = len(uncovered) / max(1, len(occurrence["node_ids"]))
                    marginal.append(float(occurrence["estimated_benefit"]) * fraction)
            marginal_benefit = sum(marginal)
            candidate["marginal_benefit"] = marginal_benefit
            candidate["selected"] = bool(marginal_benefit > 0 and len(selected) < self.max_cards)
            if not candidate["selected"]:
                continue
            selected.append(candidate)
            assigned.update(node for occurrence in candidate["occurrences"] for node in occurrence["node_ids"])
        return pool, selected

    @staticmethod
    def _add_occurrence(buckets: dict, pattern_type: str, task_id: str, nodes: Sequence[Mapping[str, Any]]) -> None:
        if len(nodes) < 2:
            return
        step_signatures = []
        for _, group in _group_by_step(nodes):
            step_signatures.append(" & ".join(str(item["normalized_action"]["signature"]) for item in group))
        signature = " -> ".join(step_signatures)
        benefit = sum(float(item.get("estimated_future_cost", 0)) for item in nodes)
        # A compound call replaces N turns with one; do not credit the retained turn.
        benefit *= max(0, len(step_signatures) - 1) / max(1, len(step_signatures))
        occurrence = {
            "task_id": task_id,
            "node_ids": [str(item["node_id"]) for item in nodes],
            "estimated_benefit": benefit,
            "action_signature": signature,
            "source_path": str(nodes[0]["source_path"]),
            "quality_score": statistics.fmean(float(item["quality_score"]) for item in nodes),
            "dependency_states": sorted({str(item["dependency_state"]) for item in nodes}),
            "trajectory_turns": max(int(item["step_id"]) + int(item["future_llm_calls"]) for item in nodes),
        }
        existing = sum(item["task_id"] == task_id for item in buckets[(pattern_type, signature)])
        if existing < 5:
            buckets[(pattern_type, signature)].append(occurrence)

    @staticmethod
    def _example(occurrence: Mapping[str, Any]) -> dict:
        return {
            "task_id": occurrence["task_id"],
            "node_ids": occurrence["node_ids"],
            "source_path": occurrence["source_path"],
            "estimated_benefit": occurrence["estimated_benefit"],
        }


def _group_by_step(nodes: Sequence[Mapping[str, Any]]) -> list[tuple[int, list[Mapping[str, Any]]]]:
    grouped: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    for node in nodes:
        grouped[int(node["step_id"])].append(node)
    return [(step, sorted(items, key=lambda item: int(item["call_index"])))
            for step, items in sorted(grouped.items())]


class InstructionCandidateBuilderV9:
    """Produce cross-task behavioral hypotheses separately from tool cards."""

    def __init__(self, *, min_support: int = 2, max_cards: int = 15,
                 output_token_cap: int = DEFAULT_OUTPUT_TOKEN_CAP):
        self.min_support = min_support
        self.max_cards = max_cards
        self.output_token_cap = output_token_cap

    def build(self, evidence: Mapping[str, Any]) -> list[dict]:
        by_task: dict[str, list[dict]] = defaultdict(list)
        for node in evidence.get("nodes", []):
            by_task[str(node["task_id"])].append(dict(node))
        signals: dict[str, list[dict]] = defaultdict(list)
        task_outcomes = {str(row["task_id"]): bool(row.get("task_success"))
                         for row in evidence.get("tasks", [])}
        for task, raw_nodes in by_task.items():
            nodes = sorted(raw_nodes, key=lambda item: (int(item["step_id"]), int(item["call_index"])))
            signatures = [str(node["normalized_action"]["signature"]) for node in nodes]
            operations = [self._primary_op(node) for node in nodes]
            for index, node in enumerate(nodes):
                failed = (node.get("returncode") not in (None, 0) or bool(node.get("exception_info")))
                if int(node.get("observation_tokens", 0)) > self.output_token_cap:
                    self._signal(signals, "large_output_narrow_scope", task, [node], task_outcomes[task])
                if failed:
                    self._signal(signals, "failed_call_change_or_fallback", task, [node], task_outcomes[task])
                if re.search(r"timeout|timed out|deadline", str(node.get("exception_info")) + str(node.get("observation_text")), re.I):
                    self._signal(signals, "timeout_narrow_or_bash_fallback", task, [node], task_outcomes[task])
                if index + 1 < len(nodes):
                    right = nodes[index + 1]
                    if signatures[index] == signatures[index + 1]:
                        self._signal(signals, "avoid_unchanged_repeat", task, [node, right], task_outcomes[task])
                        if failed:
                            self._signal(signals, "avoid_unchanged_failed_repeat", task, [node, right], task_outcomes[task])
                    if operations[index] != operations[index + 1]:
                        dynamic = f"transition:{operations[index]}>{operations[index + 1]}"
                        self._signal(signals, dynamic, task, [node, right], task_outcomes[task])
            if "edit" in operations:
                first_write = operations.index("edit")
                if "verify" not in operations[first_write + 1:]:
                    self._signal(signals, "verify_after_edit", task, [nodes[first_write]], task_outcomes[task])
            test_counts = Counter(signatures[index] for index, op in enumerate(operations) if op == "verify")
            if any(count > 1 for count in test_counts.values()):
                examples = [node for node, op in zip(nodes, operations) if op == "verify"][:3]
                self._signal(signals, "avoid_duplicate_verification", task, examples, task_outcomes[task])

        cards: list[dict] = []
        for signal, rows in signals.items():
            support_tasks = sorted({row["task_id"] for row in rows})
            if len(support_tasks) < self.min_support:
                continue
            positives = [row for row in rows if row["task_success"]]
            negatives = [row for row in rows if not row["task_success"]]
            candidate_id = "i-" + _stable_hash(signal, 10)
            cards.append({
                "schema_version": SCHEMA_VERSION,
                "candidate_id": candidate_id,
                "signal": signal,
                "support": len(support_tasks),
                "support_tasks": support_tasks,
                "recommended_rule": self._rule(signal),
                "observable_effect": self._observable(signal),
                "performance_risk": self._risk(signal),
                "positive_examples": positives[:3],
                "negative_controls": negatives[:3],
                "status": "hypothesis_requires_paired_canary",
            })
        cards.sort(key=lambda item: (-item["support"], -len(item["positive_examples"]), item["candidate_id"]))
        return cards[:self.max_cards]

    @staticmethod
    def _signal(bucket: dict[str, list[dict]], signal: str, task: str,
                nodes: Sequence[Mapping[str, Any]], success: bool) -> None:
        if any(row["task_id"] == task for row in bucket[signal]):
            return
        bucket[signal].append({
            "task_id": task,
            "node_ids": [node["node_id"] for node in nodes],
            "source_path": nodes[0]["source_path"] if nodes else "",
            "task_success": success,
        })

    @staticmethod
    def _primary_op(node: Mapping[str, Any]) -> str:
        operations = node.get("normalized_action", {}).get("operations") or ["unknown_action"]
        return str(operations[-1])

    @staticmethod
    def _rule(signal: str) -> str:
        fixed = {
            "large_output_narrow_scope": "When an observation is too broad, narrow the path, query, range, or target before requesting more output.",
            "failed_call_change_or_fallback": "After a failed operation, change its scope or parameters; if the specialized path remains unsuitable, use an equivalent scoped bash command.",
            "timeout_narrow_or_bash_fallback": "After a tool timeout, retry only with a smaller scope or fall back to an equivalent bounded bash command; do not repeat the same call.",
            "avoid_unchanged_repeat": "Do not repeat an unchanged operation unless new evidence makes its result likely to differ.",
            "avoid_unchanged_failed_repeat": "Never repeat the same failed call unchanged; diagnose the failure and choose a narrower or different operation.",
            "verify_after_edit": "After an edit, run the cheapest meaningful targeted verification before broadening the change or declaring completion.",
            "avoid_duplicate_verification": "Do not rerun an unchanged successful check unless the relevant code or environment changed afterward.",
        }
        if signal in fixed:
            return fixed[signal]
        if signal.startswith("transition:") and ">" in signal:
            left, right = signal.removeprefix("transition:").split(">", 1)
            return (f"Move from {left} to {right} when the current observation supplies the needed evidence; "
                    f"carry that evidence forward instead of repeating {left} without new information.")
        return "Use the observed workflow only when its evidence and risk conditions match the current task."

    @staticmethod
    def _observable(signal: str) -> str:
        if "repeat" in signal or "duplicate" in signal:
            return "fewer identical consecutive calls and fewer LLM turns"
        if "output" in signal:
            return "smaller observations and fewer follow-up pagination turns"
        if "timeout" in signal or "failed" in signal:
            return "fewer same-parameter failures and an observable scope reduction or bash fallback"
        if "verify" in signal:
            return "targeted post-edit checks without redundant broad validation"
        return "the phase transition occurs with fewer repeated operations"

    @staticmethod
    def _risk(signal: str) -> str:
        if "verify" in signal:
            return "Over-aggressive skipping could hide regressions; never weaken checks for high-risk changes."
        if "transition" in signal:
            return "The observed order may be task-specific; apply only when the preceding observation justifies it."
        return "Premature narrowing or fallback may miss evidence; retain a scoped recovery path."


# ---------------------------------------------------------------------------
# Prompt compiler
# ---------------------------------------------------------------------------


class V9PromptBuilder:
    def __init__(self, *, output_token_cap: int = DEFAULT_OUTPUT_TOKEN_CAP,
                 tool_timeout_seconds: int = DEFAULT_TOOL_TIMEOUT_SECONDS,
                 memory_mb: int = DEFAULT_MEMORY_MB, registry_budget: int = 1600):
        self.output_token_cap = output_token_cap
        self.output_char_cap = output_token_cap * 4
        self.tool_timeout_seconds = tool_timeout_seconds
        self.memory_mb = memory_mb
        self.registry_budget = registry_budget

    def build(self, scripts_dir: Path, pattern_cards: Sequence[Mapping[str, Any]],
              instruction_cards: Sequence[Mapping[str, Any]], *, cycle: int = 1,
              history: Sequence[Mapping[str, Any]] = (), batch_index: int = 1,
              batch_count: int = 1) -> str:
        scripts_dir = Path(scripts_dir)
        tools = (scripts_dir / "tools.json").read_text(encoding="utf-8") if (scripts_dir / "tools.json").exists() else "[]\n"
        instruction = ((scripts_dir / "instruction.md").read_text(encoding="utf-8")
                       if (scripts_dir / "instruction.md").exists() else "")
        executor_summary = self._executor_summary(scripts_dir / "executor.py")
        return "\n".join([
            f"# CAHE v9 candidate-harness compiler — Cycle {cycle}, Batch {batch_index}/{batch_count}",
            "",
            "You are compiling one auditable candidate harness for a downstream coding agent. The current "
            "directory is a STAGING copy. Edit only tools.json, executor.py, instruction.md, and "
            "change_manifest.json. Never edit the active registry or historical evidence.",
            "",
            "## Objective and evidence boundary",
            "Reduce future API cost while preserving benchmark correctness. Pattern and instruction cards below "
            "are hypotheses, not commands and not proof. Implement only changes attributable to a card. When a "
            "card is too ambiguous or unsafe, leave the relevant registry behavior unchanged and record why in "
            "change_manifest.json.",
            "This is one serial evolution batch. The staging registry may already contain changes from earlier "
            "batches in this cycle. Review the current files before editing, preserve useful earlier changes, and "
            "merge new attribution into the existing change_manifest.json instead of replacing its history.",
            "There is NO fixed list of allowed tool categories or tool names. Derive structured workflows and "
            "descriptive names from the evidence. You may add, merge, narrow, repair, or remove tools when the "
            "manifest preserves exact candidate attribution. Do not create tools merely to reach a target count.",
            "",
            "## Native registry contract inherited from v6",
            "- tools.json is a JSON list of function schemas: name, description, and parameters containing a "
            "JSON-schema object with properties and required.",
            "- executor.py uses Python stdlib only and defines run_tool(action, cwd=None, timeout=120). Parameters "
            "are flat action keys. Every call returns output:str, returncode:int, exception_info:str.",
            "- tools.json and executor.py dispatch names must match exactly. Never register bash, shell, terminal, "
            "exec, or exec_command, and never expose an arbitrary command/script/code parameter.",
            "- Resolve paths under cwd, reject traversal outside cwd, use shell=False, and avoid external network "
            "or irreversible external side effects. Missing inputs produce a normal error observation.",
            f"- Every subprocess honors min(timeout, {self.tool_timeout_seconds}) seconds. Runtime isolation also "
            f"enforces {self.tool_timeout_seconds}s and {self.memory_mb}MB.",
            f"- Clamp every returned output to {self.output_char_cap} characters (approximately "
            f"{self.output_token_cap} tokens). If a deterministic result is truncated, return a top-level integer "
            "next_offset so a narrower continuation advances. Stream bounded input; do not read an entire repository "
            "or unbounded file into memory.",
            "- Timeout/error text should recommend narrowing the path, query, range, or operation, or falling back "
            "to an equivalent scoped bash command. It must not force either choice and must recommend against "
            "repeating the same failed call unchanged.",
            "",
            "## instruction.md contract",
            "Keep rules tool-agnostic, evidence-conditioned, and concise: at most 12 effective rules and about 800 "
            "tokens. Do not list tool names or parameter schemas. Never introduce unconditional test skipping, "
            "submission after environment failure, or permission for irreversible/destructive action.",
            "Always preserve these baseline meanings:\n- " + "\n- ".join(BASELINE_RULES),
            "",
            "## change_manifest.json (mandatory)",
            "Write a JSON object with tools_added, tools_modified, tools_removed, instruction_changes, rejected_cards, "
            "and expected_effect. Every added/modified tool and instruction change lists candidate_ids from the cards. "
            "Use rejected_cards only as a list of objects shaped "
            "{\"candidate_ids\": [\"p-...\"], \"reason\": \"...\"}; do not emit bare candidate-id strings. "
            "Every card presented in this or an earlier compiler batch must be accounted for by either a change or "
            "a rejected_cards object, and the manifest must retain attribution accumulated by earlier batches. "
            "Attribution belongs only in this manifest, not in public tool descriptions (which would add recurring "
            "prompt cost). A removed legacy tool needs a concrete reliability/cost reason.",
            "",
            f"Registry schema budget: approximately {self.registry_budget} tokens total. Prefer a smaller coherent "
            "registry over overlapping primitive tools. A compound tool must replace at least two observed LLM turns.",
            "",
            "## Pattern cards selected from cross-task evidence",
            json.dumps(list(pattern_cards), ensure_ascii=False, indent=2),
            "",
            "## Instruction candidate cards (paired-canary hypotheses)",
            json.dumps(list(instruction_cards), ensure_ascii=False, indent=2),
            "",
            "## Recent rejected/promoted cycle summaries",
            json.dumps(list(history)[-3:], ensure_ascii=False, indent=2),
            "",
            "## Current tools.json (complete)",
            "```json", tools.rstrip(), "```",
            "",
            "## Current instruction.md (complete)",
            "```markdown", instruction.rstrip(), "```",
            "",
            "## Current executor.py (auditable summary; inspect the local file for implementation details)",
            "```json", json.dumps(executor_summary, ensure_ascii=False, indent=2), "```",
            "",
            "## Finish criteria",
            "Validate JSON and Python syntax locally, run bounded smoke calls for every tool, and save all four files. "
            "Do not claim the candidate is promoted: structural validation and a paired 16-case canary decide that.",
        ])

    def repair_prompt(self, staging_dir: Path, warnings: Sequence[str], manifest: Mapping[str, Any]) -> str:
        snippets = {}
        for name in (*REGISTRY_FILES, "change_manifest.json"):
            path = Path(staging_dir) / name
            if path.exists():
                text = path.read_text(encoding="utf-8")
                snippets[name] = text[:6000]
        return "\n".join([
            "# CAHE v9 targeted validation repair",
            "Repair only the listed validator failures in the current staging registry. Preserve candidate "
            "attribution and do not add unrelated tools/rules or weaken the intended safety/output contracts.",
            "",
            "## Validator failures",
            *[f"- {warning}" for warning in warnings],
            "",
            "## Current change manifest",
            json.dumps(manifest, ensure_ascii=False, indent=2),
            "",
            "## Required rejection schema",
            "Represent rejected cards as {\"rejected_cards\": [{\"candidate_ids\": [\"p-...\"], "
            "\"reason\": \"...\"}]}. Every presented candidate id must remain attributed to a change or rejection; "
            "preserve entries accumulated by earlier batches.",
            "",
            "## Relevant bounded file snippets",
            json.dumps(snippets, ensure_ascii=False, indent=2),
            "",
            "Re-run JSON/Python checks and save tools.json, executor.py, instruction.md, and change_manifest.json.",
        ])

    def repair_template(self) -> str:
        return self.repair_prompt(Path("<STAGING_DIR>"), ["<validator warning>"], {"candidate_ids": ["<id>"]})

    @staticmethod
    def _executor_summary(path: Path) -> dict:
        if not path.exists():
            return {"status": "missing", "required_api": "run_tool(action, cwd=None, timeout=120)"}
        source = path.read_text(encoding="utf-8")
        summary: dict[str, Any] = {
            "path": str(path), "sha256": _file_hash(path), "lines": len(source.splitlines()),
            "required_api": "run_tool(action, cwd=None, timeout=120)",
        }
        try:
            tree = ast.parse(source)
            summary["imports"] = sorted({
                alias.name.split(".")[0]
                for node in ast.walk(tree) if isinstance(node, ast.Import)
                for alias in node.names
            } | {
                (node.module or "").split(".")[0]
                for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
            })
            summary["dispatch_literals"] = sorted({
                str(node.value) for node in ast.walk(tree)
                if isinstance(node, ast.Constant) and isinstance(node.value, str)
                and re.fullmatch(r"[a-z][a-z0-9_-]{1,63}", node.value)
            })[:100]
        except SyntaxError as exc:
            summary["parse_error"] = str(exc)
        return summary


# ---------------------------------------------------------------------------
# Registry lifecycle and validation
# ---------------------------------------------------------------------------


def seed_v9(scripts_dir: Path | str) -> None:
    directory = Path(scripts_dir)
    directory.mkdir(parents=True, exist_ok=True)
    defaults = {
        "tools.json": "[]\n",
        "executor.py": (
            '"""CAHE v9 empty native-tool registry."""\n'
            "MAX_OUTPUT_CHARS = 4000\n\n"
            "def run_tool(action, cwd=None, timeout=120):\n"
            "    name = action.get('tool')\n"
            "    return {'output': f'unknown tool {name}'[:MAX_OUTPUT_CHARS], "
            "'returncode': 1, 'exception_info': 'unknown tool; consider a narrower equivalent bash command'}\n"
        ),
        "instruction.md": "# CAHE v9 baseline governance\n\n" + "\n".join(f"- {rule}" for rule in BASELINE_RULES) + "\n",
    }
    for name, content in defaults.items():
        path = directory / name
        if not path.exists():
            path.write_text(content, encoding="utf-8")


class RegistryManagerV9:
    def __init__(self, work_dir: Path, active_dir: Path):
        self.work_dir = Path(work_dir)
        self.active_dir = Path(active_dir)
        self.staging_dir = self.work_dir / "staging"

    def stage(self) -> Path:
        if self.staging_dir.exists():
            shutil.rmtree(self.staging_dir)
        self.staging_dir.mkdir(parents=True)
        seed_v9(self.active_dir)
        for name in REGISTRY_FILES:
            shutil.copy2(self.active_dir / name, self.staging_dir / name)
        return self.staging_dir

    def promote(self, gate: Mapping[str, Any], cycle: int) -> dict:
        if gate.get("promote") is not True:
            raise RuntimeError("promotion gate did not pass")
        warnings = validate_registry_v9(self.staging_dir, require_manifest=True)
        if warnings:
            raise RuntimeError("invalid staging registry: " + "; ".join(warnings))
        snapshots = self.work_dir.parent / "snapshots"
        snapshot = snapshots / f"cycle-{cycle - 1}-parent"
        if snapshot.exists():
            shutil.rmtree(snapshot)
        snapshot.mkdir(parents=True)
        for name in REGISTRY_FILES:
            shutil.copy2(self.active_dir / name, snapshot / name)
        temp = self.active_dir.parent / f".{self.active_dir.name}.v9-{os.getpid()}"
        if temp.exists():
            shutil.rmtree(temp)
        shutil.copytree(self.active_dir, temp)
        for name in REGISTRY_FILES:
            shutil.copy2(self.staging_dir / name, temp / name)
        backup = snapshots / f"cycle-{cycle}-pre-promote-full"
        if backup.exists():
            shutil.rmtree(backup)
        os.replace(self.active_dir, backup)
        try:
            os.replace(temp, self.active_dir)
        except Exception:
            os.replace(backup, self.active_dir)
            raise
        fingerprint = registry_hash(self.active_dir)
        record = {
            "schema_version": SCHEMA_VERSION,
            "cycle": cycle,
            "active_version": fingerprint,
            "parent_snapshot": str(backup),
            "gate_hash": _stable_hash(gate),
        }
        _write_json(self.active_dir / "registry_meta.json", record)
        return record


def registry_hash(path: Path) -> str:
    return _stable_hash({name: (Path(path) / name).read_text(encoding="utf-8")
                         for name in REGISTRY_FILES if (Path(path) / name).exists()}, 32)


def validate_registry_v9(scripts_dir: Path, *, output_token_cap: int = DEFAULT_OUTPUT_TOKEN_CAP,
                         registry_budget: int = 1600, pattern_cards: Sequence[Mapping[str, Any]] = (),
                         instruction_cards: Sequence[Mapping[str, Any]] = (),
                         require_manifest: bool = False) -> list[str]:
    directory = Path(scripts_dir)
    warnings: list[str] = []
    tools_path, executor_path, instruction_path = (directory / name for name in REGISTRY_FILES)
    tools = _read_json(tools_path)
    if not isinstance(tools, list):
        warnings.append("tools.json must be a JSON list")
        tools = []
    names: list[str] = []
    allowed_types = {"string", "integer", "number", "boolean", "array", "object"}
    for index, tool in enumerate(tools):
        if not isinstance(tool, Mapping):
            warnings.append(f"tool {index} is not an object")
            continue
        name = str(tool.get("name") or "")
        names.append(name)
        if not re.fullmatch(r"[a-z][a-z0-9_-]{1,63}", name):
            warnings.append(f"invalid tool name: {name!r}")
        if name in ActionNormalizerV9.SHELL_NAMES:
            warnings.append(f"reserved shell tool name: {name}")
        parameters = tool.get("parameters")
        if not isinstance(parameters, Mapping) or parameters.get("type") != "object":
            warnings.append(f"{name}: parameters must be an object JSON schema")
            continue
        properties = parameters.get("properties") or {}
        required = parameters.get("required") or []
        if not isinstance(properties, Mapping) or not isinstance(required, list):
            warnings.append(f"{name}: invalid properties/required")
            continue
        if set(required) - set(properties):
            warnings.append(f"{name}: required names missing from properties")
        for prop, schema in properties.items():
            if not isinstance(schema, Mapping) or schema.get("type") not in allowed_types:
                warnings.append(f"{name}.{prop}: unsupported or missing JSON schema type")
        if any(key.lower() in {"command", "cmd", "script", "code"} for key in properties):
            warnings.append(f"{name}: arbitrary command/script/code parameters are forbidden")
    if len(names) != len(set(names)):
        warnings.append("tool names must be unique")
    schema_tokens = math.ceil(len(_stable_json(tools)) / 4)
    if schema_tokens > registry_budget:
        warnings.append(f"registry schema estimate {schema_tokens} tokens exceeds budget {registry_budget}")

    instruction = instruction_path.read_text(encoding="utf-8") if instruction_path.exists() else ""
    rules = [line for line in instruction.splitlines() if line.strip().startswith(("- ", "* "))]
    if len(rules) > 12:
        warnings.append(f"instruction.md has {len(rules)} effective rules; maximum is 12")
    if math.ceil(len(instruction) / 4) > 800:
        warnings.append("instruction.md exceeds the approximate 800-token budget")
    normalized_rules = [re.sub(r"\W+", " ", line.lower()).strip() for line in rules]
    if len(normalized_rules) != len(set(normalized_rules)):
        warnings.append("instruction.md contains duplicate rules")
    for name in names:
        if name and re.search(rf"\b{re.escape(name)}\b", instruction, re.I):
            warnings.append(f"instruction.md leaks tool-specific name: {name}")

    source = executor_path.read_text(encoding="utf-8") if executor_path.exists() else ""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        warnings.append(f"executor.py parse failed: {exc}")
        tree = None
    if tree is not None:
        functions = {node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        if "run_tool" not in functions:
            warnings.append("executor.py must define run_tool")
        dispatch = {
            str(comparator.value)
            for node in ast.walk(tree) if isinstance(node, ast.Compare)
            for comparator in node.comparators
            if isinstance(comparator, ast.Constant) and isinstance(comparator.value, str)
            and comparator.value in set(names)
        }
        if set(names) - dispatch:
            warnings.append(f"executor dispatch is missing tools: {sorted(set(names) - dispatch)}")
        stdlib = set(getattr(__import__("sys"), "stdlib_module_names", ()))
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module.split(".")[0])
            elif isinstance(node, ast.Call):
                for keyword in node.keywords:
                    if keyword.arg == "shell" and isinstance(keyword.value, ast.Constant) and keyword.value.value is True:
                        warnings.append("executor subprocess shell=True is forbidden")
                if isinstance(node.func, ast.Attribute) and node.func.attr == "readlines":
                    warnings.append("executor unbounded readlines() is forbidden")
                if isinstance(node.func, ast.Attribute) and node.func.attr == "read" and not node.args:
                    warnings.append("executor unbounded read() is forbidden")
        third_party = sorted({name for name in imports if name and name not in stdlib and name != "__future__"})
        if third_party:
            warnings.append(f"executor.py must be stdlib-only; unexpected imports: {third_party}")
    if names and not any(marker in source for marker in ("MAX_OUTPUT", "output_char_cap", "OUTPUT_CHAR", "[:4000]", "[: 4000]")):
        warnings.append(f"executor.py has no visible output clamp near {output_token_cap * 4} characters")
    if "subprocess" in source and "timeout=" not in source:
        warnings.append("executor subprocess calls must pass timeout=")
    if re.search(r"https?://|requests\.|urllib\.request", source):
        warnings.append("executor.py must not access external network endpoints")

    # Isolated worker smoke: imports and calls happen under the same memory,
    # output, and deadline boundary used by rollout.  Generic fixtures need not
    # make every domain-specific call succeed, but the worker must always
    # return a bounded observation with the stable result shape.
    worker = ROOT / "agent/mini-swe-agent/src/minisweagent/extra/evolve_tools_v6/worker.py"
    if tree is not None and names and worker.exists():
        with tempfile.TemporaryDirectory(prefix="v9-registry-smoke-") as temp_dir:
            fixture = Path(temp_dir)
            (fixture / "sample.py").write_text("needle = 1\n" * 1000, encoding="utf-8")
            tool_by_name = {str(tool.get("name")): tool for tool in tools if isinstance(tool, Mapping)}
            for name in names:
                schema = ((tool_by_name.get(name) or {}).get("parameters") or {})
                properties = schema.get("properties") or {}
                required = schema.get("required") or []
                sample_action = {"tool": name}
                for prop in required if isinstance(required, list) else []:
                    sample_action[prop] = _smoke_value(str(prop), properties.get(prop) or {})
                for label, action in (("missing-argument", {"tool": name}),
                                      ("generic", sample_action)):
                    payload = json.dumps({"executor_path": str(executor_path), "action": action,
                                          "cwd": str(fixture), "timeout": 1})
                    env = {**os.environ, "EVOLVE_TOOLS_V6_MEMORY_MB": "512",
                           "EVOLVE_TOOLS_V6_OUTPUT_TOKENS": str(output_token_cap)}
                    try:
                        completed = subprocess.run(
                            [sys.executable, str(worker)], input=payload, capture_output=True,
                            text=True, timeout=3, env=env,
                        )
                    except subprocess.TimeoutExpired:
                        warnings.append(f"{name}: {label} isolated smoke timed out")
                        continue
                    result = _read_json_text(completed.stdout)
                    if completed.returncode != 0 or not isinstance(result, Mapping):
                        warnings.append(f"{name}: {label} isolated smoke returned invalid worker JSON")
                        continue
                    expected_shape = {"output": str, "returncode": int, "exception_info": str}
                    if any(not isinstance(result.get(key), kind) for key, kind in expected_shape.items()):
                        warnings.append(f"{name}: {label} smoke violated output/returncode/exception_info shape")
                    if len(str(result.get("output", ""))) > output_token_cap * 4:
                        warnings.append(f"{name}: {label} smoke violated the runtime output cap")

    manifest_path = directory / "change_manifest.json"
    manifest = _read_json(manifest_path)
    if require_manifest and not isinstance(manifest, Mapping):
        warnings.append("change_manifest.json is required and must be an object")
    if isinstance(manifest, Mapping):
        allowed_ids = {str(card.get("candidate_id")) for card in (*pattern_cards, *instruction_cards)
                       if card.get("candidate_id")}
        found_ids = _manifest_candidate_ids(manifest)
        unsupported = sorted(found_ids - allowed_ids) if allowed_ids else []
        if unsupported:
            warnings.append(f"change manifest cites unsupported candidate ids: {unsupported}")
        missing = sorted(allowed_ids - found_ids)
        if missing:
            warnings.append(f"change manifest does not account for candidate ids: {missing}")
    return list(dict.fromkeys(warnings))


def _manifest_candidate_ids(value: Any) -> set[str]:
    result: set[str] = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key == "candidate_ids" and isinstance(item, list):
                result.update(str(candidate) for candidate in item
                              if isinstance(candidate, (str, int, float)) and not isinstance(candidate, bool))
            elif key == "rejected_cards" and isinstance(item, list):
                # Early v9 compiler prompts did not spell out the rejection schema,
                # so valid semantic rejections sometimes arrived as ["p-..."] rather
                # than [{"candidate_ids": ["p-..."], "reason": "..."}].  Accept
                # that legacy spelling while keeping arbitrary manifest strings from
                # being mistaken for candidate attribution.
                result.update(str(candidate) for candidate in item if isinstance(candidate, str))
                for candidate in item:
                    if not isinstance(candidate, str):
                        result.update(_manifest_candidate_ids(candidate))
            else:
                result.update(_manifest_candidate_ids(item))
    elif isinstance(value, list):
        for item in value:
            result.update(_manifest_candidate_ids(item))
    return result


def _read_json_text(value: str) -> Any:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return None


def _smoke_value(name: str, schema: Mapping[str, Any]) -> Any:
    kind = schema.get("type")
    lowered = name.lower()
    if kind == "integer":
        return 10 if any(token in lowered for token in ("max", "limit", "end")) else 0
    if kind == "number":
        return 1.0
    if kind == "boolean":
        return False
    if kind == "array":
        item = schema.get("items") if isinstance(schema.get("items"), Mapping) else {"type": "string"}
        return [_smoke_value(name.removesuffix("s"), item)]
    if kind == "object":
        return {}
    if any(token in lowered for token in ("file", "path")):
        return "sample.py"
    if any(token in lowered for token in ("dir", "root", "cwd")):
        return "."
    if any(token in lowered for token in ("query", "pattern", "regex", "needle", "symbol")):
        return "needle"
    return "sample"


# ---------------------------------------------------------------------------
# Benchmark metrics and relaxed paired gate
# ---------------------------------------------------------------------------


class BenchmarkMetricAdapter:
    def __init__(self, benchmark: str, prices: PriceModel):
        self.benchmark = benchmark
        self.prices = prices

    def extract_run(self, run_dir: Path, expected_ids: Sequence[str]) -> dict[str, TaskMetrics]:
        result: dict[str, TaskMetrics] = {}
        expected = set(expected_ids)
        for trajectory_path in _trajectory_files(Path(run_dir)):
            task_id = self._match_task_id(trajectory_path, expected_ids)
            if task_id not in expected:
                logger.warning("ignoring trajectory outside locked case set: %s", trajectory_path)
                continue
            if task_id in result:
                raise RuntimeError(f"duplicate trial for {task_id} in locked run {run_dir}")
            result[task_id] = self.extract_case(trajectory_path, task_id)
        return result

    def extract_case(self, trajectory_path: Path, task_id: str) -> TaskMetrics:
        trajectory = _read_json(trajectory_path, {}) or {}
        trial = trajectory_path.parent.parent
        result = _read_json(trial / "result.json", {}) or {}
        reward_file = _read_json(trial / "verifier" / "reward.json", {}) or {}
        rewards = ((result.get("verifier_result") or {}).get("rewards") or reward_file)
        rewards = dict(rewards) if isinstance(rewards, Mapping) else {}
        score, success = self._primary(rewards)
        agent_result = result.get("agent_result") or {}
        final = trajectory.get("final_metrics") or {}
        prompt = int(agent_result.get("n_input_tokens", final.get("total_prompt_tokens", 0)) or 0)
        cached = int(agent_result.get("n_cache_tokens", final.get("total_cached_tokens", 0)) or 0)
        output = int(agent_result.get("n_output_tokens", final.get("total_completion_tokens", 0)) or 0)
        # Provider/framework cost fields are deliberately ignored.  Some adapters
        # emit cost_usd=0 even when usage is non-zero, and configs may use a
        # different currency.  The experiment's single source of truth is usage
        # multiplied by the prices locked in the model config.
        cost = self.prices.calculate(prompt, cached, output)
        native_calls = native_failures = 0
        for step in _action_steps(trajectory):
            calls = ContrastiveEvidenceBuilderV9._calls(step)
            results = ContrastiveEvidenceBuilderV9._observation_results(step.get("observation"))
            for index, call in enumerate(calls):
                name = str(call.get("function_name") or call.get("name") or call.get("tool") or "").lower()
                if name and name not in ActionNormalizerV9.SHELL_NAMES:
                    native_calls += 1
                    if index >= len(results) or results[index].get("returncode") not in (None, 0):
                        native_failures += 1
        error = result.get("error") or result.get("exception")
        if not error and not trajectory:
            error = "missing trajectory"
        return TaskMetrics(
            task_id=task_id, success=success, primary_score=score, api_cost=float(cost),
            new_input_tokens=max(0, prompt - cached), cached_input_tokens=max(0, cached),
            output_tokens=max(0, output), llm_calls=len(_action_steps(trajectory)),
            native_tool_calls=native_calls, native_tool_failures=native_failures,
            error=str(error) if error else None, raw_verifier=rewards,
        )

    def _primary(self, rewards: Mapping[str, Any]) -> tuple[float, bool]:
        if self.benchmark == "deep-swe":
            # DeepSWE exposes its graded test score as `partial`; use it for the
            # primary numeric metric, while full success still requires the
            # official all-target-tests condition (or an explicit pass field).
            score_raw = rewards.get("primary_score", rewards.get("partial", rewards.get("reward", 0)))
            f2p_total = int(rewards.get("f2p_total", 0) or 0)
            p2p_total = int(rewards.get("p2p_total", 0) or 0)
            full_tests = (
                f2p_total > 0
                and int(rewards.get("f2p_passed", 0) or 0) == f2p_total
                and int(rewards.get("p2p_passed", 0) or 0) == p2p_total
            )
            explicit = rewards.get("overall_pass", rewards.get("resolved", rewards.get("reward", 0)))
            return float(score_raw or 0), bool(full_tests or float(explicit or 0) > 0)
        raw = rewards.get("resolved", rewards.get("overall_pass", rewards.get("primary_score", rewards.get("reward", 0))))
        try:
            score = float(raw or 0)
        except (TypeError, ValueError):
            score = float(bool(raw))
        return score, score > 0

    @staticmethod
    def _match_task_id(path: Path, expected_ids: Sequence[str]) -> str:
        text = str(path.parent.parent)
        matches = [task for task in expected_ids if task in text]
        if matches:
            return max(matches, key=len)
        config = _read_json(path.parent.parent / "config.json", {}) or {}
        task = config.get("task") or {}
        if isinstance(task, Mapping) and task.get("path"):
            return Path(str(task["path"])).name
        return path.parent.parent.name.rsplit("__", 1)[0]


class RelaxedPromotionGateV9:
    def __init__(self, *, max_success_drop_rate: float = 0.10,
                 max_regression_rate: float = 0.20, min_cost_saving_rate: float = 0.0,
                 max_candidate_error_rate: float = 0.10, bootstrap_samples: int = 2000,
                 seed: int = 9):
        self.max_success_drop_rate = max_success_drop_rate
        self.max_regression_rate = max_regression_rate
        self.min_cost_saving_rate = min_cost_saving_rate
        self.max_candidate_error_rate = max_candidate_error_rate
        self.bootstrap_samples = bootstrap_samples
        self.seed = seed

    def evaluate(self, parent: Mapping[str, TaskMetrics], candidate: Mapping[str, TaskMetrics],
                 expected_ids: Sequence[str], *, hard_failures: Sequence[str] = ()) -> dict:
        expected = list(dict.fromkeys(str(item) for item in expected_ids))
        reasons = [str(item) for item in hard_failures]
        missing_parent = sorted(set(expected) - set(parent))
        missing_candidate = sorted(set(expected) - set(candidate))
        if missing_parent:
            reasons.append(f"parent coverage is incomplete: {missing_parent}")
        candidate_errors = sorted({task for task in expected
                                   if task not in candidate or candidate[task].error})
        error_rate = len(candidate_errors) / max(1, len(expected))
        if error_rate > self.max_candidate_error_rate:
            reasons.append(f"candidate error rate {error_rate:.3f} exceeds {self.max_candidate_error_rate:.3f}")
        parent_success = sum(bool(parent[task].success) for task in expected if task in parent)
        candidate_success = sum(bool(candidate[task].success) for task in expected if task in candidate)
        regressions = sorted(task for task in expected if task in parent and parent[task].success
                             and (task not in candidate or not candidate[task].success))
        improvements = sorted(task for task in expected if task in parent and not parent[task].success
                              and task in candidate and candidate[task].success)
        allowed_drop = math.ceil(self.max_success_drop_rate * len(expected))
        actual_drop = max(0, parent_success - candidate_success)
        if actual_drop > allowed_drop:
            reasons.append(f"aggregate success drop {actual_drop} exceeds allowed {allowed_drop}")
        allowed_regressions = math.ceil(self.max_regression_rate * parent_success)
        if len(regressions) > allowed_regressions:
            reasons.append(f"success regressions {len(regressions)} exceed allowed {allowed_regressions}: {regressions}")

        deltas: list[float] = []
        parent_total = candidate_total = 0.0
        missing_cost = []
        for task in expected:
            if task not in parent or parent[task].api_cost is None:
                missing_cost.append(task)
                continue
            base = float(parent[task].api_cost)
            # Missing/error candidates are charged the parent cost, preventing
            # incomplete work from creating artificial savings.
            treated = (float(candidate[task].api_cost) if task in candidate
                       and candidate[task].api_cost is not None and not candidate[task].error else base)
            parent_total += base
            candidate_total += treated
            deltas.append(base - treated)
        if missing_cost:
            reasons.append(f"parent API cost is unavailable for: {sorted(missing_cost)}")
        saving_ratio = ((parent_total - candidate_total) / parent_total if parent_total else 0.0)
        if saving_ratio < self.min_cost_saving_rate:
            reasons.append(f"cost saving ratio {saving_ratio:.4f} is below {self.min_cost_saving_rate:.4f}")
        interval = self._bootstrap(deltas)
        decision = GateDecision(
            promote=not reasons,
            reasons=reasons,
            paired_task_count=len(deltas),
            success_regressions=regressions,
            success_improvements=improvements,
            mean_cost_delta=statistics.fmean(deltas) if deltas else 0.0,
            cost_saving_ratio=saving_ratio,
            bootstrap_interval=interval,
        )
        result = asdict(decision)
        result.update({
            "schema_version": SCHEMA_VERSION,
            "status": "passed" if decision.promote else "failed",
            "expected_task_count": len(expected),
            "parent_success": parent_success,
            "candidate_success": candidate_success,
            "allowed_success_drop": allowed_drop,
            "allowed_regressions": allowed_regressions,
            "candidate_errors": candidate_errors,
            "candidate_error_rate": error_rate,
            "missing_candidate": missing_candidate,
            "parent_cost": parent_total,
            "candidate_cost": candidate_total,
            "thresholds": {
                "max_success_drop_rate": self.max_success_drop_rate,
                "max_regression_rate": self.max_regression_rate,
                "min_cost_saving_rate": self.min_cost_saving_rate,
                "max_candidate_error_rate": self.max_candidate_error_rate,
            },
        })
        return result

    def _bootstrap(self, values: Sequence[float]) -> tuple[float, float]:
        if not values:
            return (0.0, 0.0)
        rng = random.Random(self.seed)
        means = sorted(statistics.fmean(rng.choice(values) for _ in values)
                       for _ in range(max(1, self.bootstrap_samples)))
        low = means[max(0, int(0.025 * len(means)))]
        high = means[min(len(means) - 1, int(0.975 * len(means)))]
        return (low, high)


# ---------------------------------------------------------------------------
# Cycle preparation, prompt reconstruction, and experiment orchestration
# ---------------------------------------------------------------------------


def batch_candidate_cards(pattern_cards: Sequence[Mapping[str, Any]],
                          instruction_cards: Sequence[Mapping[str, Any]],
                          batch_size: int) -> list[dict]:
    """Pair bounded card slices; each serial compiler call sees at most N+N cards."""
    if batch_size <= 0:
        raise ValueError(f"evolve batch size must be positive, got {batch_size}")
    count = max(math.ceil(len(pattern_cards) / batch_size),
                math.ceil(len(instruction_cards) / batch_size))
    return [{
        "batch_index": index + 1,
        "batch_count": count,
        "pattern_cards": list(pattern_cards[index * batch_size:(index + 1) * batch_size]),
        "instruction_cards": list(instruction_cards[index * batch_size:(index + 1) * batch_size]),
    } for index in range(count)]


def write_initial_batch_prompts(*, work_dir: Path, scripts_dir: Path,
                                builder: V9PromptBuilder,
                                pattern_cards: Sequence[Mapping[str, Any]],
                                instruction_cards: Sequence[Mapping[str, Any]],
                                batch_size: int, cycle: int,
                                history: Sequence[Mapping[str, Any]]) -> list[dict]:
    """Persist reviewable previews; runtime rebuilds later prompts from live staging."""
    batches = batch_candidate_cards(pattern_cards, instruction_cards, batch_size)
    rows: list[dict] = []
    root = Path(work_dir) / "initial_batch_prompts"
    for batch in batches:
        batch_dir = root / f"batch-{batch['batch_index']:02d}"
        prompt = builder.build(
            scripts_dir, batch["pattern_cards"], batch["instruction_cards"], cycle=cycle,
            history=history, batch_index=batch["batch_index"], batch_count=batch["batch_count"],
        )
        _write_text(batch_dir / "compile_prompt.md", prompt)
        rows.append({
            "batch_index": batch["batch_index"],
            "batch_count": batch["batch_count"],
            "pattern_candidate_ids": [card["candidate_id"] for card in batch["pattern_cards"]],
            "instruction_candidate_ids": [card["candidate_id"] for card in batch["instruction_cards"]],
            "prompt_path": str(batch_dir / "compile_prompt.md"),
            "prompt_sha256": _file_hash(batch_dir / "compile_prompt.md"),
            "registry_hash": registry_hash(scripts_dir),
            "preview_note": "Actual runtime prompt is rebuilt from staging after prior serial batches.",
        })
    _write_json(Path(work_dir) / "batch_manifest.json", {
        "schema_version": SCHEMA_VERSION, "evolve_batch_size": batch_size,
        "batch_count": len(rows), "batches": rows,
    })
    return rows


def prepare_evidence_prompt(*, sample_paths: Sequence[Path], current_scripts: Path,
                            work_dir: Path, prices: PriceModel = PriceModel(),
                            min_support: int = 2, max_pattern_cards: int = 15,
                            max_instruction_cards: int = 15,
                            evolve_batch_size: int = 2,
                            output_token_cap: int = DEFAULT_OUTPUT_TOKEN_CAP,
                            tool_timeout_seconds: int = DEFAULT_TOOL_TIMEOUT_SECONDS,
                            registry_budget: int = 1600, cycle: int = 1,
                            history: Sequence[Mapping[str, Any]] = ()) -> dict:
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    frozen_registry = work_dir / "current_registry"
    if frozen_registry.exists():
        shutil.rmtree(frozen_registry)
    frozen_registry.mkdir()
    for name in REGISTRY_FILES:
        source = Path(current_scripts) / name
        if source.exists():
            shutil.copy2(source, frozen_registry / name)
    seed_v9(frozen_registry)
    evidence = ContrastiveEvidenceBuilderV9(prices, output_token_cap=output_token_cap).build_from_samples(sample_paths)
    pool, selected = PatternMinerV9(min_support=min_support, max_cards=max_pattern_cards).mine(evidence)
    instruction_cards = InstructionCandidateBuilderV9(
        min_support=min_support, max_cards=max_instruction_cards, output_token_cap=output_token_cap
    ).build(evidence)
    builder = V9PromptBuilder(output_token_cap=output_token_cap,
                              tool_timeout_seconds=tool_timeout_seconds,
                              registry_budget=registry_budget)
    prompt = builder.build(frozen_registry, selected, instruction_cards, cycle=cycle, history=history)
    batch_rows = write_initial_batch_prompts(
        work_dir=work_dir, scripts_dir=frozen_registry, builder=builder,
        pattern_cards=selected, instruction_cards=instruction_cards,
        batch_size=evolve_batch_size, cycle=cycle, history=history,
    )

    _write_json(work_dir / "trajectory_index.json", {
        "schema_version": SCHEMA_VERSION,
        "samples": [{"path": str(path), "sha256": _file_hash(Path(path))} for path in sorted(sample_paths)],
    })
    _write_json(work_dir / "quality_report.json", {
        "schema_version": SCHEMA_VERSION,
        "sample_count": evidence["sample_count"],
        "usable_for_tool_patterns": sum(bool(row["usable_for_tool_patterns"]) for row in evidence["tasks"]),
        "rejected_for_tool_patterns": sum(not bool(row["usable_for_tool_patterns"]) for row in evidence["tasks"]),
        "tasks": evidence["tasks"],
    })
    _write_json(work_dir / "rejected_sample_report.json", {
        "schema_version": SCHEMA_VERSION, "samples": evidence["rejected_samples"],
    })
    _write_json(work_dir / "tool_call_evidence.json", {
        "schema_version": SCHEMA_VERSION, "nodes": evidence["nodes"],
    })
    _write_json(work_dir / "cost_attribution.json", {
        "schema_version": SCHEMA_VERSION,
        "nodes": [{"node_id": node["node_id"], "task_id": node["task_id"],
                   "observation_tokens": node["observation_tokens"],
                   "future_llm_calls": node["future_llm_calls"],
                   "estimated_future_cost": node["estimated_future_cost"]}
                  for node in evidence["nodes"]],
    })
    _write_json(work_dir / "pattern_candidate_pool.json", {
        "schema_version": SCHEMA_VERSION, "min_support": min_support, "candidates": pool,
    })
    _write_json(work_dir / "pattern_occurrences.json", {
        "schema_version": SCHEMA_VERSION,
        "occurrences": [occurrence for candidate in pool for occurrence in candidate["occurrences"]],
    })
    _write_json(work_dir / "pattern_cards.json", {
        "schema_version": SCHEMA_VERSION, "min_support": min_support, "cards": selected,
    })
    _write_json(work_dir / "instruction_candidate_cards.json", {
        "schema_version": SCHEMA_VERSION, "min_support": min_support, "cards": instruction_cards,
    })
    _write_text(work_dir / "combined_compile_prompt.md", prompt)
    _write_text(work_dir / "repair_prompt_template.md", builder.repair_template())
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "cycle": cycle,
        "source_registry": str(Path(current_scripts).resolve()),
        "source_registry_hash": registry_hash(frozen_registry),
        "sample_count": len(sample_paths),
        "usable_sample_count": sum(bool(row["usable_for_tool_patterns"]) for row in evidence["tasks"]),
        "pattern_pool_count": len(pool),
        "selected_pattern_card_count": len(selected),
        "instruction_candidate_count": len(instruction_cards),
        "min_support": min_support,
        "max_pattern_cards": max_pattern_cards,
        "evolve_batch_size": evolve_batch_size,
        "evolve_batch_count": len(batch_rows),
        "output_token_cap": output_token_cap,
        "combined_prompt_sha256": _file_hash(work_dir / "combined_compile_prompt.md"),
        "llm_invoked": False,
    }
    _write_json(work_dir / "prompt_manifest.json", manifest)
    return manifest


def prepare_run_prompt(*, run_dir: Path, current_scripts: Path, work_dir: Path,
                       prices: PriceModel, min_support: int = 2, max_pattern_cards: int = 15,
                       max_instruction_cards: int = 15,
                       evolve_batch_size: int = 2,
                       output_token_cap: int = DEFAULT_OUTPUT_TOKEN_CAP,
                       tool_timeout_seconds: int = DEFAULT_TOOL_TIMEOUT_SECONDS,
                       registry_budget: int = 1600, cycle: int = 1,
                       history: Sequence[Mapping[str, Any]] = ()) -> dict:
    work_dir.mkdir(parents=True, exist_ok=True)
    evidence = ContrastiveEvidenceBuilderV9(prices, output_token_cap=output_token_cap).build_from_run(run_dir)
    pool, selected = PatternMinerV9(min_support=min_support, max_cards=max_pattern_cards).mine(evidence)
    instructions = InstructionCandidateBuilderV9(
        min_support=min_support, max_cards=max_instruction_cards, output_token_cap=output_token_cap
    ).build(evidence)
    builder = V9PromptBuilder(output_token_cap=output_token_cap,
                              tool_timeout_seconds=tool_timeout_seconds,
                              registry_budget=registry_budget)
    prompt = builder.build(current_scripts, selected, instructions, cycle=cycle, history=history)
    _write_json(work_dir / "quality_report.json", {"schema_version": SCHEMA_VERSION, "tasks": evidence["tasks"]})
    _write_json(work_dir / "rejected_sample_report.json", {"schema_version": SCHEMA_VERSION,
                                                            "samples": evidence["rejected_samples"]})
    _write_json(work_dir / "tool_call_evidence.json", {"schema_version": SCHEMA_VERSION,
                                                        "nodes": evidence["nodes"]})
    _write_json(work_dir / "cost_attribution.json", {
        "schema_version": SCHEMA_VERSION,
        "nodes": [{"node_id": node["node_id"], "task_id": node["task_id"],
                   "observation_tokens": node["observation_tokens"],
                   "future_llm_calls": node["future_llm_calls"],
                   "estimated_future_cost": node["estimated_future_cost"]}
                  for node in evidence["nodes"]],
    })
    _write_json(work_dir / "pattern_candidate_pool.json", {"schema_version": SCHEMA_VERSION,
                                                            "candidates": pool})
    _write_json(work_dir / "pattern_occurrences.json", {
        "schema_version": SCHEMA_VERSION,
        "occurrences": [occurrence for candidate in pool for occurrence in candidate["occurrences"]],
    })
    _write_json(work_dir / "pattern_cards.json", {"schema_version": SCHEMA_VERSION, "cards": selected})
    _write_json(work_dir / "instruction_candidate_cards.json", {"schema_version": SCHEMA_VERSION,
                                                                  "cards": instructions})
    _write_text(work_dir / "combined_compile_prompt.md", prompt)
    write_initial_batch_prompts(
        work_dir=work_dir, scripts_dir=current_scripts, builder=builder,
        pattern_cards=selected, instruction_cards=instructions,
        batch_size=evolve_batch_size, cycle=cycle, history=history,
    )
    _write_text(work_dir / "repair_prompt_template.md", builder.repair_template())
    return {"selected": selected, "instruction_cards": instructions, "evidence": evidence}


def discover_v6_prompt_jobs(v6_root: Path, prep_root: Path) -> list[dict]:
    benchmark_map = {"swebench": "swebench-verified"}
    jobs: list[dict] = []
    for scripts in sorted(Path(v6_root).glob("*/*/scripts")):
        benchmark = scripts.parent.parent.name
        run_id = scripts.parent.name
        prep_benchmark = benchmark_map.get(benchmark, benchmark)
        candidates = []
        for run in sorted((Path(prep_root) / prep_benchmark).glob("prep-*")):
            samples = sorted(run.glob("**/agent/contrastive_sample.json"))
            if samples:
                candidates.append((run, samples))
        if not candidates:
            logger.warning("no v6 contrastive source found for %s/%s", benchmark, run_id)
            continue
        selected_run, samples = min(candidates, key=lambda item: _timestamp_distance(run_id, item[0].name))
        jobs.append({
            "benchmark": benchmark, "v6_run_id": run_id, "scripts_dir": scripts,
            "prep_run": selected_run, "sample_paths": samples,
        })
    return jobs


def _timestamp_distance(left: str, right: str) -> int:
    pattern = re.compile(r"(\d{4})-(\d{6})")
    lmatch, rmatch = pattern.search(left), pattern.search(right)
    if not lmatch or not rmatch:
        return 10**20
    return abs(int("20" + lmatch.group(1) + lmatch.group(2)) - int("20" + rmatch.group(1) + rmatch.group(2)))


def prepare_all_v6_prompts(*, v6_root: Path, prep_root: Path, output_root: Path,
                           prices: PriceModel, min_support: int = 2,
                           max_pattern_cards: int = 15, max_instruction_cards: int = 15,
                           evolve_batch_size: int = 2,
                           output_token_cap: int = DEFAULT_OUTPUT_TOKEN_CAP,
                           registry_budget: int = 1600) -> dict:
    rows = []
    for job in discover_v6_prompt_jobs(v6_root, prep_root):
        destination = Path(output_root) / job["benchmark"] / f"{job['v6_run_id']}-from-v6" / "cycle-1"
        manifest = prepare_evidence_prompt(
            sample_paths=job["sample_paths"], current_scripts=job["scripts_dir"], work_dir=destination,
            prices=prices, min_support=min_support, max_pattern_cards=max_pattern_cards,
            max_instruction_cards=max_instruction_cards, output_token_cap=output_token_cap,
            registry_budget=registry_budget, evolve_batch_size=evolve_batch_size,
        )
        row = {"benchmark": job["benchmark"], "v6_run_id": job["v6_run_id"],
               "v6_scripts_dir": str(job["scripts_dir"]), "prep_run": str(job["prep_run"]),
               "output_dir": str(destination), **manifest}
        rows.append(row)
        logger.info("prepared v9 review prompt for %s/%s: patterns=%d instructions=%d",
                    job["benchmark"], job["v6_run_id"], manifest["selected_pattern_card_count"],
                    manifest["instruction_candidate_count"])
    report = {"schema_version": SCHEMA_VERSION, "llm_invoked": False, "jobs": rows}
    _write_json(Path(output_root) / "v6_prompt_review_index.json", report)
    lines = ["# v9 initial prompts reconstructed from v6 artifacts", "",
             "No LLM or benchmark was invoked. Each row links frozen Cycle-1 batch prompts and its evidence audit.", "",
             "| benchmark | v6 run | samples | usable | pattern cards | instruction cards | batches | location |",
             "| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |"]
    for row in rows:
        rel = Path(row["output_dir"]).relative_to(Path(output_root))
        lines.append(f"| {row['benchmark']} | {row['v6_run_id']} | {row['sample_count']} | "
                     f"{row['usable_sample_count']} | {row['selected_pattern_card_count']} | "
                     f"{row['instruction_candidate_count']} | {row['evolve_batch_count']} | "
                     f"`{rel}/initial_batch_prompts/` |")
    _write_text(Path(output_root) / "README.md", "\n".join(lines))
    return report


class CycleStateV9:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.data = _read_json(self.path, {}) or {"schema_version": SCHEMA_VERSION, "stages": {}}

    def start(self, stage: str, inputs: Mapping[str, Any]) -> None:
        self.data["stages"][stage] = {"status": "running", "input_hash": _stable_hash(inputs, 32),
                                              "started_at": time.time()}
        _write_json(self.path, self.data)

    def finish(self, stage: str, outputs: Mapping[str, Any]) -> None:
        row = self.data["stages"].setdefault(stage, {})
        row.update({"status": "completed", "output_hash": _stable_hash(outputs, 32),
                    "finished_at": time.time()})
        _write_json(self.path, self.data)

    def fail(self, stage: str, error: Exception | str) -> None:
        row = self.data["stages"].setdefault(stage, {})
        row.update({"status": "failed", "error": str(error), "finished_at": time.time()})
        _write_json(self.path, self.data)


class EvolveV9Experiment:
    """Three-cycle staging/canary loop. Final 64-case eval remains read-only."""

    def __init__(self, *, benchmark: str, config: str, active_dir: Path, work_dir: Path,
                 evolve_case_ids: Sequence[str], final_eval_case_ids: Sequence[str],
                 baseline_dir: Optional[Path] = None, n_cycles: int = 3, n_concurrent: int = 16,
                 final_baseline_dir: Optional[Path] = None,
                 final_results_root: Path = ROOT / "results" / "eval",
                 mini_swe_agent_dir: Path = DEFAULT_MINI_SWE_AGENT, min_support: int = 2,
                 max_pattern_cards: int = 15, max_instruction_cards: int = 15,
                 evolve_batch_size: int = 2,
                 output_token_cap: int = DEFAULT_OUTPUT_TOKEN_CAP,
                 tool_timeout_seconds: int = DEFAULT_TOOL_TIMEOUT_SECONDS,
                 registry_budget: int = 1600, max_success_drop_rate: float = 0.10,
                 max_regression_rate: float = 0.20, min_cost_saving_rate: float = 0.0,
                 max_candidate_error_rate: float = 0.10, bootstrap_samples: int = 2000,
                 dry_run: bool = False):
        self.benchmark = benchmark
        self.config = str(Path(config).resolve())
        self.active_dir = Path(active_dir).resolve()
        self.work_dir = Path(work_dir).resolve()
        self.evolve_ids = list(evolve_case_ids)
        self.final_ids = list(final_eval_case_ids)
        self.baseline_dir = Path(baseline_dir).resolve() if baseline_dir else None
        self.final_baseline_dir = Path(final_baseline_dir).resolve() if final_baseline_dir else None
        self.final_results_root = Path(final_results_root).resolve()
        self.n_cycles = n_cycles
        self.n_concurrent = n_concurrent
        self.mini_swe_agent_dir = Path(mini_swe_agent_dir).resolve()
        self.min_support = min_support
        self.max_pattern_cards = max_pattern_cards
        self.max_instruction_cards = max_instruction_cards
        if evolve_batch_size <= 0:
            raise ValueError(f"evolve batch size must be positive, got {evolve_batch_size}")
        self.evolve_batch_size = evolve_batch_size
        self.output_token_cap = output_token_cap
        self.tool_timeout_seconds = tool_timeout_seconds
        self.registry_budget = registry_budget
        self.dry_run = dry_run
        self.prices = PriceModel.from_config(self.config)
        self.gate = RelaxedPromotionGateV9(
            max_success_drop_rate=max_success_drop_rate,
            max_regression_rate=max_regression_rate,
            min_cost_saving_rate=min_cost_saving_rate,
            max_candidate_error_rate=max_candidate_error_rate,
            bootstrap_samples=bootstrap_samples,
        )
        self._validate_split()

    def _validate_split(self) -> None:
        if len(self.evolve_ids) != 16 or len(set(self.evolve_ids)) != 16:
            raise ValueError(f"evolve set must contain exactly 16 unique cases, got {len(set(self.evolve_ids))}")
        if len(self.final_ids) != 64 or len(set(self.final_ids)) != 64:
            raise ValueError(f"final eval set must contain exactly 64 unique cases, got {len(set(self.final_ids))}")
        overlap = sorted(set(self.evolve_ids) & set(self.final_ids))
        if overlap:
            raise ValueError(f"evolve and final-eval cases must be disjoint: {overlap}")

    def run(self) -> dict:
        self.work_dir.mkdir(parents=True, exist_ok=True)
        if not self.dry_run and self.final_baseline_dir is None:
            raise ValueError("v9 final eval requires --final-baseline-dir to lock the no-evolve case set")
        locked_final_baseline: Optional[dict[str, TaskMetrics]] = None
        if self.final_baseline_dir is not None:
            if not self.final_baseline_dir.is_dir():
                raise ValueError(f"final no-evolve baseline does not exist: {self.final_baseline_dir}")
            locked_final_baseline = BenchmarkMetricAdapter(self.benchmark, self.prices).extract_run(
                self.final_baseline_dir, self.final_ids
            )
            missing_baseline = sorted(set(self.final_ids) - set(locked_final_baseline))
            if missing_baseline:
                raise ValueError(
                    f"final eval cases do not match no-evolve baseline; missing {missing_baseline}"
                )
        # RolloutAgent copies the current environment into benchmark scripts;
        # _bench_common then forwards these values into every agent container.
        os.environ["EVOLVE_TOOLS_V6_TIMEOUT_SECONDS"] = str(self.tool_timeout_seconds)
        os.environ["EVOLVE_TOOLS_V6_MEMORY_MB"] = str(DEFAULT_MEMORY_MB)
        os.environ["EVOLVE_TOOLS_V6_OUTPUT_TOKENS"] = str(self.output_token_cap)
        # Keep raw evolve rollouts within this experiment directory instead of
        # creating legacy top-level results/<benchmark>/ directories.
        os.environ["RESULTS_DIR"] = str(self.work_dir / "rollouts")
        seed_v9(self.active_dir)
        if not self.dry_run:
            deploy_v6(self.active_dir, api_type=_llm_api_type(self.config),
                      max_completion_tokens=_max_completion_tokens(), container=True)
        _write_json(self.work_dir / "experiment_split_manifest.json", {
            "schema_version": SCHEMA_VERSION, "benchmark": self.benchmark,
            "evolve_cases": self.evolve_ids, "final_eval_cases": self.final_ids,
            "overlap": [], "selection_locked": True,
            "final_baseline_dir": str(self.final_baseline_dir) if self.final_baseline_dir else None,
            "evolve_rollout_results_root": str(self.work_dir / "rollouts"),
            "final_eval_results_root": str(self.final_results_root),
            "cost_method": "token_usage_x_configured_price",
            "configured_prices_per_million_tokens": asdict(self.prices),
        })
        rollout = RolloutAgent(self.benchmark, self.config, n_tasks=16, n_concurrent=self.n_concurrent,
                               taskdir_root=self.work_dir / "taskdirs")
        current_run = self.baseline_dir
        current_run_role = "original_code_agent_rollout" if current_run is not None else None
        history: list[dict] = []
        report = {"schema_version": SCHEMA_VERSION, "benchmark": self.benchmark,
                  "cost_method": "token_usage_x_configured_price",
                  "cycles": [], "final_eval": None}
        evolution_cost = {"schema_version": SCHEMA_VERSION, "components": [],
                          "cost_method": "token_usage_x_configured_price",
                          "known_api_cost_total": 0.0, "unknown_cost_components": []}
        for cycle in range(1, self.n_cycles + 1):
            cycle_dir = self.work_dir / f"cycle-{cycle}"
            cycle_dir.mkdir(parents=True, exist_ok=True)
            state = CycleStateV9(cycle_dir / "state.json")
            try:
                generated_parent = current_run is None
                if current_run is None:
                    current_run = rollout.rollout(
                        self.active_dir, self.evolve_ids,
                        f"v9c{cycle}-parent-{self.benchmark}-{os.getpid()}", cycle,
                        dry_run=self.dry_run,
                    ).run_dir
                    current_run_role = "original_code_agent_rollout"
                if self.dry_run:
                    report["cycles"].append({"cycle": cycle, "decision": "dry-run"})
                    continue
                evidence_run, evidence_ids = self._annotation_copy(
                    current_run, cycle_dir / "parent_evidence_run", self.evolve_ids
                )
                parent_registry_hash = registry_hash(self.active_dir)
                _write_json(cycle_dir / "trajectory_lineage.json", {
                    "schema_version": SCHEMA_VERSION,
                    "cycle": cycle,
                    "source_role": current_run_role,
                    "source_run": str(current_run),
                    "source_harness_registry_hash": parent_registry_hash,
                    "expected_evolve_cases": self.evolve_ids,
                    "copied_evidence_cases": evidence_ids,
                    "exact_case_set": set(evidence_ids) == set(self.evolve_ids),
                })
                annotated_count = self._ensure_annotated(evidence_run)
                parent_metrics = BenchmarkMetricAdapter(self.benchmark, self.prices).extract_run(
                    current_run, self.evolve_ids
                )
                missing_parent = sorted(set(self.evolve_ids) - set(parent_metrics))
                if missing_parent:
                    raise RuntimeError(f"parent rollout is missing evolve trajectories: {missing_parent}")
                if generated_parent:
                    self._add_cost_component(
                        evolution_cost, cycle, "parent_rollout",
                        sum(float(item.api_cost or 0) for item in parent_metrics.values()),
                        detail={"run_dir": str(current_run), "cases": len(parent_metrics)},
                    )
                # The current annotator does not persist per-request usage.
                # Record that explicitly instead of silently under-counting it.
                if annotated_count:
                    evolution_cost["unknown_cost_components"].append({
                        "cycle": cycle, "stage": "dependency_annotation",
                        "trajectories": annotated_count,
                        "reason": "TrajectoryAnnotator does not persist API usage per request",
                    })
                _write_json(cycle_dir / "parent_metrics.json", {
                    "schema_version": SCHEMA_VERSION,
                    "cases": {task: asdict(metrics) for task, metrics in parent_metrics.items()},
                })
                manager = RegistryManagerV9(cycle_dir, self.active_dir)
                staging = manager.stage()
                prep = prepare_run_prompt(
                    run_dir=evidence_run, current_scripts=staging, work_dir=cycle_dir,
                    prices=self.prices, min_support=self.min_support,
                    max_pattern_cards=self.max_pattern_cards,
                    max_instruction_cards=self.max_instruction_cards,
                    evolve_batch_size=self.evolve_batch_size,
                    output_token_cap=self.output_token_cap,
                    tool_timeout_seconds=self.tool_timeout_seconds,
                    registry_budget=self.registry_budget, cycle=cycle, history=history,
                )
                pattern_cards = prep["selected"]
                instruction_cards = prep["instruction_cards"]
                cycle_report = {"cycle": cycle, "parent_run": str(current_run),
                                "parent_run_role": current_run_role,
                                "registry_before": parent_registry_hash,
                                "pattern_cards": len(pattern_cards),
                                "instruction_cards": len(instruction_cards), "promoted": False}
                if not pattern_cards and not instruction_cards:
                    summary = self._summary(cycle, "no evidence-backed candidates", problems=[
                        "No parseable cross-task candidate met the support requirement."
                    ])
                    self._rollback(cycle_dir, summary)
                    history.append(summary)
                    cycle_report.update({"decision": "rollback-and-continue", "summary": summary})
                    report["cycles"].append(cycle_report)
                    _write_json(self.work_dir / "experiment_report.json", report)
                    continue
                prompt_builder = V9PromptBuilder(output_token_cap=self.output_token_cap,
                                                 tool_timeout_seconds=self.tool_timeout_seconds,
                                                 registry_budget=self.registry_budget)
                batches = batch_candidate_cards(pattern_cards, instruction_cards,
                                                 self.evolve_batch_size)
                state.start("compile", {
                    "batch_size": self.evolve_batch_size,
                    "batch_count": len(batches),
                    "candidate_ids": [card["candidate_id"]
                                      for card in (*pattern_cards, *instruction_cards)],
                    "registry": registry_hash(staging),
                })
                warnings: list[str] = []
                batch_reports: list[dict] = []
                cumulative_patterns: list[Mapping[str, Any]] = []
                cumulative_instructions: list[Mapping[str, Any]] = []
                for batch in batches:
                    batch_index = int(batch["batch_index"])
                    batch_dir = cycle_dir / "compiler_batches" / f"batch-{batch_index:02d}"
                    cumulative_patterns.extend(batch["pattern_cards"])
                    cumulative_instructions.extend(batch["instruction_cards"])
                    prompt = prompt_builder.build(
                        staging, batch["pattern_cards"], batch["instruction_cards"], cycle=cycle,
                        history=history, batch_index=batch_index, batch_count=len(batches),
                    )
                    _write_text(batch_dir / "compile_prompt.md", prompt)
                    registry_before_batch = registry_hash(staging)
                    run_compile_agent(prompt, batch_dir, staging, self.config, self.mini_swe_agent_dir)
                    warnings = validate_registry_v9(
                        staging, output_token_cap=self.output_token_cap,
                        registry_budget=self.registry_budget, pattern_cards=cumulative_patterns,
                        instruction_cards=cumulative_instructions, require_manifest=True,
                    )
                    repairs = []
                    for attempt in range(1, 3):
                        if not warnings:
                            break
                        repair_prompt = prompt_builder.repair_prompt(
                            staging, warnings, _read_json(staging / "change_manifest.json", {}) or {}
                        )
                        repair_work = batch_dir / f"repair-{attempt}"
                        _write_text(repair_work / "repair_prompt.md", repair_prompt)
                        run_compile_agent(repair_prompt, repair_work, staging, self.config,
                                          self.mini_swe_agent_dir,
                                          output_name="compile_trajectory.json")
                        warnings = validate_registry_v9(
                            staging, output_token_cap=self.output_token_cap,
                            registry_budget=self.registry_budget, pattern_cards=cumulative_patterns,
                            instruction_cards=cumulative_instructions, require_manifest=True,
                        )
                        repairs.append({"attempt": attempt, "warnings_after": warnings})
                    snapshot = batch_dir / "registry_after"
                    snapshot.mkdir(parents=True, exist_ok=True)
                    for name in (*REGISTRY_FILES, "change_manifest.json"):
                        if (staging / name).exists():
                            shutil.copy2(staging / name, snapshot / name)
                    batch_report = {
                        "batch_index": batch_index,
                        "batch_count": len(batches),
                        "pattern_candidate_ids": [card["candidate_id"] for card in batch["pattern_cards"]],
                        "instruction_candidate_ids": [card["candidate_id"]
                                                       for card in batch["instruction_cards"]],
                        "registry_before": registry_before_batch,
                        "registry_after": registry_hash(staging),
                        "repairs": repairs,
                        "passed": not warnings,
                        "warnings": warnings,
                    }
                    _write_json(batch_dir / "batch_report.json", batch_report)
                    batch_reports.append(batch_report)
                    self._record_compiler_cost(
                        evolution_cost, cycle, batch_dir / "compile_trajectory.json",
                        stage=f"compiler_batch_{batch_index}",
                    )
                    for attempt in range(1, len(repairs) + 1):
                        self._record_compiler_cost(
                            evolution_cost, cycle,
                            batch_dir / f"repair-{attempt}" / "compile_trajectory.json",
                            stage=f"compiler_batch_{batch_index}_repair_{attempt}",
                        )
                    if warnings:
                        break
                _write_json(cycle_dir / "validation.json", {"schema_version": SCHEMA_VERSION,
                                                            "passed": not warnings, "warnings": warnings})
                _write_json(cycle_dir / "compile_repairs.json", {"schema_version": SCHEMA_VERSION,
                                                                  "batches": batch_reports})
                _write_json(self.work_dir / "evolution_cost.json", evolution_cost)
                if warnings:
                    state.fail("compile", "; ".join(warnings))
                    summary = self._summary(cycle, "compile validation failed", problems=warnings)
                    self._rollback(cycle_dir, summary)
                    history.append(summary)
                    cycle_report.update({"decision": "rollback-and-continue", "summary": summary})
                    report["cycles"].append(cycle_report)
                    _write_json(self.work_dir / "experiment_report.json", report)
                    continue
                state.finish("compile", {"registry": registry_hash(staging),
                                          "completed_batches": len(batch_reports)})
                deploy_v6(staging, api_type=_llm_api_type(self.config),
                          max_completion_tokens=_max_completion_tokens(), container=True)
                candidate_run = rollout.rollout(
                    staging, self.evolve_ids,
                    f"v9c{cycle}-canary-{self.benchmark}-{os.getpid()}", cycle,
                ).run_dir
                candidate_metrics = BenchmarkMetricAdapter(self.benchmark, self.prices).extract_run(
                    candidate_run, self.evolve_ids
                )
                self._add_cost_component(
                    evolution_cost, cycle, "candidate_canary",
                    sum(float(item.api_cost or 0) for item in candidate_metrics.values()),
                    detail={"run_dir": str(candidate_run), "cases": len(candidate_metrics)},
                )
                _write_json(self.work_dir / "evolution_cost.json", evolution_cost)
                _write_json(cycle_dir / "candidate_metrics.json", {
                    "schema_version": SCHEMA_VERSION,
                    "cases": {task: asdict(metrics) for task, metrics in candidate_metrics.items()},
                })
                gate = self.gate.evaluate(parent_metrics, candidate_metrics, self.evolve_ids)
                missing_candidate = sorted(set(self.evolve_ids) - set(candidate_metrics))
                if missing_candidate:
                    gate["promote"] = False
                    gate.setdefault("reasons", []).append(
                        f"candidate rollout missing trajectories for {missing_candidate}"
                    )
                gate["cost_method"] = "token_usage_x_configured_price"
                _write_json(cycle_dir / "gate.json", gate)
                cycle_report.update({"candidate_run": str(candidate_run), "gate": gate})
                if not gate["promote"]:
                    summary = self._summary(cycle, "promotion gate failed", gate=gate)
                    self._rollback(cycle_dir, summary)
                    history.append(summary)
                    cycle_report.update({"decision": "rollback-and-continue", "summary": summary})
                    report["cycles"].append(cycle_report)
                    _write_json(self.work_dir / "experiment_report.json", report)
                    continue
                lineage = manager.promote(gate, cycle)
                deploy_v6(self.active_dir, api_type=_llm_api_type(self.config),
                          max_completion_tokens=_max_completion_tokens(), container=True)
                (cycle_dir / "PROMOTED").write_text(lineage["active_version"] + "\n", encoding="utf-8")
                summary = self._summary(cycle, "promoted", gate=gate)
                history.append(summary)
                cycle_report.update({"decision": "promote", "promoted": True,
                                     "lineage": lineage, "summary": summary})
                report["cycles"].append(cycle_report)
                current_run = candidate_run
                current_run_role = f"promoted_candidate_cycle_{cycle}_rollout"
                _write_json(self.work_dir / "experiment_report.json", report)
                self._save_history(history)
            except Exception as exc:
                state.fail("cycle", exc)
                summary = self._summary(cycle, "cycle exception", problems=[str(exc)])
                self._rollback(cycle_dir, summary)
                history.append(summary)
                report["cycles"].append({"cycle": cycle, "decision": "rollback-and-continue",
                                          "exception": str(exc), "summary": summary})
                _write_json(self.work_dir / "experiment_report.json", report)
                self._save_history(history)
                logger.exception("v9 cycle %d failed; active registry preserved and next cycle continues", cycle)
        self._save_history(history)
        _write_json(self.work_dir / "evolution_cost.json", evolution_cost)
        if not self.dry_run:
            os.environ["RESULTS_DIR"] = str(self.final_results_root)
            final_rollout = RolloutAgent(
                self.benchmark, self.config, n_tasks=64, n_concurrent=self.n_concurrent,
                taskdir_root=self.work_dir / "final_eval_taskdirs",
            )
            final_run_id = f"evolve-v9cycle-{self.benchmark}-{self.work_dir.name}"
            final_run = final_rollout.rollout(
                self.active_dir, self.final_ids,
                final_run_id, self.n_cycles + 1,
            ).run_dir
            final_metrics = BenchmarkMetricAdapter(self.benchmark, self.prices).extract_run(
                final_run, self.final_ids
            )
            missing = sorted(set(self.final_ids) - set(final_metrics))
            errors = sorted(task for task, metrics in final_metrics.items() if metrics.error)
            final_report = {
                "schema_version": SCHEMA_VERSION,
                "run_dir": str(final_run),
                "registry_hash": registry_hash(self.active_dir),
                "expected_cases": 64,
                "completed_cases": len(final_metrics),
                "missing_cases": missing,
                "error_cases": errors,
                "case_set_matches_no_evolve": not missing and locked_final_baseline is not None,
                "successes": sum(metrics.success for metrics in final_metrics.values()),
                "total_api_cost": sum(float(metrics.api_cost or 0) for metrics in final_metrics.values()),
                "cases": {task: asdict(metrics) for task, metrics in final_metrics.items()},
                "harness_frozen": True,
            }
            if self.final_baseline_dir and locked_final_baseline is not None:
                baseline_metrics = locked_final_baseline
                paired_rows = []
                for task in self.final_ids:
                    parent = baseline_metrics.get(task)
                    candidate = final_metrics.get(task)
                    paired_rows.append({
                        "task_id": task,
                        "baseline": asdict(parent) if parent else None,
                        "candidate": asdict(candidate) if candidate else None,
                        "success_delta": (int(candidate.success) - int(parent.success)
                                          if parent and candidate else None),
                        "cost_delta": (float(parent.api_cost or 0) - float(candidate.api_cost or 0)
                                       if parent and candidate else None),
                    })
                paired_cost = [float(row["cost_delta"]) for row in paired_rows
                               if row["cost_delta"] is not None]
                saving_per_eval = sum(paired_cost)
                final_report["baseline_run_dir"] = str(self.final_baseline_dir)
                final_report["paired_case_count"] = len(paired_cost)
                final_report["success_regressions"] = [
                    row["task_id"] for row in paired_rows if row["success_delta"] == -1
                ]
                final_report["success_improvements"] = [
                    row["task_id"] for row in paired_rows if row["success_delta"] == 1
                ]
                final_report["cost_saving_per_64_cases"] = saving_per_eval
                final_report["cost_saving_bootstrap_interval"] = self.gate._bootstrap(paired_cost)
                final_report["break_even_64_case_evaluations_lower_bound"] = (
                    evolution_cost["known_api_cost_total"] / saving_per_eval
                    if saving_per_eval > 0 else None
                )
                final_report["break_even_caveat"] = (
                    "Lower bound only while evolution_cost.unknown_cost_components is non-empty."
                    if evolution_cost["unknown_cost_components"] else None
                )
                _write_json(self.work_dir / "paired_final_metrics.json", {
                    "schema_version": SCHEMA_VERSION, "rows": paired_rows,
                })
            _write_json(self.work_dir / "final_report.json", final_report)
            report["final_eval"] = final_report
            _write_json(self.work_dir / "experiment_report.json", report)
        return report

    @staticmethod
    def _add_cost_component(ledger: dict, cycle: int, stage: str, cost: float,
                            *, detail: Optional[Mapping[str, Any]] = None) -> None:
        value = float(cost)
        ledger["components"].append({
            "cycle": cycle, "stage": stage, "api_cost": value,
            "cost_source": "token_usage_x_configured_price",
            "detail": dict(detail or {}),
        })
        ledger["known_api_cost_total"] = float(ledger.get("known_api_cost_total", 0)) + value

    def _record_compiler_cost(self, ledger: dict, cycle: int, path: Path, *, stage: str) -> None:
        data = _read_json(path, {}) or {}
        usage = _message_usage_totals(data)
        if usage["api_calls"] > 0:
            cost = self.prices.calculate(
                usage["prompt_tokens"], usage["cached_tokens"], usage["completion_tokens"]
            )
            self._add_cost_component(
                ledger, cycle, stage, cost,
                detail={"trajectory": str(path), **usage},
            )
        else:
            ledger["unknown_cost_components"].append({
                "cycle": cycle, "stage": stage, "trajectory": str(path),
                "reason": "compiler trajectory did not persist per-request token usage",
            })

    def _annotation_copy(self, run_dir: Path, destination: Path,
                         expected_ids: Sequence[str]) -> tuple[Path, list[str]]:
        """Copy exactly the locked evolve split and reject incomplete/duplicate evidence."""
        if destination.exists():
            shutil.rmtree(destination)
        expected = set(expected_ids)
        copied: dict[str, Path] = {}
        for trajectory in _trajectory_files(run_dir):
            task_id = BenchmarkMetricAdapter._match_task_id(trajectory, expected_ids)
            if task_id not in expected:
                continue
            if task_id in copied:
                raise RuntimeError(
                    f"duplicate evolve trajectory for {task_id}: {copied[task_id]} and {trajectory}"
                )
            trial_name = trajectory.parent.parent.name
            trial = destination / trial_name
            (trial / "agent").mkdir(parents=True, exist_ok=True)
            shutil.copy2(trajectory, trial / "agent" / "trajectory.json")
            for relative in (Path("result.json"), Path("config.json"), Path("verifier/reward.json")):
                source = trajectory.parent.parent / relative
                if source.exists():
                    target = trial / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
            copied[task_id] = trajectory
        missing = sorted(expected - set(copied))
        if missing:
            raise RuntimeError(f"evolve evidence run is missing trajectories: {missing}")
        return destination, sorted(copied)

    def _ensure_annotated(self, run_dir: Path) -> int:
        missing = []
        for path in _trajectory_files(run_dir):
            trajectory = _read_json(path, {}) or {}
            dependencies = trajectory.get("dependencies") or {}
            if not all(str(index) in dependencies for index in range(1, len(_action_steps(trajectory)) + 1)):
                missing.append(path)
        if missing:
            TrajectoryAnnotator(self.config, workers=self.n_concurrent).run(run_dir)
        return len(missing)

    @staticmethod
    def _summary(cycle: int, decision: str, *, gate: Optional[Mapping[str, Any]] = None,
                 problems: Sequence[str] = ()) -> dict:
        all_problems = list(problems) + list((gate or {}).get("reasons", []))
        recommendations = []
        if any("cost" in problem for problem in all_problems):
            recommendations.append("Reduce schema/output overhead and eliminate duplicate native/bash work.")
        if any("regression" in problem or "success" in problem for problem in all_problems):
            recommendations.append("Narrow or abandon workflows used by parent-success/candidate-failure cases.")
        if any("error" in problem or "coverage" in problem for problem in all_problems):
            recommendations.append("Fix runtime reliability and incomplete trials before reusing this candidate.")
        return {"schema_version": SCHEMA_VERSION, "cycle": cycle, "decision": decision,
                "staging_abandoned": decision != "promoted", "problems": all_problems,
                "gate": gate, "recommendations_for_next_cycle": recommendations}

    def _rollback(self, cycle_dir: Path, summary: Mapping[str, Any]) -> None:
        (cycle_dir / "ROLLED_BACK").write_text(registry_hash(self.active_dir) + "\n", encoding="utf-8")
        _write_json(cycle_dir / "evolution_summary.json", summary)
        lines = [f"# Cycle {summary['cycle']} evolution summary", "", f"Decision: {summary['decision']}", "",
                 "## Problems", *[f"- {item}" for item in summary.get("problems", [])], "",
                 "## Recommendations for the next cycle",
                 *[f"- {item}" for item in summary.get("recommendations_for_next_cycle", [])]]
        _write_text(cycle_dir / "evolution_summary.md", "\n".join(lines))
        self._save_history([])

    def _save_history(self, history: Sequence[Mapping[str, Any]]) -> None:
        if not history:
            return
        _write_json(self.work_dir / "history.json", {"schema_version": SCHEMA_VERSION, "cycles": list(history)})
        _write_text(self.work_dir / "history.jsonl", "\n".join(_stable_json(row) for row in history))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _case_ids(path: Path) -> list[str]:
    return [line.strip() for line in Path(path).read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")]


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Evolve v9 — auditable cost-aware harness evolution")
    parser.add_argument("--log-file", default=None)
    sub = parser.add_subparsers(dest="cmd", required=True)

    offline = sub.add_parser("prepare-v6-prompts", help="reconstruct all v9 Cycle-1 prompts from v6 artifacts")
    offline.add_argument("--v6-root", default=str(ROOT / "results/evolve/v6cycle"))
    offline.add_argument("--prep-root", default=str(ROOT / "results/prep/runs"))
    offline.add_argument("--output-root", default=str(ROOT / "results/evolve/v9cycle/prompt_review"))
    offline.add_argument("--pricing-config", default=str(ROOT / "_config/deepseekv4_flash.yaml"))
    offline.add_argument("--min-support", type=int, default=2)
    offline.add_argument("--max-pattern-cards", type=int, default=15)
    offline.add_argument("--max-instruction-cards", type=int, default=15)
    offline.add_argument("--evolve-batch-size", type=int, default=2)
    offline.add_argument("--output-token-cap", type=int, default=DEFAULT_OUTPUT_TOKEN_CAP)
    offline.add_argument("--registry-budget", type=int, default=1600)

    prepare = sub.add_parser("prepare", help="prepare one v9 prompt from contrastive samples")
    prepare.add_argument("--sample-root", required=True)
    prepare.add_argument("--current-scripts", required=True)
    prepare.add_argument("--work-dir", required=True)
    prepare.add_argument("--pricing-config", default=str(ROOT / "_config/deepseekv4_flash.yaml"))
    prepare.add_argument("--min-support", type=int, default=2)
    prepare.add_argument("--max-pattern-cards", type=int, default=15)
    prepare.add_argument("--max-instruction-cards", type=int, default=15)
    prepare.add_argument("--evolve-batch-size", type=int, default=2)
    prepare.add_argument("--output-token-cap", type=int, default=DEFAULT_OUTPUT_TOKEN_CAP)
    prepare.add_argument("--registry-budget", type=int, default=1600)

    check = sub.add_parser("check-registry", help="validate a v9 staging registry")
    check.add_argument("--scripts-dir", required=True)
    check.add_argument("--output-token-cap", type=int, default=DEFAULT_OUTPUT_TOKEN_CAP)
    check.add_argument("--registry-budget", type=int, default=1600)
    check.add_argument("--require-manifest", action="store_true")

    experiment = sub.add_parser("experiment", help="run three evolve cycles; keep 64-case final set sealed")
    experiment.add_argument("--benchmark", required=True, choices=list(BENCHMARKS))
    experiment.add_argument("--config", required=True)
    experiment.add_argument("--scripts-dir", required=True, help="active registry directory")
    experiment.add_argument("--work-dir", required=True)
    experiment.add_argument("--evolve-cases-file", required=True)
    experiment.add_argument("--final-eval-cases-file", required=True)
    experiment.add_argument("--baseline-dir", default=None)
    experiment.add_argument("--final-baseline-dir", default=None,
                            help="matching no-evolve 64-case run for paired final analysis")
    experiment.add_argument("--final-results-root", default=str(ROOT / "results" / "eval"),
                            help="raw final rollout root; benchmark subdir is added automatically")
    experiment.add_argument("--n-cycles", type=int, default=3)
    experiment.add_argument("--n-concurrent", type=int, default=16)
    experiment.add_argument("--mini-swe-agent-dir", default=str(DEFAULT_MINI_SWE_AGENT))
    experiment.add_argument("--min-support", type=int, default=2)
    experiment.add_argument("--max-pattern-cards", type=int, default=15)
    experiment.add_argument("--max-instruction-cards", type=int, default=15)
    experiment.add_argument("--evolve-batch-size", type=int, default=2)
    experiment.add_argument("--output-token-cap", type=int, default=DEFAULT_OUTPUT_TOKEN_CAP)
    experiment.add_argument("--tool-timeout-seconds", type=int, default=DEFAULT_TOOL_TIMEOUT_SECONDS)
    experiment.add_argument("--registry-budget", type=int, default=1600)
    experiment.add_argument("--max-success-drop-rate", type=float, default=0.10)
    experiment.add_argument("--max-regression-rate", type=float, default=0.20)
    experiment.add_argument("--min-cost-saving-rate", type=float, default=0.0)
    experiment.add_argument("--max-candidate-error-rate", type=float, default=0.10)
    experiment.add_argument("--bootstrap-samples", type=int, default=2000)
    experiment.add_argument("--dry-run", action="store_true")

    args = parser.parse_args(argv)
    _setup_logging(args.log_file)
    if args.cmd == "prepare-v6-prompts":
        prices = PriceModel.from_config(args.pricing_config)
        report = prepare_all_v6_prompts(
            v6_root=Path(args.v6_root), prep_root=Path(args.prep_root),
            output_root=Path(args.output_root), prices=prices, min_support=args.min_support,
            max_pattern_cards=args.max_pattern_cards,
            max_instruction_cards=args.max_instruction_cards,
            evolve_batch_size=args.evolve_batch_size,
            output_token_cap=args.output_token_cap, registry_budget=args.registry_budget,
        )
        logger.info("prepared %d v6-derived v9 prompt sets", len(report["jobs"]))
        return 0 if report["jobs"] else 2
    if args.cmd == "prepare":
        samples = sorted(Path(args.sample_root).glob("**/agent/contrastive_sample.json"))
        if not samples:
            samples = sorted(Path(args.sample_root).glob("**/contrastive_sample.json"))
        if not samples:
            raise ValueError(f"no contrastive samples under {args.sample_root}")
        manifest = prepare_evidence_prompt(
            sample_paths=samples, current_scripts=Path(args.current_scripts), work_dir=Path(args.work_dir),
            prices=PriceModel.from_config(args.pricing_config), min_support=args.min_support,
            max_pattern_cards=args.max_pattern_cards,
            max_instruction_cards=args.max_instruction_cards,
            evolve_batch_size=args.evolve_batch_size,
            output_token_cap=args.output_token_cap, registry_budget=args.registry_budget,
        )
        logger.info("prompt prepared: %s", manifest)
        return 0
    if args.cmd == "check-registry":
        warnings = validate_registry_v9(Path(args.scripts_dir), output_token_cap=args.output_token_cap,
                                        registry_budget=args.registry_budget,
                                        require_manifest=args.require_manifest)
        for warning in warnings:
            logger.error("v9 registry: %s", warning)
        return 2 if warnings else 0
    if args.cmd == "experiment":
        report = EvolveV9Experiment(
            benchmark=args.benchmark, config=args.config, active_dir=Path(args.scripts_dir),
            work_dir=Path(args.work_dir), evolve_case_ids=_case_ids(Path(args.evolve_cases_file)),
            final_eval_case_ids=_case_ids(Path(args.final_eval_cases_file)),
            baseline_dir=Path(args.baseline_dir) if args.baseline_dir else None,
            final_baseline_dir=Path(args.final_baseline_dir) if args.final_baseline_dir else None,
            final_results_root=Path(args.final_results_root),
            n_cycles=args.n_cycles, n_concurrent=args.n_concurrent,
            mini_swe_agent_dir=Path(args.mini_swe_agent_dir), min_support=args.min_support,
            max_pattern_cards=args.max_pattern_cards,
            max_instruction_cards=args.max_instruction_cards,
            evolve_batch_size=args.evolve_batch_size,
            output_token_cap=args.output_token_cap,
            tool_timeout_seconds=args.tool_timeout_seconds,
            registry_budget=args.registry_budget,
            max_success_drop_rate=args.max_success_drop_rate,
            max_regression_rate=args.max_regression_rate,
            min_cost_saving_rate=args.min_cost_saving_rate,
            max_candidate_error_rate=args.max_candidate_error_rate,
            bootstrap_samples=args.bootstrap_samples, dry_run=args.dry_run,
        ).run()
        logger.info("v9 experiment completed: %s", report)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
