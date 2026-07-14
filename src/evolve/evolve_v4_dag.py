"""Evolve v4: Mini-DAG based chunking — three explicit contrastive signals.

设计文档见 ``evolve_v4_mini_dag_based_chunking.md``。本模块只做实现落地。

与 v2 的核心差异
----------------
v2 一个 chunk 产 1 个 graph contrastive 样本（minimal vs. full），evolve agent
要自己推断"为什么这些 step 可省"。v4 把同一个 T*（minimal subgraph，仍由 v2
的 anchor-based 逻辑产出）拆成三类**显式标注**的细粒度信号：

1. ``v4_skippable``  — T 中被 T* 跳过的连续 step 段，用 ``<skippable_steps>``
   标出，并给出段前（T* 中依赖）+ 段后（T* 中出边到达）的最小上下文。
2. ``v4_mergeable``  — T* 中"前序依赖集合相同 + op_type 相同 + 都成功 +
   files_touched 不相交"的 step 对，用 ``<mergable_steps>`` 标出。
3. ``v4_optobs``     — T* 中 observation 过长的 step，标
   ``<optimizable_observation_step>``（纯标注，不要求 evolve agent 现场生成
   retrieve trajectory），提示可设计 indexing/retrieve 脚本。

T* 直接复用 v2 ``_build_positive_sample`` 的产物（anchor 选择 + 失败 step 过滤
+ 跨 chunk 前序上下文）。v4 不重新发明 T* —— 否则会重新踩"最后一步经常是失败
命令"的坑（见 ``graph_contrastive_improvement.md``）。

prompt 层面 v4 还做了三件事（见 ``ChunkEvolvePromptBuilderV4``）：
* 顶部突出一条 SKIP 指示：样本若不提供新的成本优化建议，**不要改动已有
  scripts**，no-op 优于回归。
* instruction.md 只放 high-level 指导思想，不放任何 script 用法。
* 每个 script 修改后必须验证。

evolver 层面加了 **no-op 检测**：每个 batch 跑完比对 scripts 目录内容 hash，
无变化时记 INFO（不视为失败），把 SKIP 指示落到可观测层。

用法
----
::

    # 全量跑
    python -m src.evolve.evolve_v4_dag run results/... \\
        --config _config/deepseekv4_flash.yaml \\
        --scripts-dir .evolve_scripts_v4 --workers 4 --batch-size 4

    # Phase 0 离线统计（决定三类信号各自上限，不写 evolve 逻辑）
    python -m src.evolve.evolve_v4_dag dry-stat results/...

    # 单段调试
    python -m src.evolve.evolve_v4_dag contrastive results/...
    python -m src.evolve.evolve_v4_dag evolve      results/... --dry-run
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from ._chunk_helpers import (
    classify_step_meta,
    extract_bash_command,
    observation_chars,
)
from .evolve_v1_chunk import LONG_OBS_THRESHOLD_DEFAULT
from .evolve_v2_chunk import (
    ChunkContrastiveSampleBuilderV2,
    ChunkEvolvePromptBuilderV2,
    ChunkScriptEvolverV2,
    ChunkTrajectoryAnnotatorV2,
    MiniSweAgentRunnerV2,
)
from .evolver import TrajectorySerializer
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


# 每 trajectory 各类样本上限（防 prompt 膨胀 / 长尾失控，见设计文档 §6）
MAX_SKIPPABLE_PER_CHUNK = 3
MAX_MERGEABLE_PER_CHUNK = 2
MAX_OPTOBS_PER_CHUNK = 2
# skippable 段至少这么长才输出（单步可跳信号太弱）
MIN_SKIPPABLE_SEGMENT_LEN = 2
# context_before 最多展示的 T* 前序 step 数
MAX_CONTEXT_BEFORE = 5


# ============================================================================
# Stage 1: V4 annotator — 直接复用 v2（dependencies + step_meta），无新增标注
# ============================================================================


class ChunkTrajectoryAnnotatorV4(ChunkTrajectoryAnnotatorV2):
    """v4 复用 v2 的 annotate（dependencies + brief_observations + step_meta）。

    v4 思路明确"复用 v2 的 annotate"，所以这里不改任何标注逻辑，只换 stage
    名字以便 ``--skip annotate_chunk_v4`` 生效。
    """

    name = "annotate_chunk_v4"


# ============================================================================
# Stage 2: V4 contrastive builder — 三类显式信号
# ============================================================================


class ChunkContrastiveSampleBuilderV4(ChunkContrastiveSampleBuilderV2):
    """v4 contrastive builder：把 v2 的 T* 拆成 skippable/mergeable/optobs 三类。

    输出文件（写到 trajectory.json 同目录的 ``agent/`` 下）::

        v4_skippable_chunk_<id>_seg<n>.json
        v4_mergeable_chunk_<id>_pair<n>.json
        v4_optobs_chunk_<id>_step<s>.json

    文件名都含 ``chunk_<id>``，所以 v1-chunk 的 ``_chunk_key`` 仍能按
    (trajectory, chunk_id) 分组，同一 chunk 的三类样本会进同一个 prompt。
    """

    name = "contrastive_chunk_v4"

    # optobs 触发阈值（observation 字符数）。class 级默认保证直接实例化也安全；
    # make_v4_contrastive_builder 会按 CLI 参数覆盖。
    long_obs_threshold: int = LONG_OBS_THRESHOLD_DEFAULT

    def build_file(self, path) -> List[Path]:
        samples = self.compute_samples(path)
        outs: List[Path] = []
        for sample in samples:
            stype = sample["type"]  # v4_skippable / v4_mergeable / v4_optobs
            kind = stype.split("_", 1)[1]  # skippable / mergeable / optobs
            tag = sample.get("_file_tag", "0")
            out = path.with_name(f"v4_{kind}_chunk_{sample['chunk_id']}_{tag}.json")
            out.write_text(
                json.dumps(sample, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            outs.append(out)
        logger.info("built %d v4 samples for %s", len(outs), path)
        return outs

    # ---------- 样本构造（纯函数，不写盘） ----------

    def compute_samples(self, path) -> List[dict]:
        """返回一个 trajectory 的所有 v4 样本（不写文件）。

        供 ``build_file`` 和 ``dry-stat`` 共用。流程：
        1. 复用 v2 的 phase-based ``_split_into_chunks`` 切 chunk；
        2. 每个 chunk 复用 v2 的 ``_build_positive_sample`` 拿到 T*；
        3. anchor 无效（chunk 全失败）的 chunk 跳过 —— 没学习价值；
        4. 从 (T, T*) 派生 skippable / mergeable / optobs 三类样本。
        """
        trajectory = json.loads(Path(path).read_text(encoding="utf-8"))
        if trajectory.get("dependencies") is None:
            raise ValueError(
                "trajectory has no dependencies field; run annotate_chunk_v4 first"
            )
        # _build_positive_sample 通过 getattr 读 self._current_trajectory 做
        # 跨 chunk 前序回溯，这里缓存整条 trajectory 供它使用。
        self._current_trajectory = trajectory
        try:
            chunks = self._split_into_chunks(trajectory, self.CHUNK_SIZE)
            samples: List[dict] = []
            for chunk in chunks:
                positive = self._build_positive_sample(chunk)
                if not positive.get("anchor_success"):
                    # chunk 没有成功的 verify/write/read anchor → 无信号
                    continue
                samples.extend(self._build_skippable(path, chunk, positive))
                samples.extend(self._build_mergeable(path, chunk, positive))
                samples.extend(self._build_optobs(path, chunk, positive))
        finally:
            self._current_trajectory = None
        return samples

    # ----- skippable -----

    def _build_skippable(
        self, path: Path, chunk: dict, positive: dict
    ) -> List[dict]:
        """T 中被 T* 跳过的连续 step 段 → v4_skippable 样本。"""
        local_steps = self._local_action_steps(chunk)
        n_local = len(local_steps)
        keep = set(positive.get("minimal_step_indices", []) or [])
        # skippable = {1..n_local} \ keep
        skippable_idx = [i for i in range(1, n_local + 1) if i not in keep]

        # 切成连续段
        segments: List[List[int]] = []
        for i in skippable_idx:
            if segments and i == segments[-1][-1] + 1:
                segments[-1].append(i)
            else:
                segments.append([i])
        # 过滤过短段 + 按段长降序取前 N
        segments = [s for s in segments if len(s) >= MIN_SKIPPABLE_SEGMENT_LEN]
        segments.sort(key=len, reverse=True)
        segments = segments[:MAX_SKIPPABLE_PER_CHUNK]

        outs: List[dict] = []
        for n, seg in enumerate(segments, start=1):
            seg_start, seg_end = seg[0], seg[-1]
            context_before = [
                i for i in sorted(keep) if i < seg_start
            ][-MAX_CONTEXT_BEFORE:]
            context_after = self._pick_context_after(keep, chunk, seg)
            outs.append({
                "type": "v4_skippable",
                "chunk_id": chunk["chunk_id"],
                "chunk_range": chunk["chunk_range"],
                "source_trajectory": str(path),
                "skippable_local_indices": seg,
                "context_before_local": context_before,
                "context_after_local": context_after,
                "context_before": [
                    self._step_brief(local_steps[i - 1], self._global_index(chunk, i))
                    for i in context_before
                ],
                "skippable_steps": [
                    self._step_brief(local_steps[i - 1], self._global_index(chunk, i))
                    for i in seg
                ],
                "context_after": [
                    self._step_brief(local_steps[i - 1], self._global_index(chunk, i))
                    for i in context_after
                ],
                "_file_tag": f"seg{n}",
            })
        return outs

    def _pick_context_after(
        self, keep: set, chunk: dict, segment: List[int]
    ) -> List[int]:
        """选段后的 T* step：优先"出边命中段内 step"的后继，否则取段后第一个 T* step。"""
        deps = chunk.get("dependencies", {}) or {}
        seg_set = set(segment)
        seg_end = segment[-1]
        for i in sorted(keep):
            if i <= seg_end:
                continue
            i_deps = {int(d) for d in deps.get(str(i), [])}
            if i_deps & seg_set:
                return [i]
        # 兜底：段后第一个 keep step
        for i in sorted(keep):
            if i > seg_end:
                return [i]
        return []

    # ----- mergeable -----

    def _build_mergeable(
        self, path: Path, chunk: dict, positive: dict
    ) -> List[dict]:
        """T* 中前序依赖相同 + op_type 相同 + 都成功 + files 不交的 step 对。"""
        local_steps = self._local_action_steps(chunk)
        keep = sorted(i for i in positive.get("minimal_step_indices", []) or [] if i > 0)
        deps = chunk.get("dependencies", {}) or {}

        def dep_set(i: int) -> set:
            return {int(d) for d in deps.get(str(i), [])}

        pairs: List[Tuple[int, int]] = []
        for a in range(len(keep)):
            for b in range(a + 1, len(keep)):
                i, j = keep[a], keep[b]
                meta_i = local_steps[i - 1].get("step_meta") or classify_step_meta(local_steps[i - 1])
                meta_j = local_steps[j - 1].get("step_meta") or classify_step_meta(local_steps[j - 1])
                if dep_set(i) != dep_set(j):
                    continue
                if meta_i.get("op_type") != meta_j.get("op_type"):
                    continue
                if not (meta_i.get("success") and meta_j.get("success")):
                    continue
                if set(meta_i.get("files_touched", [])) & set(meta_j.get("files_touched", [])):
                    continue
                pairs.append((i, j))
                if len(pairs) >= MAX_MERGEABLE_PER_CHUNK:
                    break
            if len(pairs) >= MAX_MERGEABLE_PER_CHUNK:
                break

        outs: List[dict] = []
        for n, (i, j) in enumerate(pairs, start=1):
            step_i, step_j = local_steps[i - 1], local_steps[j - 1]
            outs.append({
                "type": "v4_mergeable",
                "chunk_id": chunk["chunk_id"],
                "chunk_range": chunk["chunk_range"],
                "source_trajectory": str(path),
                "merge_pair": [i, j],
                "shared_dependencies": sorted(dep_set(i)),
                "steps": [
                    self._step_brief(step_i, self._global_index(chunk, i)),
                    self._step_brief(step_j, self._global_index(chunk, j)),
                ],
                "merged_form_example": self._merged_form_example(step_i, step_j),
                "_file_tag": f"pair{n}",
            })
        return outs

    @staticmethod
    def _merged_form_example(step_i: dict, step_j: dict) -> str:
        """把两个 step 的 bash 命令拼成 `a && b` 作示意（标注未验证）。"""
        cmd_i = extract_bash_command(step_i)
        cmd_j = extract_bash_command(step_j)
        if cmd_i and cmd_j:
            return f"{cmd_i} && {cmd_j}  # example merged form, not verified"
        return ""

    # ----- optobs -----

    def _build_optobs(
        self, path: Path, chunk: dict, positive: dict
    ) -> List[dict]:
        """T* 中 observation 过长的 step → v4_optobs 纯标注样本。"""
        local_steps = self._local_action_steps(chunk)
        deps = chunk.get("dependencies", {}) or {}
        keep = set(positive.get("minimal_step_indices", []) or [])

        candidates = []
        for i in sorted(k for k in keep if k > 0):
            step = local_steps[i - 1]
            nchars = observation_chars(step.get("observation", ""))
            if nchars > self.long_obs_threshold:
                candidates.append((i, nchars))
        # 按字符数降序取前 N
        candidates.sort(key=lambda x: x[1], reverse=True)
        candidates = candidates[:MAX_OPTOBS_PER_CHUNK]

        outs: List[dict] = []
        total_chunk_obs = sum(observation_chars(s.get("observation", "")) for s in local_steps)
        for n, (i, nchars) in enumerate(candidates, start=1):
            predecessors, successors = self._dep_and_dependent(i, keep, deps)
            outs.append({
                "type": "v4_optobs",
                "chunk_id": chunk["chunk_id"],
                "chunk_range": chunk["chunk_range"],
                "source_trajectory": str(path),
                "step_local_index": i,
                "observation_chars": nchars,
                "pct_of_chunk_obs": round(nchars / total_chunk_obs * 100, 1) if total_chunk_obs else 0.0,
                "target_step": self._step_brief(local_steps[i - 1], self._global_index(chunk, i)),
                "predecessor_actions": [
                    self._action_only(local_steps[k - 1], self._global_index(chunk, k))
                    for k in predecessors
                ],
                "successor_actions": [
                    self._action_only(local_steps[k - 1], self._global_index(chunk, k))
                    for k in successors
                ],
                "_file_tag": f"step{i}",
            })
        return outs

    @staticmethod
    def _dep_and_dependent(
        i: int, keep: set, deps: Dict[str, list]
    ) -> Tuple[List[int], List[int]]:
        """返回 step i 在 T* 内的前序 + 后继（local index）。"""
        preds = sorted(k for k in {int(d) for d in deps.get(str(i), [])} if k in keep and k > 0)
        succs = []
        for k in sorted(keep):
            if k <= i or k == 0:
                continue
            k_deps = {int(d) for d in deps.get(str(k), [])}
            if i in k_deps:
                succs.append(k)
        return preds, succs

    # ----- 共享 step 渲染辅助 -----

    @staticmethod
    def _global_index(chunk: dict, local_i: int) -> int:
        """local chunk action-step 索引(1-based) → 原始 trajectory T 的全局 action-step 索引。"""
        cr = chunk.get("chunk_range", [0, 0])
        return cr[0] + local_i - 1

    def _local_action_steps(self, chunk: dict) -> List[dict]:
        """chunk.steps 里的 action step 列表（local 1-based 索引对应这里）。"""
        return [s for s in chunk.get("steps", []) if self._is_action_step(s)]

    @staticmethod
    def _step_brief(step: dict, display_index: Optional[int] = None) -> dict:
        """精简 step：只留 tool_calls + observation，供 prompt 渲染。

        display_index 为原始 trajectory T 的全局 step index，注入 _display_index 后
        TrajectorySerializer 会用它而非 block 内位置计数（避免 <skippable_steps> 等块内
        步号从 1 重新计数，保持与原始 trajectory 一致）。
        """
        brief = {
            "tool_calls": copy.deepcopy(step.get("tool_calls") or []),
            "observation": copy.deepcopy(step.get("observation", "")),
        }
        if display_index is not None:
            brief["_display_index"] = display_index
        return brief

    @staticmethod
    def _action_only(step: dict, display_index: Optional[int] = None) -> dict:
        """只留 action（tool_calls），不给 observation（optobs 上下文用）。"""
        brief = {"tool_calls": copy.deepcopy(step.get("tool_calls") or [])}
        if display_index is not None:
            brief["_display_index"] = display_index
        return brief

    # ---------- Phase 0 离线统计 ----------

    def stat_file(self, path) -> dict:
        """返回单条 trajectory 的三类信号统计（不写盘）。"""
        samples = self.compute_samples(path)
        traj = json.loads(Path(path).read_text(encoding="utf-8"))
        n_action = sum(1 for s in traj.get("steps", []) if self._is_action_step(s))
        counts = {"v4_skippable": 0, "v4_mergeable": 0, "v4_optobs": 0}
        skippable_steps = 0
        for s in samples:
            t = s["type"]
            if t in counts:
                counts[t] += 1
            if t == "v4_skippable":
                skippable_steps += len(s.get("skippable_local_indices", []))
        return {
            "trajectory": str(path),
            "n_action_steps": n_action,
            "samples": counts,
            "skippable_steps": skippable_steps,
            "skippable_ratio": round(skippable_steps / n_action, 3) if n_action else 0.0,
        }


# ============================================================================
# Stage 3: V4 prompt builder — SKIP 指示 + 三类渲染
# ============================================================================


# 顶部突出的 SKIP 指示（用户新增要求）：样本若不提供新成本优化建议，不要改动 scripts。
SKIP_DIRECTIVE = (
    "\n## ⚠️ IMPORTANT — Do not change scripts unless there is a NEW cost-saving idea\n"
    "The samples below are SIGNALS, not obligations. If they do not suggest a NEW\n"
    "cost optimization that your current scripts do not already capture, DO NOT\n"
    "modify, add, merge, or delete any script — finish immediately with scripts\n"
    "unchanged. You are NOT required to make a big change every evolution round.\n"
    "Effectiveness is the only priority: a no-op evolution is strictly better than\n"
    "a regression.\n"
)

# 三类信号的英文标签 + 描述（每个 Task 内按此顺序渲染，描述仅在每类首个 sample 出现一次）。
TYPE_LABELS = {
    "skippable": "Skippable-step",
    "mergeable": "Mergeable-step",
    "optobs": "Optimizable-observation",
}
TYPE_DESCRIPTIONS = {
    "skippable": (
        "Type 1 — Skippable-step samples. The steps wrapped in <skippable_steps> "
        "appear in the original trajectory T but are absent from T* (the minimal "
        "trajectory, i.e. the dependency closure of the task anchor). Removing them "
        "does not change the task outcome. A script that batches over or skips these "
        "steps saves agent round-trips."
    ),
    "mergeable": (
        "Type 2 — Mergeable-step samples. The two steps wrapped in <mergable_steps> "
        "belong to T*, share identical dependencies, have the same op_type, both "
        "succeed, and touch disjoint files. They are safe to merge into a single step, "
        "saving agent round-trips."
    ),
    "optobs": (
        "Type 3 — Optimizable-observation samples. The step wrapped in "
        "<optimizable_observation_step> belongs to T* but its observation is overly "
        "long. An indexing/retrieve script that reads only the relevant portion could "
        "shorten it, cutting token cost. Dependent/dependency steps are given as "
        "action-only context (observations omitted)."
    ),
}


class ChunkEvolvePromptBuilderV4(ChunkEvolvePromptBuilderV2):
    """v4 prompt builder：SKIP 指示 + 三类细粒度信号渲染。

    与 v2 的区别：
    * HEADER 顶部插入 ``SKIP_DIRECTIVE``（高亮、紧跟 ``# Evolve task``）。
    * instruction.md 段强化为"只放 high-level 指导思想，不放任何 script 用法"。
    * verification 段强化为"未验证的 script 比没有 script 更糟"。
    * ``build`` 按 (trajectory, chunk_id) 分组三类 v4 样本，同 chunk 的多类样本
      合并渲染到一个 prompt 块（防样本数膨胀导致 batch 翻倍）。
    * optobs 每个 case 只渲染 observation 最长的 ``max_optobs_per_task`` 个 step。
    * step 在 prompt 中的编号用原始 trajectory T 的全局 step index（非 block 内
      位置计数），避免 ``<skippable_steps>`` 等块内步号从 1 重新计数。
    """

    # 每个 case（trajectory）optobs 最多渲染的 sample 数（取 observation 最长者）。
    max_optobs_per_task: int = 3

    HEADER = [
        "# Evolve task",
        SKIP_DIRECTIVE,
        "You are evolving helper scripts that a downstream mini-swe-agent will call as "
        "NATIVE FUNCTION TOOLS to solve similar tasks with fewer steps. Each script you "
        "write is auto-converted into a function tool: the agent calls it BY NAME (e.g. "
        "`read-lines`) with structured JSON parameters — it does NOT shell out to "
        "`bash <path>/main.sh`. Your `main.sh` is the executor that receives the rendered "
        "CLI args; `intro.json` defines the tool's name, description, and parameter schema.",
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
        "    \"examples\": [{\"call\": \"main.sh <args>\",",
        "                   \"expected\": \"ONE short line\"}],",
        "    \"cost_saving_rationale\": \"ONE short sentence: why this script saves cost\"",
        "  }",
        "Rules:",
        "- `description` ≤ 1 sentence. `parameter.description` ≤ 1 phrase. "
        "`cost_saving_rationale` ≤ 1 sentence. `examples[*].expected` ≤ 1 line.",
        "- `examples` is OPTIONAL; at most ONE example. No verbose multi-step walkthroughs.",
        "- `examples[*].call` uses the relative `main.sh <args>` form to document the CLI "
        "(the rollout agent never sees this path — it calls the tool by name).",
        "- Do NOT include `when_to_use` — the `description` already tells the agent when.",
        "- Merge similar scripts: before creating a new directory, check whether an existing "
        "script could be extended with a new action/flag instead. Fewer, more general "
        "scripts = lower downstream prompt cost. When you remove a script, delete its directory.",
        "",
        "## How `parameters` become the tool schema",
        "Each parameter's `name` encodes its CLI form; the converter uses it to render a "
        "function call back into `main.sh` argv:",
        "  - `--flag`              → boolean flag (emit `--flag` when the value is true)",
        "  - `--key=VALUE`         → valued flag (emit `--key=<value>`)",
        "  - `-c code` / `--x N`   → space-separated flag (emit the flag then the value)",
        "  - `file` (no leading `-`) → positional argument (emit `<value>` at the end, in order)",
        "`type` (`string`/`int`/`bool`) becomes the JSON-schema type the LLM sees. Use clean, "
        "conventional flag names; a single positional like `command` may carry a multi-word "
        "value (e.g. `go test ./...`) and will be shell-split into argv.",
        "",
        "## instruction.md (HIGH-LEVEL COST-SAVING RULES)",
        "Write ≤ 20 short rules (each ≤ 1 line) telling the downstream agent how to "
        "spend fewer steps and tokens. ",
        "Your evolved scripts are exposed to the rollout agent as native function tools "
        "(called by name with structured params). Reference tools by name in your rules "
        "(e.g. \"use find-src then read-lines\"), not by bash path. ",
        "Add STOP-PROGRESSING-IF rules for stuck behavior (e.g. 3 consecutive actions "
        "with no useful change).",
        "",
        "## Cost model (for prioritizing your designs)",
        "Effect on real cost, largest to smallest:",
        "  1. Fewer agent steps — each step costs cache write + output tokens. ",
        "  2. Shorter tool_call commands. ",
        "  3. Smaller observations — low priority. ",
        "A batching script that collapses N repeated calls into 1 step saves N-1 steps. ",
        "",
        "## Verification (REQUIRED after every script add/update)",
        "1. Run `bash <script_dir>/main.sh <sample_args>` on testing source files and confirm "
        "it returns the desired content.",
        "2. Validate intro.json: `python -c \"import json; json.load(open('<script_dir>/intro.json'))\"`.",
        "3. Re-read the script — confirm it is GENERIC (no hardcoded file paths from the samples).",
        "An UNVERIFIED script is worse than no script — verify before finishing.",
    ]

    FOOTER = (
        "\n# Your task\n"
        "Modify, add, merge, or remove scripts under your cwd based on the samples above. "
        "After EVERY change you MUST run the verification steps listed above — an "
        "unverified script is worse than no script, and an untested change can regress "
        "downstream agents. "
        "You may create test files under `test_codebase/` (a subdirectory of your cwd) to "
        "exercise newly added or modified scripts against realistic inputs before finishing. "
        "Do NOT edit the prompt file or sample files. "
        "Finish once scripts + intro.json + instruction.md are saved and verified."
    )

    def build(
        self,
        sample_paths: Iterable[Path],
        cwd_name: str = ".",
        scripts_dir: Optional[Path] = None,
    ) -> str:
        # 按轨迹 (trajectory) 分组 —— 一条 trajectory = 一个 Task。Task 内按三类
        # 信号顺序渲染：第一类 skippable → 第二类 mergeable → 第三类 optobs。
        # 每类只在第一个 sample 处给出该类的英文描述（见 TYPE_DESCRIPTIONS）。
        tasks: Dict[str, dict] = {}
        order: List[str] = []  # 保留首次出现顺序（find_samples 已按路径排序）
        bucket_of = {
            "v4_skippable": "skippable",
            "v4_mergeable": "mergeable",
            "v4_optobs": "optobs",
        }
        for p in sample_paths:
            data = json.loads(Path(p).read_text(encoding="utf-8"))
            data["_sample_path"] = str(p)
            traj_name = self._chunk_key(Path(p))[0]
            if traj_name not in tasks:
                tasks[traj_name] = {"skippable": [], "mergeable": [], "optobs": []}
                order.append(traj_name)
            bucket = bucket_of.get(data.get("type"))
            if bucket:
                tasks[traj_name][bucket].append(data)

        parts: List[str] = [line.replace("{cwd}", cwd_name) for line in self.HEADER]
        if scripts_dir is not None:
            parts.append(
                f"\nWorking directory absolute path: `{Path(scripts_dir).resolve()}`"
            )
            parts += self._current_scripts_block(Path(scripts_dir))
        if self.downstream_stats:
            parts += self._downstream_stats_block()
        if self.feedback:
            parts += self._feedback_block()

        for i, traj_name in enumerate(order, start=1):
            task = tasks[traj_name]
            parts.append(f"\n## Task {i}")
            parts += self._render_task_description(task)
            for kind in ("skippable", "mergeable", "optobs"):
                samples = task[kind]
                if not samples:
                    continue
                # optobs：每个 case（trajectory）只保留 observation 最长的 3 个 step，
                # 避免长尾样本稀释信号、控制 prompt 长度。
                if kind == "optobs":
                    samples = sorted(
                        samples,
                        key=lambda x: x.get("observation_chars", 0),
                        reverse=True,
                    )[: self.max_optobs_per_task]
                type_name = TYPE_LABELS[kind]
                for j, s in enumerate(samples, start=1):
                    parts.append(f"\n### {type_name} Contrastive Sample {j}")
                    parts += self._render_sample_body(kind, s)

        parts.append(self.FOOTER)
        return "\n".join(parts)

    # ---------- task 描述 ----------

    def _render_task_description(self, task: dict) -> List[str]:
        """渲染 `### Task Description`：从 task 任意 sample 反查 trajectory 的 task desc。"""
        src = ""
        for kind in ("skippable", "mergeable", "optobs"):
            for sample in task.get(kind, []):
                src = sample.get("source_trajectory", "")
                if src:
                    break
            if src:
                break
        td = ""
        if src:
            info = ChunkEvolvePromptBuilderV2._extract_task_info(src)
            td = info.get("task_description", "")
        lines = ["\n### Task Description"]
        if td:
            td_flat = " ".join(td.split())
            if len(td_flat) > 400:
                td_flat = td_flat[:400].rstrip() + "..."
            lines.append(td_flat)
        else:
            lines.append("(task description not available)")
        return lines

    # ---------- 单个 sample 的 body（不含 ### 标题，标题由 build() 发） ----------

    def _render_sample_body(self, kind: str, s: dict) -> List[str]:
        if kind == "skippable":
            return self._render_skippable_body(s)
        if kind == "mergeable":
            return self._render_mergeable_body(s)
        return self._render_optobs_body(s)

    def _render_skippable_body(self, s: dict) -> List[str]:
        lines: List[str] = []
        if s.get("context_before"):
            lines.append("\nContext before:")
            lines.append(self.serializer.serialize({"steps": s["context_before"]}))
        lines.append("\n<skippable_steps>")
        lines.append(self.serializer.serialize({"steps": s["skippable_steps"]}))
        lines.append("</skippable_steps>")
        if s.get("context_after"):
            lines.append("\nContext after:")
            lines.append(self.serializer.serialize({"steps": s["context_after"]}))
        return lines

    def _render_mergeable_body(self, s: dict) -> List[str]:
        lines: List[str] = []
        lines.append("\n<mergable_steps>")
        lines.append(self.serializer.serialize({"steps": s["steps"]}))
        lines.append("</mergable_steps>")
        if s.get("merged_form_example"):
            lines.append(f"\nMerged form example: `{s['merged_form_example']}`")
        return lines

    def _render_optobs_body(self, s: dict) -> List[str]:
        # 顺序：前序 → 目标 step → 后继，保持与原始 trajectory 的语意连续性。
        lines: List[str] = []
        if s.get("predecessor_actions"):
            lines.append("\nPredecessor actions (observations omitted):")
            lines.append(self.serializer.serialize({"steps": s["predecessor_actions"]}))
        lines.append("\n<optimizable_observation_step>")
        lines.append(self.serializer.serialize({"steps": [s["target_step"]]}))
        lines.append("</optimizable_observation_step>")
        if s.get("successor_actions"):
            lines.append("\nSuccessor actions (observations omitted):")
            lines.append(self.serializer.serialize({"steps": s["successor_actions"]}))
        return lines


# ============================================================================
# V4 script evolver — no-op 检测 + v4 样本 glob
# ============================================================================


class ChunkScriptEvolverV4(ChunkScriptEvolverV2):
    """v4 evolver：捞 v4 样本 + 每个 batch 做 no-op 检测。

    no-op 检测：batch 跑前/后对 scripts 目录做内容 hash 快照，无变化时记
    INFO（不视为失败）。这把 prompt 里的 SKIP 指示落到可观测层，让"agent
    选择不改 scripts"成为一等公民而非异常。
    """

    name = "evolve_chunk_v4"

    def find_samples(self, result_dir, task=None) -> List[Path]:
        files = sorted(Path(result_dir).glob("**/agent/v4_*_chunk_*.json"))
        if not task:
            return files
        matched = [p for p in files if self._task_matches(p, task)]
        return matched or [p for p in files if task in str(p)]

    def run(self, result_dir, task: Optional[str] = None) -> Path:
        result_dir = Path(result_dir).resolve()
        self.scripts_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_instruction_file()

        output_dir = self.output_dir or (result_dir / "evolve_logs")
        output_dir.mkdir(parents=True, exist_ok=True)
        samples = self.find_samples(result_dir, task)
        logger.info("found %d v4 samples", len(samples))
        if not samples:
            logger.warning(
                "no v4 samples found under %s (task=%r); evolve stage is a no-op",
                result_dir, task,
            )
            return output_dir

        failures: List[int] = []
        noop_batches: List[int] = []
        batches = list(self._batched(samples, self.batch_size))
        total = len(batches)
        logger.info(
            "running %d evolve batch(es) (batch_size=%d cases/prompt, sequential — "
            "all batches write the same scripts_dir so cannot parallelize)",
            total, self.batch_size,
        )
        for batch_id, batch in enumerate(batches, start=1):
            output_path = output_dir / f"evolve_batch_{batch_id}.traj.json"
            prompt_path = output_path.with_suffix(".prompt.md")
            sentinel = output_path.with_suffix(".done")
            if self.resume and sentinel.exists():
                logger.info("batch %d/%d already done (sentinel exists), skipping", batch_id, total)
                continue
            self._maybe_refresh_stats()
            before = self._scripts_hash()
            logger.info("batch %d/%d starting (%d samples)", batch_id, total, len(batch))
            t0 = time.time()
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
                logger.exception("batch %d/%d failed: %s", batch_id, total, exc)
                failures.append(batch_id)
                continue
            dt = time.time() - t0
            after = self._scripts_hash()
            if before == after:
                noop_batches.append(batch_id)
                logger.info(
                    "batch %d/%d done in %.1fs (no-op per SKIP directive)",
                    batch_id, total, dt,
                )
            else:
                logger.info(
                    "batch %d/%d done in %.1fs (scripts changed)",
                    batch_id, total, dt,
                )
        if failures:
            logger.warning("batches failed: %s", failures)
        else:
            logger.info("all batches finished")
        if noop_batches:
            logger.info("no-op batches: %s", noop_batches)
        for w in self._validate_intros():
            logger.warning("intro.json: %s", w)
        return output_dir

    # ---------- no-op 检测 ----------

    def _scripts_hash(self) -> str:
        """scripts 目录所有文件 (相对路径 + 内容) 的稳定 hash。"""
        h = hashlib.sha256()
        if not self.scripts_dir.exists():
            return h.hexdigest()
        for p in sorted(self.scripts_dir.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(self.scripts_dir).as_posix()
            h.update(rel.encode("utf-8"))
            h.update(b"\0")
            try:
                h.update(p.read_bytes())
            except OSError as exc:
                logger.warning("cannot read %s for hash: %s", p, exc)
            h.update(b"\0")
        return h.hexdigest()


# ============================================================================
# Factory + CLI
# ============================================================================


def make_v4_annotator(
    config_path,
    workers: int = 1,
    retry_failed: int = 1,
    long_obs_threshold: int = LONG_OBS_THRESHOLD_DEFAULT,
) -> ChunkTrajectoryAnnotatorV4:
    return ChunkTrajectoryAnnotatorV4(
        config_path=config_path,
        workers=workers,
        retry_failed=retry_failed,
        long_obs_threshold=long_obs_threshold,
    )


def make_v4_contrastive_builder(
    min_reduction_ratio: float = 0.1,
    hotspot_min_occurrences: int = 3,
    hotspot_min_total_chars: int = 1000,
    long_obs_threshold: int = LONG_OBS_THRESHOLD_DEFAULT,
) -> ChunkContrastiveSampleBuilderV4:
    builder = ChunkContrastiveSampleBuilderV4(
        min_reduction_ratio=min_reduction_ratio,
        hotspot_min_occurrences=hotspot_min_occurrences,
        hotspot_min_total_chars=hotspot_min_total_chars,
    )
    # optobs 复用 brief_observations 的长 obs 阈值
    builder.long_obs_threshold = int(long_obs_threshold)
    return builder


def make_v4_evolver(
    scripts_dir,
    config_path,
    mini_swe_agent_dir,
    batch_size: int = 4,
    max_observation_chars: int = 1000,
    output_dir: Optional[str] = None,
    dry_run: bool = False,
    resume: bool = True,
    stats_provider=None,
    feedback: Optional[dict] = None,
) -> ChunkScriptEvolverV4:
    return ChunkScriptEvolverV4(
        scripts_dir=scripts_dir,
        runner=MiniSweAgentRunnerV2(
            mini_swe_agent_dir=mini_swe_agent_dir,
            llm_config=config_path,
            dry_run=dry_run,
        ),
        prompt_builder=ChunkEvolvePromptBuilderV4(
            serializer=TrajectorySerializer(max_observation_chars=max_observation_chars),
            feedback=feedback,
        ),
        batch_size=batch_size,
        output_dir=Path(output_dir).resolve() if output_dir else None,
        resume=resume,
        stats_provider=stats_provider,
    )


def run_dry_stat(result_dir, task=None, long_obs_threshold: int = LONG_OBS_THRESHOLD_DEFAULT) -> None:
    """Phase 0 离线统计：扫已标注 trajectory，报告三类信号的真实频率。

    用于决定先做哪一类、上限设几条。纯规则、零 LLM 成本。
    """
    builder = make_v4_contrastive_builder(long_obs_threshold=long_obs_threshold)
    files = sorted(Path(result_dir).glob("**/agent/trajectory.json"))
    if task:
        files = [p for p in files if task in str(p)]
    if not files:
        logger.warning("no trajectory.json found under %s", result_dir)
        return

    agg = {"v4_skippable": 0, "v4_mergeable": 0, "v4_optobs": 0}
    total_action = 0
    total_skippable = 0
    per_traj: List[dict] = []
    for p in files:
        try:
            stat = builder.stat_file(p)
        except Exception as exc:
            logger.warning("stat failed for %s: %s", p, exc)
            continue
        for k in agg:
            agg[k] += stat["samples"][k]
        total_action += stat["n_action_steps"]
        total_skippable += stat["skippable_steps"]
        per_traj.append(stat)

    n = len(per_traj)
    med_skippable = sorted(s["samples"]["v4_skippable"] for s in per_traj)[n // 2] if n else 0
    med_mergeable = sorted(s["samples"]["v4_mergeable"] for s in per_traj)[n // 2] if n else 0

    print("\n===== V4 dry-stat report =====")
    print(f"trajectories scanned : {n}")
    print(f"total action steps    : {total_action}")
    print(f"skippable steps total : {total_skippable} "
          f"({round(total_skippable / total_action * 100, 1) if total_action else 0}% of action steps)")
    print(f"skippable samples     : {agg['v4_skippable']} (median/traj = {med_skippable})")
    print(f"mergeable samples     : {agg['v4_mergeable']} (median/traj = {med_mergeable})")
    print(f"optobs samples        : {agg['v4_optobs']}")
    print("\nDecision thresholds (see evolve_v4_mini_dag_based_chunking.md §1):")
    print(f"  - skippable: median ≥ 1 → {'GO' if med_skippable >= 1 else 'HOLD'}")
    print(f"  - mergeable: median ≥ 0.5 → {'GO' if med_mergeable >= 1 else 'HOLD (likely sparse)'}")
    print("================================\n")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Evolve v4: mini-DAG based chunking with three explicit signals.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_stat = sub.add_parser("dry-stat", help="Phase 0: report signal frequencies without evolving")
    _add_common(p_stat)
    p_stat.add_argument(
        "--long-obs-threshold", type=int, default=LONG_OBS_THRESHOLD_DEFAULT,
        help="observation 字符数超过此阈值才算 optobs (default: %(default)s)",
    )

    p_annotate = sub.add_parser("annotate", help="Stage 1: reuse v2 annotate (deps + step_meta)")
    _add_common(p_annotate)
    _add_config(p_annotate)
    _add_annotate(p_annotate)
    p_annotate.add_argument(
        "--long-obs-threshold", type=int, default=LONG_OBS_THRESHOLD_DEFAULT,
        help="observation 字符数超过此阈值才标注 brief (default: %(default)s)",
    )

    p_contrast = sub.add_parser("contrastive", help="Stage 2: build v4 skippable/mergeable/optobs samples")
    _add_common(p_contrast)
    p_contrast.add_argument(
        "--long-obs-threshold", type=int, default=LONG_OBS_THRESHOLD_DEFAULT,
        help="observation 字符数超过此阈值才算 optobs (default: %(default)s)",
    )

    p_evolve = sub.add_parser("evolve", help="Stage 3: evolve scripts from v4 samples")
    _add_common(p_evolve)
    _add_config(p_evolve)
    _add_evolve(p_evolve)

    p_run = sub.add_parser("run", help="run the full v4 pipeline")
    _add_common(p_run)
    _add_config(p_run)
    _add_annotate(p_run)
    p_run.add_argument(
        "--long-obs-threshold", type=int, default=LONG_OBS_THRESHOLD_DEFAULT,
        help="observation 字符数超过此阈值才标注 brief / optobs (default: %(default)s)",
    )
    _add_evolve(p_run)
    p_run.add_argument(
        "--skip", action="append", default=[],
        help="stage name(s) to skip (annotate_chunk_v4 / contrastive_chunk_v4 / evolve_chunk_v4)",
    )

    args = parser.parse_args(argv)
    _setup_logging(getattr(args, "log_file", None))

    if args.cmd == "dry-stat":
        run_dry_stat(args.result_dir, task=args.task, long_obs_threshold=args.long_obs_threshold)
    elif args.cmd == "annotate":
        make_v4_annotator(
            config_path=args.config,
            workers=args.workers,
            retry_failed=args.retry_failed,
            long_obs_threshold=args.long_obs_threshold,
        ).run(args.result_dir, task=args.task)
    elif args.cmd == "contrastive":
        make_v4_contrastive_builder(
            long_obs_threshold=args.long_obs_threshold,
        ).run(args.result_dir, task=args.task)
    elif args.cmd == "evolve":
        make_v4_evolver(
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

        if "annotate_chunk_v4" not in skip and "annotate" not in skip:
            logger.info("[stage 1/3] annotate_chunk_v4 on %s", result_dir)
            make_v4_annotator(
                config_path=args.config,
                workers=args.workers,
                retry_failed=args.retry_failed,
                long_obs_threshold=args.long_obs_threshold,
            ).run(result_dir, task=args.task)

        if "contrastive_chunk_v4" not in skip and "contrastive" not in skip:
            logger.info("[stage 2/3] contrastive_chunk_v4 on %s", result_dir)
            make_v4_contrastive_builder(
                long_obs_threshold=args.long_obs_threshold,
            ).run(result_dir, task=args.task)

        if "evolve_chunk_v4" not in skip and "evolve" not in skip:
            logger.info("[stage 3/3] evolve_chunk_v4 on %s", result_dir)
            make_v4_evolver(
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
