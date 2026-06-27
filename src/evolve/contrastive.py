"""Stage 2: build contrastive samples from annotated trajectories.

For each annotated trajectory we keep only the steps reachable from the final
action through the `dependencies` graph. The reduced trajectory becomes the
`positive_sample` (minimal cost), and the original full trajectory becomes the
`negative_sample` (baseline cost) used by the script evolver.
"""

from __future__ import annotations

import copy
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class ContrastiveSampleBuilder:
    name = "contrastive"

    def find_trajectory_files(self, result_dir, task=None):
        files = sorted(Path(result_dir).glob("**/agent/trajectory.json"))
        return [p for p in files if not task or task in str(p)]

    def build_dir(self, result_dir, task=None):
        outs = []
        for path in self.find_trajectory_files(result_dir, task):
            try:
                out = self.build_file(path)
            except Exception as exc:
                logger.exception("failed to build contrastive sample for %s: %s", path, exc)
                continue
            logger.info("writing %s", out)
            outs.append(out)
        return outs

    # Stage interface
    run = build_dir

    def build_file(self, path) -> Path:
        trajectory = json.loads(Path(path).read_text(encoding="utf-8"))
        sample = {
            "positive_sample": self._build_positive_sample(trajectory),
            "negative_sample": trajectory,
        }
        out = Path(path).with_name("contrastive_sample.json")
        out.write_text(
            json.dumps(sample, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return out

    # ---------- helpers ----------

    @staticmethod
    def _is_action_step(step) -> bool:
        return bool(step.get("tool_calls") or "observation" in step or step.get("action"))

    @classmethod
    def _trace_minimal_indices(cls, dependencies):
        if not dependencies:
            # no action steps → only keep the initial context (step 0)
            return {0}
        try:
            last = max(int(k) for k in dependencies)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"invalid dependencies keys: {list(dependencies)!r}") from exc
        keep = set()
        stack = [last]
        while stack:
            i = stack.pop()
            if i in keep:
                continue
            keep.add(i)
            stack.extend(int(j) for j in dependencies.get(str(i), []))
        # step 0 (initial state before any action) is almost always required
        keep.add(0)
        return keep

    @classmethod
    def _build_positive_sample(cls, trajectory):
        dependencies = trajectory.get("dependencies")
        if dependencies is None:
            raise ValueError("trajectory has no dependencies field")

        keep = cls._trace_minimal_indices(dependencies)
        positive = copy.deepcopy(trajectory)
        action_i = 0
        positive_steps = []
        for step in trajectory.get("steps", []):
            if cls._is_action_step(step):
                action_i += 1
                if action_i in keep:
                    positive_steps.append(step)
            elif action_i == 0:
                # Non-action steps BEFORE any action step (system prompt,
                # task description, etc.) are required context — always keep.
                positive_steps.append(step)
            # Non-action steps in the middle of a trajectory are dropped by
            # default; they were not the agent's actions and not the task
            # boundary.

        positive["steps"] = positive_steps
        positive["dependencies"] = {
            str(i): dependencies[str(i)] for i in sorted(keep) if str(i) in dependencies
        }
        positive["minimal_step_indices"] = sorted(keep)
        return positive
