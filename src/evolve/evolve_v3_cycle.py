"""Evolve v3 cycle: 在 v2 chunk 之上闭合"演化 -> 下游验证 -> 反馈"的回路。

与 v2 的关键差异（见 ``evolve_v3_cycle.md``）
----------------------------------------------
v2 演化出 helper scripts 后**从不在下游 code agent 上验证**。v3 闭合回路：

    S0 = v2 evolve(baseline T0)            # 初始脚本（若 scripts_dir 为空则自举）
    for round in 1..max_rounds:
        T1  = 装上 S，在 16 个 evolve case 上再跑一轮 code agent
        T1  ← LLM judge 是否完成 task          # Evaluate(T1)
        T1  ← v2 annotate + contrastive      # 得到 DAG 与最小 trajectory T*1
                                              # （annotate 一次 LLM 调用同时产出
                                              #  dependencies 与 step_meta.op_type）
        指标 = T0 vs T1（API cost / step 数 / 最大 obs token / obs 平均 token）
        若 Evaluate(T1)=Success：
            收敛(|C(T1)-C(T*1)|≤t 或 |C(T0)-C(T1)|≤t 或 round≥max) → 停
            否则：基于 T1、T*1 重新走 v2 更新流程，更新 scripts
        若 Evaluate(T0)=Success 且 Evaluate(T1)=Fail（脚本搞坏了）：
            LLM 诊断错误原因 + 修改计划 → 交给下游 code agent 修 scripts

设计要点
--------
* Evaluate(T) 用 **LLM-only judge**（按文档要求）。benchmark verifier 的 reward 只作
  ``verifier_reference`` 信息字段保存，**不参与任何决策**。
* 每轮只在"16 个 evolve case"上验证：建一个临时软链 task 目录，通过
  ``DEEP_SWE_TASKS_PATH`` / ``SWE_ATLAS_DATA_DIR`` / ``SWEBENCH_TASK_PATH`` 把
  ``scripts/run_*.sh`` 指过去（见 ``scripts/`` 下对应脚本的 ``:-`` 默认，完全向后兼容）。
* 收敛成本 ``Cost(T) = Σ n_action_steps``（步数是真实成本主驱动，见 v2 分析报告
  "省了约 11% 步数"）。token / obs 指标另行保存供对比。
* 复用 v2 的 stage 类（``ChunkTrajectoryAnnotatorV2`` 等），只在外层编排。

用法
----
::

    python -m src.evolve.evolve_v3_cycle run \\
        --benchmark deep-swe \\
        --baseline-dir results/deep-swe/deepseek-flash-without-evolve-tools \\
        --scripts-dir .evolve_scripts_v3_deep-swe \\
        --work-dir results/v3_cycle/deep-swe \\
        --config _config/deepseekv4_flash.yaml \\
        --eval-cases-file .evolve_scripts_deep-swe/evolve_used_case_id.txt

    # 单段调试
    python -m src.evolve.evolve_v3_cycle evaluate  <run_dir> --benchmark deep-swe ...
    python -m src.evolve.evolve_v3_cycle metrics    <run_dir> --benchmark deep-swe ...
    python -m src.evolve.evolve_v3_cycle re-evolve  <run_dir> --scripts-dir ... ...
    python -m src.evolve.evolve_v3_cycle diagnose   <run_dir> --baseline-dir ... ...
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from src.tools.llm import LLM

from ._chunk_helpers import observation_chars
from .evolver import TrajectorySerializer
from .evolve_v2_chunk import (
    HOTSPOT_MIN_OCCURRENCES_DEFAULT,
    HOTSPOT_MIN_TOTAL_CHARS_DEFAULT,
    LONG_OBS_THRESHOLD_DEFAULT,
    MIN_REDUCTION_RATIO_DEFAULT,
    ChunkEvolvePromptBuilderV2,
    ChunkTrajectoryAnnotatorV2,
    MiniSweAgentRunnerV2,
    make_v2_annotator,
    make_v2_contrastive_builder,
    make_v2_evolver,
)
from .run_evolve import (
    DEFAULT_MINI_SWE_AGENT,
    _add_common,
    _add_config,
    _add_evolve,
    _setup_logging,
)

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCRIPTS_DIR = ROOT / ".evolve_scripts_v3"
DEFAULT_WORK_DIR = ROOT / "results" / "v3_cycle"
DEFAULT_DEEP_SWE_BASELINE = ROOT / "results" / "deep-swe" / "deepseek-flash-without-evolve-tools"

# 收敛阈值默认值
DEFAULT_MAX_ROUNDS = 5
DEFAULT_MIN_ROUNDS = 2          # 第 1 轮不判收敛，避免随机性早停
DEFAULT_CONVERGE_ABS = 1.0       # |C(T1)-C(T*1)| 或 |C(T0)-C(T1)| 步数差 ≤ 此值即收敛
DEFAULT_CONVERGE_REL = 0.05     # 相对差 ≤ 5% 即收敛

# LLM judge / 诊断的序列化上限
DEFAULT_JUDGE_MAX_STEPS = 40
DEFAULT_JUDGE_MAX_OBS = 500
DEFAULT_DIAGNOSE_MAX_STEPS = 30
DEFAULT_DIAGNOSE_MAX_FAIL_CASES = 6  # diagnose 时最多展示的失败 case 数（按步数降序）
DEFAULT_SCRIPT_DETAIL_CHARS = 2000   # diagnose 时每个 main.sh 最多展示字符


# ============================================================================
# benchmark 元信息表（镜像 scripts/evolve_in_individual_bench.sh 的 bench_* 函数）
# ============================================================================


def _bench_source_task_dir(benchmark: str) -> Optional[Path]:
    """benchmark 的源任务目录（用于软链 16 个 case）。"""
    if benchmark == "deep-swe":
        return ROOT / "benchmark" / "deep-swe" / "tasks"
    if benchmark.startswith("swe-atlas-"):
        split = benchmark.split("-", 2)[-1]   # qa / tw / rf
        return ROOT / "benchmark" / "SWE-Atlas" / "data" / split
    return None   # swebench / datamind 由调用方提供


# benchmark -> (run_script, results_subdir, split, task_path_env, temp_layout, include_only)
#   temp_layout: "flat"  -> <temp>/<case> 软链，env=<temp>
#                "split" -> <temp>/<split>/<case> 软链，env=<temp>（swe-atlas 用）
#                None    -> 不支持 include-only（datamind 走 run+filter 兜底）
BENCHMARKS: Dict[str, dict] = {
    "deep-swe": dict(
        run_script="run_deep_swe.sh", results_subdir="deep-swe", split="",
        task_path_env="DEEP_SWE_TASKS_PATH", temp_layout="flat", include_only=True,
    ),
    "swe-atlas-qa": dict(
        run_script="run_swe_atlas.sh", results_subdir="swe-atlas-qa", split="qa",
        task_path_env="SWE_ATLAS_DATA_DIR", temp_layout="split", include_only=True,
    ),
    "swe-atlas-tw": dict(
        run_script="run_swe_atlas.sh", results_subdir="swe-atlas-tw", split="tw",
        task_path_env="SWE_ATLAS_DATA_DIR", temp_layout="split", include_only=True,
    ),
    "swe-atlas-rf": dict(
        run_script="run_swe_atlas.sh", results_subdir="swe-atlas-rf", split="rf",
        task_path_env="SWE_ATLAS_DATA_DIR", temp_layout="split", include_only=True,
    ),
    "swebench": dict(
        run_script="run_swe_bench.sh", results_subdir="swebench-verified", split="",
        task_path_env="SWEBENCH_TASK_PATH", temp_layout="flat", include_only=True,
    ),
    "datamind": dict(
        run_script="run_datamind.sh", results_subdir="datamind-longds", split="",
        task_path_env=None, temp_layout=None, include_only=False,
    ),
}


# ============================================================================
# 通用工具
# ============================================================================


def is_action_step(step) -> bool:
    return bool(step.get("tool_calls") or "observation" in step or step.get("action"))


def find_trajectory_for_case(base, cid: str) -> Optional[Path]:
    """在 base 下找某 case 的 trajectory.json（trial 目录形如 <cid>__<suffix>）。"""
    base = Path(base)
    if not base.exists():
        return None
    for pat in (f"**/{cid}/agent/trajectory.json", f"**/{cid}__*/agent/trajectory.json"):
        hits = sorted(base.glob(pat))
        if hits:
            return hits[0]
    return None


def find_trial_dir(base, cid: str) -> Optional[Path]:
    t = find_trajectory_for_case(base, cid)
    return t.parent.parent if t else None


def _read_json(path) -> Optional[dict]:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _file_hash(path) -> str:
    try:
        h = hashlib.md5()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 16), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return str(Path(path).stat().st_mtime)


# ============================================================================
# TrajectoryMetrics — 单条 / 一轮 run 的成本指标
# ============================================================================


@dataclass
class CaseMetrics:
    case_id: str
    has_trajectory: bool
    trajectory_path: str = ""
    n_action_steps: int = 0          # Cost(T) 用的步数（与 minimal 同口径）
    recorded_steps: int = 0          # final_metrics.total_steps（参考）
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_cached_tokens: int = 0
    billed_tokens: int = 0           # prompt + completion - cached
    max_obs_chars: int = 0
    total_obs_chars: int = 0
    avg_obs_chars_per_step: float = 0.0
    api_cost_usd: Optional[float] = None
    verifier_reference: dict = field(default_factory=dict)   # 仅信息，不参与决策


class TrajectoryMetrics:
    """从 trajectory.json / result.json 抽成本指标。复用 ``_chunk_helpers`` 做步扫描。"""

    def __init__(self, pricing: Optional[dict] = None):
        # pricing: {"input": x, "cache": y, "output": z} 每 1M token 的 USD；None 则不估算
        self.pricing = pricing

    # ---------- 单条 ----------

    def from_trajectory(self, traj_path, case_id: str) -> CaseMetrics:
        traj_path = Path(traj_path)
        data = _read_json(traj_path) or {}
        steps = data.get("steps", [])
        action_steps = [s for s in steps if is_action_step(s)]
        n = len(action_steps)

        obs_chars = [observation_chars(s.get("observation", "")) for s in action_steps]
        total_obs = int(sum(obs_chars))
        max_obs = int(max(obs_chars)) if obs_chars else 0
        avg_obs = (total_obs / n) if n else 0.0

        fm = data.get("final_metrics", {}) or {}
        prompt = int(fm.get("total_prompt_tokens", 0) or 0)
        completion = int(fm.get("total_completion_tokens", 0) or 0)
        cached = int(fm.get("total_cached_tokens", 0) or 0)
        billed = max(0, prompt + completion - cached)

        api_cost = self._maybe_api_cost(traj_path, prompt, completion, cached)

        return CaseMetrics(
            case_id=case_id,
            has_trajectory=True,
            trajectory_path=str(traj_path),
            n_action_steps=n,
            recorded_steps=int(fm.get("total_steps", 0) or 0),
            total_prompt_tokens=prompt,
            total_completion_tokens=completion,
            total_cached_tokens=cached,
            billed_tokens=billed,
            max_obs_chars=max_obs,
            total_obs_chars=total_obs,
            avg_obs_chars_per_step=round(avg_obs, 1),
            api_cost_usd=api_cost,
            verifier_reference=self._verifier_reference(traj_path),
        )

    def missing(self, case_id: str) -> CaseMetrics:
        return CaseMetrics(case_id=case_id, has_trajectory=False)

    # ---------- 一轮 run 聚合 ----------

    def from_run(self, run_dir, case_ids: List[str]) -> dict:
        per_case: Dict[str, dict] = {}
        for cid in case_ids:
            traj = find_trajectory_for_case(run_dir, cid)
            m = self.from_trajectory(traj, cid) if traj else self.missing(cid)
            per_case[cid] = asdict(m)
        return {"per_case": per_case, "aggregate": self._aggregate(per_case)}

    # ---------- 汇总对比 ----------

    @staticmethod
    def compare(t0: dict, t1: dict) -> dict:
        """T0 vs T1 成本对比（修改1：只比 T0 vs T1，不再算 T*1 / 收敛 gap）。

        成本主驱动是步数 ``cost_T*``（Σ n_action_steps）；另存真实 API USD
        （来自 ``result.json.agent_result.cost_usd``，可能为 null = 无成本数据）、
        token、observation 指标，供 CostReviewer 判断是否达到降成本预期。
        """
        def cost(per_case):
            return sum(m["n_action_steps"] for m in per_case.values())
        c_t0 = cost(t0["per_case"])
        c_t1 = cost(t1["per_case"])

        def agg(per_case, key):
            vals = [m[key] for m in per_case.values() if m.get("has_trajectory")]
            return round(sum(vals) / len(vals), 1) if vals else 0.0

        def agg_cost(per_case):
            # api_cost_usd 可能为 None —— 只对有值的 case 求和/均值；全 None 返回 None
            vals = [m["api_cost_usd"] for m in per_case.values()
                    if m.get("has_trajectory") and m.get("api_cost_usd") is not None]
            if not vals:
                return None, None
            return round(sum(vals), 4), round(sum(vals) / len(vals), 4)

        t0_total, t0_mean = agg_cost(t0["per_case"])
        t1_total, t1_mean = agg_cost(t1["per_case"])

        return {
            "cost_T0": c_t0,
            "cost_T1": c_t1,
            "steps_gap_abs": c_t1 - c_t0,
            "steps_gap_rel": round((c_t1 - c_t0) / max(1, c_t0), 4),
            "total_api_cost_T0": t0_total,
            "mean_api_cost_T0": t0_mean,
            "total_api_cost_T1": t1_total,
            "mean_api_cost_T1": t1_mean,
            "mean_steps_T0": agg(t0["per_case"], "n_action_steps"),
            "mean_steps_T1": agg(t1["per_case"], "n_action_steps"),
            "mean_billed_T0": agg(t0["per_case"], "billed_tokens"),
            "mean_billed_T1": agg(t1["per_case"], "billed_tokens"),
            "mean_max_obs_T0": agg(t0["per_case"], "max_obs_chars"),
            "mean_max_obs_T1": agg(t1["per_case"], "max_obs_chars"),
            "mean_avg_obs_T0": agg(t0["per_case"], "avg_obs_chars_per_step"),
            "mean_avg_obs_T1": agg(t1["per_case"], "avg_obs_chars_per_step"),
        }

    # ---------- helpers ----------

    def _maybe_api_cost(self, traj_path, prompt, completion, cached) -> Optional[float]:
        # 优先用 result.json.agent_result.cost_usd（benchmark 真实账单）
        result = _read_json(traj_path.parent.parent / "result.json")
        ar = (result or {}).get("agent_result") or {}
        cost = ar.get("cost_usd")
        if isinstance(cost, (int, float)) and cost > 0:
            return round(float(cost), 4)
        if not self.pricing:
            return None
        p = self.pricing
        return round(
            (prompt - cached) * p.get("input", 0) / 1e6
            + cached * p.get("cache", 0) / 1e6
            + completion * p.get("output", 0) / 1e6,
            4,
        )

    @staticmethod
    def _aggregate(per_case: Dict[str, dict]) -> dict:
        ms = [m for m in per_case.values() if m.get("has_trajectory")]
        n = len(ms) or 1
        # api_cost_usd 可能为 None（无 result.json / 无 pricing）—— 跳过 None；
        # 全部为 None 时记 null（"无成本数据"），不要凑成 0.0。
        api_vals = [m["api_cost_usd"] for m in ms if m.get("api_cost_usd") is not None]
        api_total = round(sum(api_vals), 4) if api_vals else None
        api_mean = round(sum(api_vals) / len(api_vals), 4) if api_vals else None
        return {
            "n_cases": len(per_case),
            "n_with_trajectory": len(ms),
            "total_steps": sum(m["n_action_steps"] for m in ms),
            "total_billed_tokens": sum(m["billed_tokens"] for m in ms),
            "total_prompt_tokens": sum(m["total_prompt_tokens"] for m in ms),
            "total_completion_tokens": sum(m["total_completion_tokens"] for m in ms),
            "total_cached_tokens": sum(m["total_cached_tokens"] for m in ms),
            "total_api_cost_usd": api_total,
            "mean_api_cost_usd": api_mean,
            "mean_steps": round(sum(m["n_action_steps"] for m in ms) / n, 1),
            "mean_billed_tokens": round(sum(m["billed_tokens"] for m in ms) / n, 1),
            "mean_max_obs_chars": round(sum(m["max_obs_chars"] for m in ms) / n, 1),
            "mean_avg_obs_chars": round(sum(m["avg_obs_chars_per_step"] for m in ms) / n, 1),
        }

    @staticmethod
    def _verifier_reference(traj_path) -> dict:
        """读 verifier 的 reward，仅作信息字段保存（不参与决策）。"""
        result = _read_json(traj_path.parent.parent / "result.json") or {}
        rewards = ((result.get("verifier_result") or {}).get("rewards")) or {}
        if not rewards:
            rewards = _read_json(traj_path.parent.parent / "verifier" / "reward.json") or {}
        if not rewards:
            return {}
        return {
            "reward": rewards.get("reward"),
            "overall_pass": rewards.get("overall_pass"),
            "tests_reward": rewards.get("tests_reward"),
            "partial": rewards.get("partial"),
            "note": "informational only; not used in V3 decisions",
        }


# ============================================================================
# TrajectoryEvaluator — LLM-only judge（Evaluate(T)）
# ============================================================================


class TrajectoryEvaluator:
    """LLM judge：读 trajectory 判断是否完成 task。

    按文档要求，决策**只**用 LLM judge；benchmark verifier 的 reward 不参与。
    """

    SYSTEM_PROMPT = (
        "You judge whether a coding agent completed its assigned task. "
        "You are given the task description and a compact trace of the agent's actions "
        "and observations. A task is SUCCESS if the agent produced a plausible fix "
        "(edited relevant files, ran tests or a build, and submitted/committed the work) "
        "without getting stuck or aborting. It is FAIL if the agent produced no meaningful "
        "fix, got stuck repeating failed commands, hit fatal errors, or aborted without a "
        "patch. Be lenient on style, strict on whether a real fix was produced. "
        "Output ONLY a JSON object: {\"success\": true|false, \"reason\": \"one short sentence\"}."
    )

    def __init__(self, config_path, cache_path: Optional[Path] = None,
                 max_steps: int = DEFAULT_JUDGE_MAX_STEPS,
                 max_obs: int = DEFAULT_JUDGE_MAX_OBS):
        self.config_path = str(config_path)
        self.cache_path = Path(cache_path) if cache_path else None
        self.max_steps = int(max_steps)
        self.judge_serializer = TrajectorySerializer(
            max_observation_chars=max_obs, max_action_chars=800,
        )
        self._cache: Dict[str, dict] = {}
        self._load_cache()

    # ---------- public ----------

    def evaluate_run(self, run_dir, case_ids: List[str]) -> Dict[str, dict]:
        out: Dict[str, dict] = {}
        for cid in case_ids:
            traj = find_trajectory_for_case(run_dir, cid)
            out[cid] = self.evaluate_case(traj, cid) if traj else self._no_trajectory(cid)
        return out

    def evaluate_case(self, traj_path, case_id: str) -> dict:
        traj_path = Path(traj_path)
        key = _file_hash(traj_path)
        if key in self._cache:
            cached = dict(self._cache[key])
            cached["cached"] = True
            return cached

        data = _read_json(traj_path) or {}
        task_info = ChunkEvolvePromptBuilderV2._extract_task_info(str(traj_path))
        task_desc = (task_info or {}).get("task_description", "")
        verdict = self._llm_judge(data, task_desc, case_id)
        verdict["case_id"] = case_id
        verdict["trajectory_path"] = str(traj_path)
        verdict["cached"] = False
        self._cache[key] = verdict
        self._save_cache()
        return verdict

    # ---------- helpers ----------

    def _llm_judge(self, traj: dict, task_desc: str, case_id: str) -> dict:
        llm = LLM(self.config_path)
        user = self._build_user_prompt(traj, task_desc)
        try:
            raw = llm.query(self.SYSTEM_PROMPT, "", user)
        except Exception as exc:
            logger.exception("LLM judge failed for %s: %s", case_id, exc)
            return {"success": None, "reason": f"llm_error: {exc}"}
        parsed = self._parse_judge_json(raw)
        if parsed is None:
            logger.warning("unparseable judge output for %s: %r", case_id, raw[:300])
            return {"success": None, "reason": "judge_parse_error"}
        return parsed

    def _build_user_prompt(self, traj: dict, task_desc: str) -> str:
        action_steps = [s for s in traj.get("steps", []) if is_action_step(s)]
        if len(action_steps) > self.max_steps:
            head, tail = action_steps[:3], action_steps[-(self.max_steps - 3):]
            note = (f"\n[showing first 3 + last {self.max_steps - 3} of "
                    f"{len(action_steps)} action steps; middle omitted]\n")
            trace = note + self.judge_serializer.serialize({"steps": head + tail})
        else:
            trace = self.judge_serializer.serialize({"steps": action_steps})
        td = " ".join((task_desc or "").split())
        if len(td) > 600:
            td = td[:600].rstrip() + "..."
        return f"Task description:\n{td or '(not found)'}\n\nAgent trajectory:\n{trace}"

    @staticmethod
    def _parse_judge_json(text: str) -> Optional[dict]:
        text = text or ""
        matches = re.findall(r"\{[\s\S]*\}", text)
        candidate = matches[-1] if matches else text
        try:
            obj = json.loads(candidate)
        except json.JSONDecodeError:
            return None
        if not isinstance(obj, dict):
            return None
        success = obj.get("success")
        if isinstance(success, str):
            success = success.strip().lower() in ("true", "yes", "1", "success")
        elif not isinstance(success, bool):
            return None
        return {"success": bool(success), "reason": str(obj.get("reason", ""))[:500]}

    @staticmethod
    def _no_trajectory(case_id: str) -> dict:
        return {
            "case_id": case_id, "success": False,
            "reason": "no trajectory produced by the run",
            "trajectory_path": "", "cached": False,
        }

    # ---------- cache ----------

    def _load_cache(self) -> None:
        if not self.cache_path:
            return
        data = _read_json(self.cache_path) or {}
        if isinstance(data, dict):
            self._cache = data

    def _save_cache(self) -> None:
        if not self.cache_path:
            return
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(
                json.dumps(self._cache, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning("failed to save eval cache: %s", exc)


# ============================================================================
# BenchmarkRunner — 装脚本、在 16 个 case 上跑一轮、产出 T1
# ============================================================================


class BenchmarkRunner:
    """驱动 scripts/run_<bench>.sh：装上 evolve scripts、只在指定 case 上跑。

    通过临时软链 task 目录 + 脚本的 ``:-`` 覆盖 env（DEEP_SWE_TASKS_PATH /
    SWE_ATLAS_DATA_DIR / SWEBENCH_TASK_PATH）实现"只跑这 16 个 case"。datamind 无
    include 过滤，退化为整跑 + 按 case 过滤结果。
    """

    def __init__(self, benchmark: str, config_path, swebench_task_path: Optional[str] = None,
                 n_tasks: int = 1000, n_concurrent: int = 8, n_attempts: int = 1,
                 taskdir_root: Optional[Path] = None):
        if benchmark not in BENCHMARKS:
            raise ValueError(f"unknown benchmark: {benchmark} (known: {list(BENCHMARKS)})")
        self.benchmark = benchmark
        self.meta = BENCHMARKS[benchmark]
        self.config_path = str(config_path)
        self.swebench_task_path = swebench_task_path
        self.n_tasks = int(n_tasks)
        self.n_concurrent = int(n_concurrent)
        self.n_attempts = int(n_attempts)
        self.taskdir_root = Path(taskdir_root) if taskdir_root else (DEFAULT_WORK_DIR / "taskdirs")

    # ---------- public ----------

    def run(self, scripts_dir, case_ids: List[str], run_id: str,
            dry_run: bool = False) -> Path:
        """装 scripts、跑 16 个 case，返回 run 输出目录。"""
        scripts_dir = Path(scripts_dir)
        env = self._build_env(scripts_dir, case_ids, run_id)
        cmd = ["bash", str(ROOT / "scripts" / self.meta["run_script"])]
        run_dir = self._expected_run_dir(run_id)

        logger.info("[v3 run] %s run_id=%s cases=%d -> %s",
                    self.benchmark, run_id, len(case_ids), run_dir)
        logger.info("[v3 run] cmd: %s", " ".join(shlex.quote(x) for x in cmd))
        redacted = {k: (v if "KEY" not in k else "***") for k, v in env.items()
                    if k in self._env_keys_to_log()}
        logger.info("[v3 run] env overrides: %s", redacted)

        if dry_run:
            logger.info("[v3 run] DRY_RUN — not executing; expected run_dir=%s", run_dir)
            return run_dir

        run_dir.parent.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(cmd, env=env, cwd=str(ROOT), capture_output=True, text=True)
        if proc.stdout:
            logger.info("[v3 run] stdout tail:\n%s", proc.stdout[-3000:])
        if proc.stderr:
            logger.info("[v3 run] stderr tail:\n%s", proc.stderr[-3000:])
        if proc.returncode != 0:
            logger.warning("[v3 run] run script exited %d (partial failures may be OK)",
                           proc.returncode)
        if not run_dir.exists():
            run_dir = self._resolve_run_dir(run_id) or run_dir
        return run_dir

    # ---------- env + temp task dir ----------

    def _build_env(self, scripts_dir: Path, case_ids: List[str], run_id: str) -> dict:
        env = dict(os.environ)
        # 让子脚本的 ${VAR:-default} 沿用我们的值
        env.update({
            "ROOT_DIR": str(ROOT),
            "RESULTS_DIR": self._results_dir(),
            # 下游 code agent 用与 evolve 同一个 LLM 配置（同一被测模型）
            "LLM_CONFIG": self.config_path,
            "EVOLVE_SCRIPTS_DIR": str(scripts_dir) if scripts_dir.exists() else "",
            "RUN_ID": run_id,
            "N_TASKS": str(self.n_tasks),
            "N_CONCURRENT": str(self.n_concurrent),
            "N_ATTEMPTS": str(self.n_attempts),
            # V3 验证要 *包含* 16 个 evolve case，故强制不跳过
            "EVOLVE_SKIP_FILE": "",
        })
        if self.meta["split"]:
            env["SWE_ATLAS_SPLITS"] = self.meta["split"]

        if not self.meta["include_only"] or not case_ids:
            logger.info("[v3 run] include-only not supported for %s; running full + filter",
                        self.benchmark)
            return env

        temp = self._build_temp_task_dir(case_ids, run_id)
        if temp is None:
            return env
        # _build_temp_task_dir 已返回应赋给 env 的路径本身：
        #   flat  -> <taskdir_root>/<run_id>           （pier -p 直接指过去）
        #   split -> <taskdir_root>/<run_id>           （harbor -p = ${env}/${split}）
        env[self.meta["task_path_env"]] = str(temp)
        return env

    def _build_temp_task_dir(self, case_ids: List[str], run_id: str) -> Optional[Path]:
        """软链 16 个 case 到临时目录，返回要赋给 task_path_env 的路径。"""
        src = self._source_task_dir()
        if src is None:
            logger.warning("[v3 run] no source task dir for %s; skipping temp task-dir",
                           self.benchmark)
            return None
        base = self.taskdir_root / run_id
        # 每轮重建，避免残留
        if base.exists():
            _safe_rmtree(base)
        base.mkdir(parents=True, exist_ok=True)

        if self.meta["temp_layout"] == "split":
            target_dir = base / self.meta["split"]
            target_dir.mkdir(parents=True, exist_ok=True)
            env_value_dir = base   # SWE_ATLAS_DATA_DIR = base；-p = base/<split>
        else:
            target_dir = base
            env_value_dir = base

        n_linked = 0
        for cid in case_ids:
            case_src = src / cid
            if not case_src.exists():
                logger.warning("[v3 run] case task dir missing, skip: %s", case_src)
                continue
            link = target_dir / cid
            try:
                os.symlink(str(case_src.resolve()), str(link))
                n_linked += 1
            except OSError as exc:
                logger.warning("[v3 run] symlink failed for %s: %s", cid, exc)
        logger.info("[v3 run] linked %d/%d cases into %s", n_linked, len(case_ids), target_dir)
        return env_value_dir if self.meta["temp_layout"] == "split" else target_dir

    def _source_task_dir(self) -> Optional[Path]:
        if self.benchmark == "swebench":
            return Path(self.swebench_task_path) if self.swebench_task_path else None
        return _bench_source_task_dir(self.benchmark)

    # ---------- run dir ----------

    def _results_dir(self) -> str:
        return os.environ.get("RESULTS_DIR", str(ROOT / "results"))

    def _expected_run_dir(self, run_id: str) -> Path:
        return Path(self._results_dir()) / self.meta["results_subdir"] / run_id

    def _resolve_run_dir(self, run_id: str) -> Optional[Path]:
        base = self._expected_run_dir(run_id).parent
        if not base.exists():
            return None
        hits = sorted(base.glob(f"{run_id}*"))
        return hits[0] if hits else None

    def _env_keys_to_log(self) -> set:
        return {"EVOLVE_SCRIPTS_DIR", "RUN_ID", "N_TASKS", "N_CONCURRENT", "N_ATTEMPTS",
                "SWE_ATLAS_SPLITS", "DEEP_SWE_TASKS_PATH", "SWE_ATLAS_DATA_DIR",
                "SWEBENCH_TASK_PATH", "EVOLVE_SKIP_FILE"}


def _safe_rmtree(path: Path) -> None:
    """删临时软链目录：只删软链、不跟进目标。"""
    if not path.exists():
        return
    for entry in path.iterdir():
        if entry.is_symlink() or entry.is_file():
            entry.unlink()
        elif entry.is_dir():
            shutil.rmtree(entry, ignore_errors=True)
    # 再删空目录本身
    try:
        path.rmdir()
    except OSError:
        pass


# ============================================================================
# V2ReEvolver — 在 T1 run dir 上重走 v2 更新流程
# ============================================================================


class V2ReEvolver:
    """复用 v2 的 stage 类：annotate + contrastive（产 T*1）+ evolve（更新 scripts）。"""

    def __init__(self, config_path, mini_swe_agent_dir, workers: int = 8,
                 batch_size: int = 4, max_observation_chars: int = 1000,
                 long_obs_threshold: int = LONG_OBS_THRESHOLD_DEFAULT,
                 min_reduction_ratio: float = MIN_REDUCTION_RATIO_DEFAULT,
                 hotspot_min_occurrences: int = HOTSPOT_MIN_OCCURRENCES_DEFAULT,
                 hotspot_min_total_chars: int = HOTSPOT_MIN_TOTAL_CHARS_DEFAULT,
                 dry_run: bool = False, resume: bool = True):
        self.config_path = str(config_path)
        self.mini_swe_agent_dir = str(mini_swe_agent_dir)
        self.workers = int(workers)
        self.batch_size = int(batch_size)
        self.max_observation_chars = int(max_observation_chars)
        self.long_obs_threshold = int(long_obs_threshold)
        self.min_reduction_ratio = float(min_reduction_ratio)
        self.hotspot_min_occurrences = int(hotspot_min_occurrences)
        self.hotspot_min_total_chars = int(hotspot_min_total_chars)
        self.dry_run = bool(dry_run)
        self.resume = bool(resume)

    def annotate_and_contrastive(self, run_dir, task: Optional[str] = None) -> None:
        """Stage 1+2：标注依赖（含 LLM op_type）+ 生成 contrastive（含 T*1）。

        V3 把 step-type（op_type）合并进了 dependent-step 标注的同一次 LLM
        调用（文档第 1 条），所以 ``make_v2_annotator`` 一次 run 就同时产出
        ``dependencies`` 与 ``step_meta.op_type``（``op_type_source="llm"``，
        解析失败回退规则、标 ``rule_fallback``）。op_type 决定 phase 切分 /
        anchor 选择，已在 contrastive 之前完成。不调 mini-swe-agent。
        """
        logger.info("[v3 re-evolve] annotate (deps + LLM op_type) on %s", run_dir)
        make_v2_annotator(
            self.config_path, workers=self.workers, retry_failed=1,
            long_obs_threshold=self.long_obs_threshold,
        ).run(run_dir, task=task)
        logger.info("[v3 re-evolve] contrastive on %s", run_dir)
        make_v2_contrastive_builder(
            min_reduction_ratio=self.min_reduction_ratio,
            hotspot_min_occurrences=self.hotspot_min_occurrences,
            hotspot_min_total_chars=self.hotspot_min_total_chars,
        ).run(run_dir, task=task)

    def evolve(self, run_dir, scripts_dir, task: Optional[str] = None,
               output_dir: Optional[Path] = None,
               feedback: Optional[dict] = None) -> Path:
        """Stage 3：基于 contrastive 样本演化 scripts（就地更新 scripts_dir）。

        ``feedback``：v3 闭环传入的 T0 失败诊断（{"phase","error_reason","fix_plan",
        "scripts_to_fix","instruction_md_plan"}），作为 evolve prompt 的额外设计提示。
        None 时退化为纯 v2 evolve。
        """
        logger.info("[v3 re-evolve] evolve on %s -> %s (feedback=%s)",
                    run_dir, scripts_dir, "yes" if feedback else "none")
        return make_v2_evolver(
            scripts_dir, self.config_path, self.mini_swe_agent_dir,
            batch_size=self.batch_size, max_observation_chars=self.max_observation_chars,
            output_dir=str(output_dir) if output_dir else None,
            dry_run=self.dry_run, resume=self.resume,
            feedback=feedback,
        ).run(run_dir, task=task)

    def full(self, run_dir, scripts_dir, task: Optional[str] = None,
             output_dir: Optional[Path] = None) -> Path:
        """annotate + contrastive + evolve（用于 bootstrap 自举 S0）。"""
        self.annotate_and_contrastive(run_dir, task=task)
        return self.evolve(run_dir, scripts_dir, task=task, output_dir=output_dir)


# ============================================================================
# FixPromptBuilder — 把"错误原因 + 修改计划 + 回归 trajectory"组装成修脚本 prompt
# ============================================================================


class FixPromptBuilder(ChunkEvolvePromptBuilderV2):
    """复用 v2 prompt builder 的 scripts 列举 + 序列化，加一段修复指令。"""

    HEADER = [
        "# Repair task (V3 closed loop)",
        "",
        "The evolved helper scripts in this directory caused previously-passing tasks to "
        "FAIL after the scripts were installed. Your job is to diagnose and FIX the scripts "
        "(not to add new cost-saving scripts).",
        "",
        "## Working directory",
        "Your cwd is the absolute path shown below. Modify scripts ONLY inside this "
        "directory. Each script lives under `./<name>/` with `main.sh` + `intro.json`.",
    ]

    FOOTER = (
        "\n# Your task\n"
        "Based on the error reason and fix plan above, modify the identified scripts to fix "
        "the failure. Keep scripts GENERIC (no hardcoded paths from the samples). After each "
        "change, run it on the sample inputs and validate intro.json. "
        "Do NOT edit the prompt or sample files. "
        "Finish once the fix is saved and verified."
    )

    def build_fix(self, diagnosis: dict, regressed: List[Tuple[str, Path]],
                  t0: List[Tuple[str, Path]], scripts_dir: Path, cwd_name: str = ".") -> str:
        serializer = TrajectorySerializer(
            max_observation_chars=DEFAULT_JUDGE_MAX_OBS, max_action_chars=800,
        )
        parts: List[str] = [line.replace("{cwd}", cwd_name) for line in self.HEADER]
        parts.append(f"\nWorking directory absolute path: `{Path(scripts_dir).resolve()}`")

        parts.append("\n## Error reason")
        parts.append(diagnosis.get("error_reason", "(none)"))
        plan = diagnosis.get("fix_plan") or []
        if plan:
            parts.append("\n## Fix plan")
            for i, step in enumerate(plan, start=1):
                parts.append(f"{i}. {step}")
        to_fix = diagnosis.get("scripts_to_fix") or []
        if to_fix:
            parts.append("\n## Scripts to fix")
            parts.append(", ".join(f"`{s}`" for s in to_fix))

        parts.append("\n## Failing trajectories (with evolved scripts installed)")
        for cid, path in regressed:
            data = _read_json(path) or {}
            parts.append(f"\n### Case {cid}")
            parts.append(self._capped_trace(serializer, data, DEFAULT_DIAGNOSE_MAX_STEPS))

        parts.append("\n## Previously-passing trajectories (baseline, without scripts)")
        for cid, path in t0:
            data = _read_json(path) or {}
            parts.append(f"\n### Case {cid} (baseline)")
            parts.append(self._capped_trace(serializer, data, DEFAULT_DIAGNOSE_MAX_STEPS))

        parts += self._current_scripts_block(Path(scripts_dir))
        # 附 main.sh 详情（bug 多在脚本逻辑里）
        parts += self._scripts_detail_block(Path(scripts_dir))
        parts.append(self.FOOTER)
        return "\n".join(parts)

    @staticmethod
    def _capped_trace(serializer: TrajectorySerializer, traj: dict, max_steps: int) -> str:
        action_steps = [s for s in traj.get("steps", []) if is_action_step(s)]
        if len(action_steps) > max_steps:
            head, tail = action_steps[:3], action_steps[-(max_steps - 3):]
            note = (f"[first 3 + last {max_steps - 3} of {len(action_steps)} action steps; "
                    f"middle omitted]")
            return note + "\n" + serializer.serialize({"steps": head + tail})
        return serializer.serialize({"steps": action_steps})

    @staticmethod
    def _scripts_detail_block(scripts_dir: Path) -> List[str]:
        lines = ["\n# Current script sources (main.sh)"]
        if not scripts_dir.exists():
            lines.append("(none yet)")
            return lines
        for d in sorted(p for p in scripts_dir.iterdir() if p.is_dir()):
            main = d / "main.sh"
            lines.append(f"\n## {d.name}/main.sh")
            if not main.exists():
                lines.append("(missing)")
                continue
            try:
                text = main.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                lines.append(f"(failed to read: {exc})")
                continue
            if len(text) > DEFAULT_SCRIPT_DETAIL_CHARS:
                text = text[:DEFAULT_SCRIPT_DETAIL_CHARS].rstrip() + "\n...<truncated>"
            lines.append("```bash")
            lines.append(text)
            lines.append("```")
        return lines


# ============================================================================
# FailureDiagnoser — T0=Success ∧ T1=Fail 时诊断 + 修脚本
# ============================================================================


class FailureDiagnoser:
    """LLM 诊断回归根因 + 修改计划，再交给 mini-swe-agent 修 scripts。"""

    SYSTEM_PROMPT_T1 = (
        "You diagnose why evolved helper scripts caused a previously-passing task to now "
        "FAIL. You are given: failing trajectories (scripts installed), the passing "
        "baseline trajectories (no scripts) where available, and the current script "
        "sources. Identify the ROOT CAUSE — e.g. a script with wrong args/output, a "
        "harmful side effect, or an early-stop pressure from instruction.md. Be specific "
        "about WHICH script and WHAT behavior. Also propose instruction.md behavior-contract "
        "changes if a contract is causing the failure. Output ONLY JSON: "
        "{\"error_reason\": \"...\", \"fix_plan\": [\"step 1\", \"...\"], "
        "\"scripts_to_fix\": [\"name\", \"...\"], "
        "\"instruction_md_plan\": [\"contract 1\", \"...\"]}."
    )

    SYSTEM_PROMPT_T0 = (
        "You diagnose why a BASELINE coding agent (with NO helper scripts) failed some "
        "tasks, and propose cost-saving helper scripts + instruction.md behavior contracts "
        "that would help a future agent succeed on similar tasks with fewer steps. You are "
        "given the failing baseline trajectories and the current (possibly empty) set of "
        "helper scripts. Identify the failure mode — e.g. repeated failed commands, verbose "
        "exploration, missing batching — and propose concrete scripts/contracts that "
        "address it. Output ONLY JSON: "
        "{\"error_reason\": \"...\", \"fix_plan\": [\"step 1\", \"...\"], "
        "\"scripts_to_fix\": [\"proposed script name\", \"...\"], "
        "\"instruction_md_plan\": [\"contract 1\", \"...\"]}."
    )

    # 向后兼容：默认 T1（regression）prompt
    SYSTEM_PROMPT = SYSTEM_PROMPT_T1

    def __init__(self, config_path, mini_swe_agent_dir, work_dir: Path,
                 dry_run: bool = False):
        self.config_path = str(config_path)
        self.mini_swe_agent_dir = str(mini_swe_agent_dir)
        self.work_dir = Path(work_dir)
        self.dry_run = bool(dry_run)
        self.llm = LLM(self.config_path)
        self.serializer = TrajectorySerializer(
            max_observation_chars=DEFAULT_JUDGE_MAX_OBS, max_action_chars=800,
        )

    def diagnose_failures(self, failed_cases: List[str], run_dir,
                          prior_eval: Optional[dict] = None,
                          prior_dir=None, scripts_dir: Optional[Path] = None,
                          phase: str = "t1") -> dict:
        """LLM 诊断失败根因 + 修改计划（scripts + instruction.md），返回记录用 dict。

        - phase="t0"：基线（无脚本）失败 —— forward-looking，产 scripts/instruction 提议，
          不附 passing prior。
        - phase="t1"：装上 evolve 脚本后失败 —— 若 prior_eval 标该 case 先前 success，
          则附 passing prior trajectory 作 regression 对照。
        失败 case 按动作步数降序，cap 到 ``DEFAULT_DIAGNOSE_MAX_FAIL_CASES``（确定性，
          便于 resume 重现）。返回 dict 含 ``phase``，可直接作为 v2 evolve 的 feedback。
        """
        cases = self._select_fail_cases(failed_cases, run_dir, prior_eval, prior_dir)
        if not cases:
            return {}
        user = self._build_diagnose_prompt(cases, scripts_dir, phase)
        system = self._system_prompt(phase)
        try:
            raw = self.llm.query(system, "", user)
        except Exception as exc:
            logger.exception("diagnose LLM failed: %s", exc)
            return {"phase": phase, "error_reason": f"llm_error: {exc}",
                    "fix_plan": [], "scripts_to_fix": [], "instruction_md_plan": []}
        parsed = self._parse_diagnose_json(raw)
        if parsed is None:
            logger.warning("unparseable diagnose output: %r", (raw or "")[:300])
            return {"phase": phase, "error_reason": "diagnose_parse_error",
                    "fix_plan": [], "scripts_to_fix": [], "instruction_md_plan": [],
                    "raw": (raw or "")[:1000]}
        parsed["phase"] = phase
        return parsed

    # ---------- helpers ----------

    def _system_prompt(self, phase: str) -> str:
        return self.SYSTEM_PROMPT_T0 if phase == "t0" else self.SYSTEM_PROMPT_T1

    def _select_fail_cases(self, failed_cases, run_dir, prior_eval, prior_dir):
        """返回 ``[(cid, failing_traj, prior_traj_or_None), ...]``，按动作步数降序，
        cap 到 ``DEFAULT_DIAGNOSE_MAX_FAIL_CASES``。"""
        raw: List[Tuple[int, str, Path, Optional[Path]]] = []
        for cid in failed_cases:
            t1 = find_trajectory_for_case(run_dir, cid)
            if not t1:
                logger.warning("[v3 diagnose] no trajectory for failed case %s; skip", cid)
                continue
            prior: Optional[Path] = None
            if prior_eval and prior_dir and prior_eval.get(cid, {}).get("success") is True:
                prior = find_trajectory_for_case(prior_dir, cid)
            data = _read_json(t1) or {}
            n = sum(1 for s in data.get("steps", []) if is_action_step(s))
            raw.append((n, cid, t1, prior))
        raw.sort(key=lambda x: x[0], reverse=True)
        cap = DEFAULT_DIAGNOSE_MAX_FAIL_CASES
        if len(raw) > cap:
            logger.info("[v3 diagnose] %d failed cases; capping to %d (most steps first)",
                        len(raw), cap)
            raw = raw[:cap]
        return [(cid, t1, prior) for _, cid, t1, prior in raw]

    def apply_fix(self, scripts_dir: Path, diagnosis: dict,
                  regressed: List[Tuple[str, Path, Path]], fix_id: str) -> Path:
        """构造修复 prompt，跑 mini-swe-agent 就地修 scripts。"""
        scripts_dir = Path(scripts_dir)
        out_dir = self.work_dir / fix_id
        out_dir.mkdir(parents=True, exist_ok=True)
        prompt_path = out_dir / "fix.prompt.md"
        output_path = out_dir / "fix.traj.json"

        regressed_paths = [(cid, t1) for cid, t1, _ in regressed]
        t0_paths = [(cid, t0) for cid, _, t0 in regressed]
        prompt = FixPromptBuilder().build_fix(
            diagnosis, regressed_paths, t0_paths, scripts_dir, cwd_name=scripts_dir.name,
        )

        logger.info("[v3 fix] prompt -> %s, cwd=%s", prompt_path, scripts_dir)
        runner = MiniSweAgentRunnerV2(
            self.mini_swe_agent_dir, self.config_path, dry_run=self.dry_run,
        )
        runner.run(prompt, prompt_path, output_path, cwd=scripts_dir)
        return out_dir

    # ---------- helpers ----------

    def _build_diagnose_prompt(self, cases, scripts_dir: Optional[Path],
                              phase: str) -> str:
        """cases: [(cid, failing_traj, prior_traj_or_None), ...]"""
        if phase == "t0":
            parts: List[str] = ["# Failing baseline cases (NO evolve scripts installed)"]
            for cid, t1, _ in cases:
                data = _read_json(t1) or {}
                parts.append(f"\n## Case {cid} (FAILED, no scripts)")
                parts.append(FixPromptBuilder._capped_trace(
                    self.serializer, data, DEFAULT_DIAGNOSE_MAX_STEPS))
            if scripts_dir:
                parts += FixPromptBuilder._scripts_detail_block(scripts_dir)
            parts.append(
                "\nThese tasks failed under the BASELINE (no helper scripts). Propose "
                "helper scripts and instruction.md behavior contracts that would help a "
                "future agent succeed on similar tasks with fewer steps. "
                "`scripts_to_fix` are PROPOSED new/extended script names (not existing ones). "
                "Output ONLY the JSON object described in the system prompt.")
            return "\n".join(parts)

        parts = ["# Failing cases (with evolved scripts installed)"]
        for cid, t1, _ in cases:
            data = _read_json(t1) or {}
            parts.append(f"\n## Case {cid} (FAILED with scripts)")
            parts.append(FixPromptBuilder._capped_trace(
                self.serializer, data, DEFAULT_DIAGNOSE_MAX_STEPS))
        has_prior = any(p is not None for _, _, p in cases)
        if has_prior:
            parts.append("\n# Passing baseline (same cases, without scripts) — regression reference")
            for cid, _, prior in cases:
                if prior is None:
                    continue
                data = _read_json(prior) or {}
                parts.append(f"\n## Case {cid} (PASSED without scripts)")
                parts.append(FixPromptBuilder._capped_trace(
                    self.serializer, data, DEFAULT_DIAGNOSE_MAX_STEPS))
        if scripts_dir:
            parts += FixPromptBuilder._scripts_detail_block(scripts_dir)
        parts.append("\nOutput ONLY the JSON object described in the system prompt.")
        return "\n".join(parts)

    @staticmethod
    def _parse_diagnose_json(text: str) -> Optional[dict]:
        text = text or ""
        matches = re.findall(r"\{[\s\S]*\}", text)
        candidate = matches[-1] if matches else text
        try:
            obj = json.loads(candidate)
        except json.JSONDecodeError:
            return None
        if not isinstance(obj, dict):
            return None
        reason = str(obj.get("error_reason", ""))
        plan = obj.get("fix_plan")
        if not isinstance(plan, list):
            plan = [str(plan)] if plan else []
        else:
            plan = [str(x) for x in plan]
        to_fix = obj.get("scripts_to_fix")
        if not isinstance(to_fix, list):
            to_fix = [str(to_fix)] if to_fix else []
        else:
            to_fix = [str(x) for x in to_fix]
        instr = obj.get("instruction_md_plan")
        if not isinstance(instr, list):
            instr = [str(instr)] if instr else []
        else:
            instr = [str(x) for x in instr]
        return {"error_reason": reason, "fix_plan": plan, "scripts_to_fix": to_fix,
                "instruction_md_plan": instr}


# ============================================================================
# CostReviewer — 修改1 第 6 步：T0 vs T1 指标 + 失败诊断 → 判断是否达到降成本预期
#                   未达到则让 mini-swe-agent 改 instruction.md + scripts（一次性，不迭代）
# ============================================================================


class CostReviewer:
    """LLM 评审单轮 evolve 是否达到降成本预期；未达到则改 instruction.md + scripts。"""

    SYSTEM_PROMPT = (
        "You review whether a script-evolution cycle achieved its cost-reduction goal. "
        "You are given: T0 (baseline, no scripts) vs T1 (with evolved scripts) metrics "
        "(API cost in USD, step count, max observation chars, avg observation chars per "
        "step), the recorded error reasons + fix plans from any T0/T1 failures, and the "
        "current instruction.md + script sources. Decide whether the cost-reduction goal "
        "was achieved: cost should decrease vs T0 WITHOUT materially breaking correctness "
        "(T1 should not regress many cases T0 passed; null API cost means no cost data — "
        "judge by steps/tokens/obs instead). If NOT achieved, specify concrete changes to "
        "instruction.md (GENERAL behavior contracts only — do NOT list specific scripts "
        "there) and to scripts. Output ONLY JSON: "
        "{\"goal_achieved\": true|false, \"assessment\": \"one short paragraph\", "
        "\"instruction_md_changes\": [\"change 1\", \"...\"], "
        "\"scripts_changes\": [\"change 1\", \"...\"]}. "
        "If goal_achieved is true, leave both change lists empty."
    )

    def __init__(self, config_path, mini_swe_agent_dir, work_dir: Path,
                 dry_run: bool = False):
        self.config_path = str(config_path)
        self.mini_swe_agent_dir = str(mini_swe_agent_dir)
        self.work_dir = Path(work_dir)
        self.dry_run = bool(dry_run)
        self.llm = LLM(self.config_path)

    def review(self, compare: dict, t0_diagnosis: Optional[dict],
               t1_diagnosis: Optional[dict], scripts_dir: Path) -> dict:
        user = self._build_review_prompt(compare, t0_diagnosis, t1_diagnosis, scripts_dir)
        try:
            raw = self.llm.query(self.SYSTEM_PROMPT, "", user)
        except Exception as exc:
            logger.exception("cost review LLM failed: %s", exc)
            return {"goal_achieved": None, "assessment": f"llm_error: {exc}",
                    "instruction_md_changes": [], "scripts_changes": []}
        parsed = self._parse_review_json(raw)
        if parsed is None:
            logger.warning("unparseable review output: %r", (raw or "")[:300])
            return {"goal_achieved": None, "assessment": "review_parse_error",
                    "instruction_md_changes": [], "scripts_changes": [],
                    "raw": (raw or "")[:1000]}
        return parsed

    def apply(self, review: dict, scripts_dir: Path,
              fix_id: str = "review_fix") -> Optional[Path]:
        """若 goal_achieved 为 False，跑 mini-swe-agent 就地修 instruction.md + scripts。"""
        if review.get("goal_achieved") is True:
            logger.info("[v3 review] goal achieved; no fix applied")
            return None
        scripts_dir = Path(scripts_dir)
        out_dir = self.work_dir / fix_id
        out_dir.mkdir(parents=True, exist_ok=True)
        prompt_path = out_dir / "review_fix.prompt.md"
        output_path = out_dir / "review_fix.traj.json"
        prompt = self._build_fix_prompt(review, scripts_dir)
        logger.info("[v3 review] goal NOT achieved; applying fix -> %s, cwd=%s",
                    prompt_path, scripts_dir)
        runner = MiniSweAgentRunnerV2(
            self.mini_swe_agent_dir, self.config_path, dry_run=self.dry_run,
        )
        runner.run(prompt, prompt_path, output_path, cwd=scripts_dir)
        return out_dir

    # ---------- helpers ----------

    def _build_review_prompt(self, compare, t0_diagnosis, t1_diagnosis,
                             scripts_dir) -> str:
        parts: List[str] = ["# T0 vs T1 cost metrics (single evolve pass)"]
        parts.append(self._render_metrics(compare))
        if t0_diagnosis:
            parts.append("\n# T0 (baseline) failure diagnosis")
            parts.append(self._render_diagnosis(t0_diagnosis))
        if t1_diagnosis:
            parts.append("\n# T1 (with evolved scripts) failure diagnosis")
            parts.append(self._render_diagnosis(t1_diagnosis))
        parts.append("\n# Current instruction.md")
        parts.append(self._instruction_md_block(scripts_dir))
        parts += FixPromptBuilder._scripts_detail_block(Path(scripts_dir))
        parts.append("\nOutput ONLY the JSON object described in the system prompt.")
        return "\n".join(parts)

    @staticmethod
    def _render_metrics(compare: dict) -> str:
        def fmt(v):
            return "N/A" if v is None else v
        return (
            f"step cost (Σ n_action_steps): T0={compare.get('cost_T0')} → "
            f"T1={compare.get('cost_T1')} (Δ {fmt(compare.get('steps_gap_abs'))}, "
            f"{fmt(compare.get('steps_gap_rel'))})\n"
            f"mean action steps: T0={fmt(compare.get('mean_steps_T0'))} "
            f"T1={fmt(compare.get('mean_steps_T1'))}\n"
            f"total API cost (USD): T0={fmt(compare.get('total_api_cost_T0'))} "
            f"T1={fmt(compare.get('total_api_cost_T1'))} (null = no cost data)\n"
            f"mean API cost (USD): T0={fmt(compare.get('mean_api_cost_T0'))} "
            f"T1={fmt(compare.get('mean_api_cost_T1'))}\n"
            f"mean billed tokens: T0={fmt(compare.get('mean_billed_T0'))} "
            f"T1={fmt(compare.get('mean_billed_T1'))}\n"
            f"mean max obs chars: T0={fmt(compare.get('mean_max_obs_T0'))} "
            f"T1={fmt(compare.get('mean_max_obs_T1'))}\n"
            f"mean avg obs/step: T0={fmt(compare.get('mean_avg_obs_T0'))} "
            f"T1={fmt(compare.get('mean_avg_obs_T1'))}"
        )

    @staticmethod
    def _render_diagnosis(diag: dict) -> str:
        lines = [f"error_reason: {diag.get('error_reason', '(none)')}"]
        plan = diag.get("fix_plan") or []
        if plan:
            lines.append("fix_plan:")
            for i, s in enumerate(plan, start=1):
                lines.append(f"  {i}. {s}")
        instr = diag.get("instruction_md_plan") or []
        if instr:
            lines.append("instruction_md_plan:")
            for i, s in enumerate(instr, start=1):
                lines.append(f"  {i}. {s}")
        to_fix = diag.get("scripts_to_fix") or []
        if to_fix:
            lines.append(f"scripts_to_fix: {', '.join(to_fix)}")
        return "\n".join(lines)

    @staticmethod
    def _instruction_md_block(scripts_dir) -> str:
        p = Path(scripts_dir) / "instruction.md"
        if not p.exists():
            return "(no instruction.md yet)"
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return f"(failed to read: {exc})"
        if len(text) > DEFAULT_SCRIPT_DETAIL_CHARS:
            text = text[:DEFAULT_SCRIPT_DETAIL_CHARS].rstrip() + "\n...<truncated>"
        return "```markdown\n" + text + "\n```"

    def _build_fix_prompt(self, review: dict, scripts_dir) -> str:
        parts: List[str] = [
            "# Cost-review repair task (V3 closed loop)",
            "",
            "The evolve cycle did NOT achieve its cost-reduction goal. A review LLM has "
            "decided the following changes are needed. Apply them to instruction.md "
            "(HIGH-LEVEL behavior contracts ONLY — do NOT describe specific scripts there) "
            "and to the helper scripts in this directory. Keep scripts GENERIC (no "
            "hardcoded paths from samples).",
            "",
            f"Working directory absolute path: `{Path(scripts_dir).resolve()}`",
        ]
        instr = review.get("instruction_md_changes") or []
        if instr:
            parts.append("\n## instruction.md changes")
            for i, s in enumerate(instr, start=1):
                parts.append(f"{i}. {s}")
        scripts = review.get("scripts_changes") or []
        if scripts:
            parts.append("\n## scripts changes")
            for i, s in enumerate(scripts, start=1):
                parts.append(f"{i}. {s}")
        parts.append("\n## Current instruction.md")
        parts.append(self._instruction_md_block(scripts_dir))
        parts += FixPromptBuilder._scripts_detail_block(Path(scripts_dir))
        parts.append(
            "\n# Your task\n"
            "Apply the changes above to instruction.md and the scripts. After each change, "
            "validate intro.json (`python -c \"import json; json.load(open('<dir>/intro.json'))\"`) "
            "and run the script on sample inputs where relevant. Do NOT edit the prompt "
            "file. Finish once changes are saved and verified."
        )
        return "\n".join(parts)

    @staticmethod
    def _parse_review_json(text: str) -> Optional[dict]:
        text = text or ""
        matches = re.findall(r"\{[\s\S]*\}", text)
        candidate = matches[-1] if matches else text
        try:
            obj = json.loads(candidate)
        except json.JSONDecodeError:
            return None
        if not isinstance(obj, dict):
            return None
        ga = obj.get("goal_achieved")
        if isinstance(ga, str):
            ga = ga.strip().lower() in ("true", "yes", "1", "achieved")
        elif not isinstance(ga, bool):
            ga = None

        def coerce_list(key: str) -> List[str]:
            v = obj.get(key)
            if not isinstance(v, list):
                return [str(v)] if v else []
            return [str(x) for x in v]

        return {
            "goal_achieved": ga,
            "assessment": str(obj.get("assessment", ""))[:1000],
            "instruction_md_changes": coerce_list("instruction_md_changes"),
            "scripts_changes": coerce_list("scripts_changes"),
        }


# ============================================================================
# EvolveV3Cycle — 编排整个闭环
# ============================================================================


class EvolveV3Cycle:
    name = "evolve_v3_cycle"

    def __init__(self, benchmark: str, baseline_dir, scripts_dir, work_dir,
                 config_path, eval_cases: List[str], *,
                 mini_swe_agent_dir=DEFAULT_MINI_SWE_AGENT,
                 max_rounds: int = DEFAULT_MAX_ROUNDS,
                 min_rounds: int = DEFAULT_MIN_ROUNDS,
                 converge_abs: float = DEFAULT_CONVERGE_ABS,
                 converge_rel: float = DEFAULT_CONVERGE_REL,
                 workers: int = 8, batch_size: int = 4,
                 max_observation_chars: int = 1000,
                 swebench_task_path: Optional[str] = None,
                 n_tasks: int = 1000, n_concurrent: int = 8,
                 pricing: Optional[dict] = None,
                 judge_max_steps: int = DEFAULT_JUDGE_MAX_STEPS,
                 judge_max_obs: int = DEFAULT_JUDGE_MAX_OBS,
                 dry_run: bool = False, resume: bool = True):
        if benchmark not in BENCHMARKS:
            raise ValueError(f"unknown benchmark: {benchmark}")
        self.benchmark = benchmark
        self.baseline_dir = Path(baseline_dir).resolve() if baseline_dir else None
        self.scripts_dir = Path(scripts_dir).resolve()
        self.work_dir = Path(work_dir).resolve()
        self.config_path = str(config_path)
        self.eval_cases = list(eval_cases)
        self.mini_swe_agent_dir = str(mini_swe_agent_dir)
        self.max_rounds = int(max_rounds)
        self.min_rounds = int(min_rounds)
        self.converge_abs = float(converge_abs)
        self.converge_rel = float(converge_rel)
        self.workers = int(workers)
        self.batch_size = int(batch_size)
        self.max_observation_chars = int(max_observation_chars)
        self.swebench_task_path = swebench_task_path
        self.n_tasks = int(n_tasks)
        self.n_concurrent = int(n_concurrent)
        self.pricing = pricing
        self.judge_max_steps = int(judge_max_steps)
        self.judge_max_obs = int(judge_max_obs)
        self.dry_run = bool(dry_run)
        self.resume = bool(resume)

        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.scripts_dir.mkdir(parents=True, exist_ok=True)

        self.metrics = TrajectoryMetrics(pricing=pricing)
        self.evaluator = TrajectoryEvaluator(
            config_path, cache_path=self.work_dir / "llm_eval_cache.json",
            max_steps=judge_max_steps, max_obs=judge_max_obs,
        )
        self.runner = BenchmarkRunner(
            benchmark, config_path=config_path,
            swebench_task_path=swebench_task_path,
            n_tasks=n_tasks, n_concurrent=n_concurrent,
            taskdir_root=self.work_dir / "taskdirs",
        )
        self.re_evolver = V2ReEvolver(
            config_path, mini_swe_agent_dir, workers=workers, batch_size=batch_size,
            max_observation_chars=max_observation_chars, dry_run=dry_run, resume=resume,
        )
        self.diagnoser = FailureDiagnoser(
            config_path, mini_swe_agent_dir, self.work_dir, dry_run=dry_run,
        )
        self.cost_reviewer = CostReviewer(
            config_path, mini_swe_agent_dir, self.work_dir, dry_run=dry_run,
        )
        # T0 标注用 staging 目录（拷贝 trajectory.json，避免污染源 baseline）
        self.t0_staged = self.work_dir / "t0_staged"
        # produce_t0（无 baseline-dir 时）跑空脚本 baseline 用的占位路径——刻意不创建，
        # 让 BenchmarkRunner._build_env 见 .exists()=False 而设 EVOLVE_SCRIPTS_DIR=""
        self.empty_scripts_path = self.work_dir / "_empty_scripts_placeholder"
        # 修改1：单轮闭环，--max-rounds/--min-rounds/--converge-* 已是 deprecated no-op
        if (max_rounds != DEFAULT_MAX_ROUNDS or min_rounds != DEFAULT_MIN_ROUNDS
                or converge_abs != DEFAULT_CONVERGE_ABS or converge_rel != DEFAULT_CONVERGE_REL):
            logger.info(
                "[v3] single-pass flow (修改1): --max-rounds/--min-rounds/--converge-* "
                "are deprecated no-ops and ignored.")
        self.state: dict = self._load_state()

    # ---------- public ----------

    def run(self) -> dict:
        """修改1 单轮闭环：annotate T0 -> eval/diagnose T0 -> evolve(带 feedback)
        -> run T1 -> eval/diagnose T1 -> T0 vs T1 指标 -> LLM review（未达标则改
        instruction.md + scripts）。各 phase 支持 resume（完成后跳过，未完成则重跑）。
        """
        if self.dry_run:
            return self._dry_run_plan()

        # 1) T0 source：有 --baseline-dir 直接用；否则跑一轮空脚本产生 baseline
        if self.baseline_dir:
            t0_source = self.baseline_dir
            logger.info("[v3] T0 = baseline dir %s", t0_source)
        else:
            t0_source = self._phase_produce_t0()
        self.state["t0_source_dir"] = str(t0_source)
        self._save_state()

        # 2) 标注 T0（stage + annotate + contrastive）
        self._phase_annotate_t0(t0_source)

        # 3) eval T0 + 失败诊断（记录，供 evolve + review 用）
        t0_metrics, t0_eval, t0_diagnosis = self._phase_eval_t0(t0_source)
        self.state["t0"] = {"metrics": t0_metrics, "eval": t0_eval}
        self._save_state()

        # 4) evolve（喂入 T0 失败诊断作为 feedback）
        self._phase_evolve(t0_diagnosis)

        # 5) 装 scripts 跑 16 case -> T1
        t1_dir = self._phase_run_t1()

        # 6) eval T1 + 失败诊断（记录，供 review 用）
        t1_metrics, t1_eval, t1_diagnosis = self._phase_eval_t1(t1_dir, t0_eval, t0_source)

        # 7) T0 vs T1 指标 + LLM review；未达标则改 instruction.md + scripts
        self._phase_review(t0_metrics, t1_metrics, t0_diagnosis, t1_diagnosis)

        self._save_summary()
        return self.state

    def _dry_run_plan(self) -> dict:
        logger.info("[v3] DRY_RUN — single-pass plan (no agent/LLM calls)")
        plan = [
            ("produce_t0", "only if no --baseline-dir: run eval cases with EMPTY scripts"),
            ("annotate_t0", "stage T0 trajectory.json + v2 annotate+contrastive"),
            ("eval_t0", "metrics + LLM judge; diagnose failures (forward-looking) if any"),
            ("evolve", "v2 evolve on staged T0, feeding T0 diagnosis as feedback"),
            ("run_t1", "install evolved scripts, run eval cases -> T1"),
            ("eval_t1", "metrics + LLM judge; diagnose failures (regression) if any"),
            ("review", "T0 vs T1 metrics + LLM review; fix instruction.md+scripts if goal not met"),
        ]
        for name, desc in plan:
            logger.info("[v3 dry-run] phase %s: %s", name, desc)
        self.state["phases"] = {name: {"done": True, "dry_run": True}
                                for name, _ in plan}
        self._save_summary()
        return self.state

    # ---------- phases ----------

    def _phase_produce_t0(self) -> Path:
        name = "produce_t0"
        if self.resume and self._phase_done(name):
            rec = self.state["phases"][name]
            logger.info("[v3 produce_t0] done (resume); t0=%s", rec.get("t0_dir"))
            return Path(rec["t0_dir"])
        run_id = f"v3-t0-{self.benchmark}-{time.strftime('%m%d-%H%M%S')}"
        logger.info("[v3 produce_t0] no --baseline-dir; running %d eval cases with "
                    "EMPTY scripts -> baseline T0", len(self.eval_cases))
        t0_dir = self.runner.run(self.empty_scripts_path, self.eval_cases, run_id)
        self._mark_phase(name, t0_dir=str(t0_dir), run_id=run_id)
        return Path(t0_dir)

    def _phase_annotate_t0(self, t0_source) -> None:
        name = "annotate_t0"
        if self.resume and self._phase_done(name):
            logger.info("[v3 annotate_t0] done (resume)")
            return
        logger.info("[v3 annotate_t0] stage T0 from %s -> %s", t0_source, self.t0_staged)
        self._stage_t0(t0_source)
        logger.info("[v3 annotate_t0] v2 annotate (deps + op_type) + contrastive on %s",
                    self.t0_staged)
        self.re_evolver.annotate_and_contrastive(self.t0_staged)
        self._mark_phase(name)

    def _phase_eval_t0(self, t0_source) -> Tuple[dict, dict, dict]:
        name = "eval_t0"
        if self.resume and self._phase_done(name):
            rec = self.state["phases"][name]
            logger.info("[v3 eval_t0] done (resume)")
            return rec.get("metrics", {}), rec.get("eval", {}), rec.get("diagnosis") or {}
        logger.info("[v3 eval_t0] metrics + LLM-judge on %s", t0_source)
        t0_metrics = self.metrics.from_run(t0_source, self.eval_cases)
        t0_eval = self.evaluator.evaluate_run(t0_source, self.eval_cases)
        failed = [cid for cid, v in t0_eval.items() if v.get("success") is False]
        t0_diagnosis: dict = {}
        if failed:
            logger.info("[v3 eval_t0] %d/%d failed -> diagnose (T0, forward-looking)",
                        len(failed), len(t0_eval))
            t0_diagnosis = self.diagnoser.diagnose_failures(
                failed, t0_source, prior_eval=None, prior_dir=None,
                scripts_dir=self.scripts_dir, phase="t0")
        self._mark_phase(name, metrics=t0_metrics, eval=t0_eval,
                         diagnosis=t0_diagnosis, n_fail=len(failed))
        return t0_metrics, t0_eval, t0_diagnosis

    def _phase_evolve(self, t0_diagnosis: dict) -> None:
        name = "evolve"
        if self.resume and self._phase_done(name):
            logger.info("[v3 evolve] done (resume)")
            return
        feedback = t0_diagnosis or None
        logger.info("[v3 evolve] v2 evolve on staged T0 -> %s (feedback=%s)",
                    self.scripts_dir, "yes" if feedback else "none")
        self.re_evolver.evolve(
            self.t0_staged, self.scripts_dir,
            output_dir=self.work_dir / "evolve_logs", feedback=feedback,
        )
        self._write_used_case_ids()
        self._mark_phase(name)

    def _phase_run_t1(self) -> Path:
        name = "run_t1"
        if self.resume and self._phase_done(name):
            rec = self.state["phases"][name]
            logger.info("[v3 run_t1] done (resume); t1=%s", rec.get("t1_dir"))
            return Path(rec["t1_dir"])
        run_id = f"v3-t1-{self.benchmark}-{time.strftime('%m%d-%H%M%S')}"
        logger.info("[v3 run_t1] running %d eval cases WITH evolved scripts -> T1",
                    len(self.eval_cases))
        t1_dir = self.runner.run(self.scripts_dir, self.eval_cases, run_id)
        self._mark_phase(name, t1_dir=str(t1_dir), run_id=run_id)
        return Path(t1_dir)

    def _phase_eval_t1(self, t1_dir, t0_eval: dict, t0_source) -> Tuple[dict, dict, dict]:
        name = "eval_t1"
        if self.resume and self._phase_done(name):
            rec = self.state["phases"][name]
            logger.info("[v3 eval_t1] done (resume)")
            return rec.get("metrics", {}), rec.get("eval", {}), rec.get("diagnosis") or {}
        logger.info("[v3 eval_t1] metrics + LLM-judge on %s", t1_dir)
        t1_metrics = self.metrics.from_run(t1_dir, self.eval_cases)
        t1_eval = self.evaluator.evaluate_run(t1_dir, self.eval_cases)
        failed = [cid for cid, v in t1_eval.items() if v.get("success") is False]
        t1_diagnosis: dict = {}
        if failed:
            logger.info("[v3 eval_t1] %d/%d failed -> diagnose (T1, regression)",
                        len(failed), len(t1_eval))
            t1_diagnosis = self.diagnoser.diagnose_failures(
                failed, t1_dir, prior_eval=t0_eval, prior_dir=t0_source,
                scripts_dir=self.scripts_dir, phase="t1")
        self._mark_phase(name, metrics=t1_metrics, eval=t1_eval,
                         diagnosis=t1_diagnosis, n_fail=len(failed))
        return t1_metrics, t1_eval, t1_diagnosis

    def _phase_review(self, t0_metrics: dict, t1_metrics: dict,
                      t0_diagnosis: dict, t1_diagnosis: dict) -> dict:
        name = "review"
        if self.resume and self._phase_done(name):
            rec = self.state["phases"][name]
            logger.info("[v3 review] done (resume); goal_achieved=%s",
                        (rec.get("review") or {}).get("goal_achieved"))
            return rec.get("review") or {}
        cmp = self.metrics.compare(t0_metrics, t1_metrics)
        (self.work_dir / "compare.json").write_text(
            json.dumps(cmp, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        logger.info("[v3 review] T0 vs T1: %s",
                    json.dumps({k: cmp.get(k) for k in
                                ("cost_T0", "cost_T1", "steps_gap_abs",
                                 "total_api_cost_T0", "total_api_cost_T1")},
                               ensure_ascii=False))
        review = self.cost_reviewer.review(
            cmp, t0_diagnosis, t1_diagnosis, self.scripts_dir)
        (self.work_dir / "review.json").write_text(
            json.dumps(review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if review.get("goal_achieved") is False:
            self.cost_reviewer.apply(review, self.scripts_dir, fix_id="review_fix")
        self._mark_phase(name, compare=cmp, review=review)
        return review

    # ---------- staging + phase state ----------

    def _stage_t0(self, t0_source) -> None:
        """拷贝每个 eval case 的 trajectory.json 到 t0_staged/<case>/agent/。

        用拷贝不用软链：v2 annotate 会原地写回 ``dependencies``，会污染源 baseline。
        只拷 trajectory.json（contrastive/evolve 只读它）；``result.json`` 不拷——所以
        T0 的真实 API cost 必须从源目录读（见 ``_phase_eval_t0``）。
        """
        if self.t0_staged.exists():
            _safe_rmtree(self.t0_staged)
        self.t0_staged.mkdir(parents=True, exist_ok=True)
        n = 0
        for cid in self.eval_cases:
            traj = find_trajectory_for_case(t0_source, cid)
            if not traj:
                logger.warning("[v3 stage] no trajectory for case %s in %s; skip",
                               cid, t0_source)
                continue
            dst = self.t0_staged / cid / "agent"
            dst.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(str(traj), str(dst / "trajectory.json"))
            n += 1
        logger.info("[v3 stage] staged %d/%d trajectories -> %s",
                    n, len(self.eval_cases), self.t0_staged)

    def _phase_done(self, name: str) -> bool:
        return bool(self.state.get("phases", {}).get(name, {}).get("done"))

    def _mark_phase(self, name: str, **data) -> None:
        """phase 完成后才写——mid-phase crash 不标 done，resume 会重跑（幂等）。"""
        self.state.setdefault("phases", {})[name] = {"done": True, **data}
        self._save_state()

    def _write_used_case_ids(self) -> None:
        """把本次 evolve 用到的 case id 写入 scripts_dir/evolve_used_case_id.txt。

        与 ``evolve_in_individual_bench.sh`` 的 select 阶段同格式（每行一个
        case id）。两个作用：
          (a) 后续 v3 run 不带 --eval-cases 也能自动恢复 case 集
              （``_resolve_eval_cases`` 会优先看 --scripts-dir 下的此文件）；
          (b) 兼容下游脚本 ``evolve_skip_exclude_args`` 的自动读取约定。
        V3 验证阶段强制 ``EVOLVE_SKIP_FILE=""`` 不跳过，故文件存在不影响 16
        case 验证；仅作记录与自动恢复。
        """
        if not self.eval_cases:
            return
        self.scripts_dir.mkdir(parents=True, exist_ok=True)
        out = self.scripts_dir / "evolve_used_case_id.txt"
        out.write_text("\n".join(self.eval_cases) + "\n", encoding="utf-8")
        logger.info("[v3] wrote %d evolve case id(s) -> %s", len(self.eval_cases), out)

    # ---------- state ----------

    def _state_path(self) -> Path:
        return self.work_dir / "cycle_state.json"

    def _load_state(self) -> dict:
        """单轮闭环 state：``{"benchmark","phases":{...},"t0","t0_source_dir",...}``。

        兼容旧 round-based state：若读到 ``rounds`` 字段（旧迭代版），丢弃之，从空
        phases 重新开始单轮流程（旧迭代结果对单轮语义无意义）。
        """
        if not self.resume:
            return {"benchmark": self.benchmark, "phases": {}}
        data = _read_json(self._state_path())
        if isinstance(data, dict):
            data.pop("rounds", None)          # 旧迭代版 state，丢弃
            data.setdefault("phases", {})
            data.setdefault("benchmark", self.benchmark)
            return data
        return {"benchmark": self.benchmark, "phases": {}}

    def _save_state(self) -> None:
        self.state["benchmark"] = self.benchmark
        self.state["scripts_dir"] = str(self.scripts_dir)
        try:
            self._state_path().write_text(
                json.dumps(self.state, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8")
        except OSError as exc:
            logger.warning("failed to save cycle state: %s", exc)

    def _save_summary(self) -> None:
        """单轮闭环结束：状态恒为 completed（无 stop 决策）。"""
        self.state["status"] = "completed"
        self.state["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        self._save_state()
        summary_path = self.work_dir / "cycle_summary.json"
        summary_path.write_text(
            json.dumps(self.state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        logger.info("[v3] cycle summary -> %s", summary_path)


# ============================================================================
# Factory + CLI
# ============================================================================


def _resolve_eval_cases(benchmark: str, eval_cases: Optional[str],
                        eval_cases_file: Optional[str],
                        scripts_dir: Optional[str] = None) -> List[str]:
    if eval_cases:
        cases = [c.strip() for c in eval_cases.split(",") if c.strip()]
        if cases:
            return cases
    if eval_cases_file:
        p = Path(eval_cases_file)
        if not p.exists():
            raise FileNotFoundError(f"--eval-cases-file not found: {p}")
        return _read_case_list(p)
    # 默认查找顺序：
    #   1. --scripts-dir/evolve_used_case_id.txt    （V3 evolve 自动写入，最优先）
    #   2. .evolve_scripts_<bench>/evolve_used_case_id.txt
    #   3. .evolve_scripts/evolve_used_case_id.txt
    cands: List[Path] = []
    if scripts_dir:
        cands.append(Path(scripts_dir) / "evolve_used_case_id.txt")
    cands.append(ROOT / f".evolve_scripts_{benchmark}" / "evolve_used_case_id.txt")
    cands.append(ROOT / ".evolve_scripts" / "evolve_used_case_id.txt")
    for cand in cands:
        if cand.exists():
            logger.info("[v3] using eval-cases file: %s", cand)
            return _read_case_list(cand)
    raise FileNotFoundError(
        f"no eval cases: pass --eval-cases or --eval-cases-file, or create "
        f"evolve_used_case_id.txt under --scripts-dir "
        f"(V3 writes it automatically once it has evolved scripts)")


def _read_case_list(path: Path) -> List[str]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return list(dict.fromkeys(out))


def make_v3_cycle(args) -> EvolveV3Cycle:
    eval_cases = _resolve_eval_cases(args.benchmark, args.eval_cases, args.eval_cases_file,
                                     args.scripts_dir)
    pricing = None
    if getattr(args, "pricing", None):
        try:
            pricing = json.loads(args.pricing)
        except json.JSONDecodeError as exc:
            logger.warning("--pricing ignored (invalid JSON): %s", exc)
    # baseline 可选：不传 --baseline-dir 时为 None，由 v3 闭环第 1 轮（空脚本）在
    # eval-cases 上跑出 baseline trajectory 作 evolve 来源（“不传入结果目录”场景）。
    baseline = args.baseline_dir
    return EvolveV3Cycle(
        benchmark=args.benchmark,
        baseline_dir=baseline,
        scripts_dir=args.scripts_dir,
        work_dir=args.work_dir,
        config_path=args.config,
        eval_cases=eval_cases,
        mini_swe_agent_dir=args.mini_swe_agent_dir,
        max_rounds=args.max_rounds,
        min_rounds=args.min_rounds,
        converge_abs=args.converge_abs,
        converge_rel=args.converge_rel,
        workers=args.workers,
        batch_size=args.batch_size,
        max_observation_chars=args.max_observation_chars,
        swebench_task_path=getattr(args, "swebench_task_path", None),
        n_tasks=args.n_tasks,
        n_concurrent=args.n_concurrent,
        pricing=pricing,
        judge_max_steps=args.judge_max_steps,
        judge_max_obs=args.judge_max_obs,
        dry_run=args.dry_run,
        resume=not args.no_resume,
    )


def _add_benchmark(parser):
    parser.add_argument("--benchmark", required=True,
                        choices=list(BENCHMARKS),
                        help="benchmark to evolve against")
    parser.add_argument("--eval-cases", help="comma-separated case ids to validate on")
    parser.add_argument("--eval-cases-file",
                        help="file of case ids (one per line, # comments); "
                             "default .evolve_scripts_<bench>/evolve_used_case_id.txt")
    parser.add_argument("--swebench-task-path",
                        help="source swebench task dir (for --benchmark swebench temp task-dir)")


def _add_v3_run(parser):
    _add_benchmark(parser)
    _add_config(parser)
    parser.add_argument("--baseline-dir", help="T0 (no-scripts) run dir; default for deep-swe")
    parser.add_argument("--scripts-dir", default=str(DEFAULT_SCRIPTS_DIR))
    parser.add_argument("--work-dir", default=str(DEFAULT_WORK_DIR))
    parser.add_argument("--mini-swe-agent-dir", default=str(DEFAULT_MINI_SWE_AGENT))
    parser.add_argument("--max-rounds", type=int, default=DEFAULT_MAX_ROUNDS)
    parser.add_argument("--min-rounds", type=int, default=DEFAULT_MIN_ROUNDS,
                        help="don't stop before this round (avoid premature stop)")
    parser.add_argument("--converge-abs", type=float, default=DEFAULT_CONVERGE_ABS,
                        help="|C(T1)-C(T*1)| or |C(T0)-C(T1)| step gap <= this -> converged")
    parser.add_argument("--converge-rel", type=float, default=DEFAULT_CONVERGE_REL,
                        help="relative gap <= this -> converged")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--max-observation-chars", type=int, default=1000)
    parser.add_argument("--n-tasks", type=int, default=1000)
    parser.add_argument("--n-concurrent", type=int, default=8)
    parser.add_argument("--judge-max-steps", type=int, default=DEFAULT_JUDGE_MAX_STEPS)
    parser.add_argument("--judge-max-obs", type=int, default=DEFAULT_JUDGE_MAX_OBS)
    parser.add_argument("--pricing", help='JSON {"input":x,"cache":y,"output":z} USD per 1M tokens')
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-resume", action="store_true")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Evolve v3 closed loop: evolve -> install -> validate -> feedback.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="run the full V3 closed loop")
    _add_v3_run(p_run)
    p_run.add_argument("--log-file")

    p_eval = sub.add_parser("evaluate", help="LLM-judge a run dir (Evaluate(T))")
    _add_common(p_eval)
    _add_config(p_eval)
    _add_benchmark(p_eval)
    p_eval.add_argument("--work-dir", default=str(DEFAULT_WORK_DIR))
    p_eval.add_argument("--scripts-dir", default=str(DEFAULT_SCRIPTS_DIR),
                        help="look here for evolve_used_case_id.txt when no --eval-cases given")
    p_eval.add_argument("--judge-max-steps", type=int, default=DEFAULT_JUDGE_MAX_STEPS)
    p_eval.add_argument("--judge-max-obs", type=int, default=DEFAULT_JUDGE_MAX_OBS)

    p_metrics = sub.add_parser("metrics", help="compute T0/T1 metrics for a run dir")
    _add_common(p_metrics)
    _add_benchmark(p_metrics)
    p_metrics.add_argument("--scripts-dir", default=str(DEFAULT_SCRIPTS_DIR),
                           help="look here for evolve_used_case_id.txt when no --eval-cases given")
    p_metrics.add_argument("--pricing")

    p_reevolve = sub.add_parser("re-evolve", help="run V2 evolve on a T1 run dir -> scripts")
    _add_common(p_reevolve)
    _add_config(p_reevolve)
    _add_evolve(p_reevolve)
    p_reevolve.add_argument("--workers", type=int, default=8)

    p_diag = sub.add_parser("diagnose", help="diagnose regressions + apply fix")
    _add_common(p_diag)
    _add_config(p_diag)
    _add_benchmark(p_diag)
    p_diag.add_argument("--baseline-dir")
    p_diag.add_argument("--scripts-dir", default=str(DEFAULT_SCRIPTS_DIR))
    p_diag.add_argument("--mini-swe-agent-dir", default=str(DEFAULT_MINI_SWE_AGENT))
    p_diag.add_argument("--work-dir", default=str(DEFAULT_WORK_DIR))
    p_diag.add_argument("--dry-run", action="store_true")

    args = parser.parse_args(argv)
    _setup_logging(getattr(args, "log_file", None))

    if args.cmd == "run":
        cycle = make_v3_cycle(args)
        cycle.run()

    elif args.cmd == "evaluate":
        cases = _resolve_eval_cases(args.benchmark, args.eval_cases, args.eval_cases_file,
                                      getattr(args, "scripts_dir", None))
        ev = TrajectoryEvaluator(
            args.config, cache_path=Path(args.work_dir) / "llm_eval_cache.json",
            max_steps=args.judge_max_steps, max_obs=args.judge_max_obs,
        )
        result = ev.evaluate_run(args.result_dir, cases)
        out = Path(args.result_dir).parent / f"v3_eval_{Path(args.result_dir).name}.json"
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        logger.info("eval -> %s", out)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif args.cmd == "metrics":
        cases = _resolve_eval_cases(args.benchmark, args.eval_cases, args.eval_cases_file,
                                      getattr(args, "scripts_dir", None))
        pricing = None
        if getattr(args, "pricing", None):
            try:
                pricing = json.loads(args.pricing)
            except json.JSONDecodeError:
                pass
        m = TrajectoryMetrics(pricing=pricing)
        result = m.from_run(args.result_dir, cases)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif args.cmd == "re-evolve":
        re = V2ReEvolver(
            args.config, args.mini_swe_agent_dir, workers=args.workers,
            batch_size=args.batch_size, max_observation_chars=args.max_observation_chars,
            dry_run=args.dry_run, resume=not args.no_resume,
        )
        re.full(args.result_dir, args.scripts_dir, task=getattr(args, "task", None),
                output_dir=Path(args.output_dir) if args.output_dir else None)

    elif args.cmd == "diagnose":
        cases = _resolve_eval_cases(args.benchmark, args.eval_cases, args.eval_cases_file,
                                      getattr(args, "scripts_dir", None))
        t1_eval_path = Path(args.result_dir).parent / f"v3_eval_{Path(args.result_dir).name}.json"
        t1_eval = _read_json(t1_eval_path) or {}
        if not t1_eval:
            logger.info("no cached eval at %s; judging now", t1_eval_path)
            ev = TrajectoryEvaluator(args.config, cache_path=Path(args.work_dir) / "llm_eval_cache.json")
            t1_eval = ev.evaluate_run(args.result_dir, cases)
        baseline = args.baseline_dir
        t0_eval = {}
        if baseline:
            ev = TrajectoryEvaluator(args.config, cache_path=Path(args.work_dir) / "llm_eval_cache.json")
            t0_eval = ev.evaluate_run(baseline, cases)
        failed_cases = [cid for cid, v in t1_eval.items() if v.get("success") is False]
        if not failed_cases:
            logger.info("no failed cases in T1; nothing to diagnose")
            return
        diag = FailureDiagnoser(args.config, args.mini_swe_agent_dir, Path(args.work_dir),
                                dry_run=args.dry_run)
        diagnosis = diag.diagnose_failures(
            failed_cases, args.result_dir,
            prior_eval=t0_eval or None, prior_dir=baseline,
            scripts_dir=Path(args.scripts_dir), phase="t1")
        logger.info("diagnosis:\n%s", json.dumps(diagnosis, ensure_ascii=False, indent=2))
        # apply_fix 仅对回归 case（T0 pass -> T1 fail，且 T0 trajectory 可得）
        regressed = []
        for cid in failed_cases:
            if baseline and t0_eval.get(cid, {}).get("success") is True:
                t1p = find_trajectory_for_case(args.result_dir, cid)
                t0p = find_trajectory_for_case(baseline, cid)
                if t1p and t0p:
                    regressed.append((cid, t1p, t0p))
        if regressed:
            diag.apply_fix(Path(args.scripts_dir), diagnosis, regressed, fix_id="diagnose_fix")
        else:
            logger.info("no regression cases with T0 traj; diagnosis recorded only")


if __name__ == "__main__":
    main()
