"""Baseline evolve：跳过 annotate / contrastive，直接把原始 trajectory 喂给 agent。

工作流程
--------

    trajectory.json  ──▶  BaselineEvolvePromptBuilder  ──▶  mini-swe-agent  ──▶  evolved scripts
    (原始 trajectory)     (直接序列化进 prompt)            (就地改 scripts)

与三段式 pipeline 的区别::

    三段式:  annotate ──▶ contrastive ──▶ evolve          (本文件不涉及前两段)
    baseline: ────────────────────────▶ evolve_baseline   (直接读 trajectory.json)

* 没有 Stage 1 (annotate)：不调 LLM 标注 step 间依赖。
* 没有 Stage 2 (contrastive)：不裁剪最小 trajectory、不做 positive/negative 拆分。
* Stage 3 直接读 ``<result_dir>/<task_id>/agent/trajectory.json``，用 ``TrajectorySerializer``
  原样序列化后塞进 prompt，让 mini-swe-agent 自己看完整 trajectory 来演化脚本。

适合作为对照基线，衡量"标注依赖 + 裁剪最小 trajectory"这两步带来的增益。

设计要点
--------
* `BaselineEvolvePromptBuilder` 继承 `EvolvePromptBuilder`，只覆盖 `HEADER` 和 `build()`，
  复用父类的 `FOOTER` / `__init__` / `serializer`。
* `BaselineScriptEvolver` 继承 `ScriptEvolver`，只覆盖 `name` 和 `find_samples()`，
  复用父类的 `run()` / `_ensure_instruction_file()` / `_batched()` / sentinel 续跑逻辑。
  `ScriptEvolver.run()` 内部通过 `self.find_samples()` 和 `self.prompt_builder.build()`
  做多态调用，所以基类一行都不用改。
* CLI 复用 `run_evolve` 里的参数 helper（``_add_common`` / ``_add_config`` / ``_add_evolve`` /
  ``_setup_logging``），保持参数语义与三段式 pipeline 一致。

用法
----
::

    # 直接跑 baseline（等价于只跑 Stage 3，但读 trajectory.json 而非 contrastive_sample.json）
    python -m src.evolve.evolve_baseline results/... \\
        --config _config/deepseekv4_flash.yaml \\
        --scripts-dir .evolve_scripts \\
        --batch-size 5

    # dry-run 看 prompt 长什么样
    python -m src.evolve.evolve_baseline results/... --dry-run

    # 只处理某个 task
    python -m src.evolve.evolve_baseline results/... --task task-foo
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import List, Optional

from .evolver import (
    EvolvePromptBuilder,
    MiniSweAgentRunner,
    ScriptEvolver,
    TrajectorySerializer,
)
from .run_evolve import (
    _add_common,
    _add_config,
    _add_evolve,
    _setup_logging,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Prompt builder — 直接序列化原始 trajectory
# ---------------------------------------------------------------------------


class BaselineEvolvePromptBuilder(EvolvePromptBuilder):
    """把每个 trajectory.json 原样序列化进 prompt，不做 contrastive 拆分。

    与 `EvolvePromptBuilder` 的区别仅在于：每个 sample 是单个 trajectory（不是
    positive/negative pair），所以 prompt 里只有一段 trajectory 而非两段。
    """

    HEADER = [
        "Here are agent execution trajectories from previous runs.",
        "Evolve the scripts and instruction.md in this working directory to help future agents solve similar tasks with fewer steps/tokens while preserving correctness.",
        "Each script should live under ./<script_name>/ and contain a main.sh entrypoint plus an intro.json file.",
        "intro.json must be valid JSON with this schema:",
        "  {",
        "    \"name\": \"<script_name>\",",
        "    \"description\": \"what this script does\",",
        "    \"when_to_use\": \"under what conditions to call this script\",",
        "    \"entrypoint\": \"main.sh\",",
        "    \"parameters\": [",
        "      {\"name\": \"...\", \"type\": \"string|int|bool|...\", \"required\": true, \"description\": \"...\"}",
        "    ],",
        "    \"examples\": [{\"call\": \"main.sh <args>\", \"expected\": \"...\"}],",
        "    \"cost_saving_rationale\": \"why this script reduces cost vs. the baseline trajectory\"",
        "  }",
        "instruction.md is for HIGH-LEVEL cost-saving guidance ONLY (e.g. batching multiple tool calls per step to cut round trips, keeping tool output lean, anti-patterns to avoid). Do NOT list or describe specific scripts in instruction.md — each script's usage lives in its own intro.json.",
    ]

    def build(
        self,
        sample_paths,
        cwd_name: str = ".",
        scripts_dir: Optional[Path] = None,
    ) -> str:
        parts: List[str] = [line.replace("{cwd}", cwd_name) for line in self.HEADER]
        parts.append(
            f"The current working directory is {cwd_name}; evolve scripts in place here."
        )
        if scripts_dir is not None:
            parts += self._current_scripts_block(Path(scripts_dir))
        for i, path in enumerate(sample_paths, start=1):
            trajectory = json.loads(Path(path).read_text(encoding="utf-8"))
            parts += [
                f"\n# Trajectory {i}",
                f"Source: {path}",
                self.serializer.serialize(trajectory),
            ]
        parts.append(self.FOOTER)
        return "\n".join(parts)


# ---------------------------------------------------------------------------
# Evolver — 直接读 trajectory.json
# ---------------------------------------------------------------------------


class BaselineScriptEvolver(ScriptEvolver):
    """Baseline evolver：从 ``**/agent/trajectory.json`` 读原始 trajectory。

    与 `ScriptEvolver` 的区别仅在于 sample 来源——读 trajectory.json 而非
    contrastive_sample.json。`run()` / 续跑 / sentinel / batch 容错全部复用父类。
    """

    name = "evolve_baseline"

    def find_samples(self, result_dir, task=None) -> List[Path]:
        files = sorted(Path(result_dir).glob("**/agent/trajectory.json"))
        if not task:
            return files
        matched = [p for p in files if self._task_matches(p, task)]
        return matched or [p for p in files if task in str(p)]

    @staticmethod
    def _task_matches(path: Path, task: str) -> bool:
        stem = path.parent.parent.name  # <task_id> dir above agent/
        return stem == task or stem.startswith(f"{task}__") or task in stem.split("__")


# ---------------------------------------------------------------------------
# Factory + CLI
# ---------------------------------------------------------------------------


def make_baseline_evolver(
    scripts_dir,
    config_path,
    mini_swe_agent_dir,
    batch_size: int = 5,
    max_observation_chars: int = 1000,
    output_dir: Optional[str] = None,
    dry_run: bool = False,
    resume: bool = True,
) -> BaselineScriptEvolver:
    """构造 baseline evolver。参数语义与 `run_evolve.make_evolver` 一致。"""
    return BaselineScriptEvolver(
        scripts_dir=scripts_dir,
        runner=MiniSweAgentRunner(
            mini_swe_agent_dir=mini_swe_agent_dir,
            llm_config=config_path,
            dry_run=dry_run,
        ),
        prompt_builder=BaselineEvolvePromptBuilder(
            serializer=TrajectorySerializer(max_observation_chars=max_observation_chars),
        ),
        batch_size=batch_size,
        output_dir=Path(output_dir).resolve() if output_dir else None,
        resume=resume,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Baseline evolve: feed raw trajectories directly (no annotate/contrastive).",
    )
    _add_common(parser)
    _add_config(parser)
    _add_evolve(parser)

    args = parser.parse_args(argv)
    _setup_logging(args.log_file)

    evolver = make_baseline_evolver(
        scripts_dir=args.scripts_dir,
        config_path=args.config,
        mini_swe_agent_dir=args.mini_swe_agent_dir,
        batch_size=args.batch_size,
        max_observation_chars=args.max_observation_chars,
        output_dir=args.output_dir,
        dry_run=args.dry_run,
        resume=not args.no_resume,
    )
    evolver.run(args.result_dir, task=args.task)


if __name__ == "__main__":
    main()

"""
python -m src.evolve.evolve_baseline results/without_scripts_total_cases \
    --config ./_config/deepseekv4_flash.yaml \
    --scripts-dir ./.evolve_scripts_baseline \
    --batch-size 2 \
    --log-file ./results/evolve/evolve_baseline.log
"""