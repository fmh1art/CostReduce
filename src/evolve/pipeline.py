"""Compose stages into the script-evolution pipeline.

A pipeline is just a sequence of `Stage`s. Any object exposing `name`
(str) and `run(result_dir, task=None)` qualifies, so adding a new stage
— or swapping in an alternative annotator / contrastive builder /
evolver — is a matter of building a different list of stages and
handing it to `ScriptEvolvePipeline`.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Iterable, Optional, Protocol, Sequence, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class Stage(Protocol):
    """Minimal interface a pipeline stage must satisfy."""

    name: str

    def run(self, result_dir: Path, task: Optional[str] = None) -> Any: ...


class ScriptEvolvePipeline:
    def __init__(self, stages: Sequence[Stage]):
        self.stages = list(stages)

    def run(
        self,
        result_dir,
        task: Optional[str] = None,
        skip: Iterable[str] = (),
    ) -> None:
        result_dir = Path(result_dir).resolve()
        skip = set(skip)
        total = len(self.stages)
        for i, stage in enumerate(self.stages, start=1):
            label = f"[stage {i}/{total}] {stage.name}"
            if stage.name in skip:
                logger.info("%s — skipped", label)
                continue
            logger.info("%s on %s", label, result_dir)
            stage.run(result_dir, task=task)
