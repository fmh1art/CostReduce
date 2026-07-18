#!/usr/bin/env python3
"""Select a deterministic, codebase-diverse evolve set from Harbor flat tasks."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python >=3.11 in supported envs
    tomllib = None


def _stable_order(value: str) -> tuple[str, str]:
    digest = hashlib.sha256(f"evolve-diverse-v1:{value}".encode()).hexdigest()
    return digest, value


def _task_metadata(task_dir: Path) -> dict:
    if tomllib is None:
        return {}
    for name in ("task.toml", "task.yaml"):
        path = task_dir / name
        if name.endswith(".toml") and path.is_file():
            try:
                return tomllib.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
                return {}
    return {}


def codebase_key(benchmark: str, task_dir: Path) -> str:
    """Return the best available repository/dataset identity for a task."""
    task_id = task_dir.name
    data = _task_metadata(task_dir)
    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    repository_url = metadata.get("repository_url")
    if isinstance(repository_url, str) and repository_url.strip():
        value = repository_url.rstrip("/").removesuffix(".git")
        return value.rsplit("/", 2)[-2] + "/" + value.rsplit("/", 1)[-1]

    if benchmark == "swebench":
        # owner__repo-instance_number -> owner/repo
        match = re.match(r"^(.+?)__(.+)-\d+$", task_id)
        if match:
            return f"{match.group(1)}/{match.group(2)}"

    parts = task_id.split("__")
    if benchmark == "dab" and len(parts) >= 2:
        return f"dataset:{parts[1]}"
    if benchmark == "datamind" and len(parts) >= 2:
        return f"dataset:{parts[0]}/{parts[1]}"

    # Unknown layouts remain usable and deterministic. Treating every task as
    # its own group maximizes diversity instead of recreating lexical head bias.
    return f"task:{task_id}"


def select_cases(task_root: Path, benchmark: str, limit: int,
                 excluded: set[str] | None = None,
                 policy: str = "diverse") -> tuple[list[str], dict[str, str]]:
    excluded = excluded or set()
    groups: dict[str, list[str]] = defaultdict(list)
    membership: dict[str, str] = {}
    for task_dir in task_root.iterdir():
        if not task_dir.is_dir():
            continue
        if task_dir.name in excluded:
            continue
        group = codebase_key(benchmark, task_dir)
        groups[group].append(task_dir.name)
        membership[task_dir.name] = group

    for values in groups.values():
        values.sort(key=_stable_order)

    if policy == "sorted":
        selected = sorted(membership)[:limit]
        return selected, membership
    if policy != "diverse":
        raise ValueError(f"unknown selection policy: {policy}")

    selected: list[str] = []
    ordered_groups = sorted(groups, key=_stable_order)
    round_index = 0
    # Round-robin gives every codebase one slot before any codebase gets a
    # second slot. The hash order is stable but avoids alphabetical bias.
    while len(selected) < limit:
        added = False
        for group in ordered_groups:
            values = groups[group]
            if round_index < len(values):
                selected.append(values[round_index])
                added = True
                if len(selected) == limit:
                    break
        if not added:
            break
        round_index += 1
    return selected, membership


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-root", type=Path, required=True)
    parser.add_argument("--benchmark", required=True)
    parser.add_argument("--limit", type=int, required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--exclude-file", type=Path)
    parser.add_argument("--policy", choices=("diverse", "sorted"), default="diverse")
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit must be positive")
    excluded: set[str] = set()
    if args.exclude_file:
        excluded = {line.strip() for line in args.exclude_file.read_text(encoding="utf-8").splitlines()
                    if line.strip() and not line.lstrip().startswith("#")}
    selected, membership = select_cases(
        args.task_root, args.benchmark, args.limit, excluded, policy=args.policy
    )
    if args.manifest:
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        args.manifest.write_text(json.dumps({
            "schema_version": "evolve.case-selection.v1",
            "policy": "codebase-round-robin" if args.policy == "diverse" else "lexical-sorted",
            "benchmark": args.benchmark,
            "task_root": str(args.task_root.resolve()),
            "requested": args.limit,
            "exclude_file": str(args.exclude_file.resolve()) if args.exclude_file else None,
            "excluded_count": len(excluded),
            "selected": [{"case_id": case_id, "codebase": membership[case_id]}
                         for case_id in selected],
            "selected_codebases": len({membership[case_id] for case_id in selected}),
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("\n".join(selected))
    return 0 if selected else 1


if __name__ == "__main__":
    raise SystemExit(main())
