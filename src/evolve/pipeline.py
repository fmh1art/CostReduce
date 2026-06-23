"""End-to-end script evolution pipeline:

  1. `TrajectoryAnnotator`  — label each step's dependencies with an LLM.
  2. `ContrastiveSampleBuilder` — derive minimal vs. original trajectories.
  3. `ScriptEvolver` — let mini-swe-agent evolve scripts + instruction.md.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from .annotator import TrajectoryAnnotator
from .contrastive import ContrastiveSampleBuilder
from .evolver import ScriptEvolver

logger = logging.getLogger(__name__)


class ScriptEvolvePipeline:
    def __init__(
        self,
        annotator: TrajectoryAnnotator,
        contrastive_builder: ContrastiveSampleBuilder,
        evolver: ScriptEvolver,
    ):
        self.annotator = annotator
        self.contrastive_builder = contrastive_builder
        self.evolver = evolver

    def run(
        self,
        result_dir,
        task: Optional[str] = None,
        output_dir: Optional[str] = None,
        skip_annotate: bool = False,
        skip_contrastive: bool = False,
        skip_evolve: bool = False,
    ):
        result_dir = Path(result_dir).resolve()
        if not skip_annotate:
            logger.info("[stage 1/3] annotate trajectories under %s", result_dir)
            self.annotator.annotate_dir(result_dir, task=task)
        if not skip_contrastive:
            logger.info("[stage 2/3] build contrastive samples under %s", result_dir)
            self.contrastive_builder.build_dir(result_dir, task=task)
        if not skip_evolve:
            logger.info("[stage 3/3] evolve scripts using contrastive samples")
            self.evolver.evolve_dir(result_dir, output_dir=output_dir, task=task)
