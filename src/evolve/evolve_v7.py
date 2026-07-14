"""Evolve v7: outcome-anchored, cost-aware provenance graph compression.

V7 deliberately leaves the v6 implementation untouched.  It reuses v6's
native-tool runtime and rollout wiring, but replaces the contrastive-sample
construction and evolve prompt with:

* a typed execution provenance graph;
* final-write / verifier anchored dependency slicing;
* cost-aware AND/OR support-set search;
* compact cross-trajectory graph-macro evidence; and
* a hard prompt-size budget.

Existing trajectories do not yet contain per-write repository snapshots.  The
builder therefore consumes instrumented ``outcome_anchors`` / state metadata
when present and otherwise emits an explicitly labelled ``best_effort`` graph
from step metadata, tool arguments, dependencies, and verifier results.  A
structural candidate is never represented as replay- or verifier-validated.

Usage::

    python -m src.evolve.evolve_v7 run \
        --benchmark swebench --config _config/deepseekv4_flash.yaml \
        --eval-cases-file <cases.txt> --baseline-dir <prep-dir> \
        --scripts-dir .evolve_scripts_v7 --work-dir results/v7/swebench

    python -m src.evolve.evolve_v7 prompt-dry-run <annotated-result-dir> \
        --config _config/deepseekv4_flash.yaml --compare-v6
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import logging
import math
import os
import re
import tempfile
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from src.tools.llm import LLM

from ._chunk_helpers import (
    bash_verb,
    classify_step_meta,
    extract_bash_command,
    observation_chars,
)
from .annotator import TrajectoryAnnotator
from .contrastive import ContrastiveSampleBuilder
from .evolver import MiniSweAgentRunner, ScriptEvolver, TrajectorySerializer
from .evolve_v6_cycle import (
    BENCHMARKS,
    DEFAULT_MINI_SWE_AGENT,
    CycleReport,
    EvolveAgent,
    EvolvePromptBuilderV6,
    EvolveV6Cycle,
    RolloutResult,
    ROOT,
    _setup_logging,
)

logger = logging.getLogger(__name__)

DEFAULT_SCRIPTS_DIR = ROOT / ".evolve_scripts_v7"
DEFAULT_WORK_DIR = ROOT / "results" / "evolve" / "v7cycle"
DEFAULT_N_CYCLES = 4
DEFAULT_EVOLVE_BATCH_SIZE = 4
DEFAULT_MAX_PROMPT_CHARS = 32_000
DEFAULT_MAX_STEPS_PER_SAMPLE = 8
DEFAULT_REGISTRY_BUDGET_TOKENS = 1_200
DEFAULT_MAX_MACRO_CANDIDATES = 8

ACTION_FILE_KEYS = {
    "file", "file_path", "filepath", "path", "target", "target_file",
    "output_file", "source", "destination", "dir", "directory",
}
READ_HINTS = ("read", "grep", "search", "find", "list", "show", "locate", "cat", "head", "tail")
WRITE_HINTS = ("write", "edit", "patch", "apply", "create", "delete", "remove", "move", "copy")
VERIFY_HINTS = ("test", "pytest", "verify", "lint", "build", "check", "typecheck")


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------


def _is_action_step(step: dict) -> bool:
    return bool(step.get("tool_calls") or "observation" in step or step.get("action"))


def _action_steps(trajectory: dict) -> List[dict]:
    return [step for step in trajectory.get("steps", []) if _is_action_step(step)]


def _clip(value, limit: int) -> str:
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + "...<truncated>"


def _observation_text(step: dict, limit: int = 600) -> str:
    return TrajectorySerializer(max_observation_chars=limit)._serialize_observation(
        step.get("observation", "")
    )


def _raw_observation_payload(step: dict) -> str:
    """Return command output fields without the serializer's display clipping.

    mini-swe-agent stores large command results as ``output_head`` and
    ``output_tail``. Those fields remain evidence for repository-state anchors,
    even though the normal prompt serializer intentionally omits them.
    """
    observation = step.get("observation")
    if isinstance(observation, str):
        return observation
    if not isinstance(observation, dict):
        return ""
    chunks: List[str] = []
    for result in observation.get("results", []) or []:
        content = result.get("content") if isinstance(result, dict) else result
        if not isinstance(content, str):
            continue
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            chunks.append(content)
            continue
        if not isinstance(parsed, dict):
            chunks.append(str(parsed))
            continue
        for key in ("output", "output_head", "output_tail"):
            value = parsed.get(key)
            if isinstance(value, str) and value:
                chunks.append(value)
    return "\n".join(chunks)


def _returncode(step: dict) -> Optional[int]:
    obs = step.get("observation")
    if not isinstance(obs, dict):
        return None
    for result in obs.get("results", []) or []:
        content = result.get("content") if isinstance(result, dict) else result
        if not isinstance(content, str):
            continue
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and isinstance(parsed.get("returncode"), int):
            return parsed["returncode"]
    return None


def _stable_hash(value) -> str:
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def _normalize_path(raw: str) -> str:
    value = str(raw or "").strip().strip("'\"").rstrip(":,;)")
    value = re.sub(r"^\./", "", value)
    for prefix in ("/testbed/", "/workspace/", "/repo/", "/app/"):
        if value.startswith(prefix):
            value = value[len(prefix):]
            break
    return value


def _looks_like_path(value: str) -> bool:
    if not value or "\n" in value or len(value) > 300:
        return False
    if value.startswith(("http://", "https://")):
        return False
    if "/" in value:
        return True
    basename = value.rsplit("/", 1)[-1]
    if basename in {"Makefile", "Dockerfile", "pyproject.toml", "go.mod", "go.sum"}:
        return True
    suffix = basename.rsplit(".", 1)[-1].lower() if "." in basename else ""
    return suffix in {
        "py", "pyi", "js", "jsx", "ts", "tsx", "go", "rs", "java", "kt", "rb",
        "c", "cc", "cpp", "h", "hpp", "sh", "bash", "yaml", "yml", "toml", "json",
        "ini", "cfg", "md", "rst", "txt", "sql", "html", "css", "scss", "xml",
    }


def _clean_file_paths(values: Iterable[str]) -> List[str]:
    result = []
    for raw in values:
        path = _normalize_path(raw)
        if not path or path in {"/testbed", "/workspace", "/repo", "."}:
            continue
        if any(char in path for char in "(){}*$|\\"):
            continue
        if not _looks_like_path(path):
            continue
        if path not in result:
            result.append(path)
    return result


def _paths_from_call(call: dict) -> List[str]:
    paths: List[str] = []
    args = call.get("arguments") or {}
    if isinstance(args, dict):
        for key, value in args.items():
            key_l = str(key).lower()
            if key_l in ACTION_FILE_KEYS and isinstance(value, str) and _looks_like_path(value):
                paths.append(_normalize_path(value))
            if key_l in {"patch", "content", "command"} and isinstance(value, str):
                for match in re.findall(
                    r"(?:\*\*\* (?:Update|Add|Delete) File:|^\+\+\+ b/|^--- a/)\s*([^\n]+)",
                    value,
                    flags=re.MULTILINE,
                ):
                    paths.append(_normalize_path(match))
                if key_l == "command":
                    for match in re.findall(
                        r"(?:open|Path)\(\s*['\"]([^'\"]+)['\"]",
                        value,
                    ):
                        paths.append(_normalize_path(match))
                    for match in re.findall(
                        r"(?:^|[;&|]\s*|\s)(?:>>|(?<!>)>(?!>))\s*([^\s;&|]+)",
                        value,
                        flags=re.MULTILINE,
                    ):
                        paths.append(_normalize_path(match))
    return list(dict.fromkeys(path for path in paths if path))


def _tool_family(call: dict) -> str:
    name = str(call.get("function_name") or call.get("tool") or "action").lower()
    if name == "bash":
        command = (call.get("arguments") or {}).get("command", "")
        return bash_verb(command) or "bash"
    return name.replace("_", "-")


def _call_op_type(call: dict, fallback: str) -> str:
    family = _tool_family(call)
    if family == "bash":
        return fallback
    if any(hint in family for hint in WRITE_HINTS):
        return "write"
    if any(hint in family for hint in VERIFY_HINTS):
        return "verify"
    if any(hint in family for hint in READ_HINTS):
        return "read"
    return fallback


def _is_full_overwrite(call: dict) -> bool:
    family = _tool_family(call)
    args = call.get("arguments") or {}
    if family in {"write-file", "create-file"} and isinstance(args, dict) and "content" in args:
        return True
    if str(call.get("function_name", "")).lower() == "bash":
        command = str(args.get("command", "")) if isinstance(args, dict) else ""
        return bool(re.search(r"(?:cat\s+<<[^\n]+\s*>|(?<!>)>(?!>))\s*\S+", command))
    return False


def _summarize_call(call: dict, limit: int = 420) -> str:
    args = call.get("arguments") or {}
    safe_args = {}
    if isinstance(args, dict):
        for key, value in args.items():
            if key in {"content", "patch"} and isinstance(value, str):
                safe_args[key] = f"<{len(value)} chars; hash={_stable_hash(value)}>"
            else:
                safe_args[key] = _clip(value, 220)
    return _clip({"tool": _tool_family(call), "arguments": safe_args}, limit)


def _task_summary(trajectory: dict, limit: int = 1_200) -> str:
    pieces: List[str] = []
    for step in trajectory.get("steps", []):
        if _is_action_step(step):
            break
        for key in ("message", "content", "task"):
            value = step.get(key)
            if isinstance(value, str) and value.strip():
                pieces.append(value.strip())
                break
    return _clip("\n".join(pieces), limit)


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _sample_priority(path: Path) -> tuple:
    sample = _read_json(Path(path))
    eligible = bool((sample.get("outcome") or {}).get("eligible_for_evolve"))
    has_failure_evidence = bool(sample.get("failure_patterns"))
    return (0 if eligible else 1 if has_failure_evidence else 2, str(path))


def _verifier_pass(trajectory_path: Path) -> Tuple[Optional[bool], object]:
    result = _read_json(trajectory_path.parent.parent / "result.json")
    rewards = ((result.get("verifier_result") or {}).get("rewards") or {})
    if not rewards:
        rewards = _read_json(trajectory_path.parent.parent / "verifier" / "reward.json")
    raw = rewards.get("reward", rewards.get("overall_pass"))
    if isinstance(raw, bool):
        return raw, raw
    if isinstance(raw, (int, float)):
        return raw > 0, raw
    return None, raw


# ---------------------------------------------------------------------------
# Cost model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PriceModel:
    unit: str = "weighted_tokens"
    input_price: float = 1.0
    cached_price: float = 0.1
    output_price: float = 3.0
    per_million: bool = False

    @classmethod
    def from_config(cls, config_path: Optional[str]) -> "PriceModel":
        if not config_path:
            return cls()
        try:
            cfg = LLM._load_config(str(config_path))
        except Exception:  # noqa: BLE001 - cost fallback must not abort sample building
            return cls()
        yuan = cfg.get("price_yuan_per_million_token") or {}
        if isinstance(yuan, dict) and yuan:
            return cls(
                unit="yuan",
                input_price=float(yuan.get("input_token", 0) or 0),
                cached_price=float(yuan.get("cached_token", 0) or 0),
                output_price=float(yuan.get("output_token", 0) or 0),
                per_million=True,
            )
        pricing = cfg.get("pricing") or {}
        if isinstance(pricing, dict) and pricing:
            return cls(
                unit=str(pricing.get("unit") or "usd"),
                input_price=float(pricing.get("input", 0) or 0),
                cached_price=float(pricing.get("cache", 0) or 0),
                output_price=float(pricing.get("output", 0) or 0),
                per_million=True,
            )
        return cls()

    def turn_cost(self, step: dict) -> float:
        metrics = step.get("metrics") or {}
        prompt = int(metrics.get("prompt_tokens", 0) or 0)
        cached = min(prompt, int(metrics.get("cached_tokens", 0) or 0))
        completion = int(metrics.get("completion_tokens", 0) or 0)
        value = (
            max(0, prompt - cached) * self.input_price
            + cached * self.cached_price
            + completion * self.output_price
        )
        return value / 1_000_000 if self.per_million else value

    def schema_overhead(self, tokens: int, projected_turns: int) -> float:
        value = max(0, tokens) * max(0, projected_turns) * self.input_price
        return value / 1_000_000 if self.per_million else value


# ---------------------------------------------------------------------------
# Provenance graph and cost-aware slice
# ---------------------------------------------------------------------------


@dataclass
class SupportOption:
    nodes: List[int]
    confidence: float = 1.0
    source: str = "flat_dependency_compat"


@dataclass
class TurnRecord:
    index: int
    step: dict
    op_type: str
    success: bool
    tool_families: List[str]
    files: List[str]
    cost: float
    obs_chars: int
    signature: str
    full_overwrite: bool


@dataclass
class SliceCandidate:
    selected: List[int]
    support_choices: Dict[int, int]
    cost: float
    uncertainty_penalty: float


class CostAwareSupportSolver:
    """Beam-search K-best solver for temporal AND/OR support sets.

    Flat v6 dependencies become one AND option, so the solver degenerates to a
    deterministic outcome-anchored closure.  Future V7 annotations can provide
    several alternatives, in which case the beam compares their union cost and
    confidence penalty.
    """

    def __init__(self, costs: Dict[int, float], support_sets: Dict[int, List[SupportOption]],
                 hard_predecessors: Dict[int, set], *, beam_width: int = 32,
                 top_k: int = 3):
        self.costs = costs
        self.support_sets = support_sets
        self.hard_predecessors = hard_predecessors
        self.beam_width = max(1, int(beam_width))
        self.top_k = max(1, int(top_k))
        nonzero = [value for value in costs.values() if value > 0]
        self.confidence_scale = sum(nonzero) / len(nonzero) if nonzero else 1.0

    def solve(self, terminals: Iterable[int]) -> List[SliceCandidate]:
        initial = self._hard_closure(set(int(i) for i in terminals if int(i) > 0))
        states = [(initial, {}, 0.0)]
        completed: List[SliceCandidate] = []
        max_expansions = max(1, len(self.costs) * 3)
        for _ in range(max_expansions):
            next_states = []
            all_complete = True
            for selected, choices, penalty in states:
                unresolved = [
                    idx for idx in sorted(selected)
                    if idx in self.support_sets and idx not in choices
                ]
                if not unresolved:
                    completed.append(self._candidate(selected, choices, penalty))
                    continue
                all_complete = False
                target = unresolved[0]
                options = self.support_sets.get(target) or [SupportOption([])]
                for option_index, option in enumerate(options):
                    expanded = self._hard_closure(selected | set(option.nodes))
                    new_choices = dict(choices)
                    new_choices[target] = option_index
                    new_penalty = penalty + (1.0 - max(0.0, min(1.0, option.confidence))) * self.confidence_scale
                    next_states.append((expanded, new_choices, new_penalty))
            if all_complete or not next_states:
                break
            dedup = {}
            for state in next_states:
                key = (frozenset(state[0]), tuple(sorted(state[1].items())))
                current = dedup.get(key)
                if current is None or state[2] < current[2]:
                    dedup[key] = state
            states = sorted(dedup.values(), key=self._state_score)[:self.beam_width]
        if not completed:
            completed = [self._candidate(*state) for state in states]
        unique = {}
        for candidate in completed:
            key = tuple(candidate.selected)
            if key not in unique or candidate.cost + candidate.uncertainty_penalty < (
                unique[key].cost + unique[key].uncertainty_penalty
            ):
                unique[key] = candidate
        return sorted(
            unique.values(),
            key=lambda item: (item.cost + item.uncertainty_penalty, len(item.selected)),
        )[:self.top_k]

    def _hard_closure(self, selected: set) -> set:
        closure = set(selected)
        stack = list(selected)
        while stack:
            idx = stack.pop()
            for pred in self.hard_predecessors.get(idx, set()):
                if pred > 0 and pred not in closure:
                    closure.add(pred)
                    stack.append(pred)
        return closure

    def _state_score(self, state) -> Tuple[float, int]:
        selected, _, penalty = state
        return sum(self.costs.get(idx, 0.0) for idx in selected) + penalty, len(selected)

    def _candidate(self, selected: set, choices: dict, penalty: float) -> SliceCandidate:
        return SliceCandidate(
            selected=sorted(selected),
            support_choices={int(k): int(v) for k, v in choices.items()},
            cost=sum(self.costs.get(idx, 0.0) for idx in selected),
            uncertainty_penalty=penalty,
        )


class ProvenanceGraphBuilderV7:
    def __init__(self, price_model: PriceModel, *, beam_width: int = 32,
                 top_k: int = 3, max_observation_chars: int = 500):
        self.price_model = price_model
        self.beam_width = int(beam_width)
        self.top_k = int(top_k)
        self.max_observation_chars = int(max_observation_chars)

    def build(self, trajectory_path: Path) -> dict:
        trajectory_path = Path(trajectory_path)
        trajectory = _read_json(trajectory_path)
        dependencies = trajectory.get("dependencies")
        if not isinstance(dependencies, dict):
            raise ValueError(f"trajectory is not dependency-annotated: {trajectory_path}")
        steps = _action_steps(trajectory)
        turns = self._turn_records(steps)
        support_sets = self._support_sets(trajectory, dependencies, len(turns))
        graph, hard_predecessors, artifact_producers = self._build_graph(
            trajectory, turns, dependencies
        )
        anchors, anchor_mode = self._outcome_anchors(trajectory, turns)
        verifier_pass, verifier_reward = _verifier_pass(trajectory_path)
        evidence_turns = self._evidence_policy(turns, anchors, verifier_pass)
        terminals = sorted(set(anchors) | set(evidence_turns))

        graph["nodes"].extend(self._outcome_nodes(verifier_pass, verifier_reward, anchor_mode))
        self._connect_outcomes(
            graph, anchors, evidence_turns, artifact_producers, verifier_pass
        )

        costs = {turn.index: turn.cost for turn in turns}
        candidates = CostAwareSupportSolver(
            costs,
            support_sets,
            hard_predecessors,
            beam_width=self.beam_width,
            top_k=self.top_k,
        ).solve(terminals) if terminals else []
        best = candidates[0] if candidates else SliceCandidate([], {}, 0.0, 0.0)
        all_indices = {turn.index for turn in turns}
        selected = set(best.selected)
        original_cost = sum(turn.cost for turn in turns)
        eligible = bool(anchors) and verifier_pass is not False
        structural_valid = bool(terminals) and all(idx in all_indices for idx in selected)
        validation_level = "structurally_valid" if structural_valid else "unvalidated"

        step_summaries = [self._step_summary(turn, selected, anchors, evidence_turns) for turn in turns]
        failure_patterns = self._failure_patterns(turns)
        batching = self._batching_candidates(turns, selected, dependencies)
        pruned = all_indices - selected

        return {
            "schema_version": "evolve-v7-sample-1",
            "source_trajectory": str(trajectory_path),
            "trajectory_id": trajectory_path.parent.parent.name,
            "task_summary": _task_summary(trajectory),
            "graph": graph,
            "graph_summary": {
                "node_count": len(graph["nodes"]),
                "edge_count": len(graph["edges"]),
                "turn_count": len(turns),
                "anchor_mode": anchor_mode,
                "hard_edge_count": sum(1 for edge in graph["edges"] if edge.get("hard")),
                "soft_edge_count": sum(1 for edge in graph["edges"] if not edge.get("hard")),
            },
            "outcome": {
                "write_anchor_steps": anchors,
                "evidence_terminal_steps": evidence_turns,
                "verifier_pass": verifier_pass,
                "verifier_reward": verifier_reward,
                "eligible_for_evolve": eligible,
            },
            "cost_model": asdict(self.price_model),
            "slice": {
                "selected_steps": best.selected,
                "pruned_steps": sorted(pruned),
                "support_choices": {str(k): v for k, v in best.support_choices.items()},
                "original_cost": original_cost,
                "selected_cost": best.cost,
                "estimated_saving": max(0.0, original_cost - best.cost),
                "cost_ratio": (best.cost / original_cost) if original_cost > 0 else None,
                "uncertainty_penalty": best.uncertainty_penalty,
                "validation_level": validation_level,
                "state_replay_valid": None,
                "verifier_replay_valid": None,
                "decision_sufficient": None,
                "candidate_kind": "trace_preserving",
                "k_best": [asdict(candidate) for candidate in candidates],
            },
            "steps": step_summaries,
            "pruned_summary": self._aggregate_steps(turns, pruned),
            "batching_candidates": batching,
            "failure_patterns": failure_patterns,
            "_turns": turns,
        }

    def _turn_records(self, steps: Sequence[dict]) -> List[TurnRecord]:
        records = []
        for index, step in enumerate(steps, start=1):
            rule_meta = classify_step_meta(step)
            existing_meta = step.get("step_meta") if isinstance(step.get("step_meta"), dict) else {}
            meta = {**rule_meta, **existing_meta}
            calls = step.get("tool_calls") or []
            if not isinstance(calls, list):
                calls = []
            if not calls and step.get("action"):
                calls = [{"function_name": "action", "arguments": {"value": step.get("action")}}]
            families = list(dict.fromkeys(_tool_family(call) for call in calls)) or ["unknown"]
            files = list(meta.get("files_touched") or [])
            for call in calls:
                files.extend(_paths_from_call(call))
            files = _clean_file_paths(files)
            op_type = str(meta.get("op_type") or "explore")
            rc = _returncode(step)
            success = bool(meta.get("success", rc in (None, 0)))
            signature = f"{op_type}:{'+'.join(sorted(families))}"
            records.append(TurnRecord(
                index=index,
                step=step,
                op_type=op_type,
                success=success,
                tool_families=families,
                files=files,
                cost=self.price_model.turn_cost(step),
                obs_chars=observation_chars(step.get("observation")),
                signature=signature,
                full_overwrite=any(_is_full_overwrite(call) for call in calls),
            ))
        return records

    @staticmethod
    def _support_sets(trajectory: dict, dependencies: dict, n_turns: int) -> Dict[int, List[SupportOption]]:
        annotated = trajectory.get("support_sets") or {}
        result: Dict[int, List[SupportOption]] = {}
        for index in range(1, n_turns + 1):
            raw_options = annotated.get(str(index)) if isinstance(annotated, dict) else None
            options: List[SupportOption] = []
            if isinstance(raw_options, list):
                for raw in raw_options:
                    if isinstance(raw, dict):
                        nodes = raw.get("nodes") or raw.get("dependencies") or []
                        confidence = raw.get("confidence", 0.75)
                    else:
                        nodes, confidence = raw, 0.75
                    if isinstance(nodes, list):
                        clean = sorted({int(node) for node in nodes if str(node).isdigit() and 0 < int(node) < index})
                        options.append(SupportOption(clean, float(confidence), "v7_annotation"))
            if not options:
                deps = dependencies.get(str(index), [])
                clean = sorted({int(node) for node in deps if str(node).isdigit() and 0 < int(node) < index})
                if clean:
                    options = [SupportOption(clean, 1.0, "flat_dependency_compat")]
            if options:
                result[index] = options
        return result

    def _build_graph(self, trajectory: dict, turns: Sequence[TurnRecord], dependencies: dict):
        graph = {
            "schema_version": "evolve-v7-provenance-1",
            "nodes": [{"id": "task", "type": "TASK", "attributes": {"summary": _task_summary(trajectory)}}],
            "edges": [],
        }
        latest_artifact: Dict[str, Tuple[str, int]] = {}
        artifact_versions: Counter = Counter()
        hard_predecessors: Dict[int, set] = defaultdict(set)
        artifact_producers: Dict[int, List[str]] = defaultdict(list)

        for turn in turns:
            graph["nodes"].append({
                "id": f"turn_{turn.index}", "type": "TURN", "timestamp": turn.index,
                "cost": turn.cost,
                "attributes": {
                    "op_type": turn.op_type, "success": turn.success,
                    "tool_families": turn.tool_families, "files": turn.files,
                    "observation_chars": turn.obs_chars,
                },
            })
            if turn.index == 1:
                graph["edges"].append(self._edge("task", "turn_1", "CONTEXT", True))
            elif turn.index > 1:
                graph["edges"].append(self._edge(
                    f"turn_{turn.index - 1}", f"turn_{turn.index}", "NEXT", False
                ))
            calls = turn.step.get("tool_calls") or []
            if not isinstance(calls, list) or not calls:
                calls = [{"function_name": "action", "arguments": {}}]
            for call_index, call in enumerate(calls, start=1):
                action_id = f"action_{turn.index}_{call_index}"
                call_op = _call_op_type(call, turn.op_type)
                call_files = _paths_from_call(call) or turn.files
                graph["nodes"].append({
                    "id": action_id, "type": "ACTION", "timestamp": turn.index,
                    "attributes": {
                        "tool_family": _tool_family(call), "op_type": call_op,
                        "files": call_files, "arguments_summary": _summarize_call(call),
                    },
                })
                graph["edges"].append(self._edge(f"turn_{turn.index}", action_id, "DECIDES", True))
                for path in call_files:
                    previous = latest_artifact.get(path)
                    if previous:
                        graph["edges"].append(self._edge(previous[0], action_id, "CONSUMES_STATE", True))
                        if call_op == "write" and not _is_full_overwrite(call):
                            hard_predecessors[turn.index].add(previous[1])
                    if call_op == "write":
                        artifact_versions[path] += 1
                        artifact_id = f"artifact_{_stable_hash(path)}_v{artifact_versions[path]}"
                        graph["nodes"].append({
                            "id": artifact_id, "type": "ARTIFACT_VERSION", "timestamp": turn.index,
                            "attributes": {"path": path, "version": artifact_versions[path]},
                        })
                        graph["edges"].append(self._edge(action_id, artifact_id, "PRODUCES_STATE", True))
                        latest_artifact[path] = (artifact_id, turn.index)
                        artifact_producers[turn.index].append(artifact_id)
            obs_id = f"obs_{turn.index}"
            graph["nodes"].append({
                "id": obs_id, "type": "OBSERVATION", "timestamp": turn.index,
                "attributes": {
                    "chars": turn.obs_chars, "returncode": _returncode(turn.step),
                    "content_hash": _stable_hash(turn.step.get("observation")),
                },
            })
            for call_index in range(1, len(calls) + 1):
                graph["edges"].append(self._edge(
                    f"action_{turn.index}_{call_index}", obs_id, "RETURNS", True
                ))
            if turn.op_type == "verify":
                evidence_id = f"evidence_{turn.index}"
                graph["nodes"].append({
                    "id": evidence_id, "type": "EVIDENCE", "timestamp": turn.index,
                    "attributes": {"success": turn.success, "command_signature": turn.signature},
                })
                graph["edges"].append(self._edge(obs_id, evidence_id, "VALIDATES", True))

        for target in range(1, len(turns) + 1):
            deps = dependencies.get(str(target), [])
            for dep in deps:
                try:
                    dep_int = int(dep)
                except (TypeError, ValueError):
                    continue
                if 0 < dep_int < target:
                    graph["edges"].append({
                        **self._edge(f"obs_{dep_int}", f"turn_{target}", "OBSERVES", False),
                        "confidence": 1.0,
                        "support_group": f"turn_{target}_flat",
                    })
        for turn in turns[:-1]:
            if not turn.success:
                graph["edges"].append(self._edge(
                    f"obs_{turn.index}", f"turn_{turn.index + 1}", "CONTROL_TRIGGER", False
                ))
        return graph, hard_predecessors, artifact_producers

    @staticmethod
    def _edge(source: str, target: str, edge_type: str, hard: bool) -> dict:
        return {"source": source, "target": target, "type": edge_type, "hard": hard}

    @staticmethod
    def _outcome_anchors(trajectory: dict, turns: Sequence[TurnRecord]) -> Tuple[List[int], str]:
        provenance = trajectory.get("provenance") if isinstance(trajectory.get("provenance"), dict) else {}
        explicit = trajectory.get("outcome_anchors") or provenance.get("outcome_anchors") or {}
        raw = explicit.get("write_steps") if isinstance(explicit, dict) else explicit
        if isinstance(raw, list):
            clean = sorted({int(idx) for idx in raw if str(idx).isdigit() and 0 < int(idx) <= len(turns)})
            if clean:
                return clean, "instrumented"

        diff_paths = ProvenanceGraphBuilderV7._observed_final_diff_paths(turns)
        if diff_paths:
            last_by_diff_path: Dict[str, int] = {}
            for turn in turns:
                if turn.op_type != "write" or not turn.success:
                    continue
                # Repository-control actions can mention every changed file,
                # but they did not produce those file contents.
                if "git" in turn.tool_families:
                    continue
                for path in turn.files:
                    if path in diff_paths:
                        last_by_diff_path[path] = turn.index
            if last_by_diff_path:
                return sorted(set(last_by_diff_path.values())), "observed_git_change_summary"

        last_by_file: Dict[str, int] = {}
        unscoped_writes: List[int] = []
        for turn in turns:
            if turn.op_type != "write" or not turn.success:
                continue
            if turn.files:
                for path in turn.files:
                    last_by_file[path] = turn.index
            else:
                unscoped_writes.append(turn.index)
        anchors = sorted(set(last_by_file.values()))
        if not anchors and unscoped_writes:
            anchors = [unscoped_writes[-1]]
        return anchors, "best_effort_last_successful_write_per_file"

    @staticmethod
    def _observed_final_diff_paths(turns: Sequence[TurnRecord]) -> set:
        # Later repository inspections describe a state closer to the submitted
        # outcome. Walk backwards and use the first non-empty diff/status/stat.
        for turn in reversed(turns):
            command = extract_bash_command(turn.step)
            if not re.search(r"\bgit\s+(?:diff|status)\b", command):
                continue
            text = _raw_observation_payload(turn.step)
            found: List[str] = []
            for left, right in re.findall(r"diff --git a/(\S+) b/(\S+)", text):
                if left == right:
                    found.append(right)
            for match in re.findall(r"^\+\+\+ b/(\S+)", text, flags=re.MULTILINE):
                found.append(match)
            for match in re.findall(
                r"^\s*(?:modified|new file|deleted|renamed):\s+(.+?)\s*$",
                text,
                flags=re.MULTILINE,
            ):
                found.append(match)
            for match in re.findall(
                r"^\s*(.+?)\s+\|\s+\d+(?:\s+[+\-]+)?\s*$",
                text,
                flags=re.MULTILINE,
            ):
                found.append(match)
            for match in re.findall(
                r"^[ MADRCU?!]{1,2}\s+([^\n]+)$",
                text,
                flags=re.MULTILINE,
            ):
                found.append(match)
            normalized = []
            for path in found:
                # Git rename summaries may use either "old -> new" or
                # "prefix/{old => new}/suffix". The new side is the anchor.
                path = path.rsplit(" -> ", 1)[-1]
                path = re.sub(r"\{[^{}]* => ([^{}]*)\}", r"\1", path)
                normalized.append(path)
            clean = set(_clean_file_paths(normalized))
            if clean:
                return clean
        return set()

    @staticmethod
    def _evidence_policy(turns: Sequence[TurnRecord], anchors: Sequence[int],
                         verifier_pass: Optional[bool]) -> List[int]:
        if verifier_pass is not None:
            return []
        after = max(anchors) if anchors else 0
        candidates = [
            turn.index for turn in turns
            if turn.op_type == "verify" and turn.success and turn.index >= after
        ]
        if not candidates:
            candidates = [turn.index for turn in turns if turn.op_type == "verify" and turn.success]
        return candidates[-1:]  # coverage b_g=1 when no external verifier exists

    @staticmethod
    def _outcome_nodes(verifier_pass: Optional[bool], verifier_reward, anchor_mode: str) -> List[dict]:
        nodes = [
            {"id": "final_patch", "type": "OUTCOME", "attributes": {"anchor_mode": anchor_mode}},
            {"id": "success", "type": "OUTCOME", "attributes": {"virtual": True}},
        ]
        if verifier_pass is not None:
            nodes.append({
                "id": "verifier_result", "type": "OUTCOME",
                "attributes": {"pass": verifier_pass, "reward": verifier_reward},
            })
        return nodes

    def _connect_outcomes(self, graph: dict, anchors: Sequence[int], evidence_turns: Sequence[int],
                          artifact_producers: Dict[int, List[str]], verifier_pass: Optional[bool]) -> None:
        for index in anchors:
            producers = artifact_producers.get(index) or []
            if producers:
                for artifact_id in producers:
                    graph["edges"].append(self._edge(artifact_id, "final_patch", "SUPPORTS_OUTCOME", True))
            else:
                graph["edges"].append(self._edge(f"turn_{index}", "final_patch", "SUPPORTS_OUTCOME", True))
        graph["edges"].append(self._edge("final_patch", "success", "SUPPORTS_OUTCOME", True))
        for index in evidence_turns:
            graph["edges"].append(self._edge(
                f"evidence_{index}", "success", "SUPPORTS_OUTCOME", True
            ))
        if verifier_pass is not None:
            graph["edges"].append(self._edge(
                "final_patch", "verifier_result", "VALIDATES", True
            ))
            if verifier_pass:
                graph["edges"].append(self._edge(
                    "verifier_result", "success", "SUPPORTS_OUTCOME", True
                ))

    def _step_summary(self, turn: TurnRecord, selected: set, anchors: Sequence[int],
                      evidence: Sequence[int]) -> dict:
        calls = turn.step.get("tool_calls") or []
        if not isinstance(calls, list):
            calls = []
        action = "\n".join(_summarize_call(call) for call in calls) or _clip(
            turn.step.get("action") or turn.step.get("message") or "", 420
        )
        return {
            "index": turn.index,
            "selected": turn.index in selected,
            "outcome_anchor": turn.index in anchors,
            "evidence_terminal": turn.index in evidence,
            "op_type": turn.op_type,
            "success": turn.success,
            "signature": turn.signature,
            "files": turn.files[:8],
            "cost": turn.cost,
            "observation_chars": turn.obs_chars,
            "action": _clip(action, 520),
            "observation": _observation_text(turn.step, self.max_observation_chars),
        }

    @staticmethod
    def _aggregate_steps(turns: Sequence[TurnRecord], indices: set) -> dict:
        selected = [turn for turn in turns if turn.index in indices]
        by_op = Counter(turn.op_type for turn in selected)
        by_tool = Counter(family for turn in selected for family in turn.tool_families)
        return {
            "count": len(selected),
            "cost": sum(turn.cost for turn in selected),
            "observation_chars": sum(turn.obs_chars for turn in selected),
            "failed_count": sum(1 for turn in selected if not turn.success),
            "by_op_type": dict(by_op.most_common()),
            "top_tool_families": dict(by_tool.most_common(10)),
        }

    @staticmethod
    def _failure_patterns(turns: Sequence[TurnRecord]) -> List[dict]:
        groups: Dict[str, List[int]] = defaultdict(list)
        for turn in turns:
            if not turn.success:
                groups[turn.signature].append(turn.index)
        return [
            {"signature": signature, "steps": indices, "repeat_count": len(indices)}
            for signature, indices in sorted(groups.items(), key=lambda item: (-len(item[1]), item[0]))
            if len(indices) >= 2
        ][:5]

    @staticmethod
    def _batching_candidates(turns: Sequence[TurnRecord], selected: set,
                             dependencies: dict) -> List[dict]:
        dep_sets = {
            turn.index: {int(dep) for dep in dependencies.get(str(turn.index), []) if str(dep).isdigit()}
            for turn in turns
        }
        reads = [turn for turn in turns if turn.index in selected and turn.op_type == "read" and turn.success]
        result = []
        for pos, left in enumerate(reads):
            for right in reads[pos + 1:pos + 5]:
                if left.index in dep_sets.get(right.index, set()) or right.index in dep_sets.get(left.index, set()):
                    continue
                if set(left.files) & set(right.files):
                    continue
                saving = min(left.cost, right.cost)
                result.append({
                    "steps": [left.index, right.index],
                    "signatures": [left.signature, right.signature],
                    "estimated_turn_cost_saving": saving,
                    "reason": "dependency-independent successful reads with no detected file conflict",
                })
        return sorted(result, key=lambda item: -item["estimated_turn_cost_saving"])[:5]


# ---------------------------------------------------------------------------
# Cross-trajectory motif mining and sample writing
# ---------------------------------------------------------------------------


class MacroMinerV7:
    def __init__(self, price_model: PriceModel, *, registry_budget_tokens: int,
                 max_candidates: int, projected_registry_turns: int = 100):
        self.price_model = price_model
        self.registry_budget_tokens = max(0, int(registry_budget_tokens))
        self.max_candidates = max(0, int(max_candidates))
        self.projected_registry_turns = max(0, int(projected_registry_turns))

    def mine(self, records: Sequence[dict]) -> List[dict]:
        patterns: Dict[str, dict] = {}
        for record in records:
            if not record.get("outcome", {}).get("eligible_for_evolve"):
                continue
            turns: List[TurnRecord] = record["_turns"]
            for size in (2, 3):
                for start in range(0, max(0, len(turns) - size + 1)):
                    window = turns[start:start + size]
                    if all(turn.op_type == "explore" for turn in window):
                        continue
                    signature = " -> ".join(turn.signature for turn in window)
                    item = patterns.setdefault(signature, {
                        "signature": signature,
                        "size": size,
                        "occurrences": [],
                        "trajectory_ids": set(),
                    })
                    original_cost = sum(turn.cost for turn in window)
                    estimated_saving = max(0.0, original_cost - window[0].cost)
                    occurrence = {
                        "trajectory_id": record["trajectory_id"],
                        "steps": [turn.index for turn in window],
                        "step_keys": [f"{record['trajectory_id']}:{turn.index}" for turn in window],
                        "original_cost": original_cost,
                        "estimated_saving": estimated_saving,
                    }
                    item["occurrences"].append(occurrence)
                    item["trajectory_ids"].add(record["trajectory_id"])

        candidates = []
        for item in patterns.values():
            support = len(item["trajectory_ids"])
            occurrences = len(item["occurrences"])
            if support < 2 and occurrences < 3:
                continue
            schema_tokens = 55 + item["size"] * 22
            gross = sum(occ["estimated_saving"] for occ in item["occurrences"])
            overhead = self.price_model.schema_overhead(schema_tokens, self.projected_registry_turns)
            candidates.append({
                "signature": item["signature"],
                "size": item["size"],
                "support_trajectories": support,
                "occurrence_count": occurrences,
                "schema_overhead_tokens": schema_tokens,
                "gross_estimated_saving": gross,
                "projected_schema_cost": overhead,
                "net_estimated_gain": gross - overhead,
                "occurrences": item["occurrences"],
                "contract_hint": self._contract_hint(item["signature"]),
            })

        # Budgeted greedy coverage: overlapping occurrences only contribute
        # savings for turn nodes not already covered by a selected macro.
        selected: List[dict] = []
        covered: set = set()
        budget = self.registry_budget_tokens
        remaining = list(candidates)
        while remaining and len(selected) < self.max_candidates:
            ranked = []
            for candidate in remaining:
                if candidate["schema_overhead_tokens"] > budget:
                    continue
                marginal = 0.0
                for occurrence in candidate["occurrences"]:
                    keys = set(occurrence["step_keys"])
                    fraction = len(keys - covered) / max(1, len(keys))
                    marginal += occurrence["estimated_saving"] * fraction
                marginal -= candidate["projected_schema_cost"]
                ranked.append((marginal / max(1, candidate["schema_overhead_tokens"]), marginal, candidate))
            if not ranked:
                break
            _, marginal, best = max(ranked, key=lambda item: (item[0], item[1]))
            if marginal <= 0:
                break
            best = copy.deepcopy(best)
            best["marginal_net_gain"] = marginal
            selected.append(best)
            budget -= best["schema_overhead_tokens"]
            for occurrence in best["occurrences"]:
                covered.update(occurrence["step_keys"])
            remaining = [candidate for candidate in remaining if candidate["signature"] != best["signature"]]
        return selected

    @staticmethod
    def _contract_hint(signature: str) -> dict:
        families = []
        for part in signature.split(" -> "):
            _, _, family = part.partition(":")
            families.extend(piece for piece in family.split("+") if piece)
        return {
            "inputs": ["query/path/test selector as applicable"],
            "output": "bounded structured result",
            "preconditions": ["repository path exists"],
            "state_effects": "derive from write nodes; empty for read-only motifs",
            "tool_families": list(dict.fromkeys(families)),
        }


class V7SampleBuilder:
    name = "v7-provenance-samples"

    def __init__(self, config_path: Optional[str], *, beam_width: int = 32,
                 top_k: int = 3, max_observation_chars: int = 500,
                 registry_budget_tokens: int = DEFAULT_REGISTRY_BUDGET_TOKENS,
                 max_macro_candidates: int = DEFAULT_MAX_MACRO_CANDIDATES,
                 projected_registry_turns: int = 100):
        self.price_model = PriceModel.from_config(config_path)
        self.graph_builder = ProvenanceGraphBuilderV7(
            self.price_model,
            beam_width=beam_width,
            top_k=top_k,
            max_observation_chars=max_observation_chars,
        )
        self.macro_miner = MacroMinerV7(
            self.price_model,
            registry_budget_tokens=registry_budget_tokens,
            max_candidates=max_macro_candidates,
            projected_registry_turns=projected_registry_turns,
        )

    @staticmethod
    def find_trajectory_files(result_dir, task=None) -> List[Path]:
        files = sorted(Path(result_dir).glob("**/agent/trajectory.json"))
        return [path for path in files if not task or task in str(path)]

    def build_dir(self, result_dir, task=None) -> List[Path]:
        result_dir = Path(result_dir)
        records = []
        failures = []
        for path in self.find_trajectory_files(result_dir, task):
            try:
                records.append(self.graph_builder.build(path))
            except Exception as exc:  # noqa: BLE001 - preserve other cases
                logger.exception("[v7] failed to build provenance graph for %s: %s", path, exc)
                failures.append(str(path))
        catalog = self.macro_miner.mine(records)
        paths = []
        by_trajectory: Dict[str, Dict[str, dict]] = defaultdict(dict)
        for macro in catalog:
            occurrences_by_trajectory: Dict[str, List[List[int]]] = defaultdict(list)
            for occurrence in macro["occurrences"]:
                occurrences_by_trajectory[occurrence["trajectory_id"]].append(occurrence["steps"])
            for trajectory_id, local_occurrences in occurrences_by_trajectory.items():
                by_trajectory[trajectory_id][macro["signature"]] = {
                    key: value for key, value in macro.items() if key != "occurrences"
                } | {"local_occurrences": local_occurrences}

        for record in records:
            turns = record.pop("_turns")
            graph = record.pop("graph")
            graph_path = Path(record["source_trajectory"]).with_name("provenance_graph_v7.json")
            graph_path.write_text(json.dumps(graph, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            record["provenance_graph_path"] = str(graph_path)
            record["macro_candidates"] = sorted(
                by_trajectory.get(record["trajectory_id"], {}).values(),
                key=lambda item: -item.get("marginal_net_gain", 0),
            )
            sample_path = Path(record["source_trajectory"]).with_name("v7_evolve_sample.json")
            sample_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            paths.append(sample_path)

        catalog_path = result_dir / "v7_macro_catalog.json"
        catalog_path.write_text(json.dumps({
            "schema_version": "evolve-v7-macro-catalog-1",
            "cost_model": asdict(self.price_model),
            "selected_macros": catalog,
            "failed_trajectories": failures,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        logger.info("[v7] built %d samples, %d macros -> %s", len(paths), len(catalog), catalog_path)
        return paths

    run = build_dir


# ---------------------------------------------------------------------------
# Compact evolve prompt
# ---------------------------------------------------------------------------


class EvolvePromptBuilderV7:
    HEADER = """# Evolve task (v7: validated graph-macro evidence)

You evolve native function tools for a downstream mini-swe-agent. Edit exactly:
`tools.json`, `executor.py`, and `instruction.md` in the current directory.

Goal: reduce measured API cost without sacrificing benchmark correctness.
Evidence below is outcome-anchored. A `structurally_valid` slice is a candidate,
not proof that removed observations were unnecessary. Prefer repeated, high-gain
macro evidence. Do not create a tool from a one-off path or unvalidated guess.

Tool contract:
- `tools.json` is a JSON list of function schemas: name, one-sentence description,
  and JSON-schema parameters.
- `executor.py` defines `run_tool(action, cwd=None, timeout=120)` and always returns
  `{\"output\": str, \"returncode\": int, \"exception_info\": str}`.
- Keep registry and executor names synchronized; stdlib only; validate both files.
- Tools must have bounded output, explicit errors, generic parameters, and honor cwd.
- Registry descriptions have recurring prompt cost. Merge overlaps and remove tools
  whose projected gain does not exceed schema overhead or failure risk.
- Keep `instruction.md` to at most 25 short, tool-agnostic lines. Add a rule only
  when batching/failure evidence has repeated support.
- A no-op is correct when the evidence does not justify a safe new optimization.
"""

    FOOTER = """
Implement only evidence-supported changes. Then verify:
`python -c "import json; json.load(open('tools.json'))"`
`python -c "import ast; ast.parse(open('executor.py').read())"`
Do not edit prompt or sample files. Finish after saving the three target files.
"""

    def __init__(self, *, max_prompt_chars: int = DEFAULT_MAX_PROMPT_CHARS,
                 max_steps_per_sample: int = DEFAULT_MAX_STEPS_PER_SAMPLE,
                 max_observation_chars: int = 320, max_macro_candidates: int = 6):
        self.max_prompt_chars = max(4_000, int(max_prompt_chars))
        self.max_steps_per_sample = max(1, int(max_steps_per_sample))
        self.max_observation_chars = max(0, int(max_observation_chars))
        self.max_macro_candidates = max(0, int(max_macro_candidates))

    def build(self, sample_paths: Iterable[Path], cwd_name: str = ".",
              scripts_dir: Optional[Path] = None) -> str:
        parts = [self.HEADER, f"Current working directory: `{cwd_name}`."]
        body_limit = self.max_prompt_chars - len(self.FOOTER) - 1
        if scripts_dir is not None:
            current_files = self._current_files(Path(scripts_dir))
            remaining = body_limit - len("\n".join(parts)) - 1
            if remaining > 0:
                marker = "\n...<registration files truncated by v7 prompt budget>"
                if len(current_files) > remaining:
                    current_files = current_files[:max(0, remaining - len(marker))] + marker
                parts.append(current_files)
        included = 0
        for path in sample_paths:
            block = self._sample_block(Path(path), included + 1)
            current = "\n".join(parts)
            remaining = body_limit - len(current) - 1
            if remaining <= 200:
                break
            if len(block) > remaining:
                if included == 0:
                    marker = "\n...<sample block truncated by v7 prompt budget>"
                    block = block[:max(0, remaining - len(marker))] + marker
                else:
                    break
            parts.append(block)
            included += 1
        if included == 0:
            notice = "\n# Evidence\nNo complete sample fit the configured prompt budget. Make no changes."
            remaining = body_limit - len("\n".join(parts)) - 1
            if remaining > 0:
                parts.append(notice[:remaining])
        return "\n".join(parts) + "\n" + self.FOOTER

    @staticmethod
    def _current_files(scripts_dir: Path) -> str:
        lines = ["\n# Current registration files"]
        caps = {"tools.json": 1_500, "executor.py": 2_800, "instruction.md": 1_200}
        for name, cap in caps.items():
            path = scripts_dir / name
            lines.append(f"\n## {name}")
            if not path.exists():
                lines.append("(missing; create it)")
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except OSError as exc:
                lines.append(f"(read failed: {exc})")
                continue
            lines.append(_clip(text, cap))
        return "\n".join(lines)

    def _sample_block(self, path: Path, number: int) -> str:
        sample = _read_json(path)
        outcome = sample.get("outcome") or {}
        slice_data = sample.get("slice") or {}
        graph = sample.get("graph_summary") or {}
        cost_model = sample.get("cost_model") or {}
        lines = [
            f"\n# Evidence {number}: {sample.get('trajectory_id', path.parent.parent.name)}",
            f"Source: {path}",
            f"Task: {_clip(sample.get('task_summary', ''), 700)}",
            "Outcome/graph: " + json.dumps({
                "eligible": outcome.get("eligible_for_evolve"),
                "verifier_pass": outcome.get("verifier_pass"),
                "write_anchors": outcome.get("write_anchor_steps"),
                "validation": slice_data.get("validation_level"),
                "anchor_mode": graph.get("anchor_mode"),
                "turns": graph.get("turn_count"),
            }, ensure_ascii=False),
            "Cost: " + json.dumps({
                "unit": cost_model.get("unit"),
                "original": slice_data.get("original_cost"),
                "selected": slice_data.get("selected_cost"),
                "estimated_saving": slice_data.get("estimated_saving"),
                "ratio": slice_data.get("cost_ratio"),
            }, ensure_ascii=False),
        ]
        if not outcome.get("eligible_for_evolve"):
            lines.append("This trajectory is not correctness-eligible. Do not learn tools, batching, or patch strategy from it.")
            failures = sample.get("failure_patterns") or []
            if failures:
                lines.append("\n## Repeated failure motifs (instruction evidence only)")
                lines.extend(json.dumps(item, ensure_ascii=False) for item in failures[:4])
            else:
                lines.append("No repeated failure motif; this sample provides no actionable evidence.")
            return "\n".join(lines)

        steps = sample.get("steps") or []
        selected_steps = [step for step in steps if step.get("selected")]
        selected_steps = self._select_steps_for_prompt(selected_steps)
        lines.append("\n## Outcome-support slice (candidate, not fabricated ideal trajectory)")
        for step in selected_steps:
            flags = []
            if step.get("outcome_anchor"):
                flags.append("OUTCOME_ANCHOR")
            if step.get("evidence_terminal"):
                flags.append("EVIDENCE")
            lines.append(
                f"Step {step.get('index')} [{step.get('signature')}; cost={step.get('cost')}; "
                f"{'/'.join(flags) or 'support'}]\n"
                f"Action: {_clip(step.get('action', ''), 460)}\n"
                f"Observation: {_clip(step.get('observation', ''), self.max_observation_chars)}"
            )
        if len([step for step in steps if step.get("selected")]) > len(selected_steps):
            lines.append("(remaining selected support steps omitted by per-sample display cap)")

        lines += [
            "\n## Removed-cost aggregate",
            json.dumps(sample.get("pruned_summary") or {}, ensure_ascii=False),
        ]
        macros = (sample.get("macro_candidates") or [])[:self.max_macro_candidates]
        if macros:
            lines.append("\n## Selected repeated graph-macro evidence")
            for macro in macros:
                lines.append(json.dumps({
                    "signature": macro.get("signature"),
                    "local_occurrences": macro.get("local_occurrences"),
                    "support_trajectories": macro.get("support_trajectories"),
                    "occurrence_count": macro.get("occurrence_count"),
                    "marginal_net_gain": macro.get("marginal_net_gain"),
                    "schema_overhead_tokens": macro.get("schema_overhead_tokens"),
                    "contract_hint": macro.get("contract_hint"),
                }, ensure_ascii=False))
        batching = sample.get("batching_candidates") or []
        if batching:
            lines.append("\n## Safe batching candidates")
            lines.extend(json.dumps(item, ensure_ascii=False) for item in batching[:4])
        failures = sample.get("failure_patterns") or []
        if failures:
            lines.append("\n## Repeated failure motifs")
            lines.extend(json.dumps(item, ensure_ascii=False) for item in failures[:4])
        return "\n".join(lines)

    def _select_steps_for_prompt(self, steps: List[dict]) -> List[dict]:
        if len(steps) <= self.max_steps_per_sample:
            return steps
        mandatory = [step for step in steps if step.get("outcome_anchor") or step.get("evidence_terminal")]
        remaining = [step for step in steps if step not in mandatory]
        remaining.sort(key=lambda step: float(step.get("cost", 0) or 0), reverse=True)
        chosen = mandatory + remaining[:max(0, self.max_steps_per_sample - len(mandatory))]
        return sorted({int(step["index"]): step for step in chosen}.values(), key=lambda step: int(step["index"]))


class MiniSweAgentRunnerV7(MiniSweAgentRunner):
    """Persist the prompt even in dry-run mode so prompt size is inspectable."""

    def run(self, prompt: str, prompt_path: Path, output_path: Path, cwd: Path) -> None:
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text(prompt, encoding="utf-8")
        if self.dry_run:
            logger.info("[v7] DRY_RUN prompt saved: %s (%d chars)", prompt_path, len(prompt))
            return
        super().run(prompt, prompt_path, output_path, cwd)


class V7ScriptEvolver(ScriptEvolver):
    def run(self, result_dir, task=None) -> Path:
        # A dry-run materializes prompts only. Preserve existing resume state,
        # but remove sentinels created by this invocation so a later real run is
        # not incorrectly treated as complete.
        output_dir = self.output_dir or (Path(result_dir).resolve() / "evolve_logs")
        existing = set(output_dir.glob("evolve_batch_*.done")) if output_dir.exists() else set()
        old_resume = self.resume
        if getattr(self.runner, "dry_run", False):
            self.resume = False
        try:
            result = super().run(result_dir, task=task)
        finally:
            self.resume = old_resume
        if getattr(self.runner, "dry_run", False):
            for sentinel in set(result.glob("evolve_batch_*.done")) - existing:
                sentinel.unlink(missing_ok=True)
        return result

    def find_samples(self, result_dir, task=None) -> List[Path]:
        files = sorted(Path(result_dir).glob("**/agent/v7_evolve_sample.json"))
        if task:
            matched = [path for path in files if task in path.parent.parent.name]
            files = matched or [path for path in files if task in str(path)]
        return sorted(files, key=_sample_priority)


class EvolveAgentV7(EvolveAgent):
    def __init__(self, *args, beam_width: int = 32, top_k: int = 3,
                 registry_budget_tokens: int = DEFAULT_REGISTRY_BUDGET_TOKENS,
                 max_macro_candidates: int = DEFAULT_MAX_MACRO_CANDIDATES,
                 projected_registry_turns: int = 100,
                 max_prompt_chars: int = DEFAULT_MAX_PROMPT_CHARS,
                 max_steps_per_sample: int = DEFAULT_MAX_STEPS_PER_SAMPLE,
                 **kwargs):
        super().__init__(*args, **kwargs)
        self.beam_width = int(beam_width)
        self.top_k = int(top_k)
        self.registry_budget_tokens = int(registry_budget_tokens)
        self.max_macro_candidates = int(max_macro_candidates)
        self.projected_registry_turns = int(projected_registry_turns)
        self.max_prompt_chars = int(max_prompt_chars)
        self.max_steps_per_sample = int(max_steps_per_sample)

    def contrastive(self, run_dir: Path, task: Optional[str] = None) -> None:
        logger.info("[v7 evolve] build provenance/slice/macro samples from %s", run_dir)
        V7SampleBuilder(
            self.config_path,
            beam_width=self.beam_width,
            top_k=self.top_k,
            max_observation_chars=self.max_observation_chars,
            registry_budget_tokens=self.registry_budget_tokens,
            max_macro_candidates=self.max_macro_candidates,
            projected_registry_turns=self.projected_registry_turns,
        ).run(run_dir, task=task)

    def evolve(self, run_dir: Path, task: Optional[str] = None) -> Path:
        logger.info("[v7 evolve] evolve compact graph macros from %s -> %s", run_dir, self.scripts_dir)
        # Keep resume sentinels scoped to one rollout. Reusing the same
        # evolve_logs directory across cycles would make cycle 2 skip batch ids
        # already completed for cycle 1 even though their samples are different.
        rollout_log_dir = self.output_dir / Path(run_dir).resolve().name
        evolver = V7ScriptEvolver(
            scripts_dir=self.scripts_dir,
            runner=MiniSweAgentRunnerV7(
                mini_swe_agent_dir=self.mini_swe_agent_dir,
                llm_config=self.config_path,
                dry_run=self.dry_run,
            ),
            prompt_builder=EvolvePromptBuilderV7(
                max_prompt_chars=self.max_prompt_chars,
                max_steps_per_sample=self.max_steps_per_sample,
                max_observation_chars=min(400, self.max_observation_chars),
                max_macro_candidates=self.max_macro_candidates,
            ),
            batch_size=self.batch_size,
            output_dir=rollout_log_dir,
            resume=True,
        )
        output_dir = evolver.run(run_dir, task=task)
        self.refresh_registration()
        return output_dir


# ---------------------------------------------------------------------------
# V7 cycle: reuse v6 rollout/runtime, keep reports and run ids separate
# ---------------------------------------------------------------------------


@dataclass
class V7Report:
    benchmark: str
    n_cycles: int
    scripts_dir: str
    cycles: List[CycleReport] = field(default_factory=list)


class EvolveV7Cycle(EvolveV6Cycle):
    def __init__(self, benchmark: str, config_path, scripts_dir, *,
                 eval_cases_file: Optional[str] = None, baseline_dir: Optional[str] = None,
                 work_dir: Optional[str] = None, mini_swe_agent_dir: str = str(DEFAULT_MINI_SWE_AGENT),
                 n_cycles: int = DEFAULT_N_CYCLES, n_tasks: int = 1000,
                 n_concurrent: int = 8, n_attempts: int = 1,
                 batch_size: int = DEFAULT_EVOLVE_BATCH_SIZE,
                 max_observation_chars: int = 800, workers: int = 8,
                 dry_run: bool = False, beam_width: int = 32, top_k: int = 3,
                 registry_budget_tokens: int = DEFAULT_REGISTRY_BUDGET_TOKENS,
                 max_macro_candidates: int = DEFAULT_MAX_MACRO_CANDIDATES,
                 projected_registry_turns: int = 100,
                 max_prompt_chars: int = DEFAULT_MAX_PROMPT_CHARS,
                 max_steps_per_sample: int = DEFAULT_MAX_STEPS_PER_SAMPLE):
        super().__init__(
            benchmark=benchmark,
            config_path=config_path,
            scripts_dir=scripts_dir,
            eval_cases_file=eval_cases_file,
            baseline_dir=baseline_dir,
            work_dir=work_dir or str(DEFAULT_WORK_DIR / benchmark),
            mini_swe_agent_dir=mini_swe_agent_dir,
            n_cycles=n_cycles,
            n_tasks=n_tasks,
            n_concurrent=n_concurrent,
            n_attempts=n_attempts,
            batch_size=batch_size,
            max_observation_chars=max_observation_chars,
            workers=workers,
            dry_run=dry_run,
        )
        self.evolve_agent = EvolveAgentV7(
            self.scripts_dir,
            self.config_path,
            self.mini_swe_agent_dir,
            batch_size=batch_size,
            max_observation_chars=max_observation_chars,
            workers=workers,
            output_dir=self.work_dir / "evolve_logs",
            dry_run=dry_run,
            beam_width=beam_width,
            top_k=top_k,
            registry_budget_tokens=registry_budget_tokens,
            max_macro_candidates=max_macro_candidates,
            projected_registry_turns=projected_registry_turns,
            max_prompt_chars=max_prompt_chars,
            max_steps_per_sample=max_steps_per_sample,
        )

    def run(self) -> V7Report:
        report = V7Report(self.benchmark, self.n_cycles, str(self.scripts_dir))
        logger.info("[v7] start: benchmark=%s cycles=%d cases=%d prompt_budget=%d",
                    self.benchmark, self.n_cycles, len(self.case_ids),
                    self.evolve_agent.max_prompt_chars)
        for cycle in range(1, self.n_cycles + 1):
            logger.info("[v7] === cycle %d/%d ===", cycle, self.n_cycles)
            rollout = self._do_rollout(cycle)
            annotated = self._safe(self.evolve_agent.annotate, rollout.run_dir, label="annotate")
            contrastive_built = self._safe(
                self.evolve_agent.contrastive, rollout.run_dir, label="provenance/contrastive"
            )
            evolved = self._safe(self.evolve_agent.evolve, rollout.run_dir, label="evolve")
            report.cycles.append(CycleReport(
                cycle=cycle,
                rollout=rollout,
                annotated=annotated,
                contrastive_built=contrastive_built,
                evolved=evolved,
            ))
            self._save_report(report)
        return report

    def _do_rollout(self, cycle: int) -> RolloutResult:
        if cycle == 1 and self.baseline_dir and self.baseline_dir.exists():
            logger.info("[v7] cycle 1 reusing baseline trajectories: %s", self.baseline_dir)
            return RolloutResult(self.baseline_dir, "baseline", 1, len(self.case_ids))
        run_id = f"v7c{cycle}-{self.benchmark}-{os.getpid()}"
        return self.rollout_agent.rollout(
            self.scripts_dir, self.case_ids, run_id, cycle, dry_run=self.dry_run
        )

    def _save_report(self, report: V7Report) -> None:
        path = self.work_dir / "v7_report.json"
        path.write_text(json.dumps({
            "benchmark": report.benchmark,
            "n_cycles": report.n_cycles,
            "scripts_dir": report.scripts_dir,
            "prompt_budget_chars": self.evolve_agent.max_prompt_chars,
            "cycles": [
                {
                    "cycle": cycle.cycle,
                    "run_dir": str(cycle.rollout.run_dir),
                    "run_id": cycle.rollout.run_id,
                    "n_cases": cycle.rollout.n_cases,
                    "annotated": cycle.annotated,
                    "provenance_samples_built": cycle.contrastive_built,
                    "evolved": cycle.evolved,
                    "notes": cycle.notes,
                }
                for cycle in report.cycles
            ],
        }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Prompt dry-run and CLI
# ---------------------------------------------------------------------------


def _prompt_stats(prompt: str) -> dict:
    return {
        "characters": len(prompt),
        "lines": prompt.count("\n") + 1,
        "estimated_tokens_chars_div_4": math.ceil(len(prompt) / 4),
    }


def build_prompt_comparison(result_dir: Path, config_path: str, scripts_dir: Path,
                            output_dir: Path, *, batch_size: int = DEFAULT_EVOLVE_BATCH_SIZE,
                            max_observation_chars: int = 800,
                            max_prompt_chars: int = DEFAULT_MAX_PROMPT_CHARS,
                            max_steps_per_sample: int = DEFAULT_MAX_STEPS_PER_SAMPLE,
                            compare_v6: bool = True,
                            registry_budget_tokens: int = DEFAULT_REGISTRY_BUDGET_TOKENS,
                            max_macro_candidates: int = DEFAULT_MAX_MACRO_CANDIDATES) -> dict:
    result_dir = Path(result_dir).resolve()
    output_dir = Path(output_dir).resolve()
    scripts_dir = Path(scripts_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    scripts_dir.mkdir(parents=True, exist_ok=True)

    v7_paths = V7SampleBuilder(
        config_path,
        max_observation_chars=max_observation_chars,
        registry_budget_tokens=registry_budget_tokens,
        max_macro_candidates=max_macro_candidates,
    ).run(result_dir)
    selected_v7 = sorted(v7_paths, key=_sample_priority)[:max(1, int(batch_size))]
    if not selected_v7:
        raise ValueError(f"no annotated trajectories found under {result_dir}")
    v7_prompt = EvolvePromptBuilderV7(
        max_prompt_chars=max_prompt_chars,
        max_steps_per_sample=max_steps_per_sample,
        max_observation_chars=min(400, max_observation_chars),
        max_macro_candidates=max_macro_candidates,
    ).build(selected_v7, cwd_name=scripts_dir.name, scripts_dir=scripts_dir)
    v7_path = output_dir / "v7_initial_prompt.md"
    v7_path.write_text(v7_prompt, encoding="utf-8")
    report = {
        "result_dir": str(result_dir),
        "sample_count": len(selected_v7),
        "v7": {"path": str(v7_path), **_prompt_stats(v7_prompt)},
    }

    if compare_v6:
        with tempfile.TemporaryDirectory(prefix="evolve-v7-v6-compare-") as temp:
            temp_dir = Path(temp)
            v6_sample_paths = []
            for index, v7_sample_path in enumerate(selected_v7, start=1):
                v7_sample = _read_json(v7_sample_path)
                trajectory = _read_json(Path(v7_sample["source_trajectory"]))
                v6_sample = {
                    "positive_sample": ContrastiveSampleBuilder._build_positive_sample(trajectory),
                    "negative_sample": trajectory,
                }
                path = temp_dir / f"contrastive_{index}.json"
                path.write_text(json.dumps(v6_sample, ensure_ascii=False), encoding="utf-8")
                v6_sample_paths.append(path)
            v6_prompt = EvolvePromptBuilderV6(
                serializer=TrajectorySerializer(
                    max_observation_chars=max_observation_chars,
                    max_action_chars=1_000,
                )
            ).build(v6_sample_paths, cwd_name=scripts_dir.name, scripts_dir=scripts_dir)
        v6_path = output_dir / "v6_initial_prompt.md"
        v6_path.write_text(v6_prompt, encoding="utf-8")
        v6_stats = {"path": str(v6_path), **_prompt_stats(v6_prompt)}
        report["v6"] = v6_stats
        report["comparison"] = {
            "v7_to_v6_character_ratio": round(len(v7_prompt) / max(1, len(v6_prompt)), 4),
            "character_reduction": len(v6_prompt) - len(v7_prompt),
            "estimated_token_reduction": (
                _prompt_stats(v6_prompt)["estimated_tokens_chars_div_4"]
                - _prompt_stats(v7_prompt)["estimated_tokens_chars_div_4"]
            ),
        }
    report_path = output_dir / "prompt_comparison.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    logger.info("[v7] prompt comparison -> %s", report_path)
    return report


def _add_run_args(parser: argparse.ArgumentParser) -> None:
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
    parser.add_argument("--batch-size", type=int, default=DEFAULT_EVOLVE_BATCH_SIZE)
    parser.add_argument("--max-observation-chars", type=int, default=800)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--beam-width", type=int, default=32)
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--registry-budget-tokens", type=int, default=DEFAULT_REGISTRY_BUDGET_TOKENS)
    parser.add_argument("--max-macro-candidates", type=int, default=DEFAULT_MAX_MACRO_CANDIDATES)
    parser.add_argument("--projected-registry-turns", type=int, default=100)
    parser.add_argument("--max-prompt-chars", type=int, default=DEFAULT_MAX_PROMPT_CHARS)
    parser.add_argument("--max-steps-per-sample", type=int, default=DEFAULT_MAX_STEPS_PER_SAMPLE)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--log-file", default=None)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(
        description="Evolve v7: outcome-anchored provenance slicing and compact graph-macro evolution."
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_run = sub.add_parser("run", help="run the V7 rollout/evolve cycle")
    _add_run_args(p_run)

    p_samples = sub.add_parser("build-samples", help="build V7 graph/slice/macro samples only")
    p_samples.add_argument("result_dir")
    p_samples.add_argument("--config", default=str(ROOT / "_config" / "deepseekv4_flash.yaml"))
    p_samples.add_argument("--beam-width", type=int, default=32)
    p_samples.add_argument("--top-k", type=int, default=3)
    p_samples.add_argument("--max-observation-chars", type=int, default=800)
    p_samples.add_argument("--registry-budget-tokens", type=int, default=DEFAULT_REGISTRY_BUDGET_TOKENS)
    p_samples.add_argument("--max-macro-candidates", type=int, default=DEFAULT_MAX_MACRO_CANDIDATES)
    p_samples.add_argument("--projected-registry-turns", type=int, default=100)
    p_samples.add_argument("--task", default=None)
    p_samples.add_argument("--log-file", default=None)

    p_prompt = sub.add_parser("prompt-dry-run", help="build V7 prompt and optionally compare V6")
    p_prompt.add_argument("result_dir")
    p_prompt.add_argument("--config", default=str(ROOT / "_config" / "deepseekv4_flash.yaml"))
    p_prompt.add_argument("--scripts-dir", default=str(DEFAULT_SCRIPTS_DIR))
    p_prompt.add_argument("--output-dir", default=str(DEFAULT_WORK_DIR / "prompt_dry_run"))
    p_prompt.add_argument("--batch-size", type=int, default=DEFAULT_EVOLVE_BATCH_SIZE)
    p_prompt.add_argument("--max-observation-chars", type=int, default=800)
    p_prompt.add_argument("--max-prompt-chars", type=int, default=DEFAULT_MAX_PROMPT_CHARS)
    p_prompt.add_argument("--max-steps-per-sample", type=int, default=DEFAULT_MAX_STEPS_PER_SAMPLE)
    p_prompt.add_argument("--registry-budget-tokens", type=int, default=DEFAULT_REGISTRY_BUDGET_TOKENS)
    p_prompt.add_argument("--max-macro-candidates", type=int, default=DEFAULT_MAX_MACRO_CANDIDATES)
    p_prompt.add_argument("--compare-v6", action=argparse.BooleanOptionalAction, default=True)
    p_prompt.add_argument("--log-file", default=None)

    args = parser.parse_args(argv)
    _setup_logging(getattr(args, "log_file", None))
    if args.cmd == "run":
        EvolveV7Cycle(
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
            beam_width=args.beam_width,
            top_k=args.top_k,
            registry_budget_tokens=args.registry_budget_tokens,
            max_macro_candidates=args.max_macro_candidates,
            projected_registry_turns=args.projected_registry_turns,
            max_prompt_chars=args.max_prompt_chars,
            max_steps_per_sample=args.max_steps_per_sample,
        ).run()
    elif args.cmd == "build-samples":
        V7SampleBuilder(
            args.config,
            beam_width=args.beam_width,
            top_k=args.top_k,
            max_observation_chars=args.max_observation_chars,
            registry_budget_tokens=args.registry_budget_tokens,
            max_macro_candidates=args.max_macro_candidates,
            projected_registry_turns=args.projected_registry_turns,
        ).run(args.result_dir, task=args.task)
    elif args.cmd == "prompt-dry-run":
        report = build_prompt_comparison(
            result_dir=Path(args.result_dir),
            config_path=args.config,
            scripts_dir=Path(args.scripts_dir),
            output_dir=Path(args.output_dir),
            batch_size=args.batch_size,
            max_observation_chars=args.max_observation_chars,
            max_prompt_chars=args.max_prompt_chars,
            max_steps_per_sample=args.max_steps_per_sample,
            compare_v6=args.compare_v6,
            registry_budget_tokens=args.registry_budget_tokens,
            max_macro_candidates=args.max_macro_candidates,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
