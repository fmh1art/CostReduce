"""端到端运行 script evolution pipeline 的可执行入口。

Pipeline 分三段::

    ┌──────────────┐     ┌────────────────┐     ┌──────────────┐
    │  1. annotate │ ──▶ │ 2. contrastive │ ──▶ │  3. evolve   │
    └──────────────┘     └────────────────┘     └──────────────┘
        TrajectoryAnnotator  ContrastiveSampleBuilder   ScriptEvolver
        (LLM 标注依赖)       (按依赖图裁剪 trajectory)   (mini-swe-agent 演化脚本)

每一段的输入/输出契约
--------------------

Stage 1 — annotate (`TrajectoryAnnotator`)
    输入 : ``<result_dir>/<task_id>/agent/trajectory.json``
            文件结构: {schema_version, agent, steps:[...], final_metrics, ...}
            每个 action step 含 tool_calls / observation / message。
    输出 : 原地写回 trajectory.json，新增 ``dependencies`` 字段:
            {"0": [], "1": [0], "2": [0,1], ...}
            key 是 action step 的 1-based 序号，value 是它依赖的之前 step 序号列表。
            step 0 表示"初始状态"，几乎总是被依赖。
    副产物: 日志（每步的 LLM 输出）。

Stage 2 — contrastive (`ContrastiveSampleBuilder`)
    输入 : Stage 1 产出的、含 ``dependencies`` 字段的 trajectory.json
    输出 : 同目录下 ``contrastive_sample.json``:
            {
              "positive_sample": <裁剪后的最小 trajectory>,
              "negative_sample": <原始完整 trajectory>,
            }
            positive_sample 仅保留从 final action 反向可达的 step + 初始上下文 step，
            并新增 ``minimal_step_indices`` 字段记录保留下来的 action step 序号。

Stage 3 — evolve (`ScriptEvolver`)
    输入 : Stage 2 产出的所有 contrastive_sample.json
            + ``--scripts-dir`` 指向的工作目录（含可被 agent 就地修改的脚本）
    输出 : ``<output_dir>/evolve_batch_<id>.traj.json``  — mini-swe-agent 的 trajectory
            ``<output_dir>/evolve_batch_<id>.prompt.md``  — 发给 agent 的 prompt
            ``<output_dir>/evolve_batch_<id>.done``       — 完成标记（含 batch 内 sample 列表）
            scripts_dir 下的脚本/instruction.md 被 agent 就地修改。
    断点续跑: 默认开启。已存在 .done sentinel 的 batch 会被跳过，可用 ``--no-resume`` 关闭。

替换中间模块
------------
所有 stage 都满足 `Stage` 协议（``name`` + ``run(result_dir, task=None)``）。
要替换某段，只需写一个实现该协议的类，在 :func:`build_pipeline` 里替换对应实例即可。
更细粒度的替换：

* 换 LLM 调用方式 → 传 ``--config`` 不同 yaml，或直接子类化 `TrajectoryAnnotator`。
* 换 trajectory 序列化 / prompt 模板 → 子类化 `TrajectorySerializer` / `EvolvePromptBuilder`，
  注入 :class:`ScriptEvolver`。
* 换 agent 后端 → 实现 `AgentRunner.run`，注入 :class:`ScriptEvolver`。

典型用法
--------
::

    # 全量跑
    python -m src.evolve.run_evolve run results/deep-swe/deepseek-flash-without-evolve-tools \\
        --config _config/deepseekv4_flash.yaml \\
        --scripts-dir .evolve_scripts \\
        --workers 4 --batch-size 5

    # 只跑单段（便于调试 / 替换中间模块后只验证这一段）
    python -m src.evolve.run_evolve annotate  results/... --workers 4
    python -m src.evolve.run_evolve contrastive results/...
    python -m src.evolve.run_evolve evolve     results/... --dry-run

    # 跑全量但跳过某段
    python -m src.evolve.run_evolve run results/... --skip annotate
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import List, Optional

from .annotator import TrajectoryAnnotator
from .contrastive import ContrastiveSampleBuilder
from .evolver import (
    AgentRunner,
    EvolvePromptBuilder,
    MiniSweAgentRunner,
    ScriptEvolver,
    TrajectorySerializer,
)
from .pipeline import ScriptEvolvePipeline, Stage

logger = logging.getLogger(__name__)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MINI_SWE_AGENT = ROOT / "agent" / "mini-swe-agent"
DEFAULT_SCRIPTS_DIR = ROOT / ".evolve_scripts"
DEFAULT_LLM_CONFIG = ROOT / "_config" / "deepseekv4_flash.yaml"


# ---------------------------------------------------------------------------
# Stage factories — 每个工厂返回一个 Stage 实例，方便单独替换
# ---------------------------------------------------------------------------


def make_annotator(config_path, workers: int = 1, retry_failed: int = 1) -> TrajectoryAnnotator:
    """Stage 1: 标注 trajectory step 间依赖关系。"""
    return TrajectoryAnnotator(
        config_path=config_path,
        workers=workers,
        retry_failed=retry_failed,
    )


def make_contrastive_builder() -> ContrastiveSampleBuilder:
    """Stage 2: 从已标注 trajectory 生成 contrastive sample。"""
    return ContrastiveSampleBuilder()


def make_evolver(
    scripts_dir,
    config_path,
    mini_swe_agent_dir,
    batch_size: int = 5,
    max_observation_chars: int = 500,
    output_dir: Optional[str] = None,
    dry_run: bool = False,
    resume: bool = True,
    runner: Optional[AgentRunner] = None,
    prompt_builder: Optional[EvolvePromptBuilder] = None,
) -> ScriptEvolver:
    """Stage 3: 把 contrastive sample 喂给 mini-swe-agent 演化脚本。

    可选参数 ``runner`` / ``prompt_builder`` 用于注入自定义实现（替换 agent 后端 / prompt 模板）。
    """
    runner = runner or MiniSweAgentRunner(
        mini_swe_agent_dir=mini_swe_agent_dir,
        llm_config=config_path,
        dry_run=dry_run,
    )
    prompt_builder = prompt_builder or EvolvePromptBuilder(
        serializer=TrajectorySerializer(max_observation_chars=max_observation_chars),
    )
    return ScriptEvolver(
        scripts_dir=scripts_dir,
        runner=runner,
        prompt_builder=prompt_builder,
        batch_size=batch_size,
        output_dir=Path(output_dir).resolve() if output_dir else None,
        resume=resume,
    )


def build_pipeline(args) -> ScriptEvolvePipeline:
    """构造三段 pipeline。要替换某个 stage，改这里即可。"""
    stages: List[Stage] = [
        make_annotator(
            config_path=args.config,
            workers=args.workers,
            retry_failed=args.retry_failed,
        ),
        make_contrastive_builder(),
        make_evolver(
            scripts_dir=args.scripts_dir,
            config_path=args.config,
            mini_swe_agent_dir=args.mini_swe_agent_dir,
            batch_size=args.batch_size,
            max_observation_chars=args.max_observation_chars,
            output_dir=args.output_dir,
            dry_run=args.dry_run,
            resume=not args.no_resume,
        ),
    ]
    return ScriptEvolvePipeline(stages=stages)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


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
    parser.add_argument(
        "result_dir",
        help="result/run directory containing <task_id>/agent/trajectory.json",
    )
    parser.add_argument("--task", help="optional task id/name substring filter")
    parser.add_argument("--log-file", help="optional log file path")


def _add_config(parser):
    parser.add_argument("--config", default=str(DEFAULT_LLM_CONFIG))


def _add_annotate(parser):
    parser.add_argument(
        "--workers", type=int, default=1,
        help="total parallel LLM calls across trajectory files and steps",
    )
    parser.add_argument(
        "--retry-failed", type=int, default=1,
        help="retry failed trajectory files after the first pass",
    )


def _add_evolve(parser):
    parser.add_argument("--scripts-dir", default=str(DEFAULT_SCRIPTS_DIR))
    parser.add_argument("--mini-swe-agent-dir", default=str(DEFAULT_MINI_SWE_AGENT))
    parser.add_argument(
        "--batch-size", type=int, default=5,
        help="每 prompt 含几个 case（trajectory）；v3 闭环另语义。default: %(default)s",
    )
    parser.add_argument("--max-observation-chars", type=int, default=1000)
    parser.add_argument(
        "--output-dir",
        help="where to save evolve prompts and mini-swe-agent trajectories "
             "(default: <result_dir>/evolve_logs)",
    )
    parser.add_argument("--dry-run", action="store_true", help="print cmd only, do not run agent")
    parser.add_argument(
        "--no-resume", action="store_true",
        help="ignore existing .done sentinels and re-run every batch",
    )


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Script evolution pipeline (annotate -> contrastive -> evolve).",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_annotate = sub.add_parser("annotate", help="Stage 1: annotate trajectory dependencies")
    _add_common(p_annotate)
    _add_config(p_annotate)
    _add_annotate(p_annotate)

    p_contrast = sub.add_parser("contrastive", help="Stage 2: build contrastive samples")
    _add_common(p_contrast)

    p_evolve = sub.add_parser("evolve", help="Stage 3: evolve scripts from contrastive samples")
    _add_common(p_evolve)
    _add_config(p_evolve)
    _add_evolve(p_evolve)

    p_run = sub.add_parser("run", help="run the full pipeline")
    _add_common(p_run)
    _add_config(p_run)
    _add_annotate(p_run)
    _add_evolve(p_run)
    p_run.add_argument(
        "--skip", action="append", default=[],
        help="stage name(s) to skip; may be repeated (e.g. --skip annotate)",
    )

    args = parser.parse_args(argv)
    _setup_logging(getattr(args, "log_file", None))

    if args.cmd == "annotate":
        make_annotator(
            config_path=args.config,
            workers=args.workers,
            retry_failed=args.retry_failed,
        ).run(args.result_dir, task=args.task)
    elif args.cmd == "contrastive":
        make_contrastive_builder().run(args.result_dir, task=args.task)
    elif args.cmd == "evolve":
        make_evolver(
            scripts_dir=args.scripts_dir,
            config_path=args.config,
            mini_swe_agent_dir=args.mini_swe_agent_dir,
            batch_size=args.batch_size,
            max_observation_chars=args.max_observation_chars,
            output_dir=args.output_dir,
            dry_run=args.dry_run,
            resume=not args.no_resume,
        ).run(args.result_dir, task=args.task)
    elif args.cmd == "run":
        pipeline = build_pipeline(args)
        pipeline.run(args.result_dir, task=args.task, skip=args.skip)


if __name__ == "__main__":
    main()
