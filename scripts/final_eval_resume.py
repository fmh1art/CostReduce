#!/usr/bin/env python3
"""Persist and validate the identity of a resumable final-eval job.

Pier/Harbor can resume a job when its output directory, job name, and resolved
job configuration are unchanged.  This helper supplies the missing stable
state for ``run_evolve_experiment.sh`` and removes interrupted trial scratch
directories before the runner is invoked again.

The helper is intentionally independent from the experiment entry scripts so
it can be developed and tested without hot-replacing a currently running
experiment.  The shell integration consumes the JSON emitted by ``prepare``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = "coat.final-eval-resume.v1"
STATE_DIR_NAME = ".final_eval_resume"
MANIFEST_NAME = "state.json"
PROMPT_TEMPLATE_NAME = "evolve_prompt.j2"
TRIAL_MARKERS = (
    "config.json",
    "trial.log",
    "agent",
    "verifier",
    "artifacts",
)
IGNORED_HARNESS_PARTS = {"__pycache__", ".pytest_cache"}
IGNORED_HARNESS_SUFFIXES = {".pyc", ".pyo"}
LEGACY_PROMPT_NAME = re.compile(r"evolve_prompt\.[A-Za-z0-9_-]+")


class ResumeStateError(RuntimeError):
    """Raised when existing results are unsafe to resume."""


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _resolved(path: Path) -> Path:
    return path.expanduser().resolve()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ResumeStateError(f"cannot fingerprint file {path}: {exc}") from exc
    return digest.hexdigest()


def _harness_files(scripts_dir: Path) -> Iterable[Path]:
    for path in sorted(scripts_dir.rglob("*")):
        relative = path.relative_to(scripts_dir)
        if any(part in IGNORED_HARNESS_PARTS for part in relative.parts):
            continue
        if path.suffix in IGNORED_HARNESS_SUFFIXES:
            continue
        if path.is_file() or path.is_symlink():
            yield path


def _sha256_harness(scripts_dir: Path) -> str:
    """Hash stable harness inputs without recording their potentially secret text."""

    if not scripts_dir.is_dir():
        raise ResumeStateError(f"evolved scripts directory does not exist: {scripts_dir}")
    digest = hashlib.sha256()
    found = False
    for path in _harness_files(scripts_dir):
        found = True
        relative = path.relative_to(scripts_dir).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        if path.is_symlink():
            digest.update(b"symlink\0")
            digest.update(os.readlink(path).encode("utf-8"))
        else:
            digest.update(b"file\0")
            digest.update(_sha256_file(path).encode("ascii"))
        digest.update(b"\0")
    if not found:
        raise ResumeStateError(f"evolved scripts directory is empty: {scripts_dir}")
    return digest.hexdigest()


def _read_case_ids(path: Path) -> list[str]:
    if not path.is_file():
        raise ResumeStateError(f"final-eval case file does not exist: {path}")
    try:
        case_ids = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    except OSError as exc:
        raise ResumeStateError(f"cannot read final-eval case file {path}: {exc}") from exc
    case_ids = [case_id for case_id in case_ids if case_id]
    if not case_ids:
        raise ResumeStateError(f"final-eval case file is empty: {path}")
    duplicates = sorted(
        case_id for case_id in set(case_ids) if case_ids.count(case_id) > 1
    )
    if duplicates:
        raise ResumeStateError(
            "final-eval case file contains duplicate IDs: " + ", ".join(duplicates)
        )
    return case_ids


def _sha256_json(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _parse_extra_identity(values: list[str]) -> dict[str, str]:
    extras: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise ResumeStateError(
                f"invalid --identity {value!r}; expected KEY=VALUE"
            )
        key, item = value.split("=", 1)
        key = key.strip()
        if not key:
            raise ResumeStateError("--identity key cannot be empty")
        if key in extras:
            raise ResumeStateError(f"duplicate --identity key: {key}")
        extras[key] = item
    return dict(sorted(extras.items()))


def build_identity(
    *,
    benchmark: str,
    eval_scope: str,
    cases_file: Path,
    expected_case_count: int,
    llm_config: Path,
    scripts_dir: Path,
    results_parent: Path,
    n_concurrent: int,
    runner: str,
    extra_identity: dict[str, str] | None = None,
) -> dict[str, Any]:
    cases_file = _resolved(cases_file)
    llm_config = _resolved(llm_config)
    scripts_dir = _resolved(scripts_dir)
    results_parent = _resolved(results_parent)
    if not llm_config.is_file():
        raise ResumeStateError(f"LLM config does not exist: {llm_config}")
    case_ids = _read_case_ids(cases_file)
    if len(case_ids) != expected_case_count:
        raise ResumeStateError(
            "final-eval case count changed: "
            f"expected {expected_case_count}, found {len(case_ids)} in {cases_file}"
        )
    if n_concurrent < 1:
        raise ResumeStateError(f"n_concurrent must be positive, got {n_concurrent}")
    return {
        "benchmark": benchmark,
        "eval_scope": eval_scope,
        "case_count": len(case_ids),
        "case_ids_sha256": _sha256_json(case_ids),
        "llm_config": str(llm_config),
        "llm_config_sha256": _sha256_file(llm_config),
        "scripts_dir": str(scripts_dir),
        "scripts_sha256": _sha256_harness(scripts_dir),
        "results_parent": str(results_parent),
        "n_concurrent": n_concurrent,
        "runner": runner,
        "extra": dict(sorted((extra_identity or {}).items())),
    }


def _load_json_object(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _looks_like_trial_dir(path: Path) -> bool:
    return any((path / marker).exists() for marker in TRIAL_MARKERS) or (
        path / "result.json"
    ).exists()


def _is_complete_trial_dir(path: Path) -> bool:
    return (
        _load_json_object(path / "config.json") is not None
        and _load_json_object(path / "result.json") is not None
    )


def cleanup_incomplete_trials(job_dir: Path) -> list[str]:
    """Delete only direct child trial directories lacking a complete result.

    Pier/Harbor performs the same cleanup while opening an existing job.  Doing
    it here makes the behavior explicit and also handles a truncated JSON file,
    which the runner would otherwise fail to parse.  Metadata directories with
    no trial markers (for example ``.critiques``) are preserved.
    """

    raw_job_dir = job_dir.expanduser()
    if raw_job_dir.is_symlink():
        raise ResumeStateError(
            f"final-eval job directory cannot be a symlink: {raw_job_dir}"
        )
    job_dir = raw_job_dir.resolve()
    if not job_dir.exists():
        return []
    if not job_dir.is_dir():
        raise ResumeStateError(f"final-eval job path is not a directory: {job_dir}")
    removed: list[str] = []
    for child in sorted(job_dir.iterdir(), key=lambda item: item.name):
        if not child.is_dir() or not _looks_like_trial_dir(child):
            continue
        if _is_complete_trial_dir(child):
            continue
        if child.is_symlink():
            raise ResumeStateError(
                f"refusing to delete incomplete trial symlink: {child}"
            )
        shutil.rmtree(child)
        removed.append(child.name)
    return removed


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _manifest_mismatches(
    existing: dict[str, Any], current: dict[str, Any]
) -> list[str]:
    keys = sorted(set(existing) | set(current))
    return [key for key in keys if existing.get(key) != current.get(key)]


def _validate_run_id(run_id: Any, *, label: str) -> str:
    if not isinstance(run_id, str) or not run_id or run_id in {".", ".."}:
        raise ResumeStateError(f"{label} cannot be empty")
    if Path(run_id).name != run_id:
        raise ResumeStateError(f"{label} must be one path component: {run_id!r}")
    return run_id


def _validate_job_path(job_dir: Path, results_parent: Path) -> None:
    if job_dir.parent != results_parent:
        raise ResumeStateError(
            f"final-eval job directory escapes results parent: {job_dir}"
        )
    if job_dir.is_symlink():
        raise ResumeStateError(f"final-eval job directory cannot be a symlink: {job_dir}")
    if job_dir.exists() and not job_dir.is_dir():
        raise ResumeStateError(f"final-eval job path is not a directory: {job_dir}")


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _validate_prompt_template_path(
    value: Any, *, state_dir: Path, allow_legacy_tmp: bool
) -> Path:
    if not isinstance(value, str) or not value:
        raise ResumeStateError("resume state has no valid prompt template path")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ResumeStateError(f"prompt template path must be absolute: {path}")
    if path.is_symlink():
        raise ResumeStateError(f"prompt template path cannot be a symlink: {path}")
    path = path.resolve()
    expected = (state_dir / PROMPT_TEMPLATE_NAME).resolve()
    if path == expected:
        return path
    if (
        allow_legacy_tmp
        and path.parent == Path("/tmp")
        and LEGACY_PROMPT_NAME.fullmatch(path.name)
    ):
        return path
    raise ResumeStateError(f"unsafe prompt template path in resume state: {path}")


def _validate_adopted_job(
    job_dir: Path,
    *,
    work_dir: Path,
    results_parent: Path,
    identity: dict[str, Any],
    state_dir: Path,
) -> tuple[Path, Path]:
    """Validate and extract the stable prompt path from a pre-feature job."""

    raw_job_dir = job_dir.expanduser()
    if raw_job_dir.is_symlink():
        raise ResumeStateError(f"cannot adopt a job-directory symlink: {raw_job_dir}")
    job_dir = raw_job_dir.resolve()
    _validate_job_path(job_dir, results_parent)
    if not job_dir.is_dir():
        raise ResumeStateError(f"legacy final-eval job does not exist: {job_dir}")
    config_path = job_dir / "config.json"
    config = _load_json_object(config_path)
    if config is None:
        raise ResumeStateError(f"legacy final-eval job has invalid config: {config_path}")
    if config.get("job_name") != job_dir.name:
        raise ResumeStateError(
            f"legacy job_name does not match its directory: {config_path}"
        )
    jobs_dir = config.get("jobs_dir")
    if not isinstance(jobs_dir, str) or _resolved(Path(jobs_dir)) != results_parent:
        raise ResumeStateError(
            f"legacy job points at a different results parent: {config_path}"
        )
    if config.get("n_concurrent_trials") != identity.get("n_concurrent"):
        raise ResumeStateError(
            f"legacy job concurrency differs from requested resume: {config_path}"
        )

    datasets = config.get("datasets")
    if not isinstance(datasets, list) or len(datasets) != 1:
        raise ResumeStateError(
            f"legacy job must contain exactly one final-eval dataset: {config_path}"
        )
    dataset = datasets[0] if isinstance(datasets[0], dict) else {}
    if dataset.get("n_tasks") != identity.get("case_count"):
        raise ResumeStateError(
            f"legacy job case count differs from requested resume: {config_path}"
        )
    dataset_path = dataset.get("path")
    expected_task_dir = (work_dir / "final_eval_taskdir").resolve()
    if not isinstance(dataset_path, str) or not _is_within(
        _resolved(Path(dataset_path)), expected_task_dir
    ):
        raise ResumeStateError(
            f"legacy job was not created from this WORK_DIR: {config_path}"
        )

    agents = config.get("agents")
    if not isinstance(agents, list) or len(agents) != 1:
        raise ResumeStateError(
            f"legacy job must contain exactly one final-eval agent: {config_path}"
        )
    agent = agents[0] if isinstance(agents[0], dict) else {}
    kwargs = agent.get("kwargs") if isinstance(agent.get("kwargs"), dict) else {}
    expected_agent_config = (
        Path(str(identity["scripts_dir"])) / ".evolve_tools_v6_config.yaml"
    ).resolve()
    agent_config = kwargs.get("config_file")
    if (
        not isinstance(agent_config, str)
        or _resolved(Path(agent_config)) != expected_agent_config
    ):
        raise ResumeStateError(
            f"legacy job uses a different evolved harness config: {config_path}"
        )
    prompt_path = _validate_prompt_template_path(
        kwargs.get("prompt_template_path"),
        state_dir=state_dir,
        allow_legacy_tmp=True,
    )
    return job_dir, prompt_path


def prepare_resume(
    *,
    work_dir: Path,
    results_parent: Path,
    proposed_run_id: str,
    identity: dict[str, Any],
    cleanup_partial: bool = True,
    adopt_job_dir: Path | None = None,
) -> dict[str, Any]:
    """Create or reuse final-eval resume state and return runner parameters."""

    work_dir = _resolved(work_dir)
    results_parent = _resolved(results_parent)
    if identity.get("results_parent") != str(results_parent):
        raise ResumeStateError("identity results_parent does not match prepare input")
    proposed_run_id = _validate_run_id(proposed_run_id, label="proposed run ID")
    state_dir = work_dir / STATE_DIR_NAME
    if state_dir.is_symlink():
        raise ResumeStateError(f"resume state directory cannot be a symlink: {state_dir}")
    manifest_path = state_dir / MANIFEST_NAME
    resumed = manifest_path.exists()

    if resumed:
        manifest = _load_json_object(manifest_path)
        if manifest is None:
            raise ResumeStateError(f"resume manifest is invalid JSON: {manifest_path}")
        if manifest.get("schema_version") != SCHEMA_VERSION:
            raise ResumeStateError(
                "unsupported final-eval resume manifest schema: "
                f"{manifest.get('schema_version')!r}"
            )
        existing_identity = manifest.get("identity")
        if not isinstance(existing_identity, dict):
            raise ResumeStateError(f"resume manifest has no valid identity: {manifest_path}")
        mismatches = _manifest_mismatches(existing_identity, identity)
        if mismatches:
            raise ResumeStateError(
                "cannot resume final eval because immutable inputs changed: "
                + ", ".join(mismatches)
            )
        run_id = _validate_run_id(
            manifest.get("run_id"), label="resume manifest run_id"
        )
        expected_dir = results_parent / run_id
        _validate_job_path(expected_dir, results_parent)
        if manifest.get("final_eval_dir") != str(expected_dir):
            raise ResumeStateError(
                f"resume manifest final_eval_dir is inconsistent: {manifest_path}"
            )
        final_eval_dir = expected_dir
        prompt_template_path = _validate_prompt_template_path(
            manifest.get("prompt_template_path"),
            state_dir=state_dir,
            allow_legacy_tmp=bool(manifest.get("adopted_existing_job")),
        )
    else:
        if adopt_job_dir is not None:
            final_eval_dir, prompt_template_path = _validate_adopted_job(
                adopt_job_dir,
                work_dir=work_dir,
                results_parent=results_parent,
                identity=identity,
                state_dir=state_dir,
            )
            run_id = final_eval_dir.name
        else:
            run_id = proposed_run_id
            final_eval_dir = results_parent / run_id
            _validate_job_path(final_eval_dir, results_parent)
            if final_eval_dir.exists() and any(final_eval_dir.iterdir()):
                raise ResumeStateError(
                    "refusing to attach new resume state to a non-empty job directory: "
                    f"{final_eval_dir}"
                )
            prompt_template_path = (state_dir / PROMPT_TEMPLATE_NAME).resolve()
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "created_at": _now(),
            "run_id": run_id,
            "final_eval_dir": str(final_eval_dir),
            "prompt_template_path": str(prompt_template_path),
            "identity": identity,
            "resume_count": 0,
            "adopted_existing_job": adopt_job_dir is not None,
        }

    removed = cleanup_incomplete_trials(final_eval_dir) if cleanup_partial else []
    manifest["last_prepared_at"] = _now()
    manifest["last_cleanup_removed"] = removed
    if resumed:
        manifest["resume_count"] = int(manifest.get("resume_count", 0)) + 1
    _atomic_write_json(manifest_path, manifest)
    return {
        "schema_version": SCHEMA_VERSION,
        "resumed": resumed,
        "run_id": run_id,
        "final_eval_dir": str(final_eval_dir),
        "prompt_template_path": str(prompt_template_path),
        "manifest_path": str(manifest_path),
        "removed_incomplete_trials": removed,
        "completed_trials": count_complete_trials(final_eval_dir),
    }


def count_complete_trials(job_dir: Path) -> int:
    job_dir = _resolved(job_dir)
    if not job_dir.is_dir():
        return 0
    return sum(
        1
        for child in job_dir.iterdir()
        if child.is_dir() and _is_complete_trial_dir(child)
    )


def _prepare_command(args: argparse.Namespace) -> dict[str, Any]:
    identity = build_identity(
        benchmark=args.benchmark,
        eval_scope=args.eval_scope,
        cases_file=args.cases_file,
        expected_case_count=args.expected_case_count,
        llm_config=args.llm_config,
        scripts_dir=args.scripts_dir,
        results_parent=args.results_parent,
        n_concurrent=args.n_concurrent,
        runner=args.runner,
        extra_identity=_parse_extra_identity(args.identity),
    )
    return prepare_resume(
        work_dir=args.work_dir,
        results_parent=args.results_parent,
        proposed_run_id=args.proposed_run_id,
        identity=identity,
        cleanup_partial=not args.no_cleanup_partial,
        adopt_job_dir=args.adopt_job_dir,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser(
        "prepare", help="create or validate a resumable final-eval state"
    )
    prepare.add_argument("--work-dir", type=Path, required=True)
    prepare.add_argument("--results-parent", type=Path, required=True)
    prepare.add_argument("--proposed-run-id", required=True)
    prepare.add_argument("--benchmark", required=True)
    prepare.add_argument("--eval-scope", required=True)
    prepare.add_argument("--cases-file", type=Path, required=True)
    prepare.add_argument("--expected-case-count", type=int, required=True)
    prepare.add_argument("--llm-config", type=Path, required=True)
    prepare.add_argument("--scripts-dir", type=Path, required=True)
    prepare.add_argument("--n-concurrent", type=int, required=True)
    prepare.add_argument("--runner", required=True)
    prepare.add_argument(
        "--adopt-job-dir",
        type=Path,
        help=(
            "adopt a pre-feature Pier/Harbor job; its config must match this "
            "WORK_DIR, result parent, concurrency, case count, and harness"
        ),
    )
    prepare.add_argument(
        "--identity",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="additional runner setting that must remain unchanged",
    )
    prepare.add_argument(
        "--no-cleanup-partial",
        action="store_true",
        help="validate state without deleting interrupted trial directories",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "prepare":
            payload = _prepare_command(args)
        else:  # pragma: no cover - argparse prevents this branch
            raise ResumeStateError(f"unsupported command: {args.command}")
    except ResumeStateError as exc:
        print(f"final-eval-resume: error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
