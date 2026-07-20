"""Deterministic metadata helpers for COAT's focused-DAG pipeline.

Functions here are stateless and fall into four groups:

1. Bash command parsing — ``extract_bash_command``, ``bash_verb``
2. Step classification — ``classify_step_meta`` produces ``{op_type, success,
   idempotent, bash_verbs, files_touched}`` for a single step
3. Phase identification — ``identify_phases`` groups consecutive action steps
   by ``op_type`` for phase-based chunk splitting
4. Minimal subgraph — ``find_anchor_step`` + ``trace_minimal_indices`` pick
   the most valuable step in a chunk and trace its dependency closure,
   filtering failed steps

The module is framework-private so COAT never imports an older versioned
evolution entrypoint.
"""

from __future__ import annotations

import json
import re
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Bash command parsing
# ---------------------------------------------------------------------------


def extract_bash_command(step: dict) -> str:
    """Return the first bash command string in a step's tool_calls."""
    tcs = step.get("tool_calls") or []
    for tc in tcs:
        if tc.get("function_name") == "bash":
            args = tc.get("arguments", {})
            if isinstance(args, dict):
                return args.get("command", "") or ""
    return ""


def bash_verb(cmd: str) -> str:
    """Extract the primary bash verb, skipping wrappers (env/sudo/cd).

    Compound commands (``a && b``, ``a; b``, ``a | b``) return the first
    non-wrapper verb. ``cd /path && sed ...`` returns ``sed`` (the cd
    wrapper segment is skipped entirely).
    """
    if not cmd:
        return ""
    for seg in re.split(r"&&|\|\||;|\|", cmd):
        tokens = seg.strip().split()
        if not tokens:
            continue
        i = 0
        while i < len(tokens) and (
            ("=" in tokens[i] and not tokens[i].startswith("-"))
            or tokens[i] in {"sudo", "env", "time", "nohup", "exec"}
        ):
            i += 1
        if i >= len(tokens):
            continue
        if tokens[i] == "cd":
            continue
        return tokens[i].rsplit("/", 1)[-1].lower()
    return ""


# ---------------------------------------------------------------------------
# Step classification — op_type / success / idempotent / files_touched
# ---------------------------------------------------------------------------


# verb → default op_type (when no special-case rule applies)
_OP_TYPE_MAP: Dict[str, str] = {
    "cat": "read", "head": "read", "tail": "read", "sed": "read",
    "grep": "read", "rg": "read", "find": "read", "ls": "read",
    "nl": "read", "wc": "read", "file": "read", "stat": "read",
    "awk": "read", "less": "read", "more": "read", "od": "read",
    "rm": "write", "mv": "write", "cp": "write", "mkdir": "write",
    "touch": "write", "chmod": "write", "chown": "write", "tee": "write",
    "ln": "write", "truncate": "write", "install": "write",
    "go": "verify", "cargo": "verify", "npm": "verify", "npx": "verify",
    "pytest": "verify", "make": "verify", "mvn": "verify", "gradle": "verify",
    "python": "verify", "python3": "verify", "ruby": "verify", "node": "verify",
    "pwd": "explore", "whoami": "explore", "uname": "explore",
    "env": "explore", "echo": "explore", "date": "explore", "which": "explore",
}

_GIT_EXPLORE_SUBCMDS = {
    "status", "branch", "log", "diff", "show", "remote", "blame",
    "ls-files", "rev-parse", "config", "stash-list",
}
_GIT_WRITE_SUBCMDS = {
    "checkout", "reset", "apply", "add", "commit", "stash", "rm",
    "mv", "init", "rebase", "merge", "cherry-pick",
}


def classify_op_type(cmd: str, verb: str) -> str:
    """Classify a bash command into ``read`` / ``write`` / ``verify`` / ``explore``.

    Special cases:
    - ``sed -i`` and ``cat > / cat >>`` are ``write`` (otherwise ``read``).
    - ``git status/branch/log/...`` are ``explore``; ``git checkout/apply/...``
      are ``write``.
    """
    if verb == "sed" and re.search(r"\bsed\b[^|]*\s-i\b", cmd):
        return "write"
    if verb in ("cat", "tee") and re.search(r">\s*>|>(?=\s*\S)", cmd):
        return "write"
    if verb == "git":
        m = re.match(r"\s*git\s+(\w+)", cmd)
        sub = m.group(1) if m else ""
        if sub in _GIT_EXPLORE_SUBCMDS:
            return "explore"
        if sub in _GIT_WRITE_SUBCMDS:
            return "write"
        return "explore"
    return _OP_TYPE_MAP.get(verb, "explore")


def extract_returncode(step: dict) -> Optional[int]:
    """Extract returncode from a step's observation. Returns None if not found."""
    obs = step.get("observation", "")
    if not isinstance(obs, dict) or not isinstance(obs.get("results"), list):
        return None
    for r in obs["results"]:
        if not isinstance(r, dict):
            continue
        content = r.get("content", "")
        if not isinstance(content, str):
            continue
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and "returncode" in parsed:
            rc = parsed["returncode"]
            if isinstance(rc, int):
                return rc
    return None


def is_idempotent(verb: str, op_type: str) -> bool:
    """A step is idempotent if re-running produces the same result.

    - ``write`` steps are never idempotent (they mutate the filesystem).
    - All other op_types are idempotent by default.
    """
    if op_type == "write":
        return False
    return True


def extract_files_touched(cmd: str) -> List[str]:
    """Extract file-like tokens from a bash command.

    Heuristic: tokens that contain ``/`` or have a file extension are
    considered file paths. Used for cross-step file dependency analysis.
    """
    if not cmd:
        return []
    skip_tokens = {"sudo", "env", "time", "nohup", "exec", "cd",
                   "&&", "||", ";", "|"}
    files: List[str] = []
    for seg in re.split(r"&&|\|\||;|\|", cmd):
        tokens = seg.strip().split()
        for tok in tokens:
            if not tok or tok.startswith("-"):
                continue
            if tok in skip_tokens:
                continue
            if "=" in tok and not tok.startswith("/"):
                continue
            if "/" in tok or re.search(r"\.\w{1,8}$", tok):
                files.append(tok.strip("'\""))
    return files


def classify_step_meta(step: dict) -> dict:
    """Classify a single step into a step_meta dict.

    Returns ``{op_type, success, idempotent, bash_verbs, files_touched}``.
    Pure rule-based (no LLM call).
    """
    cmd = extract_bash_command(step)
    verb = bash_verb(cmd)
    all_verbs: List[str] = []
    if cmd:
        for seg in re.split(r"&&|\|\||;|\|", cmd):
            v = bash_verb(seg)
            if v:
                all_verbs.append(v)
    op_type = classify_op_type(cmd, verb)
    rc = extract_returncode(step)
    success = (rc == 0) if rc is not None else True
    return {
        "op_type": op_type,
        "success": success,
        "idempotent": is_idempotent(verb, op_type),
        "bash_verbs": all_verbs,
        "files_touched": extract_files_touched(cmd),
    }


# ---------------------------------------------------------------------------
# Phase identification — group consecutive action steps by op_type
# ---------------------------------------------------------------------------


def identify_phases(
    n_action_steps: int,
    step_metas: Dict[int, dict],
    min_phase_size: int = 3,
    max_phase_size: int = 30,
) -> List[Tuple[int, int, str]]:
    """Group action steps (1..n) into phases by op_type.

    Args:
        n_action_steps: total number of action steps in trajectory
        step_metas: ``{action_num (1-based): step_meta dict}``
        min_phase_size: phases smaller than this merge into adjacent
        max_phase_size: phases larger than this split into multiple chunks

    Returns:
        List of ``(start_idx, end_idx, op_type)`` where ``start_idx`` /
        ``end_idx`` are 0-based offsets into a 0-indexed action-step list
        (``end_idx`` exclusive).
    """
    if n_action_steps <= 0:
        return []

    # Pass 1: raw group-by
    raw: List[List] = []
    cur_start = 0
    cur_op = step_metas.get(1, {}).get("op_type", "explore")
    for i in range(1, n_action_steps):
        op = step_metas.get(i + 1, {}).get("op_type", "explore")
        if op != cur_op:
            raw.append([cur_start, i, cur_op])
            cur_start = i
            cur_op = op
    raw.append([cur_start, n_action_steps, cur_op])

    # Pass 2: merge tiny phases into adjacent
    merged: List[List] = []
    for phase in raw:
        if not merged:
            merged.append(phase)
            continue
        prev = merged[-1]
        size = phase[1] - phase[0]
        if size < min_phase_size:
            # Absorb this tiny phase into prev (keep prev's op_type)
            prev[1] = phase[1]
        elif prev[1] - prev[0] < min_phase_size:
            # Prev was tiny, absorb prev into this (take this's op_type)
            prev[1] = phase[1]
            prev[2] = phase[2]
        else:
            merged.append(phase)

    # Pass 3: split large phases
    final: List[Tuple[int, int, str]] = []
    for start, end, op in merged:
        size = end - start
        if size <= max_phase_size:
            final.append((start, end, op))
        else:
            for sub_start in range(start, end, max_phase_size):
                sub_end = min(sub_start + max_phase_size, end)
                final.append((sub_start, sub_end, op))
    return final


# ---------------------------------------------------------------------------
# Minimal subgraph — anchor selection + dependency closure with failure filter
# ---------------------------------------------------------------------------


def find_anchor_step(action_steps_with_meta: List[Tuple[dict, dict]]) -> Optional[int]:
    """Find the most valuable step in a chunk for minimal subgraph anchoring.

    Priority: last successful ``verify`` > last successful ``write`` > last
    successful ``read`` > last step (regardless of success).

    Returns:
        1-based index within the chunk, or None if chunk is empty.
    """
    if not action_steps_with_meta:
        return None
    for target_op in ("verify", "write", "read"):
        for i in reversed(range(len(action_steps_with_meta))):
            _, meta = action_steps_with_meta[i]
            if meta.get("op_type") == target_op and meta.get("success"):
                return i + 1
    return len(action_steps_with_meta)


def trace_minimal_indices(
    dependencies: Dict[str, List[int]],
    anchor_idx: int,
    step_metas: Dict[str, dict],
) -> set:
    """Trace dependency closure from anchor, filtering failed steps.

    Args:
        dependencies: ``{str(action_num): [dep_action_nums]}``
        anchor_idx: 1-based action number of the anchor step
        step_metas: ``{str(action_num): step_meta dict}``

    Returns:
        Set of action_nums to keep (always includes 0 for initial state).
        Failed non-explore steps are excluded from the closure — they don't
        contribute to the anchor's outcome.
    """
    keep = {0, anchor_idx}
    stack = [anchor_idx]
    while stack:
        i = stack.pop()
        for dep in dependencies.get(str(i), []):
            try:
                dep_int = int(dep)
            except (TypeError, ValueError):
                continue
            if dep_int in keep:
                continue
            meta = step_metas.get(str(dep_int), {})
            if not meta.get("success", True) and meta.get("op_type") != "explore":
                continue
            keep.add(dep_int)
            stack.append(dep_int)
    return keep


# ---------------------------------------------------------------------------
# Observation size (cost proxy for cost_hotspot)
# ---------------------------------------------------------------------------


def observation_chars(observation) -> int:
    """Estimate observation char count (uncapped). Used as cost proxy."""
    if observation is None:
        return 0
    if isinstance(observation, str):
        return len(observation)
    if isinstance(observation, dict) and isinstance(observation.get("results"), list):
        total = 0
        for r in observation["results"]:
            if isinstance(r, dict) and "content" in r:
                content = r["content"]
                if isinstance(content, str):
                    try:
                        parsed = json.loads(content)
                        if isinstance(parsed, dict) and "output" in parsed:
                            total += len(str(parsed.get("output", "")))
                            continue
                    except json.JSONDecodeError:
                        pass
                    total += len(content)
                else:
                    total += len(str(content))
            else:
                total += len(json.dumps(r, default=str, ensure_ascii=False))
        return total
    return len(json.dumps(observation, default=str, ensure_ascii=False))
