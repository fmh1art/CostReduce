# Graph contrastive 改进计划

## 背景

当前 graph contrastive（v1-chunk + v2 都沿用）的核心问题：

1. **positive 经常是垃圾**：minimal subgraph 取「最后一步的依赖闭包」，但最后一步经常是失败的探索命令（`cd /workspace && git status` 报错），给 evolve agent 看「minimal = 1 个失败 cd」没学习价值。
2. **dependencies 标注太粗**：只标了「step N 依赖哪些前置 step」，没有 step 类型（读/写/验证）、执行结果（成功/失败）、是否 idempotent 等信息，反向追溯出的 minimal subgraph 准确度低。
3. **chunk 切分按固定 step 数**：每 20 action step 切一个 chunk，与任务逻辑边界完全无关，导致 chunk 边界经常切断「探索 → 修改 → 验证」的自然阶段。

用户的方向：annotator 标注更丰富的 step 元信息（读/写、是否成功），并提出基于 step DAG 的更优 chunk 切分方法。本文档给出具体实现计划。

## 设计目标

让 graph contrastive 真正能给 evolve agent 传达「这个 trajectory 里有可省的 step」的信号，方法是：

1. **step 元信息更丰富**：除依赖关系外，标注 `op_type`（read/write/verify/explore）、`success`（bool）、`idempotent`（bool，重跑是否幂等）。
2. **chunk 切分按 DAG 阶段**：把 trajectory 的 step DAG 切成若干「阶段」（每个阶段是一组强连通的 step），按阶段边界切 chunk，而不是固定 step 数。
3. **minimal subgraph 更有意义**：不再取「最后一步的依赖闭包」，改成取「同一阶段内、对最终目标贡献最大的关键路径」，并过滤掉失败 step。

## Stage 1 改进：annotator 标注更丰富的 step 元信息

### 新增字段

trajectory.json 里每个 action step 增加一个 `step_meta` 字段：

```json
{
  "steps": [
    {
      "tool_calls": [...],
      "observation": {...},
      "step_meta": {
        "op_type": "read" | "write" | "verify" | "explore",
        "success": true | false,
        "idempotent": true | false,
        "bash_verbs": ["cat", "sed"],
        "files_touched": ["src/foo.rs", "src/bar.rs"]
      }
    }
  ]
}
```

### 字段定义

| 字段 | 类型 | 含义 | 用途 |
|---|---|---|---|
| `op_type` | enum | `read`=只读不修改（cat/sed -n/grep/find/ls）；`write`=修改文件系统（sed -i/cat > /rm/mv）；`verify`=跑测试/构建（go test/cargo build/npm test）；`explore`=探索无副作用（pwd/ls/git status） | 区分 step 在 trajectory 里的角色，minimal subgraph 优先保留 write + verify |
| `success` | bool | observation 的 returncode 是否 0（对 verify 类）；对 write 类，是否没报错；对 explore 类，恒为 true | 过滤掉失败 step 不进 minimal subgraph |
| `idempotent` | bool | 重跑这个 step 是否产生相同结果（read/explore 通常幂等，write 不幂等，verify 幂等但结果可能变） | 判断一个 step 是否可以安全跳过/重跑 |
| `bash_verbs` | list[str] | step 里所有 bash 命令的 verb（复用 v2 cost_hotspot 的 `_bash_verb` 逻辑） | cost_hotspot 信号交叉验证 |
| `files_touched` | list[str] | step 读写过的文件路径（从 command args 抽取） | 跨 step 文件依赖图构建 |

### 标注实现

**Option A：LLM 标注**（最准但贵）

每个 step 多输出一段 JSON：

```python
SYSTEM_PROMPT = """
For each step, output:
{
  "op_type": "read|write|verify|explore",
  "success": true|false,
  "idempotent": true|false,
  "files_touched": ["path1", "path2"]
}
"""
```

成本：trajectory 平均 100 step × 1 LLM call/step = 100 calls/trajectory。DeepSeek-v4-flash 单 call ~500 token，~0.05 元/trajectory，100 trajectory ~5 元。

**Option B：规则 + 启发式**（便宜但稍粗）

```python
def classify_step(step):
    cmd = extract_bash_command(step)
    returncode = extract_returncode(step.observation)
    verbs = _bash_verb_multi(cmd)
    return {
        "op_type": _classify_op_type(verbs),  # cat/sed -n/grep/find/ls → read; sed -i/cat >/rm/mv → write; go test/cargo/npm → verify; pwd/git status → explore
        "success": returncode == 0,
        "idempotent": _classify_idempotent(verbs),  # read/explore → True; write → False; verify → True
        "bash_verbs": verbs,
        "files_touched": _extract_files(cmd),
    }
```

成本：0（纯规则）。

**推荐 Option B 起步**，跑一轮看效果，准确率不够再上 Option A 或混合（规则 + LLM 兜底）。理由：bash verb 已经有 v2 cost_hotspot 的 `_bash_verb` 实现，复用成本低；op_type 用 verb 映射表（cat → read, sed -i → write, go test → verify）准确率 90%+；只有「这个 cat 是为了查看 fix 后的状态还是探索」这种语义判断需要 LLM。

## Stage 1 改进：基于 step DAG 的 chunk 切分

### 当前 chunk 切分（v1-chunk）

```python
for start in range(0, len(action_step_indices), chunk_size):  # chunk_size=20
    end = min(start + chunk_size, len(action_step_indices))
    # 切 [start, end) 这一段 action steps
```

问题：固定步数切分，与逻辑边界无关。trajectory 里的「探索阶段」（step 1-15，全是 cat/grep）和「修改阶段」（step 16-30，全是 sed -i/cat >）和「验证阶段」（step 31-40，全是 go test）会被切碎到不同 chunk。

### 新切分方法：按 DAG 阶段切分

**Step 1：构建 step DAG**

用 `dependencies` 字段（已有的）+ `step_meta.op_type` 构建一个 DAG，节点是 step，边是依赖。

**Step 2：识别阶段边界**

一个阶段（phase）的定义：**连续的、op_type 同质的一段 step**。识别方法：

- 计算每个 step 的 `op_type`
- 扫 trajectory，op_type 改变的位置就是阶段边界
- 合并过短阶段（< 3 step）到相邻阶段

举例：

```
step 1-15: op_type=read     → phase 1 (exploration)
step 16-22: op_type=write   → phase 2 (modification)
step 23-28: op_type=verify  → phase 3 (verification)
step 29-35: op_type=read    → phase 4 (re-exploration after test failure)
step 36-40: op_type=write   → phase 5 (fix)
step 41-45: op_type=verify  → phase 6 (final verification)
```

**Step 3：按阶段切 chunk**

```python
def split_by_phase(trajectory, max_steps_per_chunk=30):
    phases = identify_phases(trajectory)  # [{start: 1, end: 15, op_type: read}, ...]
    chunks = []
    for phase in phases:
        # 单个 phase 太大再按 max_steps 切
        if phase.end - phase.start + 1 > max_steps_per_chunk:
            for sub in split_chunk(phase, max_steps_per_chunk):
                chunks.append(sub)
        else:
            chunks.append(phase)
    return chunks
```

效果：每个 chunk 内 op_type 同质，minimal subgraph 的语义清晰：

- read 阶段的 chunk → minimal subgraph 是「最后几个 read」（去掉冗余探索）
- write 阶段的 chunk → minimal subgraph 是「关键写操作」（去掉重复/失败写）
- verify 阶段的 chunk → minimal subgraph 是「最后一个成功的 verify」

## Stage 2 改进：minimal subgraph 选择更智能

### 当前选择逻辑

```python
# 取最后一步的依赖闭包
last = max(int(k) for k in dependencies)
keep = trace_minimal_indices(dependencies, last)
positive_steps = [steps[i-1] for i in keep if 1 <= i <= len(steps)]
```

问题：最后一步经常是失败命令。

### 新选择逻辑

**Step 1：找 anchor step**

不再固定取「最后一步」，改成取「chunk 内最有价值的一步」：

```python
def find_anchor_step(chunk):
    # 优先级：verify 成功 > write 成功 > read 成功 > 任意成功 > 最后一步
    for step in reversed(chunk.steps):
        if step.meta.op_type == "verify" and step.meta.success:
            return step
    for step in reversed(chunk.steps):
        if step.meta.op_type == "write" and step.meta.success:
            return step
    for step in reversed(chunk.steps):
        if step.meta.success:
            return step
    return chunk.steps[-1]  # 兜底：最后一步
```

**Step 2：trace 依赖闭包（过滤失败 step）**

```python
def trace_minimal_indices_v2(dependencies, anchor, step_meta):
    keep = {0, anchor.index}
    stack = [anchor.index]
    while stack:
        i = stack.pop()
        for dep in dependencies.get(str(i), []):
            if dep in keep:
                continue
            # 过滤失败 step：失败 step 不进 minimal subgraph
            if not step_meta[dep].success and step_meta[dep].op_type != "explore":
                continue
            keep.add(dep)
            stack.append(dep)
    return keep
```

**Step 3：保证 minimal subgraph 内步骤数 ≥ 1**

如果过滤后 minimal subgraph 为空（全部 step 都失败），这个 chunk 不生成 graph contrastive sample（没学习价值）。

## Stage 3 改进：prompt 渲染说明 minimal subgraph 来源

当前 prompt 只说「Original Chunk Trajectory」和「Minimal Chunk Trajectory」，evolve agent 不知道 minimal 是怎么来的，可能会误以为 minimal 是「理想执行路径」（实际是「依赖闭包」）。

新 prompt 渲染：

```
## Graph Contrastive

Phase: read (steps 1-15 of this chunk)
Anchor step: step 12 (last successful verify in this phase)
Minimal subgraph: {0, 12, 8, 5} (anchor + its dependency closure, failures filtered)

### Original Chunk Trajectory
<15 step 完整序列化>

### Minimal Chunk Trajectory (4 steps: anchor + closure, no failures)
<4 step 精简序列化>

Rationale: steps 1-4, 6-7, 9-11 are not in the dependency closure of
step 12, so they are removable without affecting the anchor's outcome.
Use this to design scripts that skip the removable steps.
```

## 实现路线

### Phase 1：annotator 元信息（1-2 天）

1. 在 `ChunkTrajectoryAnnotator` 子类（v3 或新 v2.1）里加 `_classify_step` 方法，用 Option B（规则）实现。
2. 在 `annotate_file` 里给每个 action step 写入 `step_meta` 字段。
3. `is_annotated` 检查加上 `step_meta` 字段存在。
4. 单测：对 boa-hierarchical trajectory 跑一遍，人工 spot-check 前 20 step 的 `op_type` 是否准确。

### Phase 2：DAG 阶段识别 + chunk 切分（2-3 天）

1. 在 `ChunkContrastiveSampleBuilder` 子类里加 `_identify_phases` 方法。
2. 覆盖 `_split_into_chunks`，改用 phase-based 切分。
3. 保留 `chunk_size` 参数作为「单 phase 内最大 step 数」上限（避免单个 phase 太大）。
4. 单测：对 boa-hierarchical trajectory 跑一遍，检查 phase 边界是否合理。

### Phase 3：minimal subgraph 智能选择（1-2 天）

1. 在 `ChunkContrastiveSampleBuilder` 子类里覆盖 `_build_positive_sample`。
2. 用 anchor step + 失败过滤的逻辑替换「最后一步依赖闭包」。
3. 单测：对包含失败 step 的 chunk 跑一遍，确认 minimal subgraph 不含失败 step。

### Phase 4：prompt 渲染（0.5 天）

1. 在 `ChunkEvolvePromptBuilderV2` 的 graph contrastive 渲染段加「Phase / Anchor / Rationale」说明。
2. 端到端测试：跑一遍 v2 → 看新 prompt 是否更易理解。

### Phase 5：A/B 对比（1 天）

1. 用旧 chunk 切分跑一轮 evolve（baseline）
2. 用新 chunk 切分跑一轮 evolve（treatment）
3. 对比下游 trial 的成本节省、evolved script 质量

## 风险

### 风险 1：phase 识别把 trajectory 切得太碎

某些 trajectory 全是 read（探索 100 步才动手），phase 切分会产出 1 个大 phase。`max_steps_per_chunk` 参数兜底：单 phase 超过 30 step 还是按 30 切。

### 风险 2：anchor step 选择不够智能

如果 trajectory 最后一步是 verify 失败，fallback 到「最后一个 write 成功」或「最后一步」。这可能导致 minimal subgraph 包含一些 explore step。可以加一个二级 fallback：找不到 verify 成功时，直接跳过 graph contrastive（这个 chunk 没学习价值）。

### 风险 3：成本上升

Option B（规则）成本 0，但准确率 90%。如果准确率不够，需要上 Option A（LLM），成本 ~5 元/100 trajectory。可控。

### 风险 4：与 v3 paired_trajectory 闭环冲突

v3 的 paired_trajectory contrastive 用真实 measurement 替代 minimal subgraph。如果 v3 跑通，graph contrastive 的改进可能没必要。

**对策**：graph contrastive 改进和 v3 不冲突 — graph contrastive 用于第一轮 evolve（baseline trajectory → v2 scripts），v3 用于后续轮次（baseline vs v2 trajectory）。两者互补。

## 验收标准

改进后跑一轮 v2 evolve + 下游 trial，对比改进前：

1. **graph contrastive 样本有效率** ≥ 80%（minimal subgraph 不为空、anchor step 不是失败命令）
2. **evolve agent 在 graph contrastive 段落产生的 script 修改** ≥ 1 个（说明信号被消费）
3. **下游 trial 成本节省** ≥ 8%（v1-chunk 4.87%，改进后目标至少 1.5 倍）

如果以上不达标，回退到 v2 当前状态（graph contrastive 保留但加 fallback 过滤），重心放在 v3 paired_trajectory contrastive。

## 与现有代码的衔接

- 不改 `src/evolve/evolve_v1_chunk.py`（v1-chunk 保持原样作为 baseline）
- 改进放在新文件 `src/evolve/evolve_v2_1_chunk.py`（v2.1）或直接进 `src/evolve/evolve_v2_chunk.py`（看用户偏好）
- v3 (`evolve_v3_cycle.md`) 的 paired_trajectory contrastive 不受影响，可以独立推进

推荐：先把 v2 的 5 个修复合进去跑一轮，看效果。如果效果不够，再上 graph contrastive 改进。如果 v3 闭环跑通后 graph contrastive 信号被替代，graph contrastive 改进可以暂缓。
