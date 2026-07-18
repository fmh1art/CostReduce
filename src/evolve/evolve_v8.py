"""Evolve v8: Validated Cost-Aware Graph Contraction (VCGC).

This module deliberately keeps discovery deterministic and validation
fail-closed.  It can mine structural candidates from annotated trajectories,
but a candidate only becomes a tool card after independently-produced replay
and held-out evidence passes all three validation gates.

The rollout/runtime wiring is reused from :mod:`evolve_v6_cycle`; v8 replaces
the contrastive-sample-to-tool guess with graph mining, conservative selection,
and evidence-backed tool cards.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import itertools
import json
import logging
import math
import os
import random
import re
import shlex
import shutil
import statistics
import tempfile
from collections import Counter, defaultdict, deque
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Optional, Sequence

from src.tools.llm import LLM

from .annotator import TrajectoryAnnotator
from .evolve_v6_cycle import (
    BENCHMARKS,
    DEFAULT_MINI_SWE_AGENT,
    EvolveAgent,
    RolloutAgent,
    _llm_api_type,
    _max_completion_tokens,
)
from .evolver import MiniSweAgentRunner
from .native_tools_v6 import deploy as deploy_v6
from .native_tools_v6 import seed as seed_v6
from .native_tools_v6 import validate as validate_v6
from .run_evolve import _setup_logging

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_VERSION = "vcgc.v8.2"
READ_ONLY_OPS = frozenset({"SEARCH", "FIND", "READ", "TEST"})
LEGACY_INSTRUCTION_POLICY_TYPES = frozenset({"early_exit", "verification_skip", "bounded_risk"})
BASELINE_INSTRUCTION_GUARDRAILS = (
    "Stop repeating an approach when it produces no new evidence; preserve the best partial result and report a hard blocker instead of looping.",
    "If a relevant check is unavailable, use the cheapest meaningful substitute and disclose the omission; never skip verification for destructive, security, data-integrity, public API/schema, or broad changes.",
    "After safer paths are exhausted, take only scoped and reversible risks: capture prior state, limit blast radius, inspect immediately, and roll back on failure; never cause irreversible or external side effects without authorization.",
)


def seed_v8(scripts_dir: Path | str) -> None:
    """Seed an actually empty registry; v8 admits no unvalidated default tool."""
    directory = Path(scripts_dir)
    directory.mkdir(parents=True, exist_ok=True)
    files = {
        "tools.json": "[]\n",
        "executor.py": (
            '"""VCGC v8 staging executor."""\n'
            "MAX_OUTPUT_CHARS = 4000\n\n"
            "def run_tool(action, cwd=None, timeout=120):\n"
            "    return {'output': ('unknown tool %r' % action.get('tool'))[:MAX_OUTPUT_CHARS], "
            "'returncode': 1, 'exception_info': 'unknown tool'}\n"
        ),
        "instruction.md": (
            "# VCGC baseline governance\n\n"
            + "\n".join(f"- {rule}" for rule in BASELINE_INSTRUCTION_GUARDRAILS)
            + "\n\nNo sample-derived decision threshold has passed validation yet.\n"
        ),
    }
    for name, content in files.items():
        path = directory / name
        if not path.exists():
            path.write_text(content, encoding="utf-8")


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _stable_hash(value: Any, length: int = 20) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()[:length]


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def _observation_text(observation: Any) -> str:
    if isinstance(observation, dict) and isinstance(observation.get("results"), list):
        chunks: list[str] = []
        for item in observation["results"]:
            content = item.get("content", item) if isinstance(item, dict) else item
            if isinstance(content, str):
                try:
                    content = json.loads(content)
                except json.JSONDecodeError:
                    pass
            if isinstance(content, dict):
                chunks.append(_text(content.get("output", content)))
            else:
                chunks.append(_text(content))
        return "\n".join(chunks)
    return _text(observation or "")


def _action_steps(trajectory: Mapping[str, Any]) -> list[dict]:
    return [
        step for step in trajectory.get("steps", [])
        if step.get("tool_calls") or "observation" in step or step.get("action")
    ]


def _trajectory_files(run_dir: Path) -> list[Path]:
    direct = sorted(run_dir.glob("**/agent/trajectory.json"))
    return direct or sorted(run_dir.glob("**/trajectory.json"))


@dataclass(frozen=True)
class NormalizedCall:
    op: str
    path_role: str
    arg_roles: tuple[str, ...]
    confidence: float
    rejected_reason: str = ""
    state_effects: tuple[str, ...] = ()

    @property
    def accepted(self) -> bool:
        return not self.rejected_reason and self.confidence >= 0.8

    @property
    def label(self) -> str:
        return f"{self.op}|{self.path_role}|{','.join(self.arg_roles)}"


class ShellNormalizer:
    """Deterministic, auditable normalizer with an explicit reject path."""

    SEARCH = frozenset({"rg", "grep", "egrep", "fgrep"})
    FIND = frozenset({"find", "fd"})
    READ = frozenset({"cat", "head", "tail", "sed", "awk", "nl", "wc", "ls", "pwd", "tree"})
    TEST = frozenset({"pytest", "tox", "nosetests"})
    WRITE = frozenset({"rm", "mv", "cp", "mkdir", "touch", "chmod", "chown", "ln", "tee"})
    SHELL_NAMES = frozenset({"bash", "shell", "terminal", "exec", "exec_command"})

    def normalize_tool_call(self, call: Any) -> list[NormalizedCall]:
        if not isinstance(call, dict):
            return [self._reject("malformed tool call")]
        name = str(call.get("function_name") or call.get("name") or call.get("tool") or "").strip()
        args = call.get("arguments") or call.get("args") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {"command": args}
        if name.lower() in self.SHELL_NAMES:
            command = args.get("command") or args.get("cmd") or args.get("script") or ""
            return self.normalize_shell(str(command))
        if not name:
            action = call.get("action")
            if isinstance(action, str):
                return self.normalize_shell(action)
            return [self._reject("missing tool name")]
        op = self._native_op(name)
        if not op:
            return [self._reject(f"unknown native tool: {name}")]
        effects = () if op in READ_ONLY_OPS else ("unknown",)
        paths = [str(v) for k, v in args.items() if any(x in k.lower() for x in ("path", "file", "cwd", "dir"))]
        return [NormalizedCall(op, self._path_role(paths[0] if paths else ""), self._argument_roles(op, args),
                               1.0, state_effects=effects)]

    def normalize_shell(self, command: str) -> list[NormalizedCall]:
        if not command.strip():
            return [self._reject("empty shell command")]
        # Discard-only/capture-only redirections do not mutate the repository.
        command = re.sub(r"(?:^|\s)(?:2>&1|[012]?>/dev/null)(?=\s|$)", " ", command)
        if re.search(r"(?:^|\s)(?:>>?|<<)(?:\s|$)", command):
            return [self._reject("shell redirection or heredoc has ambiguous side effects")]
        try:
            lexer = shlex.shlex(command, posix=True, punctuation_chars="|;&")
            lexer.whitespace_split = True
            tokens = list(lexer)
        except ValueError as exc:
            return [self._reject(f"shell parse error: {exc}")]
        if any(tok in {"&", "||"} for tok in tokens):
            return [self._reject("background or conditional-failure shell command")]
        # Real benchmark calls overwhelmingly use `cd <repo> && <read>`.  Treat
        # cd/export as context setters, not operations; multiple substantive
        # segments remain atomic labels on the same LLM-turn node.
        segments: list[list[str]] = [[]]
        for tok in tokens:
            if tok in {";", "&&"}:
                segments.append([])
            else:
                segments[-1].append(tok)
        if not all(segments):
            return [self._reject("malformed compound command")]
        normalized: list[NormalizedCall] = []
        for segment in segments:
            groups: list[list[str]] = [[]]
            for tok in segment:
                if tok == "|":
                    groups.append([])
                else:
                    groups[-1].append(tok)
            if not all(groups):
                return [self._reject("malformed pipeline")]
            for argv in groups:
                cmd = Path(argv[0]).name.lower() if argv else ""
                if cmd == "cd" and len(argv) == 2:
                    continue
                if cmd == "export" and len(argv) >= 2 and all("=" in x for x in argv[1:]):
                    continue
                normalized.append(self._normalize_argv(argv))
        return normalized or [self._reject("command only changes shell context")]

    def _normalize_argv(self, argv: Sequence[str]) -> NormalizedCall:
        env_prefix = 0
        while env_prefix < len(argv) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", argv[env_prefix]):
            env_prefix += 1
        argv = argv[env_prefix:]
        if not argv:
            return self._reject("environment assignment without command")
        cmd = Path(argv[0]).name.lower()
        if cmd.startswith("#"):
            return self._reject("comment is not an operation")
        if cmd in {"sudo", "env", "xargs", "sh", "bash", "zsh"}:
            return self._reject(f"ambiguous shell wrapper: {cmd}")
        if cmd == "python" or cmd.startswith("python3"):
            if len(argv) >= 3 and argv[1:3] == ["-m", "pytest"]:
                return self._from_argv("TEST", argv[3:])
            if len(argv) >= 2 and Path(argv[1]).name == "runtests.py":
                return self._from_argv("TEST", argv[2:])
            return self._reject("arbitrary python has unknown side effects")
        if cmd == "go" and len(argv) > 1 and argv[1] == "test":
            return self._from_argv("TEST", argv[2:])
        if cmd == "cargo" and len(argv) > 1 and argv[1] == "test":
            return self._from_argv("TEST", argv[2:])
        if cmd in {"npm", "yarn", "pnpm"} and len(argv) > 1 and "test" in argv[1]:
            return self._from_argv("TEST", argv[2:])
        if cmd == "make" and any("test" in x.lower() or "check" in x.lower() for x in argv[1:]):
            return self._from_argv("TEST", argv[1:])
        if cmd in self.SEARCH:
            return self._from_argv("SEARCH", argv[1:])
        if cmd in self.FIND:
            return self._from_argv("FIND", argv[1:])
        if cmd in {"sed", "awk"} and any(x == "-i" or x.startswith("-i") for x in argv[1:]):
            return NormalizedCall("WRITE", self._path_role(argv[-1] if len(argv) > 1 else ""), (),
                                  0.95, state_effects=("filesystem",))
        if cmd in self.READ:
            return self._from_argv("READ", argv[1:])
        if cmd in self.TEST:
            return self._from_argv("TEST", argv[1:])
        if cmd in self.WRITE or any(tok in {">", ">>", "2>"} for tok in argv):
            return NormalizedCall("WRITE", "<unknown>", (), 0.9, state_effects=("filesystem",))
        if cmd == "git" and len(argv) > 1:
            sub = argv[1]
            if sub in {"diff", "status", "show", "log", "grep"}:
                return self._from_argv("READ", argv[2:])
            return NormalizedCall("WRITE", "<repo>", (f"git:{sub}",), 0.9, state_effects=("repository",))
        return self._reject(f"unrecognized command: {cmd}")

    def _from_argv(self, op: str, argv: Sequence[str]) -> NormalizedCall:
        positionals = [x for x in argv if x and not x.startswith("-") and not x.isdigit()]
        path = next((x for x in reversed(positionals) if self._looks_like_path(x)), "")
        roles: list[str] = []
        if op == "SEARCH" and positionals:
            roles.append("symbol")
        if op == "READ" and any(re.search(r"\d", x) for x in argv if x.startswith("-")):
            roles.append("line_range")
        if op == "TEST" and positionals:
            roles.append("test_target")
        if path:
            roles.append("path")
        return NormalizedCall(op, self._path_role(path), tuple(sorted(set(roles))), 0.95)

    @staticmethod
    def _native_op(name: str) -> str:
        value = name.lower().replace("_", "-")
        if any(x in value for x in ("search", "grep", "locate-symbol")):
            return "SEARCH"
        if value.startswith("find") or "list-file" in value:
            return "FIND"
        if any(x in value for x in ("read", "show", "context")):
            return "READ"
        if any(x in value for x in ("test", "check", "verify")):
            return "TEST"
        if any(x in value for x in ("edit", "write", "patch", "remove", "create")):
            return "WRITE"
        return ""

    @staticmethod
    def _looks_like_path(value: str) -> bool:
        return "/" in value or value.startswith(".") or bool(re.search(r"\.[A-Za-z0-9]{1,8}$", value))

    @classmethod
    def _path_role(cls, path: str) -> str:
        p = path.replace("\\", "/").lower()
        if not p:
            return "<repo>"
        if "/tmp" in p or p.startswith("/tmp"):
            return "<temp>"
        if "test" in p:
            return "<test>"
        if any(x in p for x in ("src/", "lib/", "app/")):
            return "<src>"
        if Path(p).suffix in {".py", ".js", ".ts", ".go", ".rs", ".java", ".c", ".h"}:
            return "<source-file>"
        return "<repo>"

    @staticmethod
    def _argument_roles(op: str, args: Mapping[str, Any]) -> tuple[str, ...]:
        roles = []
        for key in args:
            low = key.lower()
            if any(x in low for x in ("query", "pattern", "symbol")):
                roles.append("symbol")
            elif any(x in low for x in ("path", "file", "cwd", "dir")):
                roles.append("path")
            elif any(x in low for x in ("line", "start", "end", "head", "tail")):
                roles.append("line_range")
            elif "test" in low:
                roles.append("test_target")
        return tuple(sorted(set(roles)))

    @staticmethod
    def _reject(reason: str) -> NormalizedCall:
        return NormalizedCall("REJECT", "<unknown>", (), 0.0, rejected_reason=reason,
                              state_effects=("unknown",))


@dataclass(frozen=True)
class PriceModel:
    uncached_input: float = 1.0
    cached_input: float = 0.1
    completion: float = 1.0
    chars_per_token: float = 4.0

    @classmethod
    def from_config(cls, path: Path | str) -> "PriceModel":
        config = LLM._load_config(str(path))
        prices = config.get("price_yuan_per_million_token") or {}
        if not isinstance(prices, Mapping):
            prices = {}
        # LLM._load_config has a dependency-free YAML fallback which flattens
        # nested scalar keys; accept both shapes so pricing never silently
        # changes with whether PyYAML happens to be installed.
        prices = {**{key: config.get(key) for key in ("input_token", "cached_token", "output_token")
                     if config.get(key) not in (None, "")}, **prices}
        return cls(
            uncached_input=float(prices.get("input_token", 1.0)),
            cached_input=float(prices.get("cached_token", 0.1)),
            completion=float(prices.get("output_token", 1.0)),
        )


class CostLedger:
    def __init__(self, prices: PriceModel = PriceModel()):
        self.prices = prices

    def annotate(self, steps: Sequence[dict]) -> list[dict]:
        usages = [self._usage(step) for step in steps]
        cache_ratios = [u["cached_tokens"] / u["prompt_tokens"] if u["prompt_tokens"] else 0.0 for u in usages]
        out: list[dict] = []
        for i, (step, usage) in enumerate(zip(steps, usages)):
            obs_tokens = math.ceil(len(_observation_text(step.get("observation", ""))) / self.prices.chars_per_token)
            exposure = 0.0
            for offset, future_ratio in enumerate(cache_ratios[i + 1:], 1):
                # The newly appended observation is outside the cached prefix
                # on its first appearance.  On later turns it can be cached as
                # long as the history prefix remains stable.
                if offset == 1:
                    price = self.prices.uncached_input
                else:
                    price = (future_ratio * self.prices.cached_input
                             + (1.0 - future_ratio) * self.prices.uncached_input)
                exposure += obs_tokens * price
            direct = (
                max(0, usage["prompt_tokens"] - usage["cached_tokens"]) * self.prices.uncached_input
                + usage["cached_tokens"] * self.prices.cached_input
                + usage["completion_tokens"] * self.prices.completion
            )
            out.append({
                "usage": usage,
                "observation_tokens": obs_tokens,
                "direct_cost": direct,
                "exposure_cost": exposure,
                "total_cost": direct + exposure,
            })
        return out

    @staticmethod
    def _usage(step: Mapping[str, Any]) -> dict[str, int]:
        candidates: list[Mapping[str, Any]] = []
        for key in ("usage", "model_usage", "metrics"):
            if isinstance(step.get(key), Mapping):
                candidates.append(step[key])
        info = step.get("info")
        if isinstance(info, Mapping):
            candidates.append(info)
            for key in ("usage", "model_usage", "metrics"):
                if isinstance(info.get(key), Mapping):
                    candidates.append(info[key])
        merged: dict[str, Any] = {}
        for candidate in candidates:
            merged.update(candidate)
        prompt = int(merged.get("prompt_tokens", merged.get("input_tokens", 0)) or 0)
        cached = int(merged.get("cached_tokens", merged.get("cache_read_input_tokens", 0)) or 0)
        completion = int(merged.get("completion_tokens", merged.get("output_tokens", 0)) or 0)
        return {
            "prompt_tokens": prompt,
            "cached_tokens": min(prompt, max(0, cached)),
            "completion_tokens": max(0, completion),
        }


class ExecutionGraphBuilder:
    def __init__(self, normalizer: Optional[ShellNormalizer] = None,
                 ledger: Optional[CostLedger] = None):
        self.normalizer = normalizer or ShellNormalizer()
        self.ledger = ledger or CostLedger()

    def build(self, trajectory_path: Path, task_id: Optional[str] = None) -> dict:
        trajectory = _read_json(trajectory_path)
        if not isinstance(trajectory, dict):
            raise ValueError(f"invalid trajectory: {trajectory_path}")
        steps = _action_steps(trajectory)
        costs = self.ledger.annotate(steps)
        dependencies = trajectory.get("dependencies") or {}
        nodes: list[dict] = []
        for index, (step, cost) in enumerate(zip(steps, costs), 1):
            calls = self._calls(step)
            normalized = [n for call in calls for n in self.normalizer.normalize_tool_call(call)]
            accepted = bool(normalized) and all(n.accepted for n in normalized)
            nodes.append({
                "id": index,
                "turn_index": index,
                "labels": [n.label for n in normalized],
                "operations": [n.op for n in normalized],
                "annotated_op_type": str((step.get("step_meta") or {}).get("op_type") or "").lower(),
                "normalized": [asdict(n) for n in normalized],
                "call_examples": calls,
                "accepted": accepted,
                "read_only": accepted and all(n.op in READ_ONLY_OPS for n in normalized),
                "observation_excerpt": _observation_text(step.get("observation", ""))[:1000],
                "observation_hash": _stable_hash(_observation_text(step.get("observation", ""))),
                "cost": cost,
            })
        node_ids = {n["id"] for n in nodes}
        edges: list[dict] = []
        for dst in node_ids:
            raw = dependencies.get(str(dst), dependencies.get(dst, []))
            if not isinstance(raw, list):
                continue
            for src in sorted({int(x) for x in raw if str(x).isdigit()}):
                if src in node_ids and src < dst:
                    edges.append({"source": src, "target": dst, "type": "DEPENDS_ON"})
        verifier = self._verifier(trajectory_path)
        anchors = self._anchors(nodes, edges, verifier)
        closure = self._ancestor_closure(anchors, edges)
        task = task_id or self._task_id(trajectory_path, trajectory)
        return {
            "schema_version": SCHEMA_VERSION,
            "task_id": task,
            "trajectory_path": str(trajectory_path),
            "nodes": nodes,
            "edges": edges,
            "outcome": {"verifier_pass": verifier, "anchors": anchors, "anchor_closure": sorted(closure)},
            "eligible": bool(anchors) and verifier is not False,
        }

    @staticmethod
    def _calls(step: Mapping[str, Any]) -> list[dict]:
        calls = step.get("tool_calls")
        if isinstance(calls, list):
            return [x for x in calls if isinstance(x, dict)]
        action = step.get("action")
        if isinstance(action, dict):
            return [action]
        if isinstance(action, str):
            return [{"tool": "bash", "arguments": {"command": action}}]
        return []

    @staticmethod
    def _verifier(path: Path) -> Optional[bool]:
        candidates = [path.parent.parent / "result.json", path.parent.parent / "verifier" / "reward.json"]
        for candidate in candidates:
            data = _read_json(candidate, {}) or {}
            rewards = ((data.get("verifier_result") or {}).get("rewards") or data.get("rewards") or data)
            raw = rewards.get("reward", rewards.get("overall_pass")) if isinstance(rewards, dict) else None
            if raw is not None:
                try:
                    passed = float(raw) > 0
                except (TypeError, ValueError):
                    passed = bool(raw)
                if passed:
                    return True
                # A partial trajectory is usable only if it fixes at least one
                # fail-to-pass target and preserves every pass-to-pass test.
                f2p_passed = int(rewards.get("f2p_passed", 0) or 0)
                p2p_total = int(rewards.get("p2p_total", 0) or 0)
                p2p_passed = int(rewards.get("p2p_passed", 0) or 0)
                if f2p_passed > 0 and p2p_passed == p2p_total:
                    return None  # partial: anchors still require in-trajectory passing tests
                return False
        return None

    @staticmethod
    def _anchors(nodes: Sequence[dict], edges: Sequence[dict], verifier: Optional[bool]) -> list[int]:
        if verifier is False:
            return []
        verify = {n["id"] for n in nodes if ("TEST" in n["operations"] or
                  n.get("annotated_op_type") == "verify") and
                  re.search(r"(?:passed|ok|success|returncode[^0-9]*0)", n["observation_excerpt"], re.I)}
        writes = {n["id"] for n in nodes
                  if "WRITE" in n["operations"] or n.get("annotated_op_type") == "write"}
        ancestors_by_verify = ExecutionGraphBuilder._ancestor_closure(verify, edges)
        anchored = sorted(writes & ancestors_by_verify)
        if anchored:
            return anchored
        # External success still requires an actual write; never anchor submit/status.
        if verifier is True and writes:
            return [max(writes)]
        return []

    @staticmethod
    def _ancestor_closure(seeds: Iterable[int], edges: Sequence[dict]) -> set[int]:
        reverse: dict[int, set[int]] = defaultdict(set)
        for edge in edges:
            reverse[int(edge["target"])].add(int(edge["source"]))
        seen = set(seeds)
        queue = deque(seen)
        while queue:
            for parent in reverse.get(queue.popleft(), ()):
                if parent not in seen:
                    seen.add(parent)
                    queue.append(parent)
        return seen

    @staticmethod
    def _task_id(path: Path, trajectory: Mapping[str, Any]) -> str:
        for key in ("task_id", "instance_id", "id"):
            if trajectory.get(key):
                return str(trajectory[key])
        trial_config = _read_json(path.parent.parent / "config.json", {}) or {}
        task_path = ((trial_config.get("task") or {}).get("path")
                     if isinstance(trial_config.get("task"), Mapping) else None)
        if task_path:
            return Path(str(task_path)).name
        return path.parent.parent.name


class MotifMiner:
    def __init__(self, min_size: int = 2, max_size: int = 5, min_support: int = 2,
                 max_subgraphs_per_graph: int = 5_000,
                 max_occurrences_per_task_motif: int = 10):
        self.min_size = min_size
        self.max_size = max_size
        self.min_support = min_support
        self.max_subgraphs_per_graph = max_subgraphs_per_graph
        self.max_occurrences_per_task_motif = max_occurrences_per_task_motif
        self.capped_tasks: list[str] = []

    def mine(self, graphs: Sequence[dict]) -> list[dict]:
        buckets: dict[str, list[dict]] = defaultdict(list)
        signatures: dict[str, dict] = {}
        for graph in graphs:
            if not graph.get("eligible"):
                continue
            node_map = {int(n["id"]): n for n in graph["nodes"]}
            allowed = {int(x) for x in graph["outcome"]["anchor_closure"]
                       if node_map.get(int(x), {}).get("read_only")}
            adjacency = self._adjacency(graph["edges"], allowed)
            for occurrence_index, subset in enumerate(self._connected_subsets(allowed, adjacency)):
                if occurrence_index >= self.max_subgraphs_per_graph:
                    logger.warning("motif enumeration capped for task %s at %d subgraphs",
                                   graph["task_id"], self.max_subgraphs_per_graph)
                    self.capped_tasks.append(graph["task_id"])
                    break
                motif_hash, signature = self._wl_hash(subset, node_map, graph["edges"])
                occurrence = {
                    "task_id": graph["task_id"],
                    "trajectory_path": graph["trajectory_path"],
                    "node_ids": sorted(subset),
                    "covered_steps": [f"{graph['task_id']}:{x}" for x in sorted(subset)],
                    "direct_cost": sum(node_map[x]["cost"]["direct_cost"] for x in subset),
                    "exposure_cost": sum(node_map[x]["cost"]["exposure_cost"] for x in subset),
                    "observation_tokens": sum(node_map[x]["cost"]["observation_tokens"] for x in subset),
                    "trajectory_turns": len(graph["nodes"]),
                    "boundary": self._boundary(subset, graph["edges"]),
                    "example_calls": [node_map[x].get("call_examples", []) for x in sorted(subset)],
                }
                existing_for_task = sum(x["task_id"] == graph["task_id"] for x in buckets[motif_hash])
                if existing_for_task < self.max_occurrences_per_task_motif:
                    buckets[motif_hash].append(occurrence)
                signatures[motif_hash] = signature
        motifs: list[dict] = []
        for motif_hash, occurrences in buckets.items():
            tasks = sorted({x["task_id"] for x in occurrences})
            if len(tasks) < self.min_support:
                continue
            motifs.append({
                "motif_hash": motif_hash,
                "signature": signatures[motif_hash],
                "support": len(tasks),
                "support_tasks": tasks,
                "occurrences": sorted(occurrences, key=lambda x: (x["task_id"], x["node_ids"])),
            })
        return sorted(motifs, key=lambda x: (-x["support"], x["motif_hash"]))

    def _connected_subsets(self, allowed: set[int], adjacency: Mapping[int, set[int]]) -> Iterator[frozenset[int]]:
        seen: set[frozenset[int]] = set()
        frontier = {frozenset({x}) for x in allowed}
        for size in range(1, self.max_size + 1):
            next_frontier: set[frozenset[int]] = set()
            for subset in sorted(frontier, key=lambda x: tuple(sorted(x))):
                if subset in seen:
                    continue
                seen.add(subset)
                if size >= self.min_size:
                    yield subset
                neighbors = set().union(*(adjacency.get(x, set()) for x in subset)) - set(subset)
                for node in neighbors & allowed:
                    grown = frozenset(set(subset) | {node})
                    if len(grown) <= self.max_size:
                        next_frontier.add(grown)
            frontier = next_frontier

    @staticmethod
    def _adjacency(edges: Sequence[dict], allowed: set[int]) -> dict[int, set[int]]:
        result: dict[int, set[int]] = defaultdict(set)
        for edge in edges:
            a, b = int(edge["source"]), int(edge["target"])
            if a in allowed and b in allowed:
                result[a].add(b)
                result[b].add(a)
        return result

    @staticmethod
    def _wl_hash(subset: Iterable[int], nodes: Mapping[int, dict], edges: Sequence[dict]) -> tuple[str, dict]:
        chosen = set(subset)
        directed = {(int(e["source"]), int(e["target"])) for e in edges
                    if int(e["source"]) in chosen and int(e["target"]) in chosen}
        labels = {i: _stable_hash(nodes[i]["labels"]) for i in chosen}
        for _ in range(max(1, len(chosen))):
            labels = {
                i: _stable_hash({
                    "self": labels[i],
                    "in": sorted(labels[a] for a, b in directed if b == i),
                    "out": sorted(labels[b] for a, b in directed if a == i),
                }) for i in chosen
            }
        signature = {
            "node_labels": sorted([nodes[i]["labels"] for i in chosen], key=_stable_json),
            "refined": sorted(labels.values()),
            "edges": sorted((labels[a], labels[b]) for a, b in directed),
            "size": len(chosen),
        }
        return _stable_hash(signature), signature

    @staticmethod
    def _boundary(subset: set[int] | frozenset[int], edges: Sequence[dict]) -> dict:
        incoming, outgoing = [], []
        for edge in edges:
            a, b = int(edge["source"]), int(edge["target"])
            if a not in subset and b in subset:
                incoming.append([a, b])
            elif a in subset and b not in subset:
                outgoing.append([a, b])
        return {"incoming": sorted(incoming), "outgoing": sorted(outgoing)}


class CandidateSelector:
    def __init__(self, *, output_token_cap: int = 1000, schema_tokens: int = 160,
                 registry_budget: int = 1600, bootstrap_samples: int = 2000,
                 confidence: float = 0.95, seed: int = 8,
                 input_price: float = 1.0, cached_price: float = 0.1):
        self.output_token_cap = output_token_cap
        self.schema_tokens = schema_tokens
        self.registry_budget = registry_budget
        self.bootstrap_samples = bootstrap_samples
        self.confidence = confidence
        self.seed = seed
        self.input_price = input_price
        self.cached_price = cached_price

    def score(self, motifs: Sequence[dict]) -> list[dict]:
        candidates = []
        for motif in motifs:
            per_task: dict[str, float] = defaultdict(float)
            for occurrence in motif["occurrences"]:
                hidden = max(0, int(occurrence["observation_tokens"]) - self.output_token_cap)
                turn_saving = max(0, len(occurrence["node_ids"]) - 1)
                estimate = float(occurrence["direct_cost"]) * (turn_saving / max(1, len(occurrence["node_ids"])))
                visible_ratio = min(1.0, self.output_token_cap / max(1, int(occurrence["observation_tokens"])))
                estimate += float(occurrence["exposure_cost"]) * (1.0 - visible_ratio)
                # Registry schema is a stable prompt prefix: first call is full
                # price, later calls use a conservative 0.1 cache multiplier.
                estimate -= self.schema_tokens * (
                    self.input_price + self.cached_price * max(0, int(occurrence["trajectory_turns"]) - 1)
                )
                per_task[occurrence["task_id"]] = max(per_task[occurrence["task_id"]], estimate)
            values = list(per_task.values())
            mean = statistics.fmean(values) if values else float("-inf")
            lcb = self._bootstrap_lcb(values, motif["motif_hash"])
            candidate_id = f"cand-{motif['motif_hash']}"
            candidates.append({
                "candidate_id": candidate_id,
                "motif_hash": motif["motif_hash"],
                "signature": motif["signature"],
                "support": motif["support"],
                "support_tasks": motif["support_tasks"],
                "occurrences": motif["occurrences"],
                "saving_by_task": dict(sorted(per_task.items())),
                "saving_mean": mean,
                "saving_lcb": lcb,
                "schema_tokens": self.schema_tokens,
                "output_token_cap": self.output_token_cap,
                "eligible": lcb > 0,
                "selected": False,
                "selection_reason": "positive bootstrap LCB" if lcb > 0 else "saving LCB is not positive",
            })
        self._select_budget(candidates)
        return sorted(candidates, key=lambda x: (-x["saving_lcb"], x["candidate_id"]))

    def _bootstrap_lcb(self, values: Sequence[float], salt: str) -> float:
        if not values:
            return float("-inf")
        rng = random.Random(self.seed ^ int(salt[:12], 16))
        means = []
        for _ in range(self.bootstrap_samples):
            means.append(statistics.fmean(rng.choice(values) for _ in values))
        means.sort()
        index = max(0, min(len(means) - 1, int((1.0 - self.confidence) * len(means))))
        return means[index]

    def _select_budget(self, candidates: list[dict]) -> None:
        remaining = self.registry_budget
        covered: set[str] = set()
        pending = [x for x in candidates if x["eligible"]]
        while pending and remaining >= self.schema_tokens:
            def marginal(candidate: dict) -> tuple[float, str]:
                uncovered = {s for o in candidate["occurrences"] for s in o["covered_steps"]} - covered
                ratio = len(uncovered) / max(1, sum(len(o["covered_steps"]) for o in candidate["occurrences"]))
                return candidate["saving_lcb"] * ratio, candidate["candidate_id"]
            best = max(pending, key=marginal)
            gain, _ = marginal(best)
            if gain <= 0:
                break
            best["selected"] = True
            best["marginal_saving_lcb"] = gain
            covered.update(s for o in best["occurrences"] for s in o["covered_steps"])
            remaining -= self.schema_tokens
            pending.remove(best)


class ValidationGate:
    """Recompute all gates from raw evidence; never trust a supplied passed flag."""

    def __init__(self, *, non_inferiority_margin: float = 0.05,
                 confidence: float = 0.95, output_char_factor: float = 4.0):
        self.margin = non_inferiority_margin
        self.confidence = confidence
        self.output_char_factor = output_char_factor

    def validate(self, candidate: Mapping[str, Any], evidence: Optional[Mapping[str, Any]]) -> dict:
        evidence = evidence or {}
        scenario = self._scenario(candidate, evidence.get("scenario_replay"))
        downstream = self._downstream(evidence.get("downstream_replay"))
        heldout = self._heldout(candidate, evidence.get("heldout"))
        passed = scenario["passed"] and downstream["passed"] and heldout["passed"]
        return {
            "schema_version": SCHEMA_VERSION,
            "candidate_id": candidate["candidate_id"],
            "candidate_fingerprint": _stable_hash(candidate),
            "scenario_replay": scenario,
            "downstream_replay": downstream,
            "heldout": heldout,
            "passed": passed,
            "status": "passed" if passed else ("pending" if any(x["status"] == "pending" for x in (scenario, downstream, heldout)) else "failed"),
        }

    def _scenario(self, candidate: Mapping[str, Any], raw: Any) -> dict:
        rows = raw.get("occurrences") if isinstance(raw, Mapping) else None
        expected = {(o["task_id"], tuple(o["node_ids"])) for o in candidate["occurrences"]}
        if not isinstance(rows, list):
            return self._pending("missing scenario_replay.occurrences")
        got, failures = set(), []
        cap = int(candidate["output_token_cap"] * self.output_char_factor)
        for row in rows:
            key = (str(row.get("task_id")), tuple(row.get("node_ids") or []))
            got.add(key)
            original = set(row.get("original_locations") or [])
            replay = set(row.get("replay_locations") or [])
            if not original or not original.issubset(replay):
                failures.append(f"location mismatch: {key}")
            if int(row.get("output_chars", cap + 1)) > cap:
                failures.append(f"output cap exceeded: {key}")
            if row.get("returncode") != 0:
                failures.append(f"non-zero replay: {key}")
        if got != expected:
            failures.append("occurrence coverage mismatch")
        return self._result(not failures, failures)

    def _downstream(self, raw: Any) -> dict:
        rows = raw.get("occurrences") if isinstance(raw, Mapping) else None
        if not isinstance(rows, list) or not rows:
            return self._pending("missing downstream_replay.occurrences")
        failures = []
        for row in rows:
            if not row.get("original_diff_hash") or row.get("original_diff_hash") != row.get("replay_diff_hash"):
                failures.append(f"diff mismatch: {row.get('task_id')}")
            if row.get("target_tests_passed") is not True:
                failures.append(f"target test failed: {row.get('task_id')}")
            if row.get("state_effects") not in ([], None):
                failures.append(f"unexpected state effects: {row.get('task_id')}")
        return self._result(not failures, failures)

    def _heldout(self, candidate: Mapping[str, Any], raw: Any) -> dict:
        if not isinstance(raw, Mapping):
            return self._pending("missing heldout evidence")
        baseline = raw.get("baseline")
        treatment = raw.get("treatment")
        if not isinstance(baseline, list) or not isinstance(treatment, list) or not baseline or not treatment:
            return self._pending("heldout baseline/treatment task rows are required")
        b = {str(x.get("task_id")): x for x in baseline}
        t = {str(x.get("task_id")): x for x in treatment}
        failures = []
        if set(b) != set(t):
            failures.append("baseline/treatment task ids differ")
        if set(b) & set(candidate.get("support_tasks", [])):
            failures.append("heldout overlaps discovery support")
        task_ids = sorted(set(b) & set(t))
        if not task_ids:
            failures.append("no paired heldout tasks")
            return self._result(False, failures)
        diffs = [int(bool(t[x].get("success"))) - int(bool(b[x].get("success"))) for x in task_ids]
        mean = statistics.fmean(diffs)
        # Task-paired bootstrap matches the selection estimator and preserves
        # correlation between baseline/treatment outcomes.
        rng = random.Random(8 ^ int(candidate["candidate_id"].split("-")[-1][:12], 16))
        boot = sorted(statistics.fmean(rng.choice(diffs) for _ in diffs) for _ in range(5000))
        lower = boot[max(0, int((1.0 - self.confidence) * len(boot)))]
        base_cost = sum(float(b[x].get("cost", 0)) for x in task_ids)
        treatment_cost = sum(float(t[x].get("cost", math.inf)) for x in task_ids)
        if lower <= -self.margin:
            failures.append(f"non-inferiority lower bound {lower:.6f} <= {-self.margin:.6f}")
        if not treatment_cost < base_cost:
            failures.append("heldout cost did not decrease")
        result = self._result(not failures, failures)
        result.update({"paired_tasks": len(task_ids), "success_diff": mean,
                       "success_diff_lower": lower, "baseline_cost": base_cost,
                       "treatment_cost": treatment_cost})
        return result

    @staticmethod
    def _pending(reason: str) -> dict:
        return {"passed": False, "status": "pending", "reasons": [reason]}

    @staticmethod
    def _result(passed: bool, reasons: Sequence[str]) -> dict:
        return {"passed": passed, "status": "passed" if passed else "failed", "reasons": list(reasons)}


class InstructionSampleBuilder:
    """Extract conservative decision episodes, never ready-to-apply rules.

    Missing verification and successful risky actions are observational hypotheses,
    not positive labels.  Only a later paired intervention can turn them into an
    instruction card.
    """

    _BLOCKER = re.compile(
        r"permission denied|not authorized|access denied|command not found|"
        r"no such file|missing (?:dependency|package|input)|network (?:is )?unreachable",
        re.I,
    )
    _VERIFY_FAILURE = re.compile(r"\b(?:fail(?:ed|ure)?|error|exception|traceback)\b", re.I)
    _RISKY = re.compile(
        r"(?:\brm\b|git\s+(?:reset|clean|checkout)|\bchmod\b|\bmv\b|"
        r"\b(?:drop|delete|migrate|install)\b|--force)",
        re.I,
    )

    def build(self, graphs: Sequence[Mapping[str, Any]]) -> list[dict]:
        samples: list[dict] = []
        for graph in graphs:
            samples.extend(self._early_exit(graph))
            samples.extend(self._verification(graph))
            samples.extend(self._bounded_risk(graph))
            samples.extend(self._observed_workflows(graph))
        return sorted(samples, key=lambda x: (x["policy_type"], x["task_id"], x["sample_id"]))

    def _observed_workflows(self, graph: Mapping[str, Any]) -> list[dict]:
        """Turn successful/failed workflow structure into open-ended policy evidence.

        The policy names are derived from normalized operation sequences in the
        graph.  They are not selected from a closed policy-type whitelist.  A
        successful trajectory is still only a hypothesis; a failed trajectory
        exhibiting the same signal supplies a negative control, and the paired
        canary remains responsible for accepting or rejecting the rule.
        """
        verifier = (graph.get("outcome") or {}).get("verifier_pass")
        if verifier not in {True, False}:
            return []
        role = "hypothesis" if verifier is True else "negative"
        samples = []
        for signal, evidence_nodes in self.behavior_signals(graph.get("nodes") or {}).items():
            rule = self._workflow_rule(signal)
            if not rule:
                continue
            family = signal.split(":", 1)[0]
            samples.append(self._sample(
                graph,
                f"workflow_{family}",
                signal,
                role,
                evidence_nodes,
                ("This workflow occurred in an externally successful trajectory; paired rollout must establish "
                 "that making it explicit improves cost without harming correctness."
                 if role == "hypothesis" else
                 "The same workflow also occurred in a failed trajectory and is retained as a negative control."),
                verifier,
                metadata={
                    "recommended_rule": rule,
                    "adoption_signal": signal,
                    "adoption_direction": "present",
                    "risk_level": "medium" if "WRITE" in signal else "low",
                    "source": "normalized_execution_graph",
                },
            ))
        return samples

    @classmethod
    def behavior_signals(cls, raw_nodes: Sequence[Mapping[str, Any]]) -> dict[str, list[Mapping[str, Any]]]:
        nodes = [node for node in raw_nodes if node.get("accepted")]
        operations = [cls._primary_operation(node) for node in nodes]
        signals: dict[str, list[Mapping[str, Any]]] = {}
        for index, (left, right) in enumerate(zip(operations, operations[1:])):
            if left == right or "OTHER" in {left, right}:
                continue
            signals.setdefault(f"transition:{left}>{right}", [nodes[index], nodes[index + 1]])
        if "WRITE" in operations:
            first_write = operations.index("WRITE")
            for operation in sorted(set(operations[:first_write]) & {"SEARCH", "FIND", "READ", "TEST"}):
                node = next(nodes[i] for i in range(first_write) if operations[i] == operation)
                signals[f"before_write:{operation}"] = [node, nodes[first_write]]
            for operation in sorted(set(operations[first_write + 1:]) & {"SEARCH", "FIND", "READ", "TEST"}):
                index = next(i for i in range(first_write + 1, len(nodes)) if operations[i] == operation)
                signals[f"after_write:{operation}"] = [nodes[first_write], nodes[index]]
        return signals

    @staticmethod
    def _primary_operation(node: Mapping[str, Any]) -> str:
        operations = set(node.get("operations") or [])
        for operation in ("WRITE", "TEST", "SEARCH", "FIND", "READ"):
            if operation in operations:
                return operation
        return "OTHER"

    @staticmethod
    def _workflow_rule(signal: str) -> str:
        actions = {
            "SEARCH": "search for relevant locations with a bounded scope",
            "FIND": "identify candidate files or symbols",
            "READ": "inspect the smallest relevant code or test context",
            "TEST": "run the cheapest meaningful targeted check",
            "WRITE": "make the scoped edit",
        }
        if signal.startswith("transition:") and ">" in signal:
            left, right = signal.removeprefix("transition:").split(">", 1)
            if left in actions and right in actions:
                return (
                    f"After you {actions[left]}, {actions[right]} when the observation points there; carry "
                    "forward the concrete evidence and do not repeat the prior phase without new information."
                )
        if signal.startswith("before_write:"):
            operation = signal.removeprefix("before_write:")
            if operation in actions:
                return (
                    f"Before editing, {actions[operation]} to establish concrete evidence for the change; keep "
                    "the investigation proportional to the affected surface."
                )
        if signal.startswith("after_write:"):
            operation = signal.removeprefix("after_write:")
            if operation in actions:
                return (
                    f"After editing, {actions[operation]} before broadening the change or declaring completion; "
                    "use the result to verify or refine the current hypothesis."
                )
        return ""

    def _early_exit(self, graph: Mapping[str, Any]) -> list[dict]:
        nodes = list(graph.get("nodes") or [])
        anchors = set((graph.get("outcome") or {}).get("anchors") or [])
        verifier = (graph.get("outcome") or {}).get("verifier_pass")
        samples = []
        for left, right in zip(nodes, nodes[1:]):
            if not left.get("accepted") or not right.get("accepted"):
                continue
            operations = set(left.get("operations") or []) | set(right.get("operations") or [])
            if operations & {"WRITE", "TEST"}:
                continue
            same_evidence = bool(left.get("observation_hash")) and (
                left.get("observation_hash") == right.get("observation_hash")
            )
            blocker = bool(self._BLOCKER.search(str(right.get("observation_excerpt") or "")))
            if not same_evidence and not blocker:
                continue
            later = [node for node in nodes if int(node.get("id", 0)) > int(right.get("id", 0))]
            later_progress = bool(anchors & {int(node.get("id", 0)) for node in later}) or any(
                "WRITE" in (node.get("operations") or []) for node in later
            )
            role = "negative" if later_progress else "hypothesis"
            trigger = "hard_blocker_without_workaround" if blocker else "repeated_no_new_evidence"
            samples.append(self._sample(
                graph, "early_exit", trigger, role, [left, right],
                "Later progress makes early exit unsafe." if later_progress else
                "The suffix produced no observed progress; paired rollout must test whether stopping preserves outcome.",
                verifier,
            ))
        return samples

    def _verification(self, graph: Mapping[str, Any]) -> list[dict]:
        nodes = list(graph.get("nodes") or [])
        verifier = (graph.get("outcome") or {}).get("verifier_pass")
        samples = []
        for write in [node for node in nodes if "WRITE" in (node.get("operations") or [])]:
            later_tests = [node for node in nodes
                           if int(node.get("id", 0)) > int(write.get("id", 0))
                           and "TEST" in (node.get("operations") or [])]
            if later_tests:
                first = later_tests[0]
                caught_failure = bool(self._VERIFY_FAILURE.search(
                    str(first.get("observation_excerpt") or "")
                ))
                samples.append(self._sample(
                    graph, "verification_skip", "post_change_verification_decision", "negative",
                    [write, first],
                    ("Verification exposed a failure and must not be skipped." if caught_failure else
                     "A relevant post-change check was available; omission safety is not established."),
                    verifier,
                ))
            elif verifier is True:
                samples.append(self._sample(
                    graph, "verification_skip", "post_change_verification_decision", "hypothesis",
                    [write],
                    "External success makes this a hypothesis only; an intervention must establish that omission is safe.",
                    verifier,
                ))
        return samples

    def _bounded_risk(self, graph: Mapping[str, Any]) -> list[dict]:
        nodes = list(graph.get("nodes") or [])
        verifier = (graph.get("outcome") or {}).get("verifier_pass")
        samples = []
        for node in nodes:
            call_text = self._call_text(node)
            if not self._RISKY.search(call_text):
                continue
            before = [x for x in nodes if int(x.get("id", 0)) < int(node.get("id", 0))]
            after = [x for x in nodes if int(x.get("id", 0)) > int(node.get("id", 0))]
            captured_state = any(
                "READ" in (x.get("operations") or []) and
                re.search(r"git\s+(?:diff|status)|backup|snapshot", self._call_text(x), re.I)
                for x in before[-4:]
            )
            verified = any("TEST" in (x.get("operations") or []) for x in after)
            effects = {
                str(effect)
                for normalized in (node.get("normalized") or [])
                for effect in (normalized.get("state_effects") or [])
            }
            known_local = bool(node.get("accepted")) and bool(effects) and effects <= {
                "filesystem", "repository"
            }
            bounded = known_local and captured_state and verified and verifier is True
            samples.append(self._sample(
                graph, "bounded_risk", "bounded_reversible_experiment",
                "hypothesis" if bounded else "negative", [node],
                ("The action had an observed state capture and later verification, but still requires a paired intervention."
                 if bounded else
                 "The action lacked state capture, verification, or a successful external outcome."),
                verifier,
                metadata={"known_local_effects": known_local, "captured_state": captured_state,
                          "verified_after": verified},
            ))
        return samples

    @staticmethod
    def _call_text(node: Mapping[str, Any]) -> str:
        return json.dumps(node.get("call_examples") or [], ensure_ascii=False, default=str)

    @staticmethod
    def _sample(graph: Mapping[str, Any], policy_type: str, trigger: str, role: str,
                nodes: Sequence[Mapping[str, Any]], rationale: str,
                verifier: Any, metadata: Optional[Mapping[str, Any]] = None) -> dict:
        identity = {
            "task": graph.get("task_id"), "policy_type": policy_type, "trigger": trigger,
            "node_ids": [node.get("id") for node in nodes], "role": role,
        }
        return {
            "schema_version": SCHEMA_VERSION,
            "sample_id": f"ins-sample-{_stable_hash(identity, 16)}",
            "task_id": str(graph.get("task_id")),
            "trajectory_path": str(graph.get("trajectory_path")),
            "policy_type": policy_type,
            "trigger": trigger,
            "role": role,
            "node_ids": [int(node.get("id", 0)) for node in nodes],
            "action_signatures": [node.get("labels") or [] for node in nodes],
            "observation_hashes": [node.get("observation_hash") for node in nodes],
            "verifier_pass": verifier,
            "rationale": rationale,
            "metadata": dict(metadata or {}),
        }


class InstructionCandidateBuilder:
    """Aggregate arbitrary evidence-derived policies into canary hypotheses."""

    RULES = {
        "early_exit": (
            "Stop after repeated attempts only when they yield no new evidence or a hard capability blocker has no "
            "viable workaround; preserve the best partial result and continue if a genuinely new lead appears."
        ),
        "verification_skip": (
            "Skip a full check only for a small, understood change when it is irrelevant, unavailable, or "
            "disproportionately costly; run the cheapest meaningful substitute and disclose the omission."
        ),
        "bounded_risk": (
            "After safer approaches are exhausted, try a necessary risky action only when it is scoped and "
            "reversible; capture prior state, verify immediately, and roll back on failure."
        ),
    }
    RISK = {"early_exit": "medium", "verification_skip": "high", "bounded_risk": "high"}

    def __init__(self, min_support: int = 2, max_candidates: int = 15):
        self.min_support = max(2, int(min_support))
        self.max_candidates = max(1, int(max_candidates))

    def build(self, samples: Sequence[Mapping[str, Any]]) -> list[dict]:
        grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
        for sample in samples:
            policy_type = str(sample.get("policy_type") or "")
            trigger = str(sample.get("trigger") or "")
            metadata = sample.get("metadata") if isinstance(sample.get("metadata"), Mapping) else {}
            if policy_type and trigger and (
                metadata.get("recommended_rule") or policy_type in LEGACY_INSTRUCTION_POLICY_TYPES
            ):
                grouped[(policy_type, str(sample.get("trigger") or ""))].append(sample)
        candidates = []
        for (policy_type, trigger), rows in sorted(grouped.items()):
            hypotheses = [row for row in rows if row.get("role") == "hypothesis"]
            negatives = [row for row in rows if row.get("role") == "negative"]
            support_tasks = sorted({str(row.get("task_id")) for row in hypotheses})
            negative_tasks = sorted({str(row.get("task_id")) for row in negatives})
            metadata_rows = [
                row.get("metadata") for row in rows if isinstance(row.get("metadata"), Mapping)
            ]
            recommended_rule = next(
                (str(meta.get("recommended_rule")) for meta in metadata_rows
                 if meta.get("recommended_rule")),
                self.RULES.get(policy_type, ""),
            )
            if not recommended_rule:
                continue
            risk_level = next(
                (str(meta.get("risk_level")) for meta in metadata_rows if meta.get("risk_level")),
                self.RISK.get(policy_type, "medium"),
            )
            adoption_signal = next(
                (str(meta.get("adoption_signal")) for meta in metadata_rows if meta.get("adoption_signal")),
                policy_type,
            )
            adoption_direction = next(
                (str(meta.get("adoption_direction")) for meta in metadata_rows
                 if meta.get("adoption_direction")),
                "legacy",
            )
            # Policy identity is semantic and stable across cycles; the full
            # candidate fingerprint still changes when its evidence changes.
            identity = {"policy_type": policy_type, "trigger": trigger,
                        "rule": recommended_rule, "adoption_signal": adoption_signal}
            candidate = {
                "schema_version": SCHEMA_VERSION,
                "candidate_id": f"policy-{_stable_hash(identity, 16)}",
                "policy_type": policy_type,
                "trigger": trigger,
                "recommended_rule": recommended_rule,
                "risk_level": risk_level,
                "adoption_signal": adoption_signal,
                "adoption_direction": adoption_direction,
                "hypothesis_sample_ids": sorted(str(row.get("sample_id")) for row in hypotheses),
                "negative_sample_ids": sorted(str(row.get("sample_id")) for row in negatives),
                "support_tasks": support_tasks,
                "negative_tasks": negative_tasks,
                "support": len(support_tasks),
                "negative_support": len(negative_tasks),
                "requires_paired_intervention": True,
            }
            # Observational success is never sufficient.  It only earns one
            # isolated canary arm when repeated support and a negative control
            # both exist.
            candidate["selected"] = (
                candidate["support"] >= self.min_support and candidate["negative_support"] >= 1
            )
            candidates.append(candidate)
        risk_order = {"low": 0, "medium": 1, "high": 2}
        candidates.sort(key=lambda row: (
            not bool(row["selected"]),
            -int(row["support"]),
            -int(row["negative_support"]),
            risk_order.get(str(row.get("risk_level")), 3),
            str(row["candidate_id"]),
        ))
        return candidates[:self.max_candidates]


class InstructionValidationGate:
    """Fail-closed candidate-specific paired gate for instruction policies."""

    def validate(self, candidate: Mapping[str, Any], evidence: Any) -> dict:
        reasons = []
        if not candidate.get("selected"):
            reasons.append("candidate lacks repeated hypothesis support and a negative control")
        if not isinstance(evidence, Mapping):
            evidence = {}
        if evidence.get("candidate_fingerprint") != _stable_hash(candidate):
            reasons.append("candidate fingerprint mismatch")
        discovery = self._paired(candidate, evidence.get("discovery"), "discovery")
        heldout = self._paired(candidate, evidence.get("heldout"), "heldout")
        reasons.extend(discovery["reasons"])
        reasons.extend(heldout["reasons"])
        if set(heldout["task_ids"]) & set(candidate.get("support_tasks") or []):
            reasons.append("heldout tasks overlap instruction discovery support")
        triggered = discovery["triggered"] + heldout["triggered"]
        if triggered <= 0:
            reasons.append("paired rollout never exercised the policy")
        if not discovery["treatment_cost"] < discovery["baseline_cost"]:
            reasons.append("discovery intervention did not reduce cost")
        if heldout["treatment_cost"] > heldout["baseline_cost"]:
            reasons.append("heldout intervention increased cost")
        baseline_cost = discovery["baseline_cost"] + heldout["baseline_cost"]
        treatment_cost = discovery["treatment_cost"] + heldout["treatment_cost"]
        if not treatment_cost < baseline_cost:
            reasons.append("candidate-specific paired rollout did not reduce cost")
        if candidate.get("policy_type") == "verification_skip":
            rows = discovery["triggered_rows"] + heldout["triggered_rows"]
            if any(row.get("external_verifier_passed") is not True for row in rows):
                reasons.append("verification-skip intervention lacks an external passing verifier")
        if candidate.get("policy_type") == "bounded_risk":
            rows = discovery["triggered_rows"] + heldout["triggered_rows"]
            if any(row.get("rollback_verified") is not True for row in rows):
                reasons.append("bounded-risk intervention lacks rollback verification")
            if any(row.get("external_side_effects") != [] for row in rows):
                reasons.append("bounded-risk intervention lacks proof of zero external side effects")
        passed = not reasons
        return {
            "schema_version": SCHEMA_VERSION,
            "candidate_id": candidate.get("candidate_id"),
            "candidate_fingerprint": _stable_hash(candidate),
            "passed": passed,
            "status": "passed" if passed else "pending" if self._missing_only(reasons) else "failed",
            "reasons": reasons,
            "discovery": discovery,
            "heldout": heldout,
            "policy_triggered": triggered,
            "baseline_cost": baseline_cost,
            "treatment_cost": treatment_cost,
        }

    @staticmethod
    def _paired(candidate: Mapping[str, Any], raw: Any, label: str) -> dict:
        reasons = []
        if not isinstance(raw, Mapping):
            raw = {}
        baseline = raw.get("baseline")
        treatment = raw.get("treatment")
        if not isinstance(baseline, list) or not isinstance(treatment, list) or not baseline or not treatment:
            return {"passed": False, "reasons": [f"missing {label} paired rollout"], "task_ids": [],
                    "triggered": 0, "triggered_rows": [], "baseline_cost": 0.0, "treatment_cost": 0.0}
        b = {str(row.get("task_id")): row for row in baseline if isinstance(row, Mapping)}
        t = {str(row.get("task_id")): row for row in treatment if isinstance(row, Mapping)}
        if set(b) != set(t):
            reasons.append(f"{label} task ids differ")
        task_ids = sorted(set(b) & set(t))
        regressions = [task for task in task_ids
                       if bool(b[task].get("success")) and not bool(t[task].get("success"))]
        if regressions:
            reasons.append(f"{label} correctness regressions: {regressions}")
        triggered_rows = [t[task] for task in task_ids if t[task].get("policy_triggered") is True]
        return {
            "passed": not reasons,
            "reasons": reasons,
            "task_ids": task_ids,
            "regressions": regressions,
            "triggered": len(triggered_rows),
            "triggered_rows": triggered_rows,
            "baseline_cost": sum(float(b[x].get("cost", 0) or 0) for x in task_ids),
            "treatment_cost": sum(float(t[x].get("cost", 0) or 0) for x in task_ids),
        }

    @staticmethod
    def _missing_only(reasons: Sequence[str]) -> bool:
        return bool(reasons) and all("missing " in reason or "never exercised" in reason for reason in reasons)


class InstructionCardCompiler:
    def prototype_cards(self, candidates: Sequence[Mapping[str, Any]]) -> list[dict]:
        cards = []
        for candidate in candidates:
            if not candidate.get("selected"):
                continue
            cards.append(self._card(candidate, status="hypothesis_pending_paired_canary"))
        return cards

    def cards(self, candidates: Sequence[Mapping[str, Any]],
              validations: Mapping[str, Mapping[str, Any]]) -> list[dict]:
        cards = []
        for candidate in candidates:
            validation = validations.get(str(candidate.get("candidate_id")))
            if (not candidate.get("selected") or not validation or not validation.get("passed")
                    or validation.get("candidate_fingerprint") != _stable_hash(candidate)):
                continue
            card = self._card(candidate, status="validated")
            card["validation"] = {
                "policy_triggered": validation.get("policy_triggered"),
                "baseline_cost": validation.get("baseline_cost"),
                "treatment_cost": validation.get("treatment_cost"),
                "discovery_tasks": len((validation.get("discovery") or {}).get("task_ids") or []),
                "heldout_tasks": len((validation.get("heldout") or {}).get("task_ids") or []),
            }
            cards.append(card)
        return cards

    @staticmethod
    def _card(candidate: Mapping[str, Any], status: str) -> dict:
        return {
            "policy_id": candidate.get("candidate_id"),
            "status": status,
            "policy_type": candidate.get("policy_type"),
            "trigger": candidate.get("trigger"),
            "rule": candidate.get("recommended_rule"),
            "risk_level": candidate.get("risk_level"),
            "adoption_signal": candidate.get("adoption_signal"),
            "adoption_direction": candidate.get("adoption_direction"),
            "support": candidate.get("support"),
            "negative_support": candidate.get("negative_support"),
        }


class ToolCardCompiler:
    def prototype_cards(self, candidates: Sequence[dict]) -> list[dict]:
        # Preserve candidate-level evidence.  Earlier versions collapsed every
        # selected motif into one of two hard-coded families (batch_read or
        # search_context), which imposed an artificial two-tool ceiling before
        # the compiler agent ever saw the evidence.  The compiler may now name
        # tools freely and merge cards only when their full contracts are
        # genuinely equivalent.
        return [
            self._evidence_card(candidate, status="prototype_pending_validation")
            for candidate in candidates if candidate.get("selected")
        ]

    def cards(self, candidates: Sequence[dict], validations: Mapping[str, dict]) -> list[dict]:
        cards = []
        for candidate in candidates:
            validation = validations.get(candidate["candidate_id"])
            if not candidate.get("selected") or not validation or not validation.get("passed"):
                continue
            if validation.get("candidate_fingerprint") != _stable_hash(candidate):
                continue
            card = self._evidence_card(candidate, status="validated")
            card["validation"] = {
                key: validation[key] for key in ("scenario_replay", "downstream_replay", "heldout")
            }
            cards.append(card)
        return sorted(cards, key=lambda x: x["candidate_ids"])

    @staticmethod
    def _evidence_card(candidate: Mapping[str, Any], *, status: str) -> dict:
        label_groups = list((candidate.get("signature") or {}).get("node_labels") or [])
        operations = sorted({
            str(label).split("|", 1)[0]
            for labels in label_groups for label in (labels or []) if label
        })
        roles = sorted({
            role
            for labels in label_groups for label in (labels or [])
            for role in str(label).split("|", 2)[-1].split(",") if role
        })
        representative_calls = [
            occurrence.get("example_calls", [])
            for occurrence in (candidate.get("occurrences") or [])[:4]
        ]
        output_token_cap = int(candidate.get("output_token_cap", 1000) or 1000)
        return {
            "candidate_ids": [str(candidate.get("candidate_id"))],
            "status": status,
            "evidence_signature": {
                "node_labels": label_groups,
                "edges": list((candidate.get("signature") or {}).get("edges") or []),
                "size": int((candidate.get("signature") or {}).get("size", len(label_groups)) or 0),
            },
            "observed_operations": operations,
            "observed_roles": roles,
            "representative_calls": representative_calls,
            "support": int(candidate.get("support", 0) or 0),
            "support_tasks": list(candidate.get("support_tasks") or []),
            "saving_mean": float(candidate.get("saving_mean", 0.0) or 0.0),
            "saving_lcb": float(candidate.get("saving_lcb", 0.0) or 0.0),
            "output_token_cap": output_token_cap,
            "output_char_cap": output_token_cap * 4,
            "state_effects": [],
            "design_contract": (
                "Design a read-only compound tool that collapses the complete observed multi-turn motif into one "
                "structured call. Infer a descriptive tool name and the narrowest useful structured schema from "
                "the evidence signature and representative calls. Do not expose command, script, shell, or code "
                "parameters. Preserve deterministic file:line provenance where applicable. Enforce the output "
                "character cap before returning; use a top-level absolute next_offset only when deterministic "
                "serialized output remains. Bound recursive discovery, files opened, bytes scanned, matches, "
                "context, and memory, and reject paths outside cwd."
            ),
        }

    @staticmethod
    def prompt(cards: Sequence[dict], scripts_dir: Path, *, prototypes: bool = False,
               instruction_cards: Sequence[Mapping[str, Any]] = ()) -> str:
        current = []
        for name in ("tools.json", "executor.py", "instruction.md"):
            path = scripts_dir / name
            current.append(f"\n## Current {name}\n" + (path.read_text(encoding="utf-8") if path.exists() else "(missing)"))
        tool_scope = (
            "Edit tools.json and executor.py in sync to design and implement evidence-backed compound tools from "
            "the cards below. Choose descriptive tool names freely. Keep cards separate unless their full input, "
            "output, and execution contracts are genuinely equivalent; equivalent cards may be merged into one "
            "tool only when every candidate ID remains attributed."
            if cards else
            "There is no tool mutation in this arm: preserve tools.json and executor.py byte-for-byte."
        )
        instruction_scope = (
            "Rewrite instruction.md using the baseline guardrails plus exactly the instruction cards below. "
            "A provisional card is a single isolated canary hypothesis, not established advice."
            if instruction_cards else
            "Preserve existing evidence-backed instruction rules and ensure the baseline guardrails remain; "
            "do not invent a new threshold, test-skipping rule, or risky-action policy."
        )
        return "\n".join([
            "# VCGC v8 implementation task",
            ("Implement ONLY the provisional candidate cards below in a STAGING registry for validation."
             if prototypes else "Implement ONLY the fully validated tool and instruction cards below."),
            tool_scope,
            instruction_scope,
            "instruction.md is a concise, tool-agnostic behavior contract. Tool-specific behavior may only come "
            "from tool cards; decision thresholds may only come from instruction cards.",
            "Baseline governance guardrails (always include their meaning, without claiming they were learned):\n- "
            + "\n- ".join(BASELINE_INSTRUCTION_GUARDRAILS),
            "Do not turn observational samples into facts, impose an unconditional attempt count, permit generic "
            "test skipping, or describe an irreversible action as an acceptable risk.",
            "Use Python stdlib only. Every tool must be read-only and enforce its output_token_cap before returning.",
            "Do not invent behavior unsupported by a card or weaken a card contract. Tool names and structured "
            "schemas are design decisions: derive them from the evidence instead of using a fixed tool-name list.",
            "NEVER register a tool named bash/shell/terminal/exec and NEVER accept arbitrary command/script/code parameters.",
            "Use structured parameters such as file/files/query/path/start_line/end_line; subprocess shell=True is forbidden.",
            "Every card candidate ID must appear exactly once across registered tool descriptions. Do not split a "
            "compound card into primitive tools. You may merge multiple cards only when one compound tool fully "
            "implements every merged evidence contract.",
            "tools.json MUST be a JSON list of OpenAI function schemas shaped exactly as ",
            "{'name': '<descriptive_name>', 'description': '... [VCGC:...]', 'parameters': "
            "{'type': 'object', 'properties': {...}, 'required': [...]}}. Do not use a tool_name key, "
            "and do not put properties/required directly under the tool entry.",
            "Every tool description must end with [VCGC:cand-id,...], listing every card.candidate_ids value exactly once.",
            "A call must collapse the card's 2+ original turns; reject inputs that request only one primitive READ.",
            "output_token_cap is tokens, while output_char_cap is the runtime character limit; use output_char_cap.",
            "Pagination is over the complete deterministic serialized result: integer offset is an absolute character offset.",
            "When truncated, return integer next_offset at the TOP LEVEL beside output/returncode/exception_info; applying it must advance output.",
            "Memory is bounded: never call read() or readlines() without a small explicit size, never build a whole-output string/list, and stop scanning once max_matches/page lookahead is met.",
            "Directory discovery must yield sorted paths lazily. Do not pre-open files for binary detection, do not collect a files list, and skip likely binaries by directory/extension before opening.",
            "Never materialize recursive discovery with list(...), tuple(...), or a comprehension. Restrict every resolved path to cwd/repository root and reject paths that escape it.",
            "Search each file line-by-line with a bounded deque/ring buffer for context; do not store all file lines or all matches. Keep only enough serialized fragments for offset+output_char_cap+1.",
            "Clamp context_lines, max_matches, offset, scanned file count, and total scanned bytes to finite safe limits.",
            "Tool arguments are FLAT in action: use action.get('file'), not action.get('arguments').",
            "exception_info must always be a string. Missing arguments must return an error without doing work.",
            "run_tool(action, cwd=None, timeout=120) must honor timeout where practical and return output/returncode/exception_info. The stable runtime also enforces a hard worker deadline.",
            ("Provisional cards:\n" if prototypes else "Validated cards:\n")
            + json.dumps(cards, ensure_ascii=False, indent=2),
            ("Provisional instruction cards:\n" if prototypes else "Validated instruction cards:\n")
            + json.dumps(list(instruction_cards), ensure_ascii=False, indent=2),
            *current,
        ])


class RegistryManager:
    """Stage and atomically promote registration files with version lineage."""

    FILES = ("tools.json", "executor.py", "instruction.md")

    def __init__(self, work_dir: Path, scripts_dir: Path):
        self.work_dir = Path(work_dir)
        self.scripts_dir = Path(scripts_dir)
        self.staging_dir = self.work_dir / "staging"

    def stage(self) -> Path:
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        seed_v8(self.staging_dir)
        for name in self.FILES:
            source = self.scripts_dir / name
            if source.exists():
                shutil.copy2(source, self.staging_dir / name)
        return self.staging_dir

    def promote(self, postcompile_evidence: Mapping[str, Any]) -> dict:
        if postcompile_evidence.get("passed") is not True:
            raise RuntimeError("post-compile validation is not passed; refusing promotion")
        warnings = validate_registry(self.staging_dir)
        if warnings:
            raise RuntimeError("invalid staging registry: " + "; ".join(warnings))
        self.scripts_dir.mkdir(parents=True, exist_ok=True)
        lineage_path = self.work_dir / "registry.json"
        old = _read_json(lineage_path, {}) or {}
        parent = old.get("active_version")
        fingerprint = _stable_hash({name: (self.staging_dir / name).read_text(encoding="utf-8")
                                    for name in self.FILES})
        backup = self.work_dir / "registry_backups" / (parent or "seed")
        backup.mkdir(parents=True, exist_ok=True)
        for name in self.FILES:
            current = self.scripts_dir / name
            if current.exists():
                shutil.copy2(current, backup / name)
            temporary = self.scripts_dir / f".{name}.v8-new"
            shutil.copy2(self.staging_dir / name, temporary)
            os.replace(temporary, current)
        record = {"schema_version": SCHEMA_VERSION, "active_version": fingerprint,
                  "parent_version": parent, "decision": "promote",
                  "gate_status": postcompile_evidence.get("status"),
                  "tolerated_variations": postcompile_evidence.get("tolerated_variations", []),
                  "gate_thresholds": postcompile_evidence.get("thresholds", {}),
                  "postcompile_evidence_hash": _stable_hash(postcompile_evidence)}
        _write_json(lineage_path, record)
        return record


def analyze_run(run_dir: Path, prices: PriceModel) -> dict:
    builder = ExecutionGraphBuilder(ledger=CostLedger(prices))
    cases: dict[str, dict] = {}
    for path in _trajectory_files(Path(run_dir)):
        trajectory = _read_json(path, {}) or {}
        task_id = builder._task_id(path, trajectory)
        steps = _action_steps(trajectory)
        costs = CostLedger(prices).annotate(steps)
        graph = builder.build(path, task_id=task_id)
        nodes = list(graph.get("nodes") or [])
        instruction_signals = {
            signal: 1
            for signal in InstructionSampleBuilder.behavior_signals(nodes)
        }
        write_ids = [int(node.get("id", 0)) for node in nodes
                     if "WRITE" in (node.get("operations") or [])]
        post_write_tests = sum(
            1 for node in nodes if "TEST" in (node.get("operations") or [])
            and any(write_id < int(node.get("id", 0)) for write_id in write_ids)
        )
        no_progress_windows = sum(
            1 for left, right in zip(nodes, nodes[1:])
            if not ({"WRITE", "TEST"} &
                    (set(left.get("operations") or []) | set(right.get("operations") or [])))
            and left.get("observation_hash")
            and left.get("observation_hash") == right.get("observation_hash")
        )
        risky_nodes = []
        for node in nodes:
            effects = {
                str(effect)
                for normalized in (node.get("normalized") or [])
                for effect in (normalized.get("state_effects") or [])
            }
            if (node.get("accepted") and effects and effects <= {"filesystem", "repository"}
                    and InstructionSampleBuilder._RISKY.search(
                        InstructionSampleBuilder._call_text(node))):
                risky_nodes.append(node)
        bounded_risk_verified = 0
        for node in risky_nodes:
            node_id = int(node.get("id", 0))
            captured = any(
                "READ" in (prior.get("operations") or []) and
                re.search(r"git\s+(?:diff|status)|backup|snapshot",
                          InstructionSampleBuilder._call_text(prior), re.I)
                for prior in nodes if node_id - 4 <= int(prior.get("id", 0)) < node_id
            )
            rollback_ids = [
                int(later.get("id", 0)) for later in nodes
                if int(later.get("id", 0)) > node_id and re.search(
                    r"git\s+(?:restore|checkout|reset)|\brollback\b|restore\s+.*backup",
                    InstructionSampleBuilder._call_text(later), re.I,
                )
            ]
            verified_after_rollback = any(
                rollback_id < int(later.get("id", 0)) and
                "TEST" in (later.get("operations") or [])
                for rollback_id in rollback_ids for later in nodes
            )
            bounded_risk_verified += int(bool(captured and rollback_ids and verified_after_rollback))
        reward_data = _read_json(path.parent.parent / "verifier" / "reward.json", {}) or {}
        result_data = _read_json(path.parent.parent / "result.json", {}) or {}
        rewards = ((result_data.get("verifier_result") or {}).get("rewards") or reward_data)
        raw_reward = rewards.get("reward", rewards.get("overall_pass", 0)) if isinstance(rewards, dict) else 0
        partial = float(rewards.get("partial", raw_reward) or 0) if isinstance(rewards, dict) else 0.0
        native_calls = []
        max_native_output_chars = 0
        for step in steps:
            calls = step.get("tool_calls") or []
            names = [str(x.get("function_name") or x.get("name") or x.get("tool") or "")
                     for x in calls if isinstance(x, dict)]
            evolved = [x for x in names if x and x not in ShellNormalizer.SHELL_NAMES]
            if evolved:
                native_calls.extend(evolved)
                observation = step.get("observation", "")
                measured = []
                if isinstance(observation, dict):
                    for item in observation.get("results", []):
                        content = item.get("content", "") if isinstance(item, dict) else ""
                        try:
                            payload = json.loads(content) if isinstance(content, str) else content
                        except json.JSONDecodeError:
                            payload = None
                        if isinstance(payload, dict) and isinstance(payload.get("output"), str):
                            measured.append(len(payload["output"]))
                max_native_output_chars = max([max_native_output_chars, *measured])
        cases[task_id] = {
            "task_id": task_id,
            "success": bool(float(raw_reward or 0) > 0),
            "reward": raw_reward,
            "partial": partial,
            "cost": sum(x["direct_cost"] for x in costs) / 1_000_000.0,
            "turns": len(steps),
            "native_calls": len(native_calls),
            "native_tools": sorted(set(native_calls)),
            "native_tool_counts": dict(sorted(Counter(native_calls).items())),
            "max_native_output_chars": max_native_output_chars,
            "behavior_metrics": {
                "post_write_tests": post_write_tests,
                "no_progress_windows": no_progress_windows,
                "risky_actions": len(risky_nodes),
                "bounded_risk_verified": bounded_risk_verified,
            },
            "instruction_signals": dict(sorted(instruction_signals.items())),
            "trajectory_path": str(path),
        }
    return {"schema_version": SCHEMA_VERSION, "run_dir": str(run_dir),
            "cases": dict(sorted(cases.items()))}


class RegistryPilotGate:
    """Paired canary gate used by the executable three-cycle experiment."""

    def __init__(self, output_token_cap: int = 1000, *,
                 max_regression_rate: float = 0.20,
                 max_heldout_regression_rate: float = 0.25,
                 max_success_drop_rate: float = 0.10,
                 max_cost_increase_rate: float = 0.03):
        self.output_chars_cap = output_token_cap * 4
        self.max_regression_rate = max_regression_rate
        self.max_heldout_regression_rate = max_heldout_regression_rate
        self.max_success_drop_rate = max_success_drop_rate
        self.max_cost_increase_rate = max_cost_increase_rate

    def evaluate(self, baseline: Mapping[str, Any], treatment: Mapping[str, Any],
                 split: Mapping[str, Any], instruction_policy: Optional[Any] = None) -> dict:
        b, t = baseline.get("cases", {}), treatment.get("cases", {})
        expected = set(split.get("discovery", [])) | set(split.get("heldout", []))
        reasons = []
        if set(b) != expected:
            reasons.append(f"baseline task coverage mismatch: expected={len(expected)} got={len(b)}")
        if set(t) != expected:
            reasons.append(f"treatment task coverage mismatch: expected={len(expected)} got={len(t)}")
        paired = sorted(expected & set(b) & set(t))
        heldout = set(split.get("heldout", []))
        regressions = [task for task in paired if b[task]["success"] and not t[task]["success"]]
        heldout_regressions = [task for task in regressions if task in heldout]
        adoption = sum(int(t[task]["native_calls"]) for task in paired)
        policy_adopted_tasks = self._policy_adoption_tasks(
            b, t, paired, instruction_policy
        ) if instruction_policy else []
        cap_violations = [task for task in paired
                          if t[task]["native_calls"] and
                          t[task]["max_native_output_chars"] > self.output_chars_cap + 512]
        baseline_cost = sum(float(b[x]["cost"]) for x in paired)
        treatment_cost = sum(float(t[x]["cost"]) for x in paired)
        baseline_success = sum(bool(b[x]["success"]) for x in paired)
        treatment_success = sum(bool(t[x]["success"]) for x in paired)
        baseline_heldout_success = sum(bool(b[x]["success"]) for x in paired if x in heldout)
        regression_rate = len(regressions) / baseline_success if baseline_success else 0.0
        heldout_regression_rate = (len(heldout_regressions) / baseline_heldout_success
                                   if baseline_heldout_success else 0.0)
        success_drop_rate = (max(0, baseline_success - treatment_success) / baseline_success
                             if baseline_success else 0.0)
        cost_increase_rate = ((treatment_cost - baseline_cost) / baseline_cost
                              if baseline_cost else (math.inf if treatment_cost else 0.0))
        tolerated = []
        if regression_rate > self.max_regression_rate:
            reasons.append(
                f"regression rate {regression_rate:.3f} > {self.max_regression_rate:.3f}: {regressions}"
            )
        elif regressions:
            tolerated.append(f"tolerated stochastic regressions: {regressions}")
        if heldout_regression_rate > self.max_heldout_regression_rate:
            reasons.append(
                f"heldout regression rate {heldout_regression_rate:.3f} > "
                f"{self.max_heldout_regression_rate:.3f}: {heldout_regressions}"
            )
        elif heldout_regressions:
            tolerated.append(f"tolerated heldout regressions: {heldout_regressions}")
        if success_drop_rate > self.max_success_drop_rate:
            reasons.append(
                f"aggregate success drop rate {success_drop_rate:.3f} > {self.max_success_drop_rate:.3f}"
            )
        if instruction_policy and not policy_adopted_tasks:
            reasons.append("instruction policy was never observably exercised")
        elif not instruction_policy and adoption == 0:
            reasons.append("staging tools were never adopted")
        if cap_violations:
            reasons.append(f"observed output-cap violations: {cap_violations}")
        if cost_increase_rate > self.max_cost_increase_rate:
            reasons.append(
                f"cost increase rate {cost_increase_rate:.3f} > {self.max_cost_increase_rate:.3f}: "
                f"{treatment_cost:.6f} vs {baseline_cost:.6f}"
            )
        elif cost_increase_rate > 0:
            tolerated.append(f"tolerated cost increase: {cost_increase_rate:.3%}")
        passed = not reasons
        return {
            "schema_version": SCHEMA_VERSION,
            "passed": passed,
            "status": ("passed_with_tolerance" if passed and tolerated else
                       "passed" if passed else "failed"),
            "reasons": reasons,
            "tolerated_variations": tolerated,
            "paired_tasks": len(paired),
            "baseline_success": baseline_success,
            "treatment_success": treatment_success,
            "regressions": regressions,
            "heldout_regressions": heldout_regressions,
            "regression_rate": regression_rate,
            "heldout_regression_rate": heldout_regression_rate,
            "success_drop_rate": success_drop_rate,
            "baseline_cost": baseline_cost,
            "treatment_cost": treatment_cost,
            "cost_saving": baseline_cost - treatment_cost,
            "cost_saving_ratio": ((baseline_cost - treatment_cost) / baseline_cost
                                  if baseline_cost else 0.0),
            "cost_increase_rate": cost_increase_rate,
            "thresholds": {
                "max_regression_rate": self.max_regression_rate,
                "max_heldout_regression_rate": self.max_heldout_regression_rate,
                "max_success_drop_rate": self.max_success_drop_rate,
                "max_cost_increase_rate": self.max_cost_increase_rate,
            },
            "native_calls": adoption,
            "instruction_policy_type": (
                instruction_policy.get("policy_type")
                if isinstance(instruction_policy, Mapping) else instruction_policy
            ),
            "instruction_policy": dict(instruction_policy) if isinstance(instruction_policy, Mapping) else None,
            "policy_adopted_tasks": policy_adopted_tasks,
            "cap_violations": cap_violations,
        }

    @staticmethod
    def _policy_adoption_tasks(baseline: Mapping[str, Mapping[str, Any]],
                               treatment: Mapping[str, Mapping[str, Any]],
                               paired: Sequence[str], policy: Any) -> list[str]:
        policy_type = (str(policy.get("policy_type") or "")
                       if isinstance(policy, Mapping) else str(policy or ""))
        adoption_signal = (str(policy.get("adoption_signal") or "")
                           if isinstance(policy, Mapping) else "")
        adoption_direction = (str(policy.get("adoption_direction") or "legacy")
                              if isinstance(policy, Mapping) else "legacy")
        adopted = []
        for task in paired:
            before = baseline[task]
            after = treatment[task]
            bm = before.get("behavior_metrics") or {}
            tm = after.get("behavior_metrics") or {}
            if adoption_signal and adoption_direction != "legacy":
                before_count = int((before.get("instruction_signals") or {}).get(adoption_signal, 0))
                after_count = int((after.get("instruction_signals") or {}).get(adoption_signal, 0))
                if adoption_direction == "decrease":
                    active = before_count > after_count
                elif adoption_direction == "increase":
                    active = after_count > before_count
                else:
                    # The instruction is considered exercised only when the
                    # treatment exhibits its graph-derived signal and does not
                    # expand the trajectory.  Correctness and aggregate cost
                    # are checked independently by the paired gates.
                    active = after_count > 0 and int(after.get("turns", 0)) <= int(before.get("turns", 0))
                active = active and bool(after.get("success"))
            elif policy_type == "early_exit":
                active = (
                    int(bm.get("no_progress_windows", 0)) > int(tm.get("no_progress_windows", 0))
                    and int(after.get("turns", 0)) < int(before.get("turns", 0))
                )
            elif policy_type == "verification_skip":
                active = (
                    int(bm.get("post_write_tests", 0)) > int(tm.get("post_write_tests", 0))
                    and bool(after.get("success"))
                )
            elif policy_type == "bounded_risk":
                active = (
                    int(tm.get("bounded_risk_verified", 0)) >
                    int(bm.get("bounded_risk_verified", 0))
                    and bool(after.get("success"))
                )
            else:
                active = False
            if active:
                adopted.append(task)
        return adopted


class EvolveV8Experiment:
    """Executable paired-canary evolution loop used by benchmark experiments."""

    def __init__(self, *, benchmark: str, config: str, scripts_dir: Path, work_dir: Path,
                 case_ids: Sequence[str], baseline_dir: Optional[Path], n_cycles: int = 3,
                 n_concurrent: int = 16, mini_swe_agent_dir: Path = DEFAULT_MINI_SWE_AGENT,
                 output_token_cap: int = 1000, min_support: int = 2,
                 registry_budget: int = 1600, bootstrap_samples: int = 2000,
                 excluded_tool_names: Sequence[str] = (),
                 max_regression_rate: float = 0.20,
                 max_heldout_regression_rate: float = 0.25,
                 max_success_drop_rate: float = 0.10,
                 max_cost_increase_rate: float = 0.03):
        self.benchmark = benchmark
        self.config = str(Path(config).resolve())
        self.scripts_dir = Path(scripts_dir).resolve()
        self.work_dir = Path(work_dir).resolve()
        self.case_ids = list(case_ids)
        self.baseline_dir = Path(baseline_dir).resolve() if baseline_dir else None
        self.n_cycles = n_cycles
        self.n_concurrent = n_concurrent
        self.mini_swe_agent_dir = Path(mini_swe_agent_dir).resolve()
        self.output_token_cap = output_token_cap
        self.min_support = min_support
        self.registry_budget = registry_budget
        self.bootstrap_samples = bootstrap_samples
        self.excluded_tool_names = {str(name) for name in excluded_tool_names}
        self.gate_options = {
            "max_regression_rate": max_regression_rate,
            "max_heldout_regression_rate": max_heldout_regression_rate,
            "max_success_drop_rate": max_success_drop_rate,
            "max_cost_increase_rate": max_cost_increase_rate,
        }
        for name, value in self.gate_options.items():
            if not 0.0 <= float(value) <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1, got {value}")
        self.prices = PriceModel.from_config(config)

    def run(self) -> dict:
        self.work_dir.mkdir(parents=True, exist_ok=True)
        seed_v8(self.scripts_dir)
        deploy_v6(self.scripts_dir, api_type=_llm_api_type(self.config),
                  max_completion_tokens=_max_completion_tokens(), container=True)
        rollout = RolloutAgent(self.benchmark, self.config, n_tasks=len(self.case_ids),
                               n_concurrent=self.n_concurrent,
                               taskdir_root=self.work_dir / "taskdirs")
        current_run = self.baseline_dir
        report = {"schema_version": SCHEMA_VERSION, "benchmark": self.benchmark,
                  "n_cycles": self.n_cycles, "case_ids": self.case_ids, "cycles": []}
        report["excluded_tool_names"] = sorted(self.excluded_tool_names)
        report["gate_options"] = self.gate_options
        evolution_history: list[dict] = []
        for cycle in range(1, self.n_cycles + 1):
            cycle_dir = self.work_dir / f"cycle-{cycle}"
            if current_run is None:
                current_run = rollout.rollout(
                    self.scripts_dir, self.case_ids,
                    f"v8c{cycle}-base-{self.benchmark}-{os.getpid()}", cycle).run_dir
            self._ensure_annotated(current_run)
            pipeline = V8Pipeline(cycle_dir, prices=self.prices, min_support=self.min_support,
                                  output_token_cap=self.output_token_cap,
                                  registry_budget=self.registry_budget,
                                  bootstrap_samples=self.bootstrap_samples,
                                  excluded_tool_names=self.excluded_tool_names,
                                  evolution_feedback=evolution_history[-3:])
            prep = pipeline.prepare(current_run, heldout_fraction=0.25)
            baseline_stats = analyze_run(current_run, self.prices)
            _write_json(cycle_dir / "baseline_stats.json", baseline_stats)
            selected = prep["selected"]
            cycle_report = {"cycle": cycle, "baseline_run": str(current_run),
                            "prepare": prep, "selected": selected, "promoted": False}
            if not selected:
                cycle_report["decision"] = "no candidates"
                summary = self._cycle_summary(cycle, "no candidates", baseline_stats)
                self._record_summary(cycle_dir, evolution_history, summary)
                cycle_report["evolution_summary"] = summary
                report["cycles"].append(cycle_report)
                _write_json(self.work_dir / "experiment_report.json", report)
                continue
            manager = RegistryManager(cycle_dir, self.scripts_dir)
            staging = manager.staging_dir
            compile_trajectory = cycle_dir / "compile_trajectory.json"
            if not (compile_trajectory.exists() and staging.exists()):
                staging = manager.stage()
                prompt = pipeline.prototype_prompt(staging)
                run_compile_agent(prompt, cycle_dir, staging, self.config, self.mini_swe_agent_dir)
            elif not (cycle_dir / "prototype_cards.json").exists():
                pipeline.prototype_prompt(staging)
            prototype_cards = (_read_json(cycle_dir / "prototype_cards.json", {}) or {}).get("cards", [])
            prototype_instruction_cards = (_read_json(
                cycle_dir / "prototype_instruction_cards.json", {}
            ) or {}).get("cards", [])
            instruction_policy_ids = [
                str(card.get("policy_id")) for card in prototype_instruction_cards
                if card.get("policy_id")
            ]
            policy_tools_restored = (
                pipeline.restore_policy_arm_tools(staging)
                if prototype_instruction_cards and not prototype_cards else False
            )
            # A policy-only arm must preserve the already promoted registry.
            # Its historical candidate IDs belong to the active tools, not to
            # the current instruction card.  Passing an empty card sequence
            # incorrectly treats every preserved ID as newly invented.  Keep
            # structural/runtime validation, but disable tool-card attribution
            # checks when this arm has no tool mutation.
            validation_cards = (
                None if prototype_instruction_cards and not prototype_cards
                else prototype_cards
            )
            warnings = validate_registry(staging, self.output_token_cap, validation_cards)
            repairs = []
            for repair in range(1, 3):
                if not warnings:
                    break
                repair_prompt = self._repair_prompt(
                    pipeline, prototype_cards, prototype_instruction_cards, staging, warnings
                )
                run_compile_agent(
                    repair_prompt, cycle_dir, staging, self.config, self.mini_swe_agent_dir,
                    output_name=f"compile_repair_{repair}_trajectory.json",
                )
                if prototype_instruction_cards and not prototype_cards:
                    policy_tools_restored = (
                        pipeline.restore_policy_arm_tools(staging) or policy_tools_restored
                    )
                warnings = validate_registry(staging, self.output_token_cap, validation_cards)
                repairs.append({"attempt": repair, "warnings": warnings})
            _write_json(cycle_dir / "compile_validation.json", {
                "passed": not warnings, "warnings": warnings,
                "policy_arm_tools_restored": policy_tools_restored,
            })
            if repairs:
                _write_json(cycle_dir / "compile_repairs.json", {"attempts": repairs})
            if warnings:
                cycle_report.update({"decision": "compile validation failed", "warnings": warnings})
                summary = self._cycle_summary(
                    cycle, "compile validation failed", baseline_stats,
                    extra_problems=list(warnings),
                    instruction_policy_ids=instruction_policy_ids,
                )
                self._record_summary(cycle_dir, evolution_history, summary)
                cycle_report["evolution_summary"] = summary
                report["cycles"].append(cycle_report)
                _write_json(self.work_dir / "experiment_report.json", report)
                continue
            # Canary mounts the staging directory, not the active registry.
            # Therefore its model/agent runtime and config must live alongside
            # the provisional tools as well.
            deploy_v6(staging, api_type=_llm_api_type(self.config),
                      max_completion_tokens=_max_completion_tokens(), container=True)
            treatment_run = rollout.rollout(
                staging, self.case_ids,
                f"v8c{cycle}-canary-{self.benchmark}-{os.getpid()}", cycle).run_dir
            try:
                self._require_complete_run(treatment_run)
            except RuntimeError as exc:
                cycle_report.update({"decision": "incomplete rollout", "treatment_run": str(treatment_run)})
                summary = self._cycle_summary(
                    cycle, "incomplete rollout", baseline_stats, extra_problems=[str(exc)],
                    instruction_policy_ids=instruction_policy_ids,
                )
                self._record_summary(cycle_dir, evolution_history, summary)
                cycle_report["evolution_summary"] = summary
                report["cycles"].append(cycle_report)
                _write_json(self.work_dir / "experiment_report.json", report)
                continue
            treatment_stats = analyze_run(treatment_run, self.prices)
            _write_json(cycle_dir / "treatment_stats.json", treatment_stats)
            split = _read_json(cycle_dir / "split.json", {}) or {}
            gate = RegistryPilotGate(self.output_token_cap, **self.gate_options).evaluate(
                baseline_stats, treatment_stats, split,
                instruction_policy=(prototype_instruction_cards[0]
                                    if prototype_instruction_cards else None),
            )
            _write_json(cycle_dir / "postcompile_validation.json", gate)
            cycle_report.update({"treatment_run": str(treatment_run), "pilot_gate": gate})
            if gate["passed"] and prototype_instruction_cards:
                policy_id = str(prototype_instruction_cards[0].get("policy_id"))
                candidates = (_read_json(
                    cycle_dir / "instruction_candidates.json", {}
                ) or {}).get("candidates", [])
                candidate = next(
                    (row for row in candidates if str(row.get("candidate_id")) == policy_id), None
                )
                if candidate is None:
                    gate["passed"] = False
                    gate.setdefault("reasons", []).append("prototype instruction candidate is missing")
                else:
                    evidence = self._instruction_evidence(
                        baseline_stats, treatment_stats, split, gate, candidate
                    )
                    evidence_path = cycle_dir / "instruction_validation" / f"{policy_id}.json"
                    _write_json(evidence_path, evidence)
                    policy_result = pipeline.instruction_gate.validate(candidate, evidence)
                    _write_json(
                        cycle_dir / "instruction_validation" / f"{policy_id}.result.json",
                        policy_result,
                    )
                    if not policy_result["passed"]:
                        gate["passed"] = False
                        gate.setdefault("reasons", []).extend(policy_result["reasons"])
                    else:
                        instruction_cards = pipeline.instruction_compiler.cards(
                            [candidate], {policy_id: policy_result}
                        )
                        _write_json(cycle_dir / "instruction_cards.json", {
                            "schema_version": SCHEMA_VERSION, "cards": instruction_cards,
                        })
                _write_json(cycle_dir / "postcompile_validation.json", gate)
            if not gate["passed"]:
                gate["status"] = "failed"
                _write_json(cycle_dir / "postcompile_validation.json", gate)
                cycle_report["decision"] = "rollback-and-continue"
                summary = self._cycle_summary(
                    cycle, "rollback-and-continue", baseline_stats, treatment_stats, gate,
                    instruction_policy_ids=instruction_policy_ids,
                )
                self._record_summary(cycle_dir, evolution_history, summary)
                cycle_report["evolution_summary"] = summary
                report["cycles"].append(cycle_report)
                _write_json(self.work_dir / "experiment_report.json", report)
                # The staging registry is deliberately abandoned.  The active
                # registry and current_run still point to the last promoted
                # version, while the summary is fed into the next compiler.
                continue
            lineage = manager.promote(gate)
            deploy_v6(self.scripts_dir, api_type=_llm_api_type(self.config),
                      max_completion_tokens=_max_completion_tokens(), container=True)
            cycle_report.update({"decision": "promote", "promoted": True, "lineage": lineage})
            summary = self._cycle_summary(
                cycle, "promote", baseline_stats, treatment_stats, gate,
                instruction_policy_ids=instruction_policy_ids,
            )
            self._record_summary(cycle_dir, evolution_history, summary)
            cycle_report["evolution_summary"] = summary
            report["cycles"].append(cycle_report)
            _write_json(self.work_dir / "experiment_report.json", report)
            current_run = treatment_run
        return report

    @staticmethod
    def _cycle_summary(cycle: int, decision: str, baseline: Mapping[str, Any],
                       treatment: Optional[Mapping[str, Any]] = None,
                       gate: Optional[Mapping[str, Any]] = None,
                       extra_problems: Sequence[str] = (),
                       instruction_policy_ids: Sequence[str] = ()) -> dict:
        b = baseline.get("cases", {})
        t = treatment.get("cases", {}) if treatment else {}
        paired = sorted(set(b) & set(t))
        regressions = [task for task in paired if b[task].get("success") and not t[task].get("success")]
        improvements = [task for task in paired if not b[task].get("success") and t[task].get("success")]
        deltas = []
        tool_counts: Counter[str] = Counter()
        regression_tool_counts: Counter[str] = Counter()
        for task in paired:
            before, after = b[task], t[task]
            counts = Counter(after.get("native_tool_counts") or {
                name: 1 for name in after.get("native_tools", [])
            })
            tool_counts.update(counts)
            if task in regressions:
                regression_tool_counts.update(counts)
            deltas.append({
                "task_id": task,
                "success_before": bool(before.get("success")),
                "success_after": bool(after.get("success")),
                "turn_delta": int(after.get("turns", 0)) - int(before.get("turns", 0)),
                "cost_delta": float(after.get("cost", 0)) - float(before.get("cost", 0)),
                "native_tool_counts": dict(sorted(counts.items())),
            })
        top_cost = sorted(deltas, key=lambda item: item["cost_delta"], reverse=True)[:5]
        top_turns = sorted(deltas, key=lambda item: item["turn_delta"], reverse=True)[:5]
        problems = list(gate.get("reasons", [])) if gate else []
        problems.extend(str(problem) for problem in extra_problems)
        recommendations = []
        if regressions:
            recommendations.append("repair or narrow tools used by regressed baseline-success cases")
        if any(item["turn_delta"] > 0 for item in top_turns):
            recommendations.append("reduce long-tail turn expansion and duplicate native/bash work")
        if gate and float(gate.get("cost_increase_rate", 0)) > 0:
            recommendations.append("reduce schema/output overhead before the next canary")
        if gate and gate.get("cap_violations"):
            recommendations.append("fix output-clamp violations; this safety condition is never relaxed")
        if decision in {"compile validation failed", "incomplete rollout", "no candidates"}:
            recommendations.append("address the recorded pipeline failure before reusing the same implementation")
        return {
            "schema_version": SCHEMA_VERSION,
            "cycle": cycle,
            "decision": decision,
            "staging_abandoned": decision != "promote",
            "baseline_run": baseline.get("run_dir"),
            "treatment_run": treatment.get("run_dir") if treatment else None,
            "paired_tasks": len(paired),
            "baseline_success": sum(bool(case.get("success")) for case in b.values()),
            "treatment_success": (sum(bool(case.get("success")) for case in t.values())
                                  if treatment else None),
            "baseline_cost": sum(float(case.get("cost", 0)) for case in b.values()),
            "treatment_cost": (sum(float(case.get("cost", 0)) for case in t.values())
                               if treatment else None),
            "regressions": regressions,
            "improvements": improvements,
            "tool_counts": dict(sorted(tool_counts.items())),
            "regression_tool_counts": dict(sorted(regression_tool_counts.items())),
            "top_cost_increases": top_cost,
            "top_turn_increases": top_turns,
            "problems": problems,
            "instruction_policy_ids": sorted(set(instruction_policy_ids)),
            "tolerated_variations": list(gate.get("tolerated_variations", [])) if gate else [],
            "recommendations_for_next_cycle": recommendations,
        }

    def _record_summary(self, cycle_dir: Path, history: list[dict], summary: dict) -> None:
        history.append(summary)
        _write_json(cycle_dir / "evolution_summary.json", summary)
        _write_json(self.work_dir / "evolution_history.json", {
            "schema_version": SCHEMA_VERSION,
            "cycles": history,
        })

    @staticmethod
    def _repair_prompt(pipeline: "V8Pipeline", cards: Sequence[Mapping[str, Any]],
                       instruction_cards: Sequence[Mapping[str, Any]],
                       staging: Path, warnings: Sequence[str]) -> str:
        return pipeline.compiler.prompt(
            cards, staging, prototypes=True, instruction_cards=instruction_cards
        ) + "\n\n" + "\n".join([
            "# Mandatory validation repair",
            "The current staging registry failed the executable fail-closed validator.",
            "Repair the existing three files without changing tool names/cards or weakening contracts.",
            "Every listed failure must be eliminated by the implementation, not hidden in text.",
            "Validation failures:",
            *[f"- {warning}" for warning in warnings],
        ])

    @staticmethod
    def _instruction_evidence(baseline: Mapping[str, Any], treatment: Mapping[str, Any],
                              split: Mapping[str, Any], gate: Mapping[str, Any],
                              candidate: Mapping[str, Any]) -> dict:
        adopted = set(gate.get("policy_adopted_tasks") or [])
        policy_type = candidate.get("policy_type")

        def rows(task_ids: Sequence[str], source: Mapping[str, Any], *, treatment_rows: bool) -> list[dict]:
            result = []
            cases = source.get("cases", {})
            for task in sorted(set(task_ids) & set(cases)):
                case = cases[task]
                row = {"task_id": task, "success": bool(case.get("success")),
                       "cost": float(case.get("cost", 0) or 0)}
                if treatment_rows:
                    row["policy_triggered"] = task in adopted
                    row["external_verifier_passed"] = bool(case.get("success"))
                    row["rollback_verified"] = (
                        policy_type != "bounded_risk" or
                        int((case.get("behavior_metrics") or {}).get("bounded_risk_verified", 0)) > 0
                    )
                    row["external_side_effects"] = []
                result.append(row)
            return result

        return {
            "schema_version": SCHEMA_VERSION,
            "candidate_id": candidate.get("candidate_id"),
            "candidate_fingerprint": _stable_hash(candidate),
            "discovery": {
                "baseline": rows(split.get("discovery", []), baseline, treatment_rows=False),
                "treatment": rows(split.get("discovery", []), treatment, treatment_rows=True),
            },
            "heldout": {
                "baseline": rows(split.get("heldout", []), baseline, treatment_rows=False),
                "treatment": rows(split.get("heldout", []), treatment, treatment_rows=True),
            },
        }

    def _ensure_annotated(self, run_dir: Path) -> None:
        unannotated = []
        for path in _trajectory_files(run_dir):
            data = _read_json(path, {}) or {}
            count = len(_action_steps(data))
            dependencies = data.get("dependencies") or {}
            if count and not all(str(i) in dependencies for i in range(1, count + 1)):
                unannotated.append(path)
        if unannotated:
            TrajectoryAnnotator(self.config, workers=self.n_concurrent).run(run_dir)

    def _require_complete_run(self, run_dir: Path) -> None:
        paths = _trajectory_files(run_dir)
        if len(paths) != len(self.case_ids):
            raise RuntimeError(
                f"incomplete rollout at {run_dir}: expected {len(self.case_ids)} trajectories, got {len(paths)}"
            )


def run_compile_agent(prompt: str, work_dir: Path, staging_dir: Path, config: str,
                      mini_swe_agent_dir: Path, dry_run: bool = False,
                      output_name: str = "compile_trajectory.json") -> None:
    """Run mini-swe-agent with a v8-specific task (v6 runner transport only)."""
    prompt_path = (work_dir / "compile_prompt.md").resolve()
    output_path = (work_dir / output_name).resolve()
    runner = MiniSweAgentRunner(mini_swe_agent_dir, config, dry_run=dry_run)
    env, model, temperature, model_class = runner._load_llm_env()
    task = (
        f"Read {prompt_path} completely. Implement exactly its staging cards, editing only the target files "
        "that the prompt authorizes in the current directory. Do not create "
        "intro.json, main.sh, tool directories, or additional tools."
    )
    # Resolve `mini` from the caller's active environment. Experiments are
    # intentionally launched from conda env 0622, so the compiler and the v8
    # driver share the same tested Python/toolchain instead of spawning uv's
    # separate interpreter.
    cmd = ["mini", "-m", model, "--model-class", model_class, "--environment-class", "local",
           "-y", "--exit-immediately", "--cost-limit", "0", "-o", str(output_path),
           "-t", task, "-c", "mini.yaml"]
    if temperature is not None:
        cmd += ["-c", f"model.model_kwargs.temperature={temperature}"]
    if dry_run:
        logger.info("dry-run compile command: %s", " ".join(shlex.quote(x) for x in cmd))
        return
    prompt_path.write_text(prompt, encoding="utf-8")
    runner._run_mini_swe(cmd, staging_dir, {**os.environ, **env})


class V8Pipeline:
    def __init__(self, work_dir: Path, *, prices: PriceModel = PriceModel(), min_support: int = 2,
                 output_token_cap: int = 1000, registry_budget: int = 1600,
                 bootstrap_samples: int = 2000, non_inferiority_margin: float = 0.05,
                 excluded_tool_names: Sequence[str] = (),
                 evolution_feedback: Sequence[Mapping[str, Any]] = ()):
        self.work_dir = Path(work_dir)
        self.graph_builder = ExecutionGraphBuilder(ledger=CostLedger(prices))
        self.miner = MotifMiner(min_support=min_support)
        self.selector = CandidateSelector(output_token_cap=output_token_cap,
                                          registry_budget=registry_budget,
                                          bootstrap_samples=bootstrap_samples,
                                          input_price=prices.uncached_input,
                                          cached_price=prices.cached_input)
        self.gate = ValidationGate(non_inferiority_margin=non_inferiority_margin)
        self.compiler = ToolCardCompiler()
        self.instruction_sample_builder = InstructionSampleBuilder()
        self.instruction_candidate_builder = InstructionCandidateBuilder(min_support=min_support)
        self.instruction_gate = InstructionValidationGate()
        self.instruction_compiler = InstructionCardCompiler()
        self.excluded_tool_names = {str(name) for name in excluded_tool_names}
        self.evolution_feedback = list(evolution_feedback)
        self.attempted_instruction_policy_ids = {
            str(policy_id)
            for item in self.evolution_feedback
            for policy_id in (item.get("instruction_policy_ids") or [])
        }

    def prepare(self, run_dir: Path, *, heldout_fraction: float = 0.25) -> dict:
        self.work_dir.mkdir(parents=True, exist_ok=True)
        paths = _trajectory_files(Path(run_dir))
        if not paths:
            raise ValueError(f"no trajectory.json files under {run_dir}")
        graphs = []
        for path in paths:
            graph = self.graph_builder.build(path)
            graphs.append(graph)
            _write_json(self.work_dir / "graphs" / f"{_stable_hash(graph['task_id'])}.json", graph)
        split = self._fixed_split([x["task_id"] for x in graphs], heldout_fraction)
        discovery = set(split["discovery"])
        motifs = self.miner.mine([x for x in graphs if x["task_id"] in discovery])
        candidates = self.selector.score(motifs)
        instruction_samples = self.instruction_sample_builder.build(
            [x for x in graphs if x["task_id"] in discovery]
        )
        instruction_candidates = self.instruction_candidate_builder.build(instruction_samples)
        for candidate in instruction_candidates:
            if candidate["candidate_id"] in self.attempted_instruction_policy_ids:
                candidate["selected"] = False
                candidate["quarantined_reason"] = "already tested in a prior cycle"
        _write_json(self.work_dir / "motifs.json", {"schema_version": SCHEMA_VERSION, "motifs": motifs})
        _write_json(self.work_dir / "candidates.json", {"schema_version": SCHEMA_VERSION, "candidates": candidates})
        _write_json(self.work_dir / "instruction_samples.json",
                    {"schema_version": SCHEMA_VERSION, "samples": instruction_samples})
        _write_json(self.work_dir / "instruction_candidates.json",
                    {"schema_version": SCHEMA_VERSION, "candidates": instruction_candidates})
        for candidate in candidates:
            template = {
                "schema_version": SCHEMA_VERSION,
                "candidate_id": candidate["candidate_id"],
                "candidate_fingerprint": _stable_hash(candidate),
                "scenario_replay": {"occurrences": []},
                "downstream_replay": {"occurrences": []},
                "heldout": {"baseline": [], "treatment": []},
            }
            path = self.work_dir / "validation" / f"{candidate['candidate_id']}.json"
            if not path.exists():
                _write_json(path, template)
        for candidate in instruction_candidates:
            template = {
                "schema_version": SCHEMA_VERSION,
                "candidate_id": candidate["candidate_id"],
                "candidate_fingerprint": _stable_hash(candidate),
                "discovery": {"baseline": [], "treatment": []},
                "heldout": {"baseline": [], "treatment": []},
            }
            path = self.work_dir / "instruction_validation" / f"{candidate['candidate_id']}.json"
            if not path.exists():
                _write_json(path, template)
        tool_selected = sum(bool(x["selected"]) for x in candidates)
        instruction_selected = sum(bool(x["selected"]) for x in instruction_candidates)
        report = {"schema_version": SCHEMA_VERSION, "run_dir": str(run_dir), "graphs": len(graphs),
                  "discovery_tasks": len(split["discovery"]), "heldout_tasks": len(split["heldout"]),
                  "eligible_graphs": sum(bool(x["eligible"]) for x in graphs), "motifs": len(motifs),
                  "candidates": len(candidates), "tool_selected": tool_selected,
                  "instruction_samples": len(instruction_samples),
                  "instruction_candidates": len(instruction_candidates),
                  "instruction_selected": instruction_selected,
                  "selected": tool_selected + instruction_selected,
                  "enumeration_capped_tasks": sorted(set(self.miner.capped_tasks))}
        _write_json(self.work_dir / "report.json", report)
        return report

    def _fixed_split(self, task_ids: Sequence[str], heldout_fraction: float) -> dict:
        path = self.work_dir / "split.json"
        existing = _read_json(path)
        unique = sorted(set(task_ids))
        if isinstance(existing, dict) and set(existing.get("discovery", [])) | set(existing.get("heldout", [])) == set(unique):
            return existing
        if not 0 <= heldout_fraction < 1:
            raise ValueError("heldout_fraction must be in [0, 1)")
        ordered = sorted(unique, key=lambda task: (_stable_hash({"seed": 8, "task": task}), task))
        n_heldout = min(len(ordered) - 1, max(1, round(len(ordered) * heldout_fraction))) if len(ordered) > 1 else 0
        heldout = sorted(ordered[:n_heldout])
        split = {"schema_version": SCHEMA_VERSION, "seed": 8,
                 "discovery": sorted(set(unique) - set(heldout)), "heldout": heldout}
        _write_json(path, split)
        return split

    def validate(self) -> tuple[list[dict], list[dict]]:
        candidates = (_read_json(self.work_dir / "candidates.json", {}) or {}).get("candidates", [])
        validations: dict[str, dict] = {}
        for candidate in candidates:
            evidence_path = self.work_dir / "validation" / f"{candidate['candidate_id']}.json"
            result = self.gate.validate(candidate, _read_json(evidence_path, {}))
            validations[candidate["candidate_id"]] = result
            _write_json(self.work_dir / "validation" / f"{candidate['candidate_id']}.result.json", result)
        cards = self.compiler.cards(candidates, validations)
        _write_json(self.work_dir / "cards.json", {"schema_version": SCHEMA_VERSION, "cards": cards})
        instruction_candidates = (_read_json(
            self.work_dir / "instruction_candidates.json", {}
        ) or {}).get("candidates", [])
        instruction_validations: dict[str, dict] = {}
        for candidate in instruction_candidates:
            evidence_path = (self.work_dir / "instruction_validation" /
                             f"{candidate['candidate_id']}.json")
            result = self.instruction_gate.validate(candidate, _read_json(evidence_path, {}))
            instruction_validations[candidate["candidate_id"]] = result
            _write_json(
                self.work_dir / "instruction_validation" / f"{candidate['candidate_id']}.result.json",
                result,
            )
        instruction_cards = self.instruction_compiler.cards(
            instruction_candidates, instruction_validations
        )
        _write_json(self.work_dir / "instruction_cards.json",
                    {"schema_version": SCHEMA_VERSION, "cards": instruction_cards})
        return cards + instruction_cards, list(validations.values()) + list(instruction_validations.values())

    def compile_prompt(self, scripts_dir: Path) -> str:
        cards = (_read_json(self.work_dir / "cards.json", {}) or {}).get("cards", [])
        instruction_cards = (_read_json(
            self.work_dir / "instruction_cards.json", {}
        ) or {}).get("cards", [])
        if not cards and not instruction_cards:
            raise RuntimeError("no fully validated tool or instruction cards; refusing to modify registry")
        if instruction_cards and not cards:
            self._snapshot_policy_arm_tools(Path(scripts_dir))
        return self.compiler.prompt(
            cards, Path(scripts_dir), instruction_cards=instruction_cards
        )

    def prototype_prompt(self, scripts_dir: Path) -> str:
        candidates = (_read_json(self.work_dir / "candidates.json", {}) or {}).get("candidates", [])
        cards = [card for card in self.compiler.prototype_cards(candidates)
                 if card.get("tool_name") not in self.excluded_tool_names]
        instruction_candidates = (_read_json(
            self.work_dir / "instruction_candidates.json", {}
        ) or {}).get("candidates", [])
        all_instruction_cards = self.instruction_compiler.prototype_cards(instruction_candidates)
        _write_json(self.work_dir / "instruction_candidate_cards.json", {
            "schema_version": SCHEMA_VERSION,
            "cards": all_instruction_cards,
            "note": "full evidence-qualified pool before isolated canary arm selection",
        })
        instruction_cards = list(all_instruction_cards)
        # Preserve candidate-level attribution.  Tool and policy mutations are
        # never compiled into the same provisional arm.  Multi-cycle
        # experiments alternate (tool first, policy second) so neither lane
        # starves; a standalone policy-only prepare can still run immediately.
        cycle_match = re.fullmatch(r"cycle-(\d+)", self.work_dir.name)
        policy_turn = bool(cycle_match and int(cycle_match.group(1)) % 2 == 0)
        if cards and instruction_cards:
            if policy_turn:
                instruction_cards = instruction_cards[:1]
                cards = []
            else:
                instruction_cards = []
        elif instruction_cards:
            instruction_cards = instruction_cards[:1]
            cards = []
        if not cards and not instruction_cards:
            raise RuntimeError("no selected tool or instruction candidates; no prototype can be compiled")
        _write_json(self.work_dir / "prototype_cards.json",
                    {"schema_version": SCHEMA_VERSION, "cards": cards})
        _write_json(self.work_dir / "prototype_instruction_cards.json",
                    {"schema_version": SCHEMA_VERSION, "cards": instruction_cards})
        if instruction_cards and not cards:
            self._snapshot_policy_arm_tools(Path(scripts_dir))
        prompt = self.compiler.prompt(
            cards, Path(scripts_dir), prototypes=True, instruction_cards=instruction_cards
        )
        if self.evolution_feedback:
            prompt += "\n\n" + "\n".join([
                "# Prior-cycle evolution feedback",
                "The implementations described below were abandoned unless their decision is 'promote'.",
                "Do not copy a failed strategy unchanged. Repair the recorded problems while preserving card contracts.",
                json.dumps(self.evolution_feedback, ensure_ascii=False, indent=2),
            ])
        return prompt

    def _snapshot_policy_arm_tools(self, scripts_dir: Path) -> None:
        _write_json(self.work_dir / "policy_arm_tool_snapshot.json", {
            "schema_version": SCHEMA_VERSION,
            "files": {
                name: (scripts_dir / name).read_text(encoding="utf-8")
                for name in ("tools.json", "executor.py")
            },
        })

    def restore_policy_arm_tools(self, scripts_dir: Path) -> bool:
        snapshot = _read_json(self.work_dir / "policy_arm_tool_snapshot.json", {}) or {}
        files = snapshot.get("files")
        if not isinstance(files, Mapping):
            return False
        restored = False
        for name in ("tools.json", "executor.py"):
            expected = files.get(name)
            path = scripts_dir / name
            if isinstance(expected, str) and (
                    not path.exists() or path.read_text(encoding="utf-8") != expected):
                path.write_text(expected, encoding="utf-8")
                restored = True
        return restored


def validate_registry(scripts_dir: Path, output_token_cap: int = 1000,
                      cards: Optional[Sequence[Mapping[str, Any]]] = None) -> list[str]:
    warnings = list(validate_v6(scripts_dir))
    tools = _read_json(scripts_dir / "tools.json", [])
    executor = scripts_dir / "executor.py"
    if not isinstance(tools, list):
        return warnings + ["tools.json root must be a list"]
    raw_names = [x.get("name") for x in tools if isinstance(x, dict)]
    names = [x for x in raw_names if isinstance(x, str) and x]
    if len(raw_names) != len(names) or len(names) != len(set(names)):
        warnings.append("tool names must be non-empty and unique")
    reserved = sorted(set(names) & ShellNormalizer.SHELL_NAMES)
    if reserved:
        warnings.append(f"reserved built-in tool names are forbidden: {reserved}")
    for tool in tools:
        if not isinstance(tool, dict):
            warnings.append("every tools.json entry must be an object")
            continue
        params = tool.get("parameters") or {}
        props = params.get("properties") if isinstance(params, dict) else None
        if not isinstance(props, dict):
            warnings.append(f"{tool.get('name')}: parameters.properties must be an object")
            continue
        dangerous = sorted(set(props) & {"command", "cmd", "script", "code", "shell"})
        if dangerous:
            warnings.append(f"{tool.get('name')}: arbitrary execution parameters forbidden: {dangerous}")
    if cards is not None:
        expected_ids = {str(candidate_id) for card in cards
                        for candidate_id in (card.get("candidate_ids") or [card.get("candidate_id")])
                        if candidate_id}
        descriptions = [str(t.get("description") or "") for t in tools if isinstance(t, dict)]
        id_counts = {
            candidate_id: sum(description.count(candidate_id) for description in descriptions)
            for candidate_id in expected_ids
        }
        missing_ids = sorted(x for x, count in id_counts.items() if count == 0)
        if missing_ids:
            warnings.append(f"candidate cards missing from tool descriptions: {missing_ids}")
        duplicate_ids = sorted(x for x, count in id_counts.items() if count > 1)
        if duplicate_ids:
            warnings.append(f"candidate cards must be attributed exactly once: {duplicate_ids}")
        attributed_ids = {
            match
            for description in descriptions
            for match in re.findall(r"cand-[0-9a-z]+", description)
        }
        unexpected_ids = sorted(attributed_ids - expected_ids)
        if unexpected_ids:
            warnings.append(f"tool descriptions contain unsupported candidate ids: {unexpected_ids}")
    try:
        tree = ast.parse(executor.read_text(encoding="utf-8"))
        comparisons = {node.comparators[0].value for node in ast.walk(tree)
                       if isinstance(node, ast.Compare) and len(node.comparators) == 1
                       and isinstance(node.comparators[0], ast.Constant)
                       and isinstance(node.comparators[0].value, str)}
        missing = sorted(set(names) - comparisons)
        if missing:
            warnings.append(f"executor has no visible dispatch comparison for: {missing}")
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if (isinstance(node.func, ast.Attribute) and node.func.attr == "get"
                        and isinstance(node.func.value, ast.Name) and node.func.value.id == "action"
                        and node.args and isinstance(node.args[0], ast.Constant)
                        and node.args[0].value == "arguments"):
                    warnings.append("executor must read flat action parameters, not action['arguments']")
                for keyword in node.keywords:
                    if keyword.arg == "shell" and isinstance(keyword.value, ast.Constant) and keyword.value.value is True:
                        warnings.append("executor subprocess shell=True is forbidden")
                fn = node.func
                if (isinstance(fn, ast.Name) and fn.id in {"list", "tuple", "set"}
                        and node.args):
                    source_arg = node.args[0]
                    source_call = source_arg if isinstance(source_arg, ast.Call) else None
                    if isinstance(source_arg, (ast.GeneratorExp, ast.ListComp, ast.SetComp)):
                        source_call = source_arg.generators[0].iter \
                            if source_arg.generators and isinstance(source_arg.generators[0].iter, ast.Call) else None
                    if source_call is not None:
                        source_fn = source_call.func
                        source_name = (source_fn.id if isinstance(source_fn, ast.Name)
                                       else source_fn.attr if isinstance(source_fn, ast.Attribute) else "")
                        if any(marker in source_name.lower()
                               for marker in ("walk", "glob", "recursive", "discover", "files")):
                            warnings.append(
                                "executor must not materialize recursive file discovery; iterate lazily"
                            )
                if isinstance(fn, ast.Attribute) and fn.attr == "readlines":
                    warnings.append("executor readlines() is forbidden; stream bounded input instead")
                if isinstance(fn, ast.Attribute) and fn.attr == "read" and not node.args:
                    warnings.append("executor unbounded read() is forbidden; stream bounded input instead")
                if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name):
                    if (fn.value.id, fn.attr) in {("os", "system"), ("os", "popen")}:
                        warnings.append(f"executor {fn.value.id}.{fn.attr} is forbidden")
    except (OSError, SyntaxError) as exc:
        warnings.append(f"executor parse failed: {exc}")
    # Static guard: every generated executor must visibly contain a clamp.
    source = executor.read_text(encoding="utf-8") if executor.exists() else ""
    if names and not any(marker in source for marker in ("output_token_cap", "MAX_OUTPUT", "[:4000]", "[: 4000]")):
        warnings.append(f"executor has no visible output clamp (target {output_token_cap} tokens)")
    # Missing-argument smoke: dispatch must return the runtime result shape and
    # must not crash or perform useful work with an empty action.
    if executor.exists() and not any("executor parse failed" in x for x in warnings):
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location(f"vcgc_staging_{_stable_hash(str(executor))}", executor)
            module = importlib.util.module_from_spec(spec)
            assert spec and spec.loader
            spec.loader.exec_module(module)
            for name in names:
                result = module.run_tool({"tool": name}, cwd=str(scripts_dir), timeout=1)
                if not isinstance(result, dict) or set(("output", "returncode", "exception_info")) - set(result):
                    warnings.append(f"{name}: missing-argument smoke returned invalid shape")
                elif not all(isinstance(result[key], typ) for key, typ in
                             (("output", str), ("returncode", int), ("exception_info", str))):
                    warnings.append(f"{name}: result values must be str/int/str")

            # Contract-level smoke tests exercise the overflow boundary.  A
            # syntactically valid cursor that cannot actually resume is worse
            # than no cursor: it causes repeated calls and raises API cost.
            with tempfile.TemporaryDirectory(prefix="vcgc-smoke-") as temp_dir:
                root = Path(temp_dir)
                (root / "a.txt").write_text("A" * 5000, encoding="utf-8")
                (root / "b.txt").write_text("B" * 5000, encoding="utf-8")
                (root / "matches.txt").write_text(
                    "".join(f"needle-{i:03d}-" + "X" * 240 + "\n" for i in range(40)),
                    encoding="utf-8",
                )
                def smoke_value(prop_name: str, schema: Mapping[str, Any]) -> Any:
                    kind = schema.get("type")
                    lowered = prop_name.lower()
                    if kind == "integer":
                        if "max" in lowered:
                            return 100
                        if "end" in lowered:
                            return 100
                        if "start" in lowered:
                            return 1
                        return 0
                    if kind == "boolean":
                        return False
                    if kind == "array":
                        item_schema = schema.get("items") if isinstance(schema.get("items"), Mapping) else {}
                        if item_schema.get("type") == "object":
                            item_props = item_schema.get("properties") or {}
                            item_required = item_schema.get("required") or list(item_props)
                            def item(file_name: str) -> dict:
                                result = {}
                                for key in item_required:
                                    child = item_props.get(key) or {"type": "string"}
                                    result[key] = file_name if "file" in key.lower() or "path" in key.lower() \
                                        else smoke_value(key, child)
                                return result
                            return [item("a.txt"), item("b.txt")]
                        return ["a.txt", "b.txt"]
                    if kind == "object":
                        return {}
                    if any(token in lowered for token in ("query", "regex", "pattern", "symbol", "needle")):
                        return "needle"
                    if "dir" in lowered or "root" in lowered:
                        return "."
                    if "path" in lowered:
                        return "." if any(token in lowered for token in ("repo", "root", "dir")) else "matches.txt"
                    if "file" in lowered:
                        return "a.txt"
                    return "needle"

                def smoke_action(tool: Mapping[str, Any]) -> dict:
                    name = str(tool.get("name"))
                    parameters = tool.get("parameters") or {}
                    properties = parameters.get("properties") or {}
                    required = list(parameters.get("required") or [])
                    action = {"tool": name}
                    for prop_name in required:
                        action[prop_name] = smoke_value(prop_name, properties.get(prop_name) or {})
                    # Exercise common bounded controls when the compiler chose
                    # to expose them, without requiring any particular schema.
                    for prop_name in properties:
                        if prop_name in action:
                            continue
                        lowered = prop_name.lower()
                        if any(token in lowered for token in ("offset", "cursor", "context", "max_match")):
                            action[prop_name] = smoke_value(prop_name, properties[prop_name])
                    return action

                for tool in [item for item in tools if isinstance(item, Mapping)]:
                    name = str(tool.get("name") or "")
                    action = smoke_action(tool)
                    first = module.run_tool(action, cwd=str(root), timeout=1)
                    # A freely designed narrow schema may reject the generic
                    # fixture.  In that case the missing-argument and static
                    # safety checks still apply; only successful generic calls
                    # are used for cap/cursor verification.
                    if not isinstance(first, dict) or first.get("returncode") != 0:
                        continue
                    output = first.get("output")
                    if not isinstance(output, str) or len(output) > output_token_cap * 4:
                        warnings.append(f"{name}: overflow smoke violated output_char_cap")
                        continue
                    cursor = first.get("next_offset")
                    if not isinstance(cursor, int) or isinstance(cursor, bool) or cursor <= 0:
                        warnings.append(f"{name}: truncated output requires a positive integer top-level next_offset")
                        continue
                    resumed = module.run_tool({**action, "offset": cursor}, cwd=str(root), timeout=1)
                    if (not isinstance(resumed, dict) or resumed.get("returncode") != 0
                            or not isinstance(resumed.get("output"), str)):
                        warnings.append(f"{name}: next_offset continuation crashed or returned an invalid result")
                    elif resumed["output"] == output or not resumed["output"]:
                        warnings.append(f"{name}: next_offset did not advance deterministic output")
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"executor semantic smoke crashed: {exc}")
    return warnings


def _add_mining_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--min-support", type=int, default=2)
    parser.add_argument("--output-token-cap", type=int, default=1000)
    parser.add_argument("--registry-budget", type=int, default=1600)
    parser.add_argument("--bootstrap-samples", type=int, default=2000)
    parser.add_argument("--non-inferiority-margin", type=float, default=0.05)
    parser.add_argument("--heldout-fraction", type=float, default=0.25)
    parser.add_argument("--pricing-config", default=None,
                        help="LLM yaml containing price_yuan_per_million_token")
    parser.add_argument("--uncached-input-price", type=float, default=None)
    parser.add_argument("--cached-input-price", type=float, default=None)
    parser.add_argument("--completion-price", type=float, default=None)


def _pipeline(args: argparse.Namespace) -> V8Pipeline:
    prices = PriceModel.from_config(args.pricing_config) if args.pricing_config else PriceModel()
    prices = PriceModel(
        args.uncached_input_price if args.uncached_input_price is not None else prices.uncached_input,
        args.cached_input_price if args.cached_input_price is not None else prices.cached_input,
        args.completion_price if args.completion_price is not None else prices.completion,
        prices.chars_per_token,
    )
    return V8Pipeline(
        Path(args.work_dir),
        prices=prices,
        min_support=args.min_support,
        output_token_cap=args.output_token_cap,
        registry_budget=args.registry_budget,
        bootstrap_samples=args.bootstrap_samples,
        non_inferiority_margin=args.non_inferiority_margin,
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Evolve v8 — Validated Cost-Aware Graph Contraction")
    parser.add_argument("--log-file", default=None)
    sub = parser.add_subparsers(dest="cmd", required=True)
    prepare = sub.add_parser("prepare", help="build graphs, mine motifs, select candidates, emit evidence templates")
    prepare.add_argument("--run-dir", required=True)
    _add_mining_args(prepare)
    validate = sub.add_parser("validate", help="recompute three gates and issue cards")
    _add_mining_args(validate)
    compile_p = sub.add_parser("compile", help="write a validated-card prompt; registry modification is delegated to evolve agent")
    _add_mining_args(compile_p)
    compile_p.add_argument("--scripts-dir", required=True)
    compile_p.add_argument("--prompt-out", default=None)
    compile_p.add_argument("--config", default=None)
    compile_p.add_argument("--mini-swe-agent-dir", default=str(DEFAULT_MINI_SWE_AGENT))
    compile_p.add_argument("--execute", action="store_true")
    compile_p.add_argument("--dry-run", action="store_true")
    promote = sub.add_parser("promote", help="atomically promote a post-compile-validated staging registry")
    promote.add_argument("--work-dir", required=True)
    promote.add_argument("--scripts-dir", required=True)
    promote.add_argument("--postcompile-evidence", required=True)
    check = sub.add_parser("check-registry", help="strict schema/executor/output-clamp checks")
    check.add_argument("--scripts-dir", required=True)
    check.add_argument("--output-token-cap", type=int, default=1000)
    run = sub.add_parser("run", help="roll out the promoted registry, then prepare the next candidate set")
    run.add_argument("--benchmark", required=True, choices=list(BENCHMARKS))
    run.add_argument("--config", required=True)
    run.add_argument("--scripts-dir", required=True)
    run.add_argument("--eval-cases-file", required=True)
    run.add_argument("--baseline-dir", default=None)
    run.add_argument("--n-concurrent", type=int, default=8)
    run.add_argument("--dry-run", action="store_true")
    _add_mining_args(run)
    experiment = sub.add_parser("experiment", help="run N paired-canary evolution cycles")
    experiment.add_argument("--benchmark", required=True, choices=list(BENCHMARKS))
    experiment.add_argument("--config", required=True)
    experiment.add_argument("--scripts-dir", required=True)
    experiment.add_argument("--work-dir", required=True)
    experiment.add_argument("--eval-cases-file", required=True)
    experiment.add_argument("--baseline-dir", default=None)
    experiment.add_argument("--n-cycles", type=int, default=3)
    experiment.add_argument("--n-concurrent", type=int, default=16)
    experiment.add_argument("--mini-swe-agent-dir", default=str(DEFAULT_MINI_SWE_AGENT))
    experiment.add_argument("--output-token-cap", type=int, default=1000)
    experiment.add_argument("--min-support", type=int, default=2)
    experiment.add_argument("--registry-budget", type=int, default=1600)
    experiment.add_argument("--bootstrap-samples", type=int, default=2000)
    experiment.add_argument("--exclude-tool-name", action="append", default=[],
                            help="evidence-backed experimental quarantine; may be repeated")
    experiment.add_argument("--max-regression-rate", type=float, default=0.20)
    experiment.add_argument("--max-heldout-regression-rate", type=float, default=0.25)
    experiment.add_argument("--max-success-drop-rate", type=float, default=0.10)
    experiment.add_argument("--max-cost-increase-rate", type=float, default=0.03)
    args = parser.parse_args(argv)
    _setup_logging(args.log_file)

    if args.cmd == "prepare":
        report = _pipeline(args).prepare(Path(args.run_dir), heldout_fraction=args.heldout_fraction)
        logger.info("v8 prepare complete: %s", report)
        return 0
    if args.cmd == "validate":
        cards, results = _pipeline(args).validate()
        incomplete = [x for x in results if not x["passed"]]
        logger.info("v8 validation: cards=%d incomplete=%d", len(cards), len(incomplete))
        return 0 if cards and not incomplete else 2
    if args.cmd == "compile":
        manager = RegistryManager(Path(args.work_dir), Path(args.scripts_dir))
        staging = manager.stage()
        prompt = _pipeline(args).compile_prompt(staging)
        out = Path(args.prompt_out) if args.prompt_out else Path(args.work_dir) / "compile_prompt.md"
        out.write_text(prompt, encoding="utf-8")
        logger.info("validated-card compile prompt written to %s", out)
        if args.execute:
            if not args.config:
                raise ValueError("--config is required with --execute")
            run_compile_agent(prompt, Path(args.work_dir), staging, args.config,
                              Path(args.mini_swe_agent_dir), args.dry_run)
            if not args.dry_run:
                compiled_cards = (_read_json(Path(args.work_dir) / "cards.json", {}) or {}).get("cards", [])
                compiled_instruction_cards = (_read_json(
                    Path(args.work_dir) / "instruction_cards.json", {}
                ) or {}).get("cards", [])
                if compiled_instruction_cards and not compiled_cards:
                    _pipeline(args).restore_policy_arm_tools(staging)
                warnings = validate_registry(staging, args.output_token_cap, compiled_cards)
                if warnings:
                    raise RuntimeError("compiled staging registry is invalid: " + "; ".join(warnings))
                logger.info("staging registry compiled; run semantic replay and then `promote`")
        return 0
    if args.cmd == "promote":
        evidence = _read_json(Path(args.postcompile_evidence), {}) or {}
        record = RegistryManager(Path(args.work_dir), Path(args.scripts_dir)).promote(evidence)
        logger.info("registry promoted: %s", record)
        return 0
    if args.cmd == "check-registry":
        warnings = validate_registry(Path(args.scripts_dir), args.output_token_cap)
        for warning in warnings:
            logger.error("registry: %s", warning)
        return 2 if warnings else 0
    if args.cmd == "run":
        if not args.pricing_config:
            args.pricing_config = args.config
        scripts = Path(args.scripts_dir)
        if not args.dry_run:
            seed_v8(scripts)
            deploy_v6(scripts, api_type=_llm_api_type(args.config),
                      max_completion_tokens=_max_completion_tokens(), container=True)
            warnings = validate_registry(scripts, args.output_token_cap)
            if warnings:
                raise RuntimeError("refusing rollout of invalid registry: " + "; ".join(warnings))
        case_ids = [x.strip() for x in Path(args.eval_cases_file).read_text(encoding="utf-8").splitlines()
                    if x.strip() and not x.lstrip().startswith("#")]
        if args.baseline_dir:
            run_dir = Path(args.baseline_dir)
        else:
            rollout = RolloutAgent(args.benchmark, args.config, n_concurrent=args.n_concurrent)
            result = rollout.rollout(scripts, case_ids, f"v8-{args.benchmark}-{os.getpid()}", 1,
                                     dry_run=args.dry_run)
            run_dir = result.run_dir
        if args.dry_run:
            logger.info("dry-run: would prepare %s", run_dir)
            return 0
        unannotated = []
        for path in _trajectory_files(run_dir):
            data = _read_json(path, {}) or {}
            action_count = len(_action_steps(data))
            dependencies = data.get("dependencies") or {}
            if action_count and not all(str(i) in dependencies for i in range(1, action_count + 1)):
                unannotated.append(path)
        if unannotated:
            logger.info("annotating %d trajectories before graph construction", len(unannotated))
            TrajectoryAnnotator(args.config, workers=args.n_concurrent).run(run_dir)
        report = _pipeline(args).prepare(run_dir, heldout_fraction=args.heldout_fraction)
        logger.info("v8 cycle prepared; replay/heldout evidence is required before compile: %s", report)
        return 0
    if args.cmd == "experiment":
        case_ids = [x.strip() for x in Path(args.eval_cases_file).read_text(encoding="utf-8").splitlines()
                    if x.strip() and not x.lstrip().startswith("#")]
        if len(case_ids) != 16:
            raise ValueError(f"experiment requires exactly 16 evolve cases, got {len(case_ids)}")
        report = EvolveV8Experiment(
            benchmark=args.benchmark, config=args.config, scripts_dir=Path(args.scripts_dir),
            work_dir=Path(args.work_dir), case_ids=case_ids,
            baseline_dir=Path(args.baseline_dir) if args.baseline_dir else None,
            n_cycles=args.n_cycles, n_concurrent=args.n_concurrent,
            mini_swe_agent_dir=Path(args.mini_swe_agent_dir),
            output_token_cap=args.output_token_cap, min_support=args.min_support,
            registry_budget=args.registry_budget, bootstrap_samples=args.bootstrap_samples,
            excluded_tool_names=args.exclude_tool_name,
            max_regression_rate=args.max_regression_rate,
            max_heldout_regression_rate=args.max_heldout_regression_rate,
            max_success_drop_rate=args.max_success_drop_rate,
            max_cost_increase_rate=args.max_cost_increase_rate,
        ).run()
        logger.info("v8 experiment complete: %s", report)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
