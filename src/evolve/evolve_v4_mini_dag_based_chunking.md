# V4框架简述

这个框架的基本思想就是围绕着Mini-DAG来构造contrastive samples

- 复用 V2 的annotate
- 构建contrastive samples的流程如下：
    1. 根据annotate的结果，构建原始trajectory T 的DAG图，原始DAG图记为 D。从原始DAG图中，从最后一步往前找所有依赖的节点，构建一个Mini-DAG（和当前的方式一样），记为 D*，对应的trajectory记为 T*
    2. 从D*出发，找到所有的contrastive sample，一共有3种类型：
        - 有些step可以避免
            - D* 对应了一个最小的trajectory T*，可以认为解决这个任务需要的最少step。从 T* 的第1步出发，观察第 i 步和第 i步之间相较于 T 是否有省略的步数，如果有的话就是第一类contrastive sample
        - 有些step可以merge
            - 在 D* 中，如果假设存在 step i 和 step j，其中 i < j，满足 step i和step j依赖的所有前序step是相同的，则可以将这两个step merge。
        - 有些observation需要根据索引的方式简化 
            - 假如某些step的observation很长，即执行结果很长，则考虑是否可以将其和一些 indexing-then-read 的思路结合，来减少observation

下面，我举一个例子，并且列出这三个contrastive sample对应的prompt模板。假如 T 一共有  个step，对应的 D 为：

1 -> 2,3,4,6
2 -> 4,6
3 -> 6
4 -> 5
5 -> Null
6 -> 7,9
7 -> 8
8 -> Null
9 -> Null

其中，step 1属于类型A，step 2,3属于类型B，step 3,4 属于类型C，step 6,7,8属于类型D，step 9属于类型E

所以，D* 为：

1 -> 2,3,6
2 -> 6
3 -> 6
6 -> 9
9 -> Null

首先，我们从 T* 出发，T* （用原始的step index标注）对应为 step 1,2,3,6,9

（1）有些step可以避免，这类contrastive samples记为 s

- step1，2，3连续，跳过，没有生成任何samples
- step3和step6之间，跳过了step4，5，那么step4，5就是可以跳过的step，生成下面的contrastive samples s1：

Here is agent steps in chunck with steps that doesn't have to be generated (wrapped with <skippable_steps> </skippable_steps>)

Step 1: xxx
Obs 1: xxx

Step 2: xxx
Obs 2: xxx

Step 3: xxx
Obs 3: xxx

<skippable_steps>
Step 4: xxx
Obs 4: xxx

Step 5: xxx
Obs 5: xxx
</skippable_steps>

Step 6: xxx
Obs 6: xxx

为了控制prompt长度，上面不会放 T 的所有step，而是根据skippable_steps切成chunk。
为什么前面我们会加入step 1，2，3呢：因为要找到 i) 所有skippable_steps依赖的前序step，且在 T* 中的steps（我们的例子中是step1，2） ii) 所有在 i) 的steps之后，skippable_steps 中step之前，且在 T* 中的step，即step3，因为 1,2 < 3 < 4,5
为什么我们后面会加入step 6呢：因为6满足 i) 所有skippable_steps的出边到达的节点，该chunk中，skippable_steps之前的、在 T* 中step （即step1，2，3）出边到达的节点

- step6和step9之间，跳过了step7，8，可以构造下面的contrastive sample s2:

Step 6: xxx
Obs 6: xxx

<skippable_steps>
Step 7: xxx
Obs 7: xxx

Step 8: xxx
Obs 8: xxx
</skippable_steps>

Step 9: xxx
Obs 9: xxx

（2）有些step可以merge：

- 在 T* 中，step2和step3所有依赖的step都是step 1，那么他们两可以merge，构造sample的prompt如下：

Step 1: xxx
Obs 1: xxx

<mergable_steps>
Step 2: xxx
Obs 2: xxx

Step 3: xxx
Obs 3: xxx
</mergable_steps>

Step 6: xxx
Obs 6: xxx

（3）有些observation需要根据索引的方式简化

假如Step1的输出observation很长：

<optimizable_observation_step>
Step 1: xxx
Obs 1: xxx
</optimizable_observation_step>

Step 2: xxx
Step 3: xxx
Step 6: xxx

The observation of Step 1 is very long, can you come up with some trajectories to make it short? For example, retrieve relevant content or indexing?

在 optimizable_observation_step 标志前后，给出 Step1依赖和被依赖的所有step的action就够了，不用给observation

上述所有的prompt构造，Step请对应到message中的某个completion message，observation对应到agent的execution message。不用按照原来的prompt内容给出。

# 其他细节修改点

请修改evolve的prompt，确保不影响成本的基础上降低cost：
- instructions.md中不包括scripts的用法，而是high-level的指导思想
- evolve的prompts中包括evolve一个scripts后，要验证他能符合要求
- 保持prompt简洁，不要有冗余内容
- evolve的prompt中加入一条重要指示并突出：注意，上述所有samples如果不能提供新的成本优化建议，不要改动已有的scripts，可以直接跳过该evolve步骤。你不需要保证每次evolve都有大的迭代动作，确保效果是首要的。

# Evolve Samples

在evolve过程中，agent接受的初始prompt里关于contrastive sample的结构是这样的：
```
## Task 1

### Task Description

### 第一类Contrastive Sample 1 （给出第一类samples的英文描述）

### 第一类Contrastive Sample 2

...

### 第二类Contrastive Sample 1 （给出第二类samples的英文描述）

### 第二类Contrastive Sample 2

...

...

### 第三类Contrastive Sample 1 （给出第三类samples的英文描述）

### 第三类Contrastive Sample 2

...

## Task 2
... ...
```


# 如何实现上述框架（实现方案）

> 总原则：**统计先行 → skippable 先行 → 评估再扩**。三类样本里只有 skippable 是"标注即对比"的高 ROI 信号；mergeable 需要先统计真实频率再决定上线；optimizable-observation 性质是开放生成而非对比，拆出 contrastive 框架单独做。所有改动落在新文件 `src/evolve/evolve_v4_dag.py`，不改 `evolve_v2_chunk.py`（v2 保持 baseline）。

## 0. 落点与复用

- 新文件：`src/evolve/evolve_v4_dag.py`，三个 stage 类继承 v2 对应类，只覆盖必要方法（与 v2 继承 v1-chunk 的模式一致）。
  - `ChunkTrajectoryAnnotatorV4(ChunkTrajectoryAnnotatorV2)`：复用 v2 的 `step_meta` + `dependencies`，**不新增标注**（v4 思路明确"复用 v2 的 annotate"）。`is_annotated` 直接复用 v2。
  - `ChunkContrastiveSampleBuilderV4(ChunkContrastiveSampleBuilderV2)`：覆盖 `build_file`，把样本产出从 v2 的 `{positive, negative}` 改成 v4 的三类细粒度样本。
  - `ChunkEvolvePromptBuilderV4(ChunkEvolvePromptBuilderV2)`：覆盖 `_render_graph_contrastive_block` + 新增三类渲染方法，并改 FOOTER。
- 复用 `_chunk_helpers.py`：`trace_minimal_indices`（已在 v2 实现 anchor+失败过滤版）、`identify_phases`、`find_anchor_step`、`classify_step_meta`、`observation_chars`、`extract_bash_command`、`bash_verb`。
- T*（mini-DAG）直接复用 v2 `_build_positive_sample` 的产物：它已经做了 anchor 选择 + 失败 step 过滤 + 跨 chunk 前序上下文。**v4 不重新发明 T*，只是换一种渲染方式把同样的 minimal subgraph 拆成三类显式信号。** 这点很重要：v4 思路里 T* 来自"最后一步往前找依赖"，但 v2 已经证明"最后一步经常是失败命令"，所以 v4 的 T* 必须用 v2 的 anchor-based 版本，否则会重新踩 graph_contrastive_improvement.md 开篇列的坑。

## 1. Phase 0：离线统计（先做，不写 evolve 逻辑）

目的：用真实数据决定三类信号各自的上限，避免上线一个稀疏/无效的信号。

在 `evolve_v4_dag.py` 里加一个 `--dry-stat` 子命令（或独立脚本 `scripts/v4_stat.py`），扫描一批已 annotated 的 trajectory，输出：

| 统计量 | 含义 | 决策阈值 |
|---|---|---|
| `skippable_segments_per_traj` | 每条 trajectory 有几个被 T* 跳过的连续 step 段 | 中位数 ≥ 1 才值得做 |
| `skippable_steps_total` | 所有可跳 step 总数 / trajectory 总 step 数 | 比例 ≥ 10% 才值得做 |
| `mergeable_pairs_per_traj` | 每条 trajectory 有几对"前序依赖完全相同"的 step | 中位数 < 0.5 → mergeable 暂不上线，只保留 skippable |
| `long_obs_steps_per_traj` | obs 字符数 > `LONG_OBS_THRESHOLD` 的 step 数 | 单独统计，供 optobs 模块决策 |

实现：直接遍历 `trajectory["dependencies"]`，用 `trace_minimal_indices` 得 keep 集，`T \ T*` 即 skippable；mergeable 用 `dependencies[i] == dependencies[j] and i<j` 在 T* 内配对。**纯规则、零 LLM 成本。**

输出一份 markdown 报告。若统计不达阈值，Phase 2/3 暂缓——避免为稀疏信号写一堆渲染代码。

## 2. Phase 1：skippable 样本构造与渲染（最先上线）

### 2.1 数据结构

在 `ChunkContrastiveSampleBuilderV4.build_file` 里，对每个 chunk 调父类 `build_file` 拿到 v2 的 graph contrastive sample（含 `positive_sample.minimal_step_indices` = T* 闭包、`negative_sample` = T）。在此基础上算 skippable 段：

```
skippable_segments = 连续的、在 T 中但不在 T* keep 集里的 action step 段
```

每段生成一个 sample，结构：
```json
{
  "type": "v4_skippable",
  "chunk_id": ...,
  "chunk_range": [global_first, global_last],
  "skippable_local_indices": [4, 5],
  "context_before": [<T* 中、且 < skippable 的最近 N 个 step 的 action+obs>],
  "skippable_steps":  [<被跳过的 step 的 action+obs>],
  "context_after":    [<skippable 出边到达的、且在 T* 中的 step>],
  "rationale": "step 4,5 are not in the dependency closure of the anchor; skipping them does not change the outcome"
}
```

context_before / context_after 的选取规则形式化（修正 v4 文档 68-69 行的模糊表述）：
- `context_before` = T* keep 集中、global index < skippable 段起点的 step，取**最近的 N 个**（N=`MAX_PREDECESSOR_STEPS`=5，复用 v2 常量）。
- `context_after` = T* keep 集中、global index > skippable 段终点、且其 dependencies 命中 skippable 段内任一 step 的 step；若没有这样的后继，则取 T* 中 skippable 之后的第一个 step。取**最近 1 个**（避免 prompt 膨胀）。
- 用 `_chunk_helpers.trace_minimal_indices` 的失败过滤规则过滤 context_before（失败的非 explore step 不进）。

### 2.2 渲染（在 PromptBuilderV4）

新增 `_render_skippable_block(sample)`，产出：
```
## Skippable-step signal (chunk K, steps <global_range>)

Context before (steps that the skippable segment depends on):
Step 1: <action>
Obs 1: <obs>
Step 3: <action>
Obs 3: <obs>

<skippable_steps>
Step 4: <action>
Obs 4: <obs>
Step 5: <action>
Obs 5: <obs>
</skippable_steps>

Context after (step whose outcome is unchanged if the skippable steps are removed):
Step 6: <action>
Obs 6: <obs>

Rationale: <rationale>
```
渲染时 Step/Obs 对应到 message completion / execution（文档 122 行要求），复用 `TrajectorySerializer`。

### 2.3 信号有效性

skippable 的 T* 用 v2 anchor-based 版本，失败 step 已被过滤出 T*——所以 skippable 段天然不含失败命令。但仍需在 `_has_graph_optimization_space` 一级加一道：skippable 段长度 < 2 的不输出（单步可跳信号太弱，性价比低）。

## 3. Phase 2：mergeable 样本（统计达标才上线）

### 3.1 判据收紧

v4 文档的判据"依赖的前序 step 相同"只是必要条件。V4 收紧为**充分条件**：
- `dependencies[i] == dependencies[j]`（前序依赖集合相同），且
- 两者 `step_meta.op_type` 相同，且
- 两者都是 `success=True`，且
- 两者 `files_touched` 无交集（避免合并写同一文件造成顺序依赖丢失）。

满足以上才标 `<mergable_steps>`。这一步在 Phase 0 统计里就要带这个收紧判据跑——若收紧后 mergeable pair 接近 0，Phase 2 直接不做。

### 3.2 结构与渲染

```json
{
  "type": "v4_mergeable",
  "merge_pair": [local_i, local_j],
  "shared_dependencies": [...],
  "steps": [<step i>, <step j>],
  "rationale": "both depend only on step {shared}; same op_type; disjoint files; safe to merge into one step"
}
```
渲染为 `<mergable_steps>...</mergable_steps>` 包裹两步，前后各放一个 shared dependency 的 action（不给 obs，按文档 120 行规则）。

### 3.3 教学闭环

每个 mergeable sample 额外附"合并后的精简片段"——把 step i、j 的 command 合并成一条 `a && b` 的示例 step（仅作示意，标注 `-- example merged form, not verified`）。让 evolve agent 看到"省成什么样"，把对比做实（回应评价里的第 4 点）。

## 4. Phase 3：optimizable-observation（拆出 contrastive 框架）

文档里第三类要求 LLM "come up with some trajectories"，是开放生成、非对比，塞进 contrastive 框架会和 evolve 主任务冲突。**v4 实现里把它降级成一个纯标注信号**：只标 `<optimizable_observation_step>` + 给出"该 step obs 有多长、占 chunk 总 obs 的比例"，不要求 evolve agent 现场生成 retrieve/indexing trajectory。

- 判据：`observation_chars(step) > LONG_OBS_THRESHOLD_DEFAULT` 且该 step 在 T* 中。
- 渲染：`<optimizable_observation_step>` 包裹该 step 的 action + obs，前后给 dependency/action（不给 obs），并附一句"this observation is N chars (M% of chunk); a script that indexes/retrieves could shorten it"。
- 是否设计 indexing 脚本，留给 evolve agent 自行决定——和 skippable/mergeable 的"标注信号"性质一致，不再要求生成。

如果后续要恢复"生成 retrieve trajectory"的强形式，单独做一个 `--optobs-generate` 模块，不进 contrastive 主流程。

## 5. Prompt 修改（呼应"其他细节修改点"）

`ChunkEvolvePromptBuilderV4` 改三处：

1. **HEADER 里的 instruction.md 段**：v2 已经是"行为契约，非工具目录"。v4 进一步明确——instruction.md 只放 high-level 指导思想，**不放任何 script 用法**（用法由 intro.json 的 description/examples 承担）。覆盖 HEADER 第 `## instruction.md` 段措辞。

2. **verification 段**：v2 已有"REQUIRED after every script add/update"。v4 强化——在 FOOTER 前加一句独立高亮：`After evolving ANY script, you MUST run it against a sample input and confirm it returns the desired content before finishing. An unverified script is worse than no script.`（呼应"evolve 一个 script 后要验证"）。

3. **新增 SKIP 指示（用户新增要求，必须突出）**：在 HEADER 顶部、紧跟 `# Evolve task` 之后插入一段独立的、用 `⚠️` / 大写 / 空行包裹的指示：

   ```
   ## ⚠️ IMPORTANT — Do not change scripts unless there is a NEW cost-saving idea

   The samples below are SIGNALS, not obligations. If they do not suggest a NEW
   cost optimization that your current scripts do not already capture, DO NOT
   modify, add, merge, or delete any script — finish immediately with scripts
   unchanged. You are NOT required to make a big change every evolution round.
   Effectiveness is the only priority: a no-op evolution is strictly better than
   a regression.
   ```

   同时在 `ChunkScriptEvolverV4` 里加一个**no-op 检测**：batch 跑完后，若 scripts 目录的 git diff（或文件 mtime + 内容 hash）相对跑前无变化，记一条 `INFO: batch K produced no script change (acceptable per SKIP directive)`，不视为失败。这把 prompt 指示落到可观测层，避免 agent 为了"显得有产出"而硬改。

## 6. 成本预算与样本量控制（防 swe-atlas-tw 长尾失控重演）

- **每 trajectory 样本上限**：skippable 段 ≤ 3 个（按可跳 step 总数降序取前 3）；mergeable pair ≤ 2 个；optobs ≤ 2 个。超出的丢弃并 log。
- **每 batch 进 prompt 的样本上限**：复用 v2 的 `batch_size`，但 v4 prompt 里同一 trajectory 的多类样本合并渲染（一个 chunk 的 skippable+mergeable+optobs 进同一个 prompt 块），避免样本数膨胀导致 batch 数翻倍。
- **prompt 长度上限**：在 PromptBuilderV4.build 末尾加 `len(prompt)` 检查，超过阈值（如 60k chars）时按 `total_observation_chars` 降序裁剪 optobs/skippable 的 obs 字段（用 `TrajectorySerializer(max_observation_chars=...)` 已有的截断能力）。
- **长尾防护**：`ChunkScriptEvolverV4.run` 复用 v2 的 batch sentinel + resume 机制；额外加 per-batch 超时（复用 `MiniSweAgentRunnerV2.timeout`），单 batch 超时不阻塞其他 batch。

## 7. 与 v3 的衔接

明确 v4 的 pipeline 位置：v4 替换 v2 用于**第一轮** evolve（baseline trajectory → v4 scripts），产出更"显式可执行"的信号。v3 的 paired_trajectory（真实 measurement 对比）用于**后续轮次**。两者不重叠。`run_evolve` / `run_exp` 里 v4 作为 `--mode v4` 的别名接入，v3 仍保留。这与 graph_contrastive_improvement.md "graph contrastive 用于第一轮、v3 用于后续"的结论一致。

## 8. 实施步骤（按顺序）

1. **Phase 0 统计**（0.5 天）：写 `--dry-stat`，在现有 annotated trajectory 上跑，产出报告。**门槛决策点**：skippable 达标 → 继续；mergeable 不达标 → Phase 2 标注"暂缓"。
2. **骨架 + Phase 1 skippable**（1.5 天）：建 `evolve_v4_dag.py` 三个类骨架；实现 skippable 样本构造 + 渲染；加 no-op 检测 + SKIP 指示。单测：对一条已知 trajectory 跑 `contrastive` + `evolve --dry-run`，人工检查 prompt 里 skippable 段的 context_before/after 是否正确。
3. **端到端冒烟**（0.5 天）：对一个小 trajectory 集跑 `run`，确认 evolve agent 在"无新信号"时真的不改 scripts（验证 SKIP 指示生效）。
4. **Phase 2 mergeable**（1 天，仅当 Phase 0 达标）：加收紧判据 + 渲染 + 合并示例。
5. **Phase 3 optobs 降级版**（0.5 天）：纯标注，不生成。
6. **A/B 对比**（1 天）：v2 vs v4 各跑一轮 + 下游 trial，对比
   - evolved script 调用率（v2 是 2.1%，v4 目标 ≥ 8%）
   - 下游 trial 成本节省（v2 基线，v4 目标 ≥ 1.5×）
   - no-op batch 占比（验证 SKIP 指示是否被消费，期望非零）

## 9. 验收标准

1. skippable 样本有效率 ≥ 80%（context_before/after 非空、rationale 正确）。
2. evolve agent 在有 skippable 信号的 batch 产生的 script 修改 ≥ 1 个（信号被消费）；在无信号的 batch 产 0 修改（SKIP 指示生效）。
3. 下游 trial 成本节省 ≥ v2 的 1.5×；若反升，回退 v2 并复盘（重点查是否又出现长尾失控，对照 swe-atlas-tw 分析）。
4. 单条 trajectory 的 evolve prompt ≤ 60k chars（预算控制生效）。

不达标则回退：保留 v4 的 no-op 检测 + SKIP 指示（这两条对 v2 也有益，可单独回灌 v2），其余 v4 渲染暂缓。