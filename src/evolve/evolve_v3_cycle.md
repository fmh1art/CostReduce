# Evolve v3 — 闭环 contrastive（paired_trajectory）

## 1. 出发点：v2 留下的缺口

v2 修了 v1-chunk 的 4 个根因（brief_observations bug、cwd、行为契约、cost_hotspot 真实信号），但
有一个根本缺口它没补：

> 进化 agent 看到的 contrastive 永远是「自己脑补的 positive」。graph contrastive 的 positive 是
> 依赖图反推的最小子图（不保证能完成任务）；observation contrastive 的 positive 是 brief 观测（不
> 保证能复现原始结果）；v2 早期尝试的 `tool_replacement` 干脆伪造了一个假 observation。

这个缺口在 v2 里没法补：进化 agent 还没产出 evolved scripts 之前，根本不存在「用 evolved tool 跑完
的 trajectory」可以做 positive。这是 chicken-and-egg。

v3 的核心思路是 **闭环**：先用 v2 产出一版 scripts，拿这版 scripts 跑一遍下游，拿到真实的「用了
evolved tool 的 trajectory」；再在 **同一批 task** 上把 baseline trajectory 和 v2 trajectory
配对，得到真实 contrastive，喂给下一轮 evolve。这是把 v2 的 fabricated positive 换成真实 positive
的唯一办法。

```
┌────────────────┐         ┌──────────────────┐
│ baseline traj  │         │ v2 traj (用 v2   │
│ (no tools)     │ ←配对→ │  evolved tools)  │
└────────────────┘         └──────────────────┘
        │                           │
        └──────────┬────────────────┘
                   ▼
        paired_trajectory contrastive
        (negative=baseline, positive=v2)
                   │
                   ▼
        下一轮 evolve agent
        （看到真实省成本数据）
```

## 2. 与 v2 的衔接

v3 不重写 v2，而是 **继承 v2 已有的修复**：

| v2 已有                              | v3 复用方式                                   |
| ----------------------------------- | ------------------------------------------- |
| `ChunkTrajectoryAnnotatorV2` (修 `is_annotated`) | 直接继承，v3 不需要再改                      |
| `MiniSweAgentRunnerV2` (修 cwd)      | 直接继承                                     |
| `ChunkEvolvePromptBuilderV2` (行为契约 + 绝对路径) | 子类化，新增 `paired_trajectory` 段落         |
| `ChunkScriptEvolverV2` (`stats_provider`) | 子类化，stats 来源改成「读上一轮 v2 downstream trial」 |
| `cost_hotspot` 信号                  | 保留，继续作为辅助信号（与 paired_trajectory 互补） |

v3 只新增一个 stage 2 builder 类（`PairedContrastiveSampleBuilderV3`）和一个 stage 3 prompt
builder 类（`PairedEvolvePromptBuilderV3`），其他全部继承 v2。

## 3. 数据流

### 3.1 输入

v3 需要两个 trajectory 集合，都按 `<task_id>/agent/trajectory.json` 排列：

```
results/deep-swe/deepseek-flash-without-evolve-tools/<task_id>/agent/trajectory.json
results/deep-swe/deepseek-flash-with-evolve-tools-v2-chunk/<task_id>/agent/trajectory.json
```

- **baseline trajectory**：用 raw bash 命令跑完的（已有的 `deepseek-flash-without-evolve-tools/`）
- **v2 trajectory**：用 v2 evolved tools 跑完的（v2 跑完后产生的
  `deepseek-flash-with-evolve-tools-v2-chunk/`）

两个集合的 task_id **必须重叠**。下游跑 v2 trial 时应使用与 baseline 完全相同的 task 子集（即
`swe-atlas` 同一份 case 列表），否则无法配对。

### 3.2 配对策略

`PairedContrastiveSampleBuilderV3` 按 task_id 配对，过滤规则如下：

1. **task_id 必须匹配**：取两个集合的 task_id 交集。
2. **v2 trajectory 必须真的用了 evolved tool**：扫 v2 trajectory 的所有 bash command，至少有一条
   调用 `/app/.preinstalled_scripts/<name>/main.sh`。完全没用 evolved tool 的 task 不产生样本
   （v2 跑了等于没跑，没有 contrastive 价值）。
3. **必须观察到成本节省**：满足以下任一条件才输出样本：
   - v2 的 action step 数 < baseline 的 action step 数（步数减少）
   - v2 的总 observation 字符数 < baseline 的总 observation 字符数（观测减少）
   - v2 成功且 baseline 失败（这一项需要 trial-level 的 resolve 字段，可后续扩展；最小可用版本可
     先不要求）
   - 若 v2 比 baseline 更贵（步数更多、observation 更大），**也输出样本**，但标记
     `improvement_direction=regression`。进化 agent 看到回归样本会知道哪些 script 反而拖累成
     本，从而修剪它们。

### 3.3 输出格式

每个 task 输出一个 `paired_trajectory_<task_id>.json`：

```json
{
  "type": "paired_trajectory",
  "task_id": "<task_id>",
  "baseline_trajectory_path": "results/deep-swe/deepseek-flash-without-evolve-tools/<task_id>/agent/trajectory.json",
  "v2_trajectory_path": "results/deep-swe/deepseek-flash-with-evolve-tools-v2-chunk/<task_id>/agent/trajectory.json",
  "improvement_direction": "improvement" | "regression" | "neutral",
  "baseline_stats": {
    "action_steps": 47,
    "total_observation_chars": 285000,
    "evolved_tool_calls": 0,
    "raw_command_breakdown": {"sed": 67, "grep": 65, "cat": 12, ...}
  },
  "v2_stats": {
    "action_steps": 31,
    "total_observation_chars": 192000,
    "evolved_tool_calls": 14,
    "evolved_tool_breakdown": {"read-lines": 8, "search-src": 4, "run-test": 2}
  },
  "saved": {
    "action_steps": 16,
    "action_steps_ratio": 0.34,
    "observation_chars": 93000,
    "observation_chars_ratio": 0.326
  },
  "negative_sample": { "steps": [...] },
  "positive_sample": { "steps": [...] }
}
```

`negative_sample` 和 `positive_sample` 字段保留与 v1/v2 contrastive 一致的 schema，方便复用
`TrajectorySerializer`。但 **完整 trajectory 可能很长**，序列化时会触发
`max_observation_chars` 截断 — 这是预期行为，因为 evolve agent 只需要看典型 step 的对比，不需要
看完整重放。

### 3.4 stats_provider 闭环

v2 已经留了 `stats_provider` 回调（`ChunkScriptEvolverV2.run` 每个 batch 前调用）。v3 在此基础
上把 stats_provider 的数据源换成「读上一轮 v2 downstream trial 的 case-level 成本统计」：

```python
def v3_stats_provider():
    """读 results/deep-swe/deepseek-flash-with-evolve-tools-v2-chunk/ 下所有 case 的
    cost.json / report.json，聚合成 {script_name: {calls, failures, saved_yuan, notes}}。"""
    ...
```

evolve agent 看到的 stats 是真实的下游调用情况，而不是空 stats（v2 默认）。这实现了
report.md 第 10 章讲的闭环反馈。

## 4. Stage 设计

### 4.1 Stage 2: `PairedContrastiveSampleBuilderV3`

继承 `ChunkContrastiveSampleBuilderV2`，保留 graph + observation + cost_hotspot 三类样本，新增
`paired_trajectory` 类型：

```python
class PairedContrastiveSampleBuilderV3(ChunkContrastiveSampleBuilderV2):
    name = "contrastive_paired_v3"

    def __init__(
        self,
        baseline_result_dir: Path,
        v2_result_dir: Path,
        min_reduction_ratio: float = MIN_REDUCTION_RATIO_DEFAULT,
        hotspot_min_occurrences: int = HOTSPOT_MIN_OCCURRENCES_DEFAULT,
        hotspot_min_total_chars: int = HOTSPOT_MIN_TOTAL_CHARS_DEFAULT,
        paired_min_step_ratio: float = 0.1,
    ):
        super().__init__(
            min_reduction_ratio=min_reduction_ratio,
            hotspot_min_occurrences=hotspot_min_occurrences,
            hotspot_min_total_chars=hotspot_min_total_chars,
        )
        self.baseline_result_dir = Path(baseline_result_dir).resolve()
        self.v2_result_dir = Path(v2_result_dir).resolve()
        self.paired_min_step_ratio = paired_min_step_ratio

    def run(self, result_dir, task=None) -> Path:
        # 1. 先跑父类的 build_file：对 v2_result_dir 下每个 trajectory 生成 graph + obs + hotspot
        # 2. 再扫 baseline vs v2 配对，生成 paired_trajectory 样本
        ...
```

**注意**：v3 的 `result_dir` 是 v2_result_dir（因为 annotate 和 graph/obs/hotspot 都基于 v2
trajectory 生成），baseline_result_dir 只用于配对。

CLI 新增参数：

```
--baseline-result-dir <path>   # baseline trajectory 所在的 result_dir
--v2-result-dir <path>         # v2 evolved-tools trial 的 result_dir（也作为 annotate 输入）
```

### 4.2 Stage 3: `PairedEvolvePromptBuilderV3`

继承 `ChunkEvolvePromptBuilderV2`，在 HEADER 里把第 4 类样本 `paired_trajectory` 加上，并在
`build` 里新增 `paired_trajectory` 渲染段：

```python
HEADER = [
    "Here are contrastive samples and cost signals from trajectory chunks. Four types:",
    "1. **Graph contrastive**: ...",
    "2. **Observation contrastive**: ...",
    "3. **Cost hotspot**: ...",
    "4. **Paired trajectory** (v3 NEW): real baseline-vs-v2 contrastive on the SAME task. "
    "negative is the baseline trajectory (no evolved tools), positive is the v2 trajectory "
    "(using v2 evolved tools). Saved metrics (steps, observation chars) are REAL, measured "
    "from actual downstream runs — no fabrication.",
    ...
]
```

paired_trajectory 段渲染：

```
# Paired Trajectory Samples (N shown)

Each sample is a real baseline-vs-v2 comparison on the SAME task. The saved metrics are
measured from actual downstream runs, not estimated. Use these to:
  (a) Reinforce scripts that consistently appear in `improvement` samples.
  (b) Fix or remove scripts that appear in `regression` samples.
  (c) Skip scripts that appear in `neutral` samples (no measurable effect).

### Paired 1: task_id=boa-hierarchical-evaluation-canc__CW3qNfu, direction=improvement
- baseline: 47 action steps, 285000 obs chars, 0 evolved tool calls
- v2:       31 action steps, 192000 obs chars, 14 evolved tool calls (read-lines x8, search-src x4, run-test x2)
- saved:    16 steps (34%), 93000 obs chars (33%)

  Baseline trajectory (excerpt):
    <serialized baseline excerpt>

  v2 trajectory (excerpt):
    <serialized v2 excerpt>
```

### 4.3 Stage 3: `PairedScriptEvolverV3`

继承 `ChunkScriptEvolverV2`，只覆盖 `make_v3_evolver` 工厂，传入 `PairedEvolvePromptBuilderV3`
和 v3 的 `stats_provider`。不需要重写 `run` 方法 — v2 已经在每个 batch 前调用
`_maybe_refresh_stats`，v3 只需把 stats_provider 实现成「读 v2 trial 日志」。

## 5. 实现路线（最小可用版本 → 完整版）

### 5.1 MVP（最小可用版本，第一周）

目标：跑通 baseline → v2 → v3 闭环，证明 paired_trajectory contrastive 能产出。

1. **跑 v2**：`python -m src.evolve.evolve_v2_chunk run results/without_scripts_total_cases ...`
2. **跑 v2 downstream trial**：`EVOLVE_SCRIPTS_DIR=.evolve_scripts_v2_chunk RUN_ID=deepseek-flash-with-evolve-tools-v2-chunk bash scripts/run_deep_swe.sh`
3. **实现 `PairedContrastiveSampleBuilderV3`**：扫两个 result_dir，按 task_id 配对，输出
   `paired_trajectory_<task_id>.json`。
4. **实现 `PairedEvolvePromptBuilderV3`**：渲染 paired_trajectory 段。
5. **跑 v3 evolve**：用 v2 产出的 scripts 作为起点（`--scripts-dir .evolve_scripts_v2_chunk`），
   v3 在此基础上迭代。
6. **跑 v3 downstream trial**：`EVOLVE_SCRIPTS_DIR=.evolve_scripts_v3_cycle RUN_ID=deepseek-flash-with-evolve-tools-v3-cycle bash scripts/run_deep_swe.sh`，对比 v2 和 v3 的成本。

MVP 不要求 stats_provider 闭环 — v3 默认 stats 为空，等 MVP 跑通再加。

### 5.2 完整版（第二周）

1. **stats_provider 闭环**：实现 `v3_stats_provider`，读 v2 trial 日志聚合 script 调用次数、失败
   率、节省成本。`ChunkScriptEvolverV3` 用它作为默认 stats_provider。
2. **regression 检测**：在 paired_trajectory 样本里检测 `improvement_direction=regression` 的
   task，evolve agent 看到这些样本应主动删除/修改对应的 evolved tool。
3. **task 子集筛选**：v3 evolve 只在「v2 用了 evolved tool 且有节省或回归」的 task 子集上跑，
   避免在没用 evolved tool 的 task 上浪费 evolve 成本。
4. **多轮迭代**：v3 跑完后可以再跑一轮 downstream → v4 evolve，形成多轮闭环。每轮 evolve 的
   scripts_dir 都基于上一轮（`--scripts-dir .evolve_scripts_v3_cycle`，下一轮基于 v4）。

### 5.3 可选增强（第三周+）

1. **细粒度成本对比**：用 tokenizer 而非字符数估算 token 成本（精确到 cache/non-cache）。
2. **step-level diff**：除了 task-level 对比，还做 step-level diff（baseline step 5 → v2 step 3
   合并了哪些 raw 命令），让 evolve agent 看到具体的合并模式。
3. **自动 regression 修剪**：v3 跑完后自动删除 stats 显示 0 调用或 100% 失败的 script 目录，不用
   等 evolve agent 自己处理。
4. **并行 evolve**：把 stats_provider 的输出 partition 成多个独立子集（按 script family 分），并行
   跑多个 evolve agent，每个 agent 只负责一类 script。

## 6. 风险与边界条件

### 6.1 task_id 不匹配

如果 baseline 和 v2 trial 用的 task 子集不一样（比如 v2 trial 跳过了某些 case），配对会丢样本。
**对策**：下游 trial 时强制使用与 baseline 完全相同的 case 列表（通过 `EVOLVE_SKIP_FILE` 控
制）。

### 6.2 v2 trajectory 没用 evolved tool

如果 v2 evolved scripts 设计得太差，下游 agent 一次都没调用，那 paired_trajectory 样本里
`evolved_tool_calls=0`，跟 baseline 完全一样，没对比意义。**对策**：v3 builder 过滤掉这类样本
（见 §3.2 第 2 条），不浪费 evolve 成本。

### 6.3 trajectory 截断

完整 trajectory 可能很长（baseline 47 步、observation 28万字符），如果直接塞进 prompt 会爆
token。**对策**：用 `TrajectorySerializer` 的 `max_observation_chars` 参数截断，每个 paired 样本
只展示前 N 个 action step 的对比即可。

### 6.4 regression 样本过多

如果 v2 evolve 把 scripts 设计得有副作用（比如改了文件没回滚），v2 trial 可能比 baseline 更
贵，导致大量 regression 样本。**对策**：v3 prompt 显式标注 `improvement_direction`，并加一条行
为契约：「If a script appears in 3+ regression samples, REMOVE it.」

### 6.5 多轮 drift

v3 → v4 → v5 每轮都可能引入新 bug，scripts 越改越偏离原始意图。**对策**：每轮 evolve 都基于
上一轮的 scripts_dir 增量改，但保留一份 v2 baseline scripts 作为「安全回退点」；如果某轮成本
比 v2 更差，回退到 v2。

## 7. 验收标准

v3 跑完后，下游 trial 应满足：

1. **API cost reduction ≥ 15%**（v1-chunk 4.87%，v3 目标至少 3 倍）。主要来自：
   - 进化 agent 看到真实 raw-vs-evolved 对比，设计的 script 更对路
   - stats_provider 闭环让 dead script 被删掉、prompt 更精简
2. **evolved tool 调用率 ≥ 30%**（v1-chunk ~5%）。下游 agent 真的用了 evolved tool 而不是回退到
   raw bash。
3. **regression 率 ≤ 20%**：v3 trial 跑完，相比 baseline，至少 80% 的 case 成本下降或持平。

如果以上任一不达标，说明 paired_trajectory contrastive 没有产生预期信号，需要回到 v2 调阈值
（hotspot_min_occurrences、min_reduction_ratio）或改 prompt。

## 8. 与 report.md 第 9/10 章的对应

- report.md §9.1（output-token leverage）→ v3 §3.3 `total_observation_chars` 字段
- report.md §9.2（operation-merging scripts）→ v3 §3.2 第 2 条（v2 必须用了 evolved tool）
- report.md §9.3（behavior contract）→ v2 已实现，v3 继承
- report.md §9.4（token-cost-based contrastive）→ v3 §3.3 `saved` 字段（真实测量）
- report.md §9.5（downstream feedback loop）→ v3 §3.4 `stats_provider` 闭环
- report.md §10 四阶段路线 → v3 §5 三阶段实现路线（v3 本身对应 report 的第 3-4 阶段）

v3 是 report.md 设计的 **直接实现**。v2 修了 v1-chunk 的执行 bug，v3 补了 v2 的设计缺口。
