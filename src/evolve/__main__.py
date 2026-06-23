"""CLI for the script evolution pipeline.

Examples:
    # Run the full pipeline (annotate -> contrastive -> evolve).
    python -m src.evolve run RESULT_DIR \\
        --config _config/deepseekv4_flash.yaml \\
        --scripts-dir .evolve_scripts

    # Run individual stages.
    python -m src.evolve annotate RESULT_DIR --workers 4
    python -m src.evolve contrastive RESULT_DIR
    python -m src.evolve evolve RESULT_DIR --batch-size 5
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from .annotator import TrajectoryAnnotator
from .contrastive import ContrastiveSampleBuilder
from .evolver import ScriptEvolver
from .pipeline import ScriptEvolvePipeline


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MINI_SWE_AGENT = ROOT / "agent" / "mini-swe-agent"
DEFAULT_SCRIPTS_DIR = ROOT / ".evolve_scripts"
DEFAULT_LLM_CONFIG = ROOT / "_config" / "deepseekv4_flash.yaml"


def _setup_logging(log_file=None):
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


def _add_common(parser):
    parser.add_argument("result_dir", help="result/run directory containing */agent/trajectory.json")
    parser.add_argument("--task", help="optional task id/name substring filter")
    parser.add_argument("--log-file", help="optional log file path")


def _add_config(parser):
    if not any(a.dest == "config" for a in parser._actions):
        parser.add_argument("--config", default=str(DEFAULT_LLM_CONFIG))


def _add_annotate(parser):
    _add_config(parser)
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="total parallel LLM calls across trajectory files and steps",
    )
    parser.add_argument(
        "--retry-failed",
        type=int,
        default=1,
        help="retry failed trajectory files after the first pass",
    )


def _add_evolve(parser):
    _add_config(parser)
    parser.add_argument("--scripts-dir", default=str(DEFAULT_SCRIPTS_DIR))
    parser.add_argument("--mini-swe-agent-dir", default=str(DEFAULT_MINI_SWE_AGENT))
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument("--max-observation-chars", type=int, default=500)
    parser.add_argument(
        "--output-dir",
        help="where to save evolve prompts and mini-swe-agent trajectories",
    )
    parser.add_argument("--dry-run", action="store_true")


def _make_annotator(args) -> TrajectoryAnnotator:
    return TrajectoryAnnotator(
        config_path=args.config,
        workers=args.workers,
        retry_failed=args.retry_failed,
    )


def _make_evolver(args) -> ScriptEvolver:
    return ScriptEvolver(
        mini_swe_agent_dir=args.mini_swe_agent_dir,
        llm_config=args.config,
        scripts_dir=args.scripts_dir,
        batch_size=args.batch_size,
        max_observation_chars=args.max_observation_chars,
        dry_run=args.dry_run,
    )


def main():
    parser = argparse.ArgumentParser(
        description="Script evolution pipeline (annotate -> contrastive -> evolve)."
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_annotate = sub.add_parser("annotate", help="annotate trajectory dependencies")
    _add_common(p_annotate)
    _add_annotate(p_annotate)

    p_contrast = sub.add_parser("contrastive", help="build contrastive samples")
    _add_common(p_contrast)

    p_evolve = sub.add_parser("evolve", help="evolve scripts from contrastive samples")
    _add_common(p_evolve)
    _add_evolve(p_evolve)

    p_run = sub.add_parser("run", help="run the full pipeline")
    _add_common(p_run)
    _add_annotate(p_run)
    _add_evolve(p_run)
    p_run.add_argument("--skip-annotate", action="store_true")
    p_run.add_argument("--skip-contrastive", action="store_true")
    p_run.add_argument("--skip-evolve", action="store_true")

    args = parser.parse_args()
    _setup_logging(getattr(args, "log_file", None))

    if args.cmd == "annotate":
        _make_annotator(args).annotate_dir(args.result_dir, task=args.task)
    elif args.cmd == "contrastive":
        ContrastiveSampleBuilder().build_dir(args.result_dir, task=args.task)
    elif args.cmd == "evolve":
        _make_evolver(args).evolve_dir(
            args.result_dir, output_dir=args.output_dir, task=args.task
        )
    elif args.cmd == "run":
        pipeline = ScriptEvolvePipeline(
            annotator=_make_annotator(args),
            contrastive_builder=ContrastiveSampleBuilder(),
            evolver=_make_evolver(args),
        )
        pipeline.run(
            args.result_dir,
            task=args.task,
            output_dir=args.output_dir,
            skip_annotate=args.skip_annotate,
            skip_contrastive=args.skip_contrastive,
            skip_evolve=args.skip_evolve,
        )


if __name__ == "__main__":
    main()
