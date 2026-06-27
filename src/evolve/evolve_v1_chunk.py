"""Evolve v1 chunk: 解决三段式 pipeline 的 token 爆炸 + observation 冗余问题。

工作流程::

    trajectory.json
        │
        ▼
    ┌───────────────────────────┐
    │ 1. annotate_chunk         │  标注 dependencies (父类)
    │   + brief_observations    │  + 对长 observation step 标注有用行号
    └───────────────────────────┘
        │
        ▼
    ┌───────────────────────────┐
    │ 2. contrastive_chunk      │  按 20 step 切 chunk
    │   - graph contrastive     │  每 chunk 生成 graph contrastive
    │   - observation contrast  │  + 若有 brief_obs 步，生成 observation contrastive
    └───────────────────────────┘
        │
        ▼
    ┌───────────────────────────┐
    │ 3. evolve_chunk           │  batch = 2 chunks / prompt
    │   (mini-swe-agent)        │  prompt 区分两类 sample
    └───────────────────────────┘

与三段式 pipeline 的区别
------------------------
* annotate 额外标注 brief_observations：对 observation 过长的 step，让 LLM 在
  "当前 step + 1 跳后续 step" 的上下文里挑出有用行号。
* contrastive 把 trajectory 按 20 step 切 chunk，避免整段 trajectory 进 prompt
  造成 token 爆炸 / Lost in the Middle。每 chunk 生成两类 sample：
    - graph contrastive：chunk vs. mini chunk（依赖图裁剪）
    - observation contrastive：full observation + 后续 step vs. brief observation
* evolve 一个 batch 给 2 个 chunk 的所有 sample，prompt 明确：
    - scripts 要通用，不要 case-specific patch
    - 不需要每个 sample 都处理（已解决或无法解决可跳过）

设计要点
--------
* 三个 stage 类都继承自三段式的对应类，只覆盖必要方法，基类一行不改。
* CLI 复用 ``run_evolve`` 的参数 helper，参数语义与三段式 pipeline 一致。

用法
----
::

    # 全量跑
    python -m src.evolve.evolve_v1_chunk run results/... \\
        --config _config/deepseekv4_flash.yaml \\
        --scripts-dir .evolve_scripts_v1_chunk \\
        --workers 4 --batch-size 2

    # 单段调试
    python -m src.evolve.evolve_v1_chunk annotate    results/... --workers 4
    python -m src.evolve.evolve_v1_chunk contrastive results/...
    python -m src.evolve.evolve_v1_chunk evolve      results/... --dry-run
"""

from __future__ import annotations

import argparse
import ast
import copy
import json
import logging
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from .annotator import DependencyParseError, TrajectoryAnnotator
from .contrastive import ContrastiveSampleBuilder
from .evolver import (
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
    _add_evolve,
    _setup_logging,
)

logger = logging.getLogger(__name__)


# 默认参数
CHUNK_SIZE_DEFAULT = 20
LONG_OBS_THRESHOLD_DEFAULT = 2000  # observation 超过这个字符数才标注 brief
MIN_REDUCTION_RATIO_DEFAULT = 0.1  # mini chunk 相对原 chunk 至少省掉这么比例的 action step 才作为 graph contrastive sample


# ============================================================================
# Stage 1: Chunk annotator — dependencies + brief_observations
# ============================================================================


class ChunkTrajectoryAnnotator(TrajectoryAnnotator):
    """在父类的 dependencies 基础上，额外标注 ``brief_observations``。

    对 observation 长度超过 ``long_obs_threshold`` 的 action step，让 LLM 在
    "当前 step + 1 跳后续 step" 的上下文里挑出有用行号，写入
    ``trajectory["brief_observations"][str(step_idx)] = [line_numbers]``。
    """

    name = "annotate_chunk"

    OBSERVATION_SYSTEM_PROMPT = (
        "You identify the useful lines in a long observation. "
        "Given the current step's action and observation (with 1-based line numbers), "
        "plus the 1-hop successor steps (full content for direct successors, "
        "action-only for irrelevant in-between pre-steps marked 'Not relevant, so omit it.'), "
        "output the line numbers in the current step's observation that are actually useful "
        "for producing the successors. "
        "Output only a JSON list of integers (1-based line numbers)."
    )

    def __init__(
        self,
        config_path,
        workers: int = 1,
        retry_failed: int = 1,
        long_obs_threshold: int = LONG_OBS_THRESHOLD_DEFAULT,
    ):
        super().__init__(config_path, workers, retry_failed)
        self.long_obs_threshold = int(long_obs_threshold)

    def annotate_file(self, path, llm=None, step_workers: int = 1):
        # 先跑父类的 dependency 标注
        super().annotate_file(path, llm=llm, step_workers=step_workers)
        # 再标注 brief_observations（用一个新 LLM 实例，避免和父类并发状态冲突）
        from src.tools.llm import LLM
        llm = llm or LLM(self.config_path)
        self._annotate_brief_observations(path, llm)

    # ---------- brief observation 标注 ----------

    def _annotate_brief_observations(self, path, llm):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        action_steps = self._extract_action_steps(data)
        dependencies = data.get("dependencies", {})
        if not dependencies:
            logger.warning("no dependencies in %s; skipping brief_observation", path)
            return
        successors = self._build_successor_map(dependencies)
        brief_observations: Dict[str, List[int]] = {}

        for i, step in enumerate(action_steps, start=1):
            obs_text = self._render_observation_text(step.get("observation", ""))
            if len(obs_text) <= self.long_obs_threshold:
                continue
            if not successors.get(i):
                logger.info(
                    "step %d of %s has long obs (%d chars) but no successors; skipping",
                    i, path, len(obs_text),
                )
                continue
            logger.info(
                "annotating brief_observation for %s step %d (obs=%d chars, %d successors)",
                path, i, len(obs_text), len(successors[i]),
            )
            user_prompt = self._build_observation_prompt(action_steps, i, successors)
            if not user_prompt:
                continue
            try:
                raw = llm.query(self.OBSERVATION_SYSTEM_PROMPT, "", user_prompt)
            except Exception as exc:
                logger.exception("LLM call failed for %s step %d: %s", path, i, exc)
                continue
            try:
                lines = self._parse_line_list(raw, len(obs_text.splitlines()))
            except DependencyParseError as exc:
                logger.error("failed to parse brief_observation for %s step %d: %s",
                             path, i, exc)
                continue
            brief_observations[str(i)] = lines

        data["brief_observations"] = brief_observations
        Path(path).write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        logger.info(
            "finished brief_observation for %s (%d/%d steps annotated)",
            path, len(brief_observations), len(action_steps),
        )

    # ---------- prompt 构建 ----------

    def _build_observation_prompt(
        self,
        action_steps: List[dict],
        current_idx: int,
        successors: Dict[int, List[int]],
    ) -> str:
        """构造 brief_observation 标注 prompt：当前 step + 1 跳后续 step。"""
        succ = sorted(successors.get(current_idx, []))
        if not succ:
            return ""
        first, last = succ[0], succ[-1]
        current_step = action_steps[current_idx - 1]

        lines: List[str] = []
        lines.append(f"Current step {current_idx}:")
        lines.append("Action:")
        lines.append(self._step_text(current_step))
        lines.append("Observation (with 1-based line numbers):")
        obs_text = self._render_observation_text(current_step.get("observation", ""))
        lines.append(self._with_line_numbers(obs_text))

        lines.append("")
        lines.append(
            f"Successor steps (range: step {first} to step {last}; "
            f"direct successors: {succ}):"
        )
        for j in range(first, last + 1):
            if j > len(action_steps):
                continue
            succ_step = action_steps[j - 1]
            is_direct = j in succ
            tag = "direct successor" if is_direct else "pre-step of later successor, not relevant"
            lines.append(f"\n--- Step {j} ({tag}) ---")
            lines.append("Action:")
            lines.append(self._step_text(succ_step))
            if is_direct:
                lines.append("Observation:")
                lines.append(self._render_observation_text(succ_step.get("observation", "")))
            else:
                lines.append("Observation: Not relevant, so omit it.")

        lines.append("")
        lines.append(
            "Based on the above, output the line numbers in the current step's "
            "observation that are actually useful for producing the successors. "
            "Output only a JSON list of integers."
        )
        return "\n".join(lines)

    # ---------- helpers ----------

    @staticmethod
    def _build_successor_map(dependencies: Dict[str, List[int]]) -> Dict[int, List[int]]:
        """``{i: [deps]}`` → ``{j: [successors of j]}``."""
        successors: Dict[int, List[int]] = {}
        for k, deps in dependencies.items():
            try:
                i = int(k)
            except (TypeError, ValueError):
                continue
            for d in deps:
                try:
                    d_int = int(d)
                except (TypeError, ValueError):
                    continue
                successors.setdefault(d_int, []).append(i)
        return successors

    @staticmethod
    def _render_observation_text(observation) -> str:
        """把 observation 渲染为紧凑字符串（与父类 _clip_observation 一致但不截断）。"""
        if isinstance(observation, dict) and isinstance(observation.get("results"), list):
            parts: List[str] = []
            for item in observation["results"]:
                content = item.get("content", item) if isinstance(item, dict) else item
                if isinstance(content, str):
                    try:
                        parsed = json.loads(content)
                        if isinstance(parsed, dict):
                            bits = []
                            if "returncode" in parsed:
                                bits.append(f"returncode: {parsed.get('returncode')}")
                            if parsed.get("output"):
                                bits.append(f"output: {parsed.get('output')}")
                            if parsed.get("exception_info"):
                                bits.append(f"exception_info: {parsed.get('exception_info')}")
                            content = "\n".join(bits) if bits else content
                    except json.JSONDecodeError:
                        pass
                parts.append(
                    content if isinstance(content, str)
                    else json.dumps(content, ensure_ascii=False, default=str)
                )
            return "\n".join(parts)
        if isinstance(observation, str):
            return observation
        return json.dumps(observation, ensure_ascii=False, default=str)

    @staticmethod
    def _with_line_numbers(text: str) -> str:
        return "\n".join(f"{i+1}: {line}" for i, line in enumerate(text.splitlines()))

    @staticmethod
    def _parse_line_list(text: str, max_index: int) -> List[int]:
        text = (text or "").strip()
        if not text:
            raise DependencyParseError("empty LLM brief_observation output")
        matches = re.findall(r"\[[\s\S]*?\]", text)
        candidate = matches[-1] if matches else text
        try:
            values = ast.literal_eval(candidate)
        except (SyntaxError, ValueError) as exc:
            raise DependencyParseError(
                f"invalid brief_observation output {text!r}: {exc}"
            ) from exc
        if not isinstance(values, list):
            raise DependencyParseError(f"brief_observation output is not a list: {text!r}")
        lines: List[int] = []
        for x in values:
            try:
                ln = int(x)
            except (TypeError, ValueError):
                raise DependencyParseError(
                    f"non-integer line number {x!r} in {text!r}"
                )
            if 1 <= ln <= max_index and ln not in lines:
                lines.append(ln)
        return sorted(lines)


# ============================================================================
# Stage 2: Chunk contrastive builder — graph + observation contrastive
# ============================================================================


class ChunkContrastiveSampleBuilder(ContrastiveSampleBuilder):
    """按 20 action step 切 chunk，每 chunk 生成 graph + observation 两类 sample。

    输出文件（写到 trajectory.json 同目录的 ``agent/`` 下）::

        contrastive_graph_chunk_<id>.json         — graph contrastive
        contrastive_obs_chunk_<id>_step_<s>.json   — observation contrastive

    如果一个 chunk 的 graph contrastive 优化空间很小（mini chunk 与原 chunk 的
    action step 数相差小于 ``min_reduction_ratio``），就跳过 graph contrastive
    sample 的生成 —— 这种 chunk 没什么可学的。observation contrastive 与 graph
    独立，仍然照常生成。
    """

    name = "contrastive_chunk"
    CHUNK_SIZE = CHUNK_SIZE_DEFAULT

    def __init__(self, min_reduction_ratio: float = MIN_REDUCTION_RATIO_DEFAULT):
        self.min_reduction_ratio = float(min_reduction_ratio)

    def build_file(self, path) -> List[Path]:
        trajectory = json.loads(Path(path).read_text(encoding="utf-8"))
        if trajectory.get("dependencies") is None:
            raise ValueError(
                "trajectory has no dependencies field; run annotate_chunk first"
            )
        chunks = self._split_into_chunks(trajectory, self.CHUNK_SIZE)
        outs: List[Path] = []
        skipped_graph = 0
        for chunk in chunks:
            chunk_id = chunk["chunk_id"]

            # 1. graph contrastive: chunk vs. mini chunk
            positive = self._build_positive_sample(chunk)
            if self._has_graph_optimization_space(chunk, positive):
                graph_sample = {
                    "type": "contrastive_graph",
                    "chunk_id": chunk_id,
                    "source_trajectory": str(path),
                    "positive_sample": positive,
                    "negative_sample": chunk,
                }
                out = path.with_name(f"contrastive_graph_chunk_{chunk_id}.json")
                out.write_text(
                    json.dumps(graph_sample, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                outs.append(out)
            else:
                skipped_graph += 1
                logger.info(
                    "skip graph contrastive for %s chunk %d: low optimization space",
                    path, chunk_id,
                )

            # 2. observation contrastive: 每 brief_obs 步一个（与 graph 独立，仍然生成）
            for step_idx_str in chunk.get("brief_observations", {}):
                obs_sample = self._build_observation_contrastive(
                    trajectory, chunk, int(step_idx_str)
                )
                out = path.with_name(
                    f"contrastive_obs_chunk_{chunk_id}_step_{step_idx_str}.json"
                )
                out.write_text(
                    json.dumps(obs_sample, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                outs.append(out)

        logger.info(
            "built %d contrastive samples for %s (%d chunks, %d skipped graph contrastive)",
            len(outs), path, len(chunks), skipped_graph,
        )
        return outs

    # ---------- 优化空间判断 ----------

    def _has_graph_optimization_space(self, chunk: dict, positive: dict) -> bool:
        """原 chunk 与 mini chunk 的 action step 数差是否达到 ``min_reduction_ratio``。

        达不到阈值说明该 chunk 已经接近最小化，graph contrastive 没什么可学的；
        返回 False 让 caller 跳过 graph contrastive sample。
        """
        n_orig = sum(1 for s in chunk.get("steps", []) if self._is_action_step(s))
        n_mini = sum(1 for s in positive.get("steps", []) if self._is_action_step(s))
        if n_orig <= 0:
            return False
        reduction = (n_orig - n_mini) / n_orig
        return reduction >= self.min_reduction_ratio

    # ---------- chunk 切分 ----------

    def _split_into_chunks(self, trajectory: dict, chunk_size: int) -> List[dict]:
        """按 action step 切 chunk。每个 chunk 保留 leading 非动作步骤 + 该段动作步骤。

        dependencies 和 brief_observations 都重映射到 chunk 内的 1-based local index。
        """
        steps = trajectory.get("steps", [])
        dependencies = trajectory.get("dependencies", {}) or {}
        brief_obs = trajectory.get("brief_observations", {}) or {}

        # 找出所有 action step 的 (action_num, steps_array_idx)
        action_step_indices: List[Tuple[int, int]] = []
        action_i = 0
        for i, step in enumerate(steps):
            if self._is_action_step(step):
                action_i += 1
                action_step_indices.append((action_i, i))

        if not action_step_indices:
            return [trajectory]

        # chunk 0 之前的非动作步骤（system / user / task）作为初始上下文
        leading_steps = steps[: action_step_indices[0][1]]

        chunks: List[dict] = []
        for start in range(0, len(action_step_indices), chunk_size):
            end = min(start + chunk_size, len(action_step_indices))
            chunk_action_indices = action_step_indices[start:end]
            local_size = end - start
            global_first = start + 1
            global_last = end

            # chunk steps：leading 非动作（仅第一个 chunk）+ 该段动作步骤
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
                        # 同 chunk 内：重映射为 local index
                        local_d.append(d_int - start)
                    elif d_int < global_first:
                        # 跨 chunk 依赖：视为依赖初始上下文 (step 0)
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
                "chunk_id": start // chunk_size + 1,
                "chunk_range": [global_first, global_last],
            }
            chunks.append(chunk)

        return chunks

    # ---------- observation contrastive ----------

    def _build_observation_contrastive(
        self, trajectory: dict, chunk: dict, local_step_idx: int
    ) -> dict:
        """对 chunk 内的 brief_obs 步生成 observation contrastive sample。

        negative: 当前 step（global）+ 1 跳后续 step（global，与标注阶段一致）
        positive: 当前 step 但 observation 只保留 brief 行
        """
        action_steps = [s for s in trajectory.get("steps", []) if self._is_action_step(s)]
        dependencies = trajectory.get("dependencies", {}) or {}
        successors = ChunkTrajectoryAnnotator._build_successor_map(dependencies)

        # local → global
        global_first = chunk["chunk_range"][0]
        global_idx = global_first + local_step_idx - 1
        current_step = action_steps[global_idx - 1]
        succ_indices = sorted(successors.get(global_idx, []))

        # negative: current step + 1-hop successors (full)
        neg_steps = [copy.deepcopy(current_step)]
        for s in succ_indices:
            if 1 <= s <= len(action_steps):
                neg_steps.append(copy.deepcopy(action_steps[s - 1]))

        # positive: current step with brief observation only
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
            "successors": succ_indices,
            "source_trajectory": chunk.get("source_trajectory", ""),
            "negative_sample": {"steps": neg_steps},
            "positive_sample": {"steps": [pos_step]},
        }

    @staticmethod
    def _extract_brief_observation(observation, brief_lines: List[int]) -> dict:
        """从 observation 里抽取 brief_lines 指定的行，返回精简 observation。"""
        text = ChunkTrajectoryAnnotator._render_observation_text(observation)
        all_lines = text.splitlines()
        picked = []
        for ln in brief_lines:
            if 1 <= ln <= len(all_lines):
                picked.append(all_lines[ln - 1])
        brief_text = "\n".join(picked)
        # 包装回 {results: [{content: ...}]} 形式以保持 serializer 兼容
        return {"results": [{"content": brief_text}]}


# ============================================================================
# Stage 3: Chunk prompt builder + chunk evolver
# ============================================================================


class ChunkEvolvePromptBuilder(EvolvePromptBuilder):
    """把每 batch 的 sample 按 chunk 分组，区分 graph / observation 两类渲染。

    与父类的区别：
    * intro.json schema 精简：去掉 ``when_to_use``、``examples`` 上限 1 条、所有字段都要求简短。
    * instruction.md 要求是 5–15 条 bullet 的高 level 清单，不再是长文档。
    * 显式要求合并相似 script、并对每个新增/修改的 script 做验证。
    """

    HEADER = [
        "Here are contrastive samples from trajectory chunks. Two types:",
        "1. **Graph contrastive**: the original chunk vs. a minimal chunk "
        "(only steps reachable from the final action through dependencies).",
        "2. **Observation contrastive**: a step with full observation (plus 1-hop successors) "
        "vs. the same step with only the brief (useful) observation lines.",
        "",
        "Evolve GENERIC, reusable scripts that solve a class of similar tasks — "
        "NOT case-specific patches. If a script becomes too specific, update or remove it.",
        "Not every sample needs handling: if a sample is already addressed by existing scripts, "
        "or cannot be improved by script evolution, skip it. Do not force a change for every sample.",
        "",
        "## Scripts (./<script_name>/)",
        "Each script lives under ./<script_name>/ with a main.sh entrypoint plus an intro.json.",
        "**Merge similar scripts**: before creating a new script directory, check whether an "
        "existing script could be extended with a new action/flag instead. If two scripts "
        "overlap in purpose (same tool family, same cost-saving intent), merge them into one. "
        "Fewer, more general scripts = lower downstream prompt cost.",
        "When you remove a script, delete its directory.",
        "",
        "## intro.json schema (KEEP EVERY FIELD SHORT)",
        "Valid JSON with EXACTLY these fields (no extras, no `when_to_use`):",
        "  {",
        "    \"name\": \"<script_name>\",",
        "    \"description\": \"ONE sentence: what this script does.\",",
        "    \"entrypoint\": \"main.sh\",",
        "    \"parameters\": [",
        "      {\"name\": \"...\", \"type\": \"string|int|bool\", \"required\": true,",
        "       \"description\": \"ONE short phrase\"}",
        "    ],",
        "    \"examples\": [{\"call\": \"main.sh <args>\", \"expected\": \"ONE short line\"}],",
        "    \"cost_saving_rationale\": \"ONE short sentence: why this script reduces cost vs. baseline\"",
        "  }",
        "Rules:",
        "- `description` ≤ 1 sentence. `parameter.description` ≤ 1 phrase. "
        "`cost_saving_rationale` ≤ 1 sentence. `examples[*].expected` ≤ 1 line.",
        "- `examples` is OPTIONAL; if present, at most ONE example. No verbose multi-step walkthroughs.",
        "- Do NOT include `when_to_use`. The `description` already tells the agent when to use it.",
        "",
        "## instruction.md (HIGH-LEVEL ONLY, MUST BE SHORT)",
        "Maintain instruction.md as a SHORT bullet list of 5–15 high-level cost-saving rules.",
        "Each bullet ≤ 1 line. Examples of good bullets:",
        "  - \"Batch multiple tool calls per step to cut round trips.\"",
        "  - \"Keep tool output lean; pipe through grep/head when possible.\"",
        "  - \"Prefer project-specific runner scripts over raw build/test commands.\"",
        "Do NOT write a longform document. Do NOT describe per-script usage here "
        "(each script's usage lives in its own intro.json). Do NOT duplicate examples.",
        "",
        "## Verification (REQUIRED after every script add/update)",
        "After you add or modify any script, you MUST verify it before finishing:",
        "1. Run `bash <script_dir>/main.sh <sample_args>` with a representative input "
        "and confirm it exits 0 with sensible output.",
        "2. Validate intro.json: `python -c \"import json,sys; json.load(open('<script_dir>/intro.json'))\"`.",
        "3. Re-read the script and confirm it is GENERIC (no hardcoded case-specific file paths, "
        "no patterns that only match the current sample).",
        "If any check fails, fix the script before moving on. Do NOT leave broken or "
        "case-specific scripts behind.",
    ]

    FOOTER = (
        "\nYour task: modify, add, merge, or remove scripts in the current directory, "
        "and update instruction.md accordingly. "
        "Keep implementations minimal. Do not edit the prompt file or contrastive sample files. "
        "For every script you add or modify, write a concise intro.json (schema above). "
        "For every script you remove, delete its directory. "
        "After every script change, run the verification steps listed above. "
        "Finish once scripts, intro.json files, and instruction.md are saved and verified."
    )

    def build(
        self,
        sample_paths: Iterable[Path],
        cwd_name: str = ".",
        scripts_dir: Optional[Path] = None,
    ) -> str:
        # 按 (trajectory, chunk_id) 分组
        groups: Dict[Tuple[str, int], dict] = {}
        for p in sample_paths:
            data = json.loads(Path(p).read_text(encoding="utf-8"))
            key = self._chunk_key(Path(p))
            group = groups.setdefault(key, {"graph": None, "observations": []})
            sample_type = data.get("type")
            if sample_type == "contrastive_graph":
                group["graph"] = data
            elif sample_type == "contrastive_observation":
                group["observations"].append(data)

        parts: List[str] = [line.replace("{cwd}", cwd_name) for line in self.HEADER]
        parts.append(
            f"The current working directory is {cwd_name}; evolve scripts in place here."
        )
        if scripts_dir is not None:
            parts += self._current_scripts_block(Path(scripts_dir))

        for i, (key, group) in enumerate(sorted(groups.items()), start=1):
            traj_name, chunk_id = key
            parts.append(f"\n# Chunk {i}: trajectory={traj_name}, chunk_id={chunk_id}")

            if group["graph"]:
                parts.append("\n## Graph Contrastive")
                parts.append("### Original Chunk Trajectory")
                parts.append(self.serializer.serialize(group["graph"]["negative_sample"]))
                parts.append("\n### Minimal Chunk Trajectory")
                parts.append(self.serializer.serialize(group["graph"]["positive_sample"]))

            if group["observations"]:
                parts.append("\n## Observation Contrastive Samples")
                for j, obs in enumerate(group["observations"], start=1):
                    step_idx = obs.get("step_index")
                    brief_lines = obs.get("brief_lines", [])
                    parts.append(
                        f"\n### Observation Sample {j} (step {step_idx}, "
                        f"brief lines: {brief_lines})"
                    )
                    parts.append("Negative (full observation + 1-hop successors):")
                    parts.append(self.serializer.serialize(obs["negative_sample"]))
                    parts.append("\nPositive (brief observation only):")
                    parts.append(self.serializer.serialize(obs["positive_sample"]))

        parts.append(self.FOOTER)
        return "\n".join(parts)

    @staticmethod
    def _chunk_key(path: Path) -> Tuple[str, int]:
        traj = path.parent.parent.name
        m = re.search(r"chunk_(\d+)", path.name)
        return (traj, int(m.group(1)) if m else 0)


class ChunkScriptEvolver(ScriptEvolver):
    """按 chunk 批处理：``--batch-size`` 表示每个 batch 含多少个 chunk。

    与父类的区别：
    * intro.json 必填字段去掉 ``when_to_use`` 和 ``examples``（参见 ChunkEvolvePromptBuilder）。
    * ``_batched`` 改为 round-robin 跨 trajectory 取 chunk，保证一个 batch 内的
      chunk 来自不同 case，进化出的 script 更通用。
    """

    name = "evolve_chunk"

    REQUIRED_INTRO_FIELDS = (
        "name",
        "description",
        "entrypoint",
        "parameters",
        "cost_saving_rationale",
    )

    def find_samples(self, result_dir, task=None) -> List[Path]:
        files = sorted(
            Path(result_dir).glob("**/agent/contrastive_*_chunk_*.json")
        )
        if not task:
            return files
        matched = [p for p in files if self._task_matches(p, task)]
        return matched or [p for p in files if task in str(p)]

    @staticmethod
    def _task_matches(path: Path, task: str) -> bool:
        stem = path.parent.parent.name
        return stem == task or stem.startswith(f"{task}__") or task in stem.split("__")

    def _batched(self, items: List[Path], batch_size: int):
        """Round-robin 跨 trajectory 分 batch，保证 batch 内 chunk 来自不同 case。

        具体做法：把所有 chunk 按 trajectory 分桶，每桶按 chunk_id 排序后，
        依次从每个桶弹出队首，拼成一个跨 case 的 chunk 队列。
        然后按 ``batch_size`` 切片。这样在 ``batch_size <= 桶数`` 时，
        每个 batch 内的 chunk 全部来自不同 trajectory。
        """
        chunk_groups: Dict[Tuple[str, int], List[Path]] = {}
        for p in items:
            key = ChunkEvolvePromptBuilder._chunk_key(p)
            chunk_groups.setdefault(key, []).append(p)

        by_traj: Dict[str, List[Tuple[str, int]]] = {}
        for key in chunk_groups:
            by_traj.setdefault(key[0], []).append(key)
        for traj in by_traj:
            by_traj[traj].sort(key=lambda k: k[1])

        trajectories = sorted(by_traj.keys())
        chunk_queue: List[Tuple[str, int]] = []
        while any(by_traj.values()):
            for traj in trajectories:
                if by_traj[traj]:
                    chunk_queue.append(by_traj[traj].pop(0))

        for i in range(0, len(chunk_queue), batch_size):
            batch_keys = chunk_queue[i : i + batch_size]
            batch: List[Path] = []
            for k in batch_keys:
                batch.extend(chunk_groups[k])
            yield batch


# ============================================================================
# Factory + CLI
# ============================================================================


def make_chunk_annotator(
    config_path,
    workers: int = 1,
    retry_failed: int = 1,
    long_obs_threshold: int = LONG_OBS_THRESHOLD_DEFAULT,
) -> ChunkTrajectoryAnnotator:
    return ChunkTrajectoryAnnotator(
        config_path=config_path,
        workers=workers,
        retry_failed=retry_failed,
        long_obs_threshold=long_obs_threshold,
    )


def make_chunk_contrastive_builder(
    min_reduction_ratio: float = MIN_REDUCTION_RATIO_DEFAULT,
) -> ChunkContrastiveSampleBuilder:
    return ChunkContrastiveSampleBuilder(min_reduction_ratio=min_reduction_ratio)


def make_chunk_evolver(
    scripts_dir,
    config_path,
    mini_swe_agent_dir,
    batch_size: int = 2,
    max_observation_chars: int = 1000,
    output_dir: Optional[str] = None,
    dry_run: bool = False,
    resume: bool = True,
) -> ChunkScriptEvolver:
    return ChunkScriptEvolver(
        scripts_dir=scripts_dir,
        runner=MiniSweAgentRunner(
            mini_swe_agent_dir=mini_swe_agent_dir,
            llm_config=config_path,
            dry_run=dry_run,
        ),
        prompt_builder=ChunkEvolvePromptBuilder(
            serializer=TrajectorySerializer(max_observation_chars=max_observation_chars),
        ),
        batch_size=batch_size,
        output_dir=Path(output_dir).resolve() if output_dir else None,
        resume=resume,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Evolve v1 chunk: chunked trajectories with brief observation annotation.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_annotate = sub.add_parser("annotate", help="Stage 1: dependencies + brief_observations")
    _add_common(p_annotate)
    _add_config(p_annotate)
    _add_common_annotate(p_annotate)
    p_annotate.add_argument(
        "--long-obs-threshold", type=int, default=LONG_OBS_THRESHOLD_DEFAULT,
        help="observation 字符数超过此阈值才标注 brief (default: %(default)s)",
    )

    p_contrast = sub.add_parser("contrastive", help="Stage 2: chunk + graph/obs samples")
    _add_common(p_contrast)
    p_contrast.add_argument(
        "--chunk-size", type=int, default=CHUNK_SIZE_DEFAULT,
        help="每个 chunk 的 action step 数 (default: %(default)s)",
    )
    p_contrast.add_argument(
        "--min-reduction-ratio", type=float, default=MIN_REDUCTION_RATIO_DEFAULT,
        help="mini chunk 相对原 chunk 至少省掉这么比例的 action step 才作为 graph contrastive sample (default: %(default)s)",
    )

    p_evolve = sub.add_parser("evolve", help="Stage 3: evolve scripts from chunk samples")
    _add_common(p_evolve)
    _add_config(p_evolve)
    _add_evolve(p_evolve)

    p_run = sub.add_parser("run", help="run the full chunk pipeline")
    _add_common(p_run)
    _add_config(p_run)
    _add_common_annotate(p_run)
    p_run.add_argument(
        "--long-obs-threshold", type=int, default=LONG_OBS_THRESHOLD_DEFAULT,
        help="observation 字符数超过此阈值才标注 brief (default: %(default)s)",
    )
    _add_evolve(p_run)
    p_run.add_argument(
        "--chunk-size", type=int, default=CHUNK_SIZE_DEFAULT,
        help="每个 chunk 的 action step 数 (default: %(default)s)",
    )
    p_run.add_argument(
        "--min-reduction-ratio", type=float, default=MIN_REDUCTION_RATIO_DEFAULT,
        help="mini chunk 相对原 chunk 至少省掉这么比例的 action step 才作为 graph contrastive sample (default: %(default)s)",
    )
    p_run.add_argument(
        "--skip", action="append", default=[],
        help="stage name(s) to skip (annotate_chunk / contrastive_chunk / evolve_chunk)",
    )

    args = parser.parse_args(argv)
    _setup_logging(getattr(args, "log_file", None))

    if args.cmd == "annotate":
        make_chunk_annotator(
            config_path=args.config,
            workers=args.workers,
            retry_failed=args.retry_failed,
            long_obs_threshold=args.long_obs_threshold,
        ).run(args.result_dir, task=args.task)
    elif args.cmd == "contrastive":
        builder = make_chunk_contrastive_builder(
            min_reduction_ratio=args.min_reduction_ratio,
        )
        builder.CHUNK_SIZE = args.chunk_size
        builder.run(args.result_dir, task=args.task)
    elif args.cmd == "evolve":
        make_chunk_evolver(
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

        if "annotate_chunk" not in skip and "annotate" not in skip:
            logger.info("[stage 1/3] annotate_chunk on %s", result_dir)
            make_chunk_annotator(
                config_path=args.config,
                workers=args.workers,
                retry_failed=args.retry_failed,
                long_obs_threshold=args.long_obs_threshold,
            ).run(result_dir, task=args.task)

        if "contrastive_chunk" not in skip and "contrastive" not in skip:
            logger.info("[stage 2/3] contrastive_chunk on %s", result_dir)
            builder = make_chunk_contrastive_builder(
                min_reduction_ratio=args.min_reduction_ratio,
            )
            builder.CHUNK_SIZE = args.chunk_size
            builder.run(result_dir, task=args.task)

        if "evolve_chunk" not in skip and "evolve" not in skip:
            logger.info("[stage 3/3] evolve_chunk on %s", result_dir)
            make_chunk_evolver(
                scripts_dir=args.scripts_dir,
                config_path=args.config,
                mini_swe_agent_dir=args.mini_swe_agent_dir,
                batch_size=args.batch_size,
                max_observation_chars=args.max_observation_chars,
                output_dir=args.output_dir,
                dry_run=args.dry_run,
                resume=not args.no_resume,
            ).run(result_dir, task=args.task)


def _add_common_annotate(parser):
    parser.add_argument(
        "--workers", type=int, default=1,
        help="total parallel LLM calls across trajectory files and steps",
    )
    parser.add_argument(
        "--retry-failed", type=int, default=1,
        help="retry failed trajectory files after the first pass",
    )


if __name__ == "__main__":
    main()
