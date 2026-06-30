"""Evolve v2 chunk: 修复 v1-chunk 在实际跑出来的几个根因问题。

与 v1-chunk 的关键差异
------------------------
1. 修 brief_observations bug (v1-chunk 8.1)
   v1-chunk 的 ChunkTrajectoryAnnotator 没有覆盖 ``is_annotated``，导致 baseline
   pipeline 已经写过 dependencies 的 trajectory 会被整个跳过、brief_observations
   永远不会被标注、observation contrastive 永远不会生成。
   v2 在子类里覆盖 ``is_annotated``，要求 ``brief_observations`` 也存在。

2. 改 contrastive 信号 (v1-chunk 8.2-8.3)
   v1-chunk 的 graph contrastive 用"依赖图反推的最小子图"做 positive，给进化
   agent 传递了"省 70 % 步数可达"的错误信号；并且只展示 step 数差、不展示
   token 成本差，在 99 % cache 命中率下和真实成本几乎不相关。
   v2 保留 graph + observation 两类（已修 bug 后会真正生成），并新增一类
   ``cost_hotspot`` 信号：扫 trajectory 找重复 ≥3 次的 bash verb（cat/grep/
   find/sed/go test 等）及其累计 observation 字符数，给进化 agent 真实的
   "可批处理"信号。与 v2 早先版本尝试过的 ``tool_replacement`` 不同，这里
   **不伪造 positive**：evolved tool 还没运行过，没有真实输出可对比，所以
   只给原始成本信号，让进化 agent 自己设计批处理脚本。真正的 raw-vs-evolved
   对比留到 v3 闭环（见 ``evolve_v3_cycle.md``）。

3. instruction.md 改成"行为契约" (v1-chunk 9.4)
   v1-chunk 产出的 instruction.md 11 条里 6 条是 per-tool 描述，是 intro.json
   的复制品。v2 的 prompt builder 把 instruction.md 模板改成"行为契约"风格：
   - 不用 ``max N steps`` 这种硬上限（复杂任务可能需要上百步）
   - 用"停滞检测"约束：长时间没尝试 fix / 长时间没跑测试 / 多次重复 grep
     同一个 pattern 等行为信号触发提示
   - 强制用绝对路径调用工具，避免 agent 猜 ``./<name>/main.sh`` 失败重试

4. 修 cwd 问题 (v1-chunk 8.7)
   v1-chunk 用 ``uv run --directory <mini_swe_agent_dir>``，会把 cwd 强制切
   到 mini_swe_agent_dir，覆盖掉 ``subprocess.run(cwd=scripts_dir)``。agent
   启动后 ``pwd`` 看到的是 mini-swe-agent 安装目录，会自己去探索父目录、
   发现仓库里其他 evolve 输出（如 ``.evolve_scripts_baseline/``），并参考
   baseline 的脚本设计。
   v2 用 ``uv tool run --from <mini_swe_agent_dir>`` 不切换 cwd，并在 prompt
   里用绝对路径 + 显式禁止探索仓库其他目录。

5. 闭环反馈 (v1-chunk 8.4)
   v1-chunk 的 prompt 里只有 ``_current_scripts_block`` 列出 intro.json 内容，
   没有下游使用统计。v2 的 ``ChunkEvolvePromptBuilderV2`` 接受一个可选的
   ``downstream_stats`` 字段，调用方可以在 batch 之间注入下游 agent 的实际
   调用次数、失败率、节省成本统计，让进化 agent 看到真实反馈。

设计要点
--------
* 三个 stage 类都继承自 v1-chunk 的对应类，只覆盖必要方法。
* CLI 复用 ``run_evolve`` 的参数 helper，参数语义与 v1-chunk 一致。
* ``MiniSweAgentRunnerV2`` 是 ``MiniSweAgentRunner`` 的子类，只改 cmd 拼装
  方式，不动其他逻辑。

用法
----
::

    # 全量跑
    python -m src.evolve.evolve_v2_chunk run results/... \\
        --config _config/deepseekv4_flash.yaml \\
        --scripts-dir .evolve_scripts_v2_chunk \\
        --workers 4 --batch-size 4 --chunk-size 20

    # 单段调试
    python -m src.evolve.evolve_v2_chunk annotate    results/... --workers 4
    python -m src.evolve.evolve_v2_chunk contrastive results/...
    python -m src.evolve.evolve_v2_chunk evolve      results/... --dry-run
"""

from __future__ import annotations

import argparse
import copy
import json
import logging
import os
import re
import shlex
import subprocess
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from src.tools.llm import LLM

from ._chunk_helpers import (
    classify_step_meta,
    extract_bash_command,
    bash_verb,
    identify_phases,
    find_anchor_step,
    observation_chars,
    trace_minimal_indices,
)
from .annotator import DependencyParseError, TrajectoryAnnotator
from .contrastive import ContrastiveSampleBuilder
from .evolve_v1_chunk import (
    LONG_OBS_THRESHOLD_DEFAULT,
    MIN_REDUCTION_RATIO_DEFAULT,
    ChunkContrastiveSampleBuilder,
    ChunkEvolvePromptBuilder,
    ChunkScriptEvolver,
    ChunkTrajectoryAnnotator,
)
from .evolver import (
    AgentRunner,
    EvolvePromptBuilder,
    MiniSweAgentRunner,
    ScriptEvolver,
    TrajectorySerializer,
)
from .run_evolve import (
    DEFAULT_MINI_SWE_AGENT,
    DEFAULT_LLM_CONFIG,
    DEFAULT_SCRIPTS_DIR,
    _add_common,
    _add_config,
    _add_annotate,
    _add_evolve,
    _setup_logging,
)

logger = logging.getLogger(__name__)


# ============================================================================
# Stage 1: V2 annotator — 修 brief_observations bug
# ============================================================================


class ChunkTrajectoryAnnotatorV2(ChunkTrajectoryAnnotator):
    """v2 annotator：在 v1-chunk 基础上加 ``step_meta`` 字段。

    v1-chunk 已写 ``dependencies`` + ``brief_observations``。v2 在此基础上给
    每个 action step 写入 ``step_meta``（op_type / success / idempotent /
    bash_verbs / files_touched），用于后续 phase-based chunk 切分和 minimal
    subgraph 选择。

    ``step_meta`` 是纯规则分类（无 LLM 调用），所以这一步成本为零。
    """

    name = "annotate_chunk_v2"

    def annotate_file(self, path, llm=None, step_workers: int = 1):
        # 如果 step_meta 已存在，跳过整个文件（避免重复 LLM 调用）
        if self._has_step_meta(path):
            logger.info("step_meta already present for %s, skipping", path)
            return
        # 父类跑 dependencies + brief_observations（可能 LLM）
        super().annotate_file(path, llm=llm, step_workers=step_workers)
        # 加 step_meta（纯规则，便宜）
        self._annotate_step_meta(path)

    @staticmethod
    def _has_step_meta(path) -> bool:
        """检查 trajectory 是否所有 action step 都已带 step_meta。"""
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception:
            return False
        action_steps = [
            s for s in data.get("steps", [])
            if (s.get("tool_calls") or "observation" in s or s.get("action"))
        ]
        return bool(action_steps) and all("step_meta" in s for s in action_steps)

    @staticmethod
    def _annotate_step_meta(path) -> None:
        """给每个 action step 写入 step_meta 字段。

        V3 合并后，dependency 标注已经在同一次 LLM 调用里写入了
        ``step_meta.op_type``（``op_type_source="llm"``）或因解析失败而留空。
        这里用规则分类器补齐其余字段（success / idempotent / bash_verbs /
        files_touched），并尊重 LLM 的 op_type：
          - 若 op_type_source=="llm"，op_type / op_type_source 原样保留；
          - 否则用规则 op_type 覆盖，标 ``op_type_source="rule_fallback"``。
        """
        from ._chunk_helpers import classify_step_meta
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        n_llm = 0
        for step in data.get("steps", []):
            if not (step.get("tool_calls") or "observation" in step or step.get("action")):
                continue
            meta = step.get("step_meta")
            if not isinstance(meta, dict):
                step["step_meta"] = classify_step_meta(step)
                step["step_meta"]["op_type_source"] = "rule_fallback"
                continue
            rule = classify_step_meta(step)
            if meta.get("op_type_source") == "llm":
                n_llm += 1
                # 保留 LLM op_type，只补齐规则字段
                for k in ("success", "idempotent", "bash_verbs", "files_touched"):
                    meta.setdefault(k, rule.get(k))
            else:
                # 无 LLM op_type（解析失败或旧文件）：用规则 op_type
                meta.update(rule)
                meta["op_type_source"] = "rule_fallback"
        Path(path).write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        logger.info("annotated step_meta for %s (llm op_type on %d step(s))", path, n_llm)

    @staticmethod
    def is_annotated(path) -> bool:
        # 先跑父类的检查（dependencies + brief_observations 完整性）
        if not TrajectoryAnnotator.is_annotated(path):
            return False
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception:
            return False
        if not isinstance(data.get("brief_observations"), dict):
            return False
        # v2 新增：要求 step_meta 字段存在
        return ChunkTrajectoryAnnotatorV2._has_step_meta(path)


# ============================================================================
# Stage 2: V2 contrastive builder — 保留 graph + observation，新增 cost_hotspot
# ============================================================================


# 触发 cost_hotspot 的默认阈值：单个 trajectory 内同一 bash verb 至少出现这么多次，
# 且累计 observation 字符数至少这么多，才算一个值得批处理的热点。
HOTSPOT_MIN_OCCURRENCES_DEFAULT = 3
HOTSPOT_MIN_TOTAL_CHARS_DEFAULT = 1000


class ChunkContrastiveSampleBuilderV2(ChunkContrastiveSampleBuilder):
    """v2 contrastive builder：保留 graph + observation，新增 cost_hotspot。

    新增的 ``cost_hotspot`` 不是 contrastive（没有 positive/negative 对），
    而是一个真实成本信号：扫 trajectory 找重复 ≥ N 次的 bash verb，把每次
    出现的 step index、原始命令、observation 字符数都列出来，累计成总额。

    与 v2 早先尝试过的 ``tool_replacement`` contrastive 的关键差别：
    ``tool_replacement`` 给 positive 伪造了一段 evolved tool 调用 + 假的
    observation 占位符，但 evolved tool 还没运行过、根本没有真实输出可对比。
    ``cost_hotspot`` 不伪造任何东西，只统计真实成本，把"是否批处理"留给
    进化 agent 自己决定。
    """

    name = "contrastive_chunk_v2"

    def __init__(
        self,
        min_reduction_ratio: float = MIN_REDUCTION_RATIO_DEFAULT,
        hotspot_min_occurrences: int = HOTSPOT_MIN_OCCURRENCES_DEFAULT,
        hotspot_min_total_chars: int = HOTSPOT_MIN_TOTAL_CHARS_DEFAULT,
    ):
        super().__init__(min_reduction_ratio=min_reduction_ratio)
        self.hotspot_min_occurrences = int(hotspot_min_occurrences)
        self.hotspot_min_total_chars = int(hotspot_min_total_chars)

    def build_file(self, path) -> List[Path]:
        # 缓存 trajectory 供 _build_positive_sample 回溯跨 chunk 前序依赖。
        # 父类 build_file 会重读一遍 trajectory，这里缓存避免 v2 再读一次。
        self._current_trajectory = json.loads(Path(path).read_text(encoding="utf-8"))
        try:
            outs = super().build_file(path)
            outs.extend(self._build_cost_hotspot_samples(path))
        finally:
            self._current_trajectory = None
        return outs

    def _build_cost_hotspot_samples(self, path: Path) -> List[Path]:
        """扫 trajectory 找重复 bash verb，输出 cost_hotspot 样本。

        每个 cost_hotspot 样本结构（无 fabricated positive）::

            {
              "type": "cost_hotspot",
              "source_trajectory": "<path>",
              "verb": "cat",
              "occurrences": [
                {"step_index": 3, "command": "cat src/foo.py",
                 "observation_chars": 3200},
                ...
              ],
              "total_observation_chars": 15000,
              "occurrence_count": 5
            }

        阈值：同一 verb 在同一 trajectory 内出现 ≥ ``hotspot_min_occurrences``
        次，且累计 observation 字符数 ≥ ``hotspot_min_total_chars``，才输出。
        小轨迹（如只有 1-2 次重复）不会产生噪声样本。
        """
        trajectory = json.loads(Path(path).read_text(encoding="utf-8"))
        steps = trajectory.get("steps", [])

        # 按 verb 聚合 action step
        groups: Dict[str, List[dict]] = {}
        action_i = 0
        for step in steps:
            if not self._is_action_step(step):
                continue
            action_i += 1
            cmd = extract_bash_command(step)
            if not cmd:
                continue
            verb = bash_verb(cmd)
            if not verb:
                continue
            obs_chars = observation_chars(step.get("observation", ""))
            groups.setdefault(verb, []).append({
                "step_index": action_i,
                "command": cmd[:300],
                "observation_chars": obs_chars,
            })

        outs: List[Path] = []
        for verb, occs in groups.items():
            if len(occs) < self.hotspot_min_occurrences:
                continue
            total = sum(o["observation_chars"] for o in occs)
            if total < self.hotspot_min_total_chars:
                continue
            sample = {
                "type": "cost_hotspot",
                "source_trajectory": str(path),
                "verb": verb,
                "occurrences": occs,
                "total_observation_chars": total,
                "occurrence_count": len(occs),
            }
            out = path.with_name(f"cost_hotspot_{verb}.json")
            out.write_text(
                json.dumps(sample, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            outs.append(out)
        return outs

    # ---------- chunk 切分（v2: phase-based） ----------

    # 单个 phase 超过这么多 step 还是要切（避免单个超大 phase 撑爆 prompt）。
    # 这是 phase-based 切分的内部安全阀，不再作为 CLI 参数暴露。
    MAX_PHASE_SIZE = 30

    def _split_into_chunks(self, trajectory: dict, chunk_size: int) -> List[dict]:
        """v2: 按 DAG 阶段切分 chunk，而非固定 step 数。

        用 ``identify_phases`` 把 trajectory 的 action step 按 ``op_type``
        分组（read/write/verify/explore），合并过小阶段、切分过大阶段。
        每个 phase 一个 chunk，schema 与 v1-chunk 一致（``chunk_id`` /
        ``chunk_range`` / 重映射后的 ``dependencies`` / ``brief_observations``），
        额外带 ``phase_op_type`` 标记本 chunk 的主 op_type。

        ``chunk_size`` 参数是为了兼容父类 ``build_file`` 调用签名（父类传
        ``self.CHUNK_SIZE``），v2 忽略它 — phase 切分纯靠 ``op_type`` +
        ``MAX_PHASE_SIZE`` 安全阀，不再受外部参数控制。
        """
        steps = trajectory.get("steps", [])
        dependencies = trajectory.get("dependencies", {}) or {}
        brief_obs = trajectory.get("brief_observations", {}) or {}

        # 收集 action step 的 (action_num, steps_array_idx) + step_meta
        action_step_indices: List[Tuple[int, int]] = []
        step_metas: Dict[int, dict] = {}
        action_i = 0
        for i, step in enumerate(steps):
            if not self._is_action_step(step):
                continue
            action_i += 1
            action_step_indices.append((action_i, i))
            step_metas[action_i] = step.get("step_meta") or classify_step_meta(step)

        if not action_step_indices:
            return [trajectory]

        leading_steps = steps[: action_step_indices[0][1]]

        phases = identify_phases(
            len(action_step_indices),
            step_metas,
            min_phase_size=3,
            max_phase_size=self.MAX_PHASE_SIZE,
        )

        chunks: List[dict] = []
        for chunk_id, (start, end, op_type) in enumerate(phases, start=1):
            chunk_action_indices = action_step_indices[start:end]
            local_size = end - start
            global_first = start + 1
            global_last = end

            chunk_steps: List[dict] = []
            if start == 0:
                chunk_steps.extend(leading_steps)
            for _, step_idx in chunk_action_indices:
                chunk_steps.append(steps[step_idx])

            # 重映射 dependencies：global → local
            local_deps: Dict[str, List[int]] = {"0": []}
            for local_i in range(1, local_size + 1):
                global_i = start + local_i
                global_deps = dependencies.get(str(global_i), [])
                local_d: List[int] = []
                for d in global_deps:
                    try:
                        d_int = int(d)
                    except (TypeError, ValueError):
                        continue
                    if global_first <= d_int <= global_last:
                        local_d.append(d_int - start)
                    elif d_int < global_first:
                        if 0 not in local_d:
                            local_d.append(0)
                if 0 not in local_d:
                    local_d.append(0)
                local_deps[str(local_i)] = local_d

            # 重映射 brief_observations：global → local
            local_brief_obs: Dict[str, List[int]] = {}
            for k, v in brief_obs.items():
                try:
                    k_int = int(k)
                except (TypeError, ValueError):
                    continue
                if global_first <= k_int <= global_last:
                    local_brief_obs[str(k_int - start)] = v

            chunk = {
                **trajectory,
                "steps": chunk_steps,
                "dependencies": local_deps,
                "brief_observations": local_brief_obs,
                "chunk_id": chunk_id,
                "chunk_range": [global_first, global_last],
                "phase_op_type": op_type,
            }
            chunks.append(chunk)
        return chunks

    # ---------- minimal subgraph（v2: anchor + 失败过滤 + 前序上下文） ----------

    # 单个 chunk 的前序上下文最多展示这么多个 step（按 global index 取最近的 N 个）。
    # 太多会让 prompt 爆炸；太少又丢失上下文。5 是经验值。
    MAX_PREDECESSOR_STEPS = 5

    def _build_positive_sample(self, chunk: dict) -> dict:
        """v2: 用 anchor step + 失败过滤构造 minimal subgraph + 前序上下文。

        v1-chunk 取「最后一步的依赖闭包」，最后一步经常是失败命令。v2 改成：
        1. 找 anchor step：优先最后一个成功的 verify > write > read
        2. 从 anchor 依赖闭包里过滤掉失败的非 explore step（不贡献 anchor 结果）
        3. 找不到成功 anchor（chunk 全失败）时返回 ``anchor_index=None``，
           ``_has_graph_optimization_space`` 会跳过这个 chunk
        4. 额外回溯 anchor 的**跨 chunk** global dependencies，收集一个最小
           前序 step 集（success 过滤，限量 ``MAX_PREDECESSOR_STEPS`` 个），
           让 evolve agent 知道 chunk 之前发生了什么、为什么 chunk 内的操作
           是必要的。没有前序依赖（anchor 依赖为空或全在 chunk 内）时
           ``predecessor_context`` 为空列表。
        """
        dependencies = chunk.get("dependencies") or {}
        positive = copy.deepcopy(chunk)

        # 收集 chunk 内 action step 及其 step_meta
        action_steps_with_meta: List[Tuple[dict, dict]] = []
        step_metas: Dict[str, dict] = {}
        action_i = 0
        for step in chunk.get("steps", []):
            if not self._is_action_step(step):
                continue
            action_i += 1
            meta = step.get("step_meta") or classify_step_meta(step)
            action_steps_with_meta.append((step, meta))
            step_metas[str(action_i)] = meta

        anchor = find_anchor_step(action_steps_with_meta)
        # find_anchor_step 在找不到成功 verify/write/read 时返回 last step
        # 作为 fallback。这种 chunk 没学习价值，标 anchor=None 让后续跳过。
        if anchor is not None:
            anchor_meta = step_metas.get(str(anchor), {})
            if not (
                anchor_meta.get("success")
                and anchor_meta.get("op_type") in ("verify", "write", "read")
            ):
                anchor = None

        if anchor is None:
            positive["steps"] = [
                s for s in chunk.get("steps", []) if not self._is_action_step(s)
            ]
            positive["dependencies"] = {"0": []}
            positive["minimal_step_indices"] = [0]
            positive["anchor_index"] = None
            positive["anchor_op_type"] = None
            positive["anchor_success"] = False
            positive["predecessor_context"] = []
            return positive

        keep = trace_minimal_indices(dependencies, anchor, step_metas)
        positive_steps: List[dict] = []
        action_i = 0
        for step in chunk.get("steps", []):
            if self._is_action_step(step):
                action_i += 1
                if action_i in keep:
                    positive_steps.append(step)
            elif action_i == 0:
                positive_steps.append(step)
        positive["steps"] = positive_steps
        positive["dependencies"] = {
            str(i): dependencies[str(i)] for i in sorted(keep) if str(i) in dependencies
        }
        positive["minimal_step_indices"] = sorted(keep)
        positive["anchor_index"] = anchor
        positive["anchor_op_type"] = step_metas.get(str(anchor), {}).get("op_type")
        positive["anchor_success"] = step_metas.get(str(anchor), {}).get("success", False)

        # 回溯跨 chunk 前序上下文
        trajectory = getattr(self, "_current_trajectory", None)
        if trajectory is not None:
            positive["predecessor_context"] = self._trace_predecessor_context(
                trajectory, chunk, anchor,
            )
        else:
            positive["predecessor_context"] = []
        return positive

    def _trace_predecessor_context(
        self, trajectory: dict, chunk: dict, anchor_local: int,
    ) -> List[dict]:
        """从 anchor 回溯跨 chunk 前序 step（success 过滤 + 限量）。

        用 global dependencies（trajectory 上的原始依赖）从 anchor 反向追溯，
        收集所有 ``< chunk_range[0]`` 的前序 step index。失败的非 explore
        step 不进前序集（和 chunk 内闭包的失败过滤规则一致）。

        返回的前序 step 按 global index 升序排列，最多
        ``MAX_PREDECESSOR_STEPS`` 个（取最近的 N 个，因为最近的通常最相关）。
        """
        global_deps = trajectory.get("dependencies", {}) or {}
        global_first = chunk["chunk_range"][0]
        global_anchor = global_first + anchor_local - 1

        action_steps = [s for s in trajectory.get("steps", []) if self._is_action_step(s)]
        step_metas_global: Dict[int, dict] = {}
        for i, step in enumerate(action_steps, start=1):
            meta = step.get("step_meta") or classify_step_meta(step)
            step_metas_global[i] = meta

        predecessor_indices: set = set()
        stack = [global_anchor]
        visited = {global_anchor}
        while stack:
            i = stack.pop()
            for dep in global_deps.get(str(i), []):
                try:
                    dep_int = int(dep)
                except (TypeError, ValueError):
                    continue
                if dep_int in visited or dep_int == 0:
                    continue
                visited.add(dep_int)
                if dep_int < global_first:
                    meta = step_metas_global.get(dep_int, {})
                    if not meta.get("success", True) and meta.get("op_type") != "explore":
                        continue
                    predecessor_indices.add(dep_int)
                # 无论跨 chunk 还是 chunk 内，都继续回溯（前序 step 可能依赖更早的前序）
                stack.append(dep_int)

        if not predecessor_indices:
            return []
        # 取最近的 N 个（global index 最大的 N 个），再按升序输出
        selected = sorted(predecessor_indices, reverse=True)[: self.MAX_PREDECESSOR_STEPS]
        return [
            {
                "global_step_index": idx,
                "op_type": step_metas_global.get(idx, {}).get("op_type", "?"),
                "success": step_metas_global.get(idx, {}).get("success", True),
                "step": copy.deepcopy(action_steps[idx - 1]),
            }
            for idx in sorted(selected)
        ]

    def _has_graph_optimization_space(self, chunk: dict, positive: dict) -> bool:
        """v2: 额外检查 anchor 是否是成功的 verify/write/read。

        v1-chunk 只检查 step 数 reduction。v2 加前置：anchor 不存在或不是
        成功的 verify/write/read（fallback 情况）时直接跳过 — 没学习价值
        的 chunk 不浪费 evolve agent 的注意力。
        """
        if not positive.get("anchor_success"):
            return False
        if positive.get("anchor_op_type") not in ("verify", "write", "read"):
            return False
        return super()._has_graph_optimization_space(chunk, positive)

    def _build_observation_contrastive(
        self, trajectory: dict, chunk: dict, local_step_idx: int
    ) -> dict:
        """v2 override：让 negative / positive 对称（都是当前 step 一个 step）。

        v1-chunk 的实现把 negative 设成「当前 step + 1 跳后继 step」（通常 2-3 个
        step），positive 设成「当前 step + brief observation」（1 个 step）。
        step 数不对等会让 evolve agent 误以为 contrastive 的重点是「省 step」，
        但我们想表达的是「同一步的 observation 全量 vs 精简」。

        v2 改成 negative / positive 都是当前 step 一个 step，唯一差别是
        observation 字段：negative 是原始 observation，positive 是 brief
        observation（brief_lines 指定的行）。
        """
        action_steps = [s for s in trajectory.get("steps", []) if self._is_action_step(s)]

        # local → global
        global_first = chunk["chunk_range"][0]
        global_idx = global_first + local_step_idx - 1
        current_step = action_steps[global_idx - 1]

        # negative: 当前 step 的完整 observation（不追加后继）
        neg_step = copy.deepcopy(current_step)

        # positive: 当前 step + brief observation
        brief_lines = chunk.get("brief_observations", {}).get(str(local_step_idx), [])
        pos_step = copy.deepcopy(current_step)
        pos_step["observation"] = self._extract_brief_observation(
            current_step.get("observation", ""), brief_lines
        )

        return {
            "type": "contrastive_observation",
            "chunk_id": chunk["chunk_id"],
            "step_index": global_idx,
            "local_step_index": local_step_idx,
            "brief_lines": brief_lines,
            "source_trajectory": chunk.get("source_trajectory", ""),
            "negative_sample": {"steps": [neg_step]},
            "positive_sample": {"steps": [pos_step]},
        }


# ============================================================================
# Stage 3: V2 prompt builder — 行为契约 + 绝对路径 + 反馈回路
# ============================================================================


class ChunkEvolvePromptBuilderV2(ChunkEvolvePromptBuilder):
    """v2 prompt builder：极简 HEADER + 对称 observation contrastive + cost_hotspot。

    与 v1-chunk 的区别：
    * HEADER 极简化：只保留 schema、scope、cost model 三段。删了 6 条行为契约
      示例（agent 会照抄）、CRITICAL SCOPE 警告（v2 已修 cwd bug，警告冗余）、
      预设 script 名字（避免限制 agent 设计自由）。
    * instruction.md 改成「行为契约」：不预设具体规则，让 agent 基于实际样本
      设计。但给出 STOP-PROGRESSING-IF 的形式约束，禁止硬 step 上限。
    * 接受可选的 ``downstream_stats`` 字段，在 prompt 里展示当前 scripts 的
      下游使用统计（调用次数、失败率、节省成本）。
    * ``_current_scripts_block`` 用绝对路径列 scripts，避免 agent 猜路径。
    """

    HEADER = [
        "# Evolve task",
        "",
        "You are evolving bash helper scripts that downstream coding agents will call to "
        "solve similar tasks with fewer steps. The scripts you write will be bind-mounted "
        "into downstream agent containers at `/app/.preinstalled_scripts/<name>/main.sh`.",
        "",
        "## Working directory",
        "Your cwd is the absolute path shown below. Create/modify/delete files ONLY inside "
        "this directory. Each script lives under `./<name>/` with two files: `main.sh` "
        "(entrypoint) and `intro.json` (metadata).",
        "",
        "## intro.json schema",
        "Valid JSON with EXACTLY these fields (no extras, no `when_to_use`):",
        "  {",
        "    \"name\": \"<script_name>\",",
        "    \"description\": \"ONE sentence: what this script does.\",",
        "    \"entrypoint\": \"main.sh\",",
        "    \"parameters\": [",
        "      {\"name\": \"...\", \"type\": \"string|int|bool\", \"required\": true,",
        "       \"description\": \"ONE short phrase\"}",
        "    ],",
        "    \"examples\": [{\"call\": \"/app/.preinstalled_scripts/<name>/main.sh <args>\",",
        "                   \"expected\": \"ONE short line\"}],",
        "    \"cost_saving_rationale\": \"ONE short sentence: why this script saves cost\"",
        "  }",
        "Rules:",
        "- `description` ≤ 1 sentence. `parameter.description` ≤ 1 phrase. "
        "`cost_saving_rationale` ≤ 1 sentence. `examples[*].expected` ≤ 1 line.",
        "- `examples` is OPTIONAL; at most ONE example. No verbose multi-step walkthroughs.",
        "- `examples[*].call` MUST use the absolute path "
        "`/app/.preinstalled_scripts/<name>/main.sh ...` (this is the path downstream "
        "agents see after bind-mounting, not your cwd).",
        "- Do NOT include `when_to_use` — the `description` already tells the agent when.",
        "- Merge similar scripts: before creating a new directory, check whether an existing "
        "script could be extended with a new action/flag instead. Fewer, more general "
        "scripts = lower downstream prompt cost. When you remove a script, delete its directory.",
        "",
        "## instruction.md (BEHAVIOR CONTRACT, NOT TOOL CATALOG)",
        "Maintain instruction.md as ≤ 20 behavior contracts for the downstream agent. "
        "Each contract ≤ 1 line. Write contracts based on the cost patterns you observe "
        "in the samples below — do NOT just copy generic advice. ",
        "For example, a good contract can be: Perform multiple simple and clear operations in one step to save steps. ",
        "Instead, write STOP-PROGRESSING-IF rules that detect stuck behavior.",
        "",
        "## Cost model (for prioritizing your designs)",
        "Effect on real cost, largest to smallest:",
        "  1. Fewer agent steps — each step costs cache write + output tokens. ",
        "  2. Shorter tool_call commands. ",
        "  3. Smaller observations — low priority. ",
        "A batching script that collapses N repeated calls into 1 step saves N-1 steps. ",
        "",
        "## Verification (REQUIRED after every script add/update)",
        "1. Run `bash <script_dir>/main.sh <sample_args>` on testing source files and confirm it return the desired content. ",
        "2. Validate intro.json: `python -c \"import json; json.load(open('<script_dir>/intro.json'))\"`.",
        "3. Re-read the script — confirm it is GENERIC (no hardcoded file paths from "
        "the samples below).",
    ]

    FOOTER = (
        "\n# Your task\n"
        "Modify, add, merge, or remove scripts under your cwd based on the samples above. "
        "After each change, run the verification steps listed above. "
        "Do NOT edit the prompt file or sample files. "
        "Finish once scripts + intro.json + instruction.md are saved and verified."
    )

    def __init__(
        self,
        serializer: Optional[TrajectorySerializer] = None,
        downstream_stats: Optional[Dict[str, dict]] = None,
        scripts_target_root: str = "/app/.preinstalled_scripts",
    ):
        super().__init__(serializer=serializer)
        # downstream_stats: {script_name: {calls, failures, saved_yuan, notes}}
        self.downstream_stats = downstream_stats or {}
        self.scripts_target_root = scripts_target_root.rstrip("/") or "/"

    # 缓存 task info（trajectory_path → dict），避免同一 trajectory 被多次解析
    _task_info_cache: Dict[str, dict] = {}

    @classmethod
    def _extract_task_info(cls, trajectory_path: str) -> dict:
        """从 trajectory.json 抽 task description + resolve status + repo info。

        缓存到类属性 ``_task_info_cache``，因为同一 batch 里多个 sample 可能
        指向同一 trajectory，避免重复 IO + JSON 解析。

        Returns:
            {
              "task_description": str (前 600 字符),
              "submitted": bool (是否含 COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT),
              "repo_hint": str (从 task description 抽 repo 名，可能为空),
              "total_steps": int,
              "final_metrics": dict (token / step 统计),
            }
        """
        if not trajectory_path:
            return {}
        if trajectory_path in cls._task_info_cache:
            return cls._task_info_cache[trajectory_path]
        p = Path(trajectory_path)
        if not p.exists():
            cls._task_info_cache[trajectory_path] = {}
            return {}
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cls._task_info_cache[trajectory_path] = {}
            return {}
        info: dict = {}
        # task description 在 step 1 content（"Please solve this issue: ..."）
        steps = data.get("steps", [])
        for s in steps[:5]:
            content = s.get("content") or s.get("message") or ""
            if isinstance(content, str) and "Please solve this issue:" in content:
                prefix = "Please solve this issue:"
                idx = content.find(prefix) + len(prefix)
                info["task_description"] = content[idx:idx + 600].strip()
                break
        # resolve status: 找 COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT 标志
        all_text = ""
        for s in steps[-5:]:
            content = s.get("content") or s.get("message") or ""
            if isinstance(content, str):
                all_text += content + "\n"
            # tool_calls 里也可能有
            for tc in (s.get("tool_calls") or []):
                args = tc.get("arguments", {}) if isinstance(tc, dict) else {}
                if isinstance(args, dict):
                    cmd = args.get("command", "")
                    if isinstance(cmd, str):
                        all_text += cmd + "\n"
        info["submitted"] = "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT" in all_text
        info["total_steps"] = data.get("final_metrics", {}).get("total_steps") or len(steps)
        info["final_metrics"] = data.get("final_metrics", {})
        # repo hint 从 task description 抽 github URL
        td = info.get("task_description", "")
        m = re.search(r"github\.com/([\w\-]+/[\w\-]+)", td)
        if m:
            info["repo_hint"] = m.group(1)
        cls._task_info_cache[trajectory_path] = info
        return info

    @staticmethod
    def _extract_task_info_for_group(group: dict) -> dict:
        """从 group 里任意 sample 的 source_trajectory 字段抽 task info。

        v1-chunk 的 observation contrastive sample 没写 source_trajectory
        字段（graph contrastive 写了），所以这里同时支持两种反查：
        1. 优先用 sample.source_trajectory 字段
        2. 退化到从 sample._sample_path 反查同目录的 trajectory.json
        """
        for sample in [group.get("graph"), *(group.get("observations") or [])]:
            if not sample:
                continue
            src = sample.get("source_trajectory", "")
            if src:
                return ChunkEvolvePromptBuilderV2._extract_task_info(src)
            # 退化：用 _sample_path 字段反查同目录 trajectory.json
            sp = sample.get("_sample_path")
            if sp:
                traj_path = Path(sp).parent / "trajectory.json"
                if traj_path.exists():
                    return ChunkEvolvePromptBuilderV2._extract_task_info(str(traj_path))
        return {}

    def _render_graph_contrastive_block(self, graph_sample: dict) -> List[str]:
        """渲染 graph contrastive 段，加 Phase / Anchor / Predecessor / Rationale。

        v1-chunk 只展示「Original Chunk Trajectory」+「Minimal Chunk Trajectory」，
        agent 不知道 minimal 怎么来的，可能误以为是「理想执行路径」；中间
        chunk 还会因为没有前序上下文而看不懂 chunk 内操作的目的。v2 显式标注：
        - phase / anchor：minimal subgraph 怎么来的
        - predecessor context：chunk 之前的最小必要前序（success 过滤 + 限量）
        - rationale：哪些 step 可移除、为什么
        """
        neg = graph_sample.get("negative_sample", {}) or {}
        pos = graph_sample.get("positive_sample", {}) or {}
        phase = neg.get("phase_op_type", "?")
        chunk_range = neg.get("chunk_range", [0, 0])
        anchor = pos.get("anchor_index")
        anchor_op = pos.get("anchor_op_type") or "?"
        keep = pos.get("minimal_step_indices", []) or []
        n_orig = sum(1 for s in neg.get("steps", []) if (s.get("tool_calls") or "observation" in s))

        lines = ["\n## Graph Contrastive"]
        lines.append(
            f"Phase: {phase} (action steps {chunk_range[0]}-{chunk_range[1]} of this trajectory, "
            f"{n_orig} steps total)"
        )
        if anchor is None:
            lines.append(
                "Anchor: none (no successful verify/write/read in this phase — "
                "no learning signal, sample kept for context only)"
            )
        else:
            lines.append(
                f"Anchor: step {anchor} (last successful {anchor_op} in this phase)"
            )
            lines.append(
                f"Minimal subgraph: {keep} (anchor + dependency closure, "
                "failed non-explore steps filtered out)"
            )
            excluded = [i for i in range(1, n_orig + 1) if i not in keep and i != 0]
            if excluded:
                lines.append(
                    f"Removable steps (not in closure of anchor {anchor}): {excluded}. "
                    "A batching script that skips or merges these would not affect "
                    "the anchor's outcome."
                )
            else:
                lines.append(
                    "All steps are in the closure — this chunk has no removable steps; "
                    "treat as informational only."
                )

        # Predecessor context：chunk 之前的最小必要前序
        predecessor = pos.get("predecessor_context") or []
        if predecessor:
            lines.append("\n### Predecessor Context (from before this phase)")
            for p in predecessor:
                gidx = p["global_step_index"]
                lines.append(f"\n#### Earlier step (global {gidx})")
                lines.append(self.serializer.serialize({"steps": [p["step"]]}))

        lines.append("### Original Chunk Trajectory")
        lines.append(self.serializer.serialize(neg))
        lines.append("\n### Minimal Chunk Trajectory")
        lines.append(self.serializer.serialize(pos))
        return lines

    @staticmethod
    def _render_task_info_block(task_info: dict) -> List[str]:
        """渲染 task-level 上下文块（仅 task description）。"""
        td = task_info.get("task_description", "")
        if not td:
            return []
        td_flat = " ".join(td.split())
        if len(td_flat) > 400:
            td_flat = td_flat[:400].rstrip() + "..."
        return ["\n## Trajectory context", f"Task: {td_flat}"]

    def build(
        self,
        sample_paths: Iterable[Path],
        cwd_name: str = ".",
        scripts_dir: Optional[Path] = None,
    ) -> str:
        # 按 (trajectory, chunk_id, type) 分组；cost_hotspot 单独收集
        groups: Dict[Tuple[str, int], dict] = {}
        hotspot_samples: List[dict] = []
        for p in sample_paths:
            data = json.loads(Path(p).read_text(encoding="utf-8"))
            # 给每个 sample 标注来源文件路径，方便反查 trajectory.json
            data["_sample_path"] = str(p)
            stype = data.get("type")
            if stype == "cost_hotspot":
                hotspot_samples.append(data)
                continue
            key = self._chunk_key(Path(p))
            group = groups.setdefault(key, {"graph": None, "observations": []})
            if stype == "contrastive_graph":
                group["graph"] = data
            elif stype == "contrastive_observation":
                group["observations"].append(data)

        parts: List[str] = [line.replace("{cwd}", cwd_name) for line in self.HEADER]
        # 给出工作目录的绝对路径（仅一次，不重复说 cwd_name）
        if scripts_dir is not None:
            parts.append(
                f"\nWorking directory absolute path: `{Path(scripts_dir).resolve()}`"
            )
            parts.append(
                "The cwd_name shown in samples below is the basename of this path.\n"
            )
        if scripts_dir is not None:
            parts += self._current_scripts_block(Path(scripts_dir))
        # 闭环反馈：下游使用统计
        if self.downstream_stats:
            parts += self._downstream_stats_block()

        # graph + observation contrastive（与 v1-chunk 一致）
        # 同一 trajectory 的 task info 只在第一个 chunk 渲染时输出一次
        rendered_task_info: set = set()
        for i, (key, group) in enumerate(sorted(groups.items()), start=1):
            traj_name, chunk_id = key
            parts.append(f"\n# Chunk {i}")

            # 在该 trajectory 第一次出现时渲染 task info
            if traj_name not in rendered_task_info:
                rendered_task_info.add(traj_name)
                task_info = self._extract_task_info_for_group(group)
                if task_info:
                    parts += self._render_task_info_block(task_info)

            if group["graph"]:
                parts += self._render_graph_contrastive_block(group["graph"])

            if group["observations"]:
                parts.append("\n## Observation Contrastive Samples")
                parts.append(
                    "Each sample below is the SAME step shown twice: NEGATIVE has the "
                    "full observation the agent saw; POSITIVE has only the brief lines "
                    "(annotator-marked useful lines, identified by LLM). The two sides "
                    "differ ONLY in the observation field — same step, same action."
                )
                for j, obs in enumerate(group["observations"], start=1):
                    step_idx = obs.get("step_index")
                    brief_lines = obs.get("brief_lines", [])
                    rationale = obs.get("annotation_rationale", "")
                    parts.append(
                        f"\n### Observation Sample {j} (step {step_idx}, "
                        f"brief lines: {brief_lines})"
                    )
                    if rationale:
                        parts.append(f"Annotation rationale: {rationale}")
                    else:
                        parts.append(
                            "Annotation rationale: (not provided — judge whether the "
                            "brief lines are sufficient for the agent's next step)"
                        )
                    parts.append("Negative (full observation):")
                    parts.append(self.serializer.serialize(obs["negative_sample"]))
                    parts.append("\nPositive (brief observation only):")
                    parts.append(self.serializer.serialize(obs["positive_sample"]))

        # cost_hotspot 段落（v2 新增；不是 contrastive，只是真实成本信号）
        if hotspot_samples:
            # 按 total_observation_chars 降序，cap 到 15 个避免 prompt 爆炸
            hotspot_samples.sort(
                key=lambda s: s.get("total_observation_chars", 0), reverse=True
            )
            shown = hotspot_samples[:15]
            parts.append(
                f"\n# Cost Hotspot Samples ({len(shown)} of {len(hotspot_samples)} shown)"
            )
            parts.append(
                "Each hotspot is a bash verb called 3+ times in one trajectory, "
                "accumulating real observation output. total_observation_chars is "
                "the ACTUAL chars the agent saw — no fabrication. These are the "
                "strongest signal for cost-saving scripts: a batching script that "
                "collapses N occurrences into 1 step saves N-1 steps of cache+output."
            )
            for j, hs in enumerate(shown, start=1):
                verb = hs.get("verb", "?")
                occs = hs.get("occurrences", [])
                total = hs.get("total_observation_chars", 0)
                parts.append(
                    f"\n### Hotspot {j}: verb=`{verb}`, "
                    f"occurrences={len(occs)}, total={total} chars"
                )
                for k, occ in enumerate(occs, start=1):
                    parts.append(
                        f"  {k}. step {occ.get('step_index')}: "
                        f"`{occ.get('command', '')[:200]}` "
                        f"({occ.get('observation_chars', 0)} chars)"
                    )

        parts.append(self.FOOTER)
        return "\n".join(parts)

    def _current_scripts_block(self, scripts_dir: Path) -> List[str]:
        """列出当前 scripts，用绝对路径而非 ./<name>/，避免下游猜路径。"""
        lines = ["\n# Current scripts in this directory"]
        if not scripts_dir.exists():
            lines.append("(none yet)")
            return lines
        subdirs = sorted(p for p in scripts_dir.iterdir() if p.is_dir())
        if not subdirs:
            lines.append("(none yet)")
            return lines
        for d in subdirs:
            intro = d / "intro.json"
            lines.append(f"\n## {d.name}/")
            if intro.exists():
                try:
                    text = intro.read_text(encoding="utf-8").strip()
                except OSError as exc:
                    lines.append(f"(failed to read intro.json: {exc})")
                    continue
                lines.append(f"intro.json:\n{text}")
            else:
                lines.append(
                    "(no intro.json — create one if you keep this script, "
                    "or delete the directory if obsolete)"
                )
        return lines

    def _downstream_stats_block(self) -> List[str]:
        """下游使用统计：让进化 agent 看到每个 script 的真实调用情况。"""
        lines = ["\n# Downstream usage stats (from last held-out evaluation)"]
        lines.append(
            "Use these stats to decide which scripts to keep, modify, or delete. "
            "A script with 0 calls is dead weight — delete it. A script with high "
            "failure rate needs fixing."
        )
        for name, stats in sorted(self.downstream_stats.items()):
            calls = stats.get("calls", 0)
            failures = stats.get("failures", 0)
            saved = stats.get("saved_yuan", 0.0)
            notes = stats.get("notes", "")
            fail_rate = (failures / calls * 100) if calls > 0 else 0.0
            lines.append(
                f"\n## {name}: calls={calls}, failures={failures} "
                f"({fail_rate:.1f}%), saved={saved:.4f} yuan"
            )
            if notes:
                lines.append(f"  notes: {notes}")
        return lines


# ============================================================================
# V2 agent runner — 修 cwd 问题
# ============================================================================


class MiniSweAgentRunnerV2(MiniSweAgentRunner):
    """v2 agent runner：用 ``uv tool run --from`` 代替 ``uv run --directory``。

    v1-chunk 用 ``uv run --directory <mini_swe_agent_dir>``，会把 cwd 强制
    切到 mini_swe_agent_dir，覆盖 ``subprocess.run(cwd=scripts_dir)``。agent
    启动后 ``pwd`` 看到的是 mini-swe-agent 安装目录，会自己去探索父目录、
    发现仓库里其他 evolve 输出（如 ``.evolve_scripts_baseline/``），并参考
    baseline 的脚本设计。

    v2 改用 ``uv tool run --from <mini_swe_agent_dir> mini ...``，``--from``
    只指定包来源、不切换 cwd，``subprocess.run(cwd=scripts_dir)`` 生效，
    agent 启动后 ``pwd`` 直接是 scripts_dir。
    """

    def run(self, prompt: str, prompt_path: Path, output_path: Path, cwd: Path) -> None:
        env, model, temperature, model_class = self._load_llm_env()
        task = (
            f"Read the full evolution instruction from {prompt_path}. "
            "Then modify, add, or remove scripts (each with an intro.json) and update "
            "instruction.md in the current working directory as requested. "
            "Do not edit the prompt file or contrastive sample files. "
            "Do NOT explore parent directories or other evolve outputs in the repo."
        )
        cmd = [
            "uv", "tool", "run", "--from", str(self.mini_swe_agent_dir),
            "mini",
            "-m", model,
            "--model-class", model_class,
            "--environment-class", "local",
            "-y", "--exit-immediately",
            "--cost-limit", "0",
            "-o", str(output_path),
            "-t", task,
            "-c", "mini.yaml",
        ]
        if temperature is not None:
            cmd += ["-c", f"model.model_kwargs.temperature={temperature}"]
        logger.info("mini-swe-agent cmd: %s", " ".join(shlex.quote(x) for x in cmd))
        logger.info("prompt %s to %s", "would be saved" if self.dry_run else "saved", prompt_path)
        if self.dry_run:
            return
        output_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text(prompt, encoding="utf-8")
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(cwd),
                env={**os.environ, **env},
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired as exc:
            logger.error(
                "mini-swe-agent timed out after %ds (cwd=%s): %s",
                self.timeout,
                cwd,
                exc,
            )
            raise RuntimeError(f"mini-swe-agent timed out after {self.timeout}s") from exc
        if proc.returncode != 0:
            logger.error(
                "mini-swe-agent failed (rc=%d)\nstdout:\n%s\nstderr:\n%s",
                proc.returncode,
                proc.stdout,
                proc.stderr,
            )
            raise RuntimeError(
                f"mini-swe-agent exited with code {proc.returncode}"
            )
        logger.info("mini-swe-agent finished (cwd=%s)", cwd)
        if proc.stdout:
            tail = proc.stdout[-2000:]
            logger.info("mini-swe-agent stdout tail:\n%s", tail)
        if proc.stderr:
            tail = proc.stderr[-2000:]
            logger.info("mini-swe-agent stderr tail:\n%s", tail)


# ============================================================================
# V2 script evolver — 接受 downstream_stats 注入
# ============================================================================


class ChunkScriptEvolverV2(ChunkScriptEvolver):
    """v2 script evolver：接受 downstream_stats 注入到 prompt builder。

    v1-chunk 的 evolver 每个 batch 都用同一个 prompt builder，看不到下游反馈。
    v2 在 ``run`` 里调用 ``_maybe_refresh_stats`` 拉取最新的下游使用统计
    （如果有 stats_provider），重新构造 prompt builder。

    典型用法：调用方传入一个 ``stats_provider`` 回调，evolver 在每个 batch
    前调用它拿到最新的 stats。``stats_provider`` 可以读最新的下游 trial
    日志、聚合调用次数/失败率/节省成本，返回 ``{script_name: {...}}``。

    另外覆盖 ``find_samples``：v1-chunk 的 glob 只匹配
    ``contrastive_*_chunk_*.json``，漏掉了 v2 新增的 ``cost_hotspot_*.json``
    文件，导致 cost_hotspot 信号从未真正进过 prompt。v2 同时匹配两类文件名。
    """

    name = "evolve_chunk_v2"

    def find_samples(self, result_dir, task=None) -> List[Path]:
        """覆盖父类 glob，把 cost_hotspot 文件也捞进来。

        v1-chunk 的 glob 模式是 ``contrastive_*_chunk_*.json``，cost_hotspot
        文件命名为 ``cost_hotspot_<verb>.json``（不以 ``contrastive_`` 开头），
        会被父类 glob 漏掉。这里改成同时匹配两类文件名。
        """
        files = sorted(
            Path(result_dir).glob("**/agent/contrastive_*_chunk_*.json")
        ) + sorted(
            Path(result_dir).glob("**/agent/cost_hotspot_*.json")
        )
        if not task:
            return files
        matched = [p for p in files if self._task_matches(p, task)]
        return matched or [p for p in files if task in str(p)]


    def __init__(
        self,
        scripts_dir,
        runner: AgentRunner,
        prompt_builder: Optional[ChunkEvolvePromptBuilderV2] = None,
        batch_size: int = 2,
        output_dir: Optional[Path] = None,
        resume: bool = True,
        stats_provider=None,
    ):
        super().__init__(
            scripts_dir=scripts_dir,
            runner=runner,
            prompt_builder=prompt_builder or ChunkEvolvePromptBuilderV2(
                serializer=TrajectorySerializer(),
            ),
            batch_size=batch_size,
            output_dir=output_dir,
            resume=resume,
        )
        self.stats_provider = stats_provider

    def run(self, result_dir, task: Optional[str] = None) -> Path:
        """在每个 batch 前刷新 downstream_stats（如果有 stats_provider）。"""
        result_dir = Path(result_dir).resolve()
        self.scripts_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_instruction_file()

        output_dir = self.output_dir or (result_dir / "evolve_logs")
        output_dir.mkdir(parents=True, exist_ok=True)
        samples = self.find_samples(result_dir, task)
        logger.info("found %d contrastive samples", len(samples))
        if not samples:
            logger.warning(
                "no contrastive samples found under %s (task=%r); evolve stage is a no-op",
                result_dir,
                task,
            )
            return output_dir

        failures: List[int] = []
        for batch_id, batch in enumerate(self._batched(samples, self.batch_size), start=1):
            output_path = output_dir / f"evolve_batch_{batch_id}.traj.json"
            prompt_path = output_path.with_suffix(".prompt.md")
            sentinel = output_path.with_suffix(".done")
            if self.resume and sentinel.exists():
                logger.info("batch %d already done (sentinel %s exists), skipping", batch_id, sentinel)
                continue
            # 在每个 batch 前刷新 downstream_stats
            self._maybe_refresh_stats()
            try:
                self.runner.run(
                    prompt=self.prompt_builder.build(
                        batch,
                        cwd_name=self.scripts_dir.name,
                        scripts_dir=self.scripts_dir,
                    ),
                    prompt_path=prompt_path,
                    output_path=output_path,
                    cwd=self.scripts_dir,
                )
                sentinel.write_text(
                    json.dumps({"batch_id": batch_id, "samples": [str(p) for p in batch]}),
                    encoding="utf-8",
                )
            except Exception as exc:
                logger.exception("batch %d failed: %s", batch_id, exc)
                failures.append(batch_id)
                continue
        if failures:
            logger.warning("batches failed: %s", failures)
        else:
            logger.info("all batches finished")
        for w in self._validate_intros():
            logger.warning("intro.json: %s", w)
        return output_dir

    def _maybe_refresh_stats(self) -> None:
        """如果传了 stats_provider，调用它并重建 prompt_builder。"""
        if not self.stats_provider:
            return
        try:
            stats = self.stats_provider() or {}
        except Exception as exc:
            logger.warning("stats_provider failed: %s", exc)
            return
        if not isinstance(stats, dict):
            logger.warning("stats_provider returned non-dict: %r", type(stats))
            return
        # 复用旧 serializer，只换 downstream_stats
        old_serializer = getattr(self.prompt_builder, "serializer", None) or TrajectorySerializer()
        self.prompt_builder = ChunkEvolvePromptBuilderV2(
            serializer=old_serializer,
            downstream_stats=stats,
        )
        logger.info("refreshed downstream_stats: %d scripts", len(stats))


# ============================================================================
# Factory + CLI
# ============================================================================


def make_v2_annotator(
    config_path,
    workers: int = 1,
    retry_failed: int = 1,
    long_obs_threshold: int = LONG_OBS_THRESHOLD_DEFAULT,
) -> ChunkTrajectoryAnnotatorV2:
    return ChunkTrajectoryAnnotatorV2(
        config_path=config_path,
        workers=workers,
        retry_failed=retry_failed,
        long_obs_threshold=long_obs_threshold,
    )


def make_v2_contrastive_builder(
    min_reduction_ratio: float = MIN_REDUCTION_RATIO_DEFAULT,
    hotspot_min_occurrences: int = HOTSPOT_MIN_OCCURRENCES_DEFAULT,
    hotspot_min_total_chars: int = HOTSPOT_MIN_TOTAL_CHARS_DEFAULT,
) -> ChunkContrastiveSampleBuilderV2:
    return ChunkContrastiveSampleBuilderV2(
        min_reduction_ratio=min_reduction_ratio,
        hotspot_min_occurrences=hotspot_min_occurrences,
        hotspot_min_total_chars=hotspot_min_total_chars,
    )


def make_v2_evolver(
    scripts_dir,
    config_path,
    mini_swe_agent_dir,
    batch_size: int = 5,
    max_observation_chars: int = 1000,
    output_dir: Optional[str] = None,
    dry_run: bool = False,
    resume: bool = True,
    stats_provider=None,
) -> ChunkScriptEvolverV2:
    return ChunkScriptEvolverV2(
        scripts_dir=scripts_dir,
        runner=MiniSweAgentRunnerV2(
            mini_swe_agent_dir=mini_swe_agent_dir,
            llm_config=config_path,
            dry_run=dry_run,
        ),
        prompt_builder=ChunkEvolvePromptBuilderV2(
            serializer=TrajectorySerializer(max_observation_chars=max_observation_chars),
        ),
        batch_size=batch_size,
        output_dir=Path(output_dir).resolve() if output_dir else None,
        resume=resume,
        stats_provider=stats_provider,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Evolve v2 chunk: v1-chunk + is_annotated fix + cost_hotspot "
                    "signal + behavior-contract prompt + cwd fix.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_annotate = sub.add_parser("annotate", help="Stage 1: dependencies + brief_observations (fixed)")
    _add_common(p_annotate)
    _add_config(p_annotate)
    _add_annotate(p_annotate)
    p_annotate.add_argument(
        "--long-obs-threshold", type=int, default=LONG_OBS_THRESHOLD_DEFAULT,
        help="observation 字符数超过此阈值才标注 brief (default: %(default)s)",
    )

    p_contrast = sub.add_parser("contrastive", help="Stage 2: graph + obs + cost_hotspot samples")
    _add_common(p_contrast)
    p_contrast.add_argument(
        "--min-reduction-ratio", type=float, default=MIN_REDUCTION_RATIO_DEFAULT,
        help="mini chunk 相对原 chunk 至少省掉这么比例的 action step 才作为 graph contrastive sample (default: %(default)s)",
    )
    p_contrast.add_argument(
        "--hotspot-min-occurrences", type=int, default=HOTSPOT_MIN_OCCURRENCES_DEFAULT,
        help="同一 bash verb 在同一 trajectory 内至少出现这么多次才算 cost_hotspot (default: %(default)s)",
    )
    p_contrast.add_argument(
        "--hotspot-min-total-chars", type=int, default=HOTSPOT_MIN_TOTAL_CHARS_DEFAULT,
        help="cost_hotspot 的累计 observation 字符数下限，低于此值不输出 (default: %(default)s)",
    )

    p_evolve = sub.add_parser("evolve", help="Stage 3: evolve scripts from v2 chunk samples")
    _add_common(p_evolve)
    _add_config(p_evolve)
    _add_evolve(p_evolve)

    p_run = sub.add_parser("run", help="run the full v2 chunk pipeline")
    _add_common(p_run)
    _add_config(p_run)
    _add_annotate(p_run)
    p_run.add_argument(
        "--long-obs-threshold", type=int, default=LONG_OBS_THRESHOLD_DEFAULT,
        help="observation 字符数超过此阈值才标注 brief (default: %(default)s)",
    )
    _add_evolve(p_run)
    p_run.add_argument(
        "--min-reduction-ratio", type=float, default=MIN_REDUCTION_RATIO_DEFAULT,
        help="mini chunk 相对原 chunk 至少省掉这么比例的 action step 才作为 graph contrastive sample (default: %(default)s)",
    )
    p_run.add_argument(
        "--hotspot-min-occurrences", type=int, default=HOTSPOT_MIN_OCCURRENCES_DEFAULT,
        help="同一 bash verb 在同一 trajectory 内至少出现这么多次才算 cost_hotspot (default: %(default)s)",
    )
    p_run.add_argument(
        "--hotspot-min-total-chars", type=int, default=HOTSPOT_MIN_TOTAL_CHARS_DEFAULT,
        help="cost_hotspot 的累计 observation 字符数下限，低于此值不输出 (default: %(default)s)",
    )
    p_run.add_argument(
        "--skip", action="append", default=[],
        help="stage name(s) to skip (annotate_chunk_v2 / contrastive_chunk_v2 / evolve_chunk_v2)",
    )

    args = parser.parse_args(argv)
    _setup_logging(getattr(args, "log_file", None))

    if args.cmd == "annotate":
        make_v2_annotator(
            config_path=args.config,
            workers=args.workers,
            retry_failed=args.retry_failed,
            long_obs_threshold=args.long_obs_threshold,
        ).run(args.result_dir, task=args.task)
    elif args.cmd == "contrastive":
        builder = make_v2_contrastive_builder(
            min_reduction_ratio=args.min_reduction_ratio,
            hotspot_min_occurrences=args.hotspot_min_occurrences,
            hotspot_min_total_chars=args.hotspot_min_total_chars,
        )
        builder.run(args.result_dir, task=args.task)
    elif args.cmd == "evolve":
        make_v2_evolver(
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
        skip = set(args.skip)
        result_dir = Path(args.result_dir).resolve()

        if "annotate_chunk_v2" not in skip and "annotate" not in skip:
            logger.info("[stage 1/3] annotate_chunk_v2 on %s", result_dir)
            make_v2_annotator(
                config_path=args.config,
                workers=args.workers,
                retry_failed=args.retry_failed,
                long_obs_threshold=args.long_obs_threshold,
            ).run(result_dir, task=args.task)

        if "contrastive_chunk_v2" not in skip and "contrastive" not in skip:
            logger.info("[stage 2/3] contrastive_chunk_v2 on %s", result_dir)
            builder = make_v2_contrastive_builder(
                min_reduction_ratio=args.min_reduction_ratio,
                hotspot_min_occurrences=args.hotspot_min_occurrences,
                hotspot_min_total_chars=args.hotspot_min_total_chars,
            )
            builder.run(result_dir, task=args.task)

        if "evolve_chunk_v2" not in skip and "evolve" not in skip:
            logger.info("[stage 3/3] evolve_chunk_v2 on %s", result_dir)
            make_v2_evolver(
                scripts_dir=args.scripts_dir,
                config_path=args.config,
                mini_swe_agent_dir=args.mini_swe_agent_dir,
                batch_size=args.batch_size,
                max_observation_chars=args.max_observation_chars,
                output_dir=args.output_dir,
                dry_run=args.dry_run,
                resume=not args.no_resume,
            ).run(result_dir, task=args.task)


if __name__ == "__main__":
    main()
