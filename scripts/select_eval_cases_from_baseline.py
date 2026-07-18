#!/usr/bin/env python3
"""Lock final-eval cases to the cases actually present in a no-evolve run."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def extract_case_ids(run_dir: Path) -> list[str]:
    case_ids: list[str] = []
    seen: set[str] = set()
    for trial in sorted(path for path in run_dir.iterdir() if path.is_dir()):
        config_path = trial / "config.json"
        if not config_path.is_file():
            continue
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot parse baseline config {config_path}: {exc}") from exc
        task = config.get("task") if isinstance(config.get("task"), dict) else {}
        task_path = task.get("path")
        if not task_path:
            raise ValueError(f"baseline trial does not record task.path: {config_path}")
        case_id = Path(str(task_path)).name
        if case_id in seen:
            raise ValueError(f"duplicate case {case_id!r} in no-evolve baseline {run_dir}")
        seen.add(case_id)
        case_ids.append(case_id)
    return case_ids


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--expected-count", type=int, required=True)
    args = parser.parse_args()
    if not args.run_dir.is_dir():
        parser.error(f"no-evolve baseline directory does not exist: {args.run_dir}")
    case_ids = extract_case_ids(args.run_dir)
    if len(case_ids) != args.expected_count:
        parser.error(
            f"no-evolve baseline must contain exactly {args.expected_count} cases, "
            f"found {len(case_ids)} in {args.run_dir}"
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(case_ids) + "\n", encoding="utf-8")
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps({
        "schema_version": "evolve.baseline-locked-eval.v1",
        "policy": "exact-no-evolve-run-cases",
        "baseline_run": str(args.run_dir.resolve()),
        "requested": args.expected_count,
        "selected": [{"case_id": case_id} for case_id in case_ids],
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("\n".join(case_ids))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
