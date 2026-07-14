# Evolve V9：Cost-Anchored Subgraph Contraction (CASC)

## 0. 这份文档想做什么

v7 把方法堆成了一座塔（异构 provenance 本体、AND/OR 支持集、ILP、Steiner Tree、gSpan、MDL……），
其中大部分模块**在代码里根本没有真正生效**（annotator 只产扁平依赖，solver 退化成 closure，验证只到 Level 0）。
技术名词很多，但没有一个是从"当前框架真的错在哪"推导出来的。

v9 的立场很简单：

- **回退到 v6 的主干**：`rollout → step-level DAG → evolve samples → tools.json/executor.py + instruction.md → 重新 rollout 验证`。
- **只引入能被 `results/` 里的真实证据直接证明其必要性的技术**。每个技术都对应一个可量化的失败。
- 方法名 **CASC = Cost-Anchored Subgraph Contraction**：成本锚定（找对成本）+ 结果锚定（找对目标）+ 子图收缩（造对工具）。

---

## 1. v6 的实证诊断（先看数据，再谈方法）

以下数字全部来自 `results/` 中已恢复的 64-case 结果、prep trajectories、contrastive samples 与最终 `tools.json`/`instruction.md`。
不同 run 有采样随机性，用于定位设计问题，不替代最终 paired non-inferiority 实验。

### 1.1 效果问题：成本几乎没降，一半 case 反而更贵

SWE-bench Verified（`cost_analysis_result.json`，64 common cases）：

| 指标 | 值 |
|---|---|
| v6 总成本 | \$7.856 |
| baseline 总成本 | \$7.946 |
| **成本下降** | **1.14%** |
| 比 baseline **更贵**的 case | **32 / 64** |
| 比 baseline 更便宜的 case | 32 / 64 |

典型 case（v6 步数/成本 vs baseline）：

| case | v6 steps | base steps | v6 cost | base cost |
|---|---:|---:|---:|---:|
| scikit-learn-9288 | 186 | 87 | \$0.904 | \$0.284 |
| django-11734 | 141 | 94 | \$0.767 | \$0.369 |
| scikit-learn-14710 | 191 | 151 | \$0.845 | \$0.518 |

**结论**：v6 有时压缩、有时反而膨胀，净效果接近 0。它没有一个稳定对齐真实成本的优化目标。

### 1.2 最关键的发现：成本 80% 来自"上下文被反复计费"，不是步数

SWE-bench v6 的 token 账本按计费桶分解：

| 计费桶 | 成本占比 | 说明 |
|---|---:|---|
| cached input | **79.8%** | 历史 context 在每一轮 prompt 中被重复携带 |
| output | 14.7% | 模型生成 |
| uncached input | 5.4% | 每个 token 第一次进入 context |

- **cache / uncached token 比 = 56.8×**：同一段 observation 平均被当作 cached context 重新计费约 57 次。
- deep-swe 的 turns 从 118.2 降到 106.6（−9.8%），QA 从 48.2 降到 46.4（−3.7%），
  但 SWE-bench 成本只降 1.1% —— **"减步数" 与 "降成本" 严重脱钩**。

这说明真正的成本驱动量不是 step 数，而是**每段 observation 的 token 数 × 它之后还要被携带的轮数**。
一段早期产生的长 observation，即使便宜的 cache 单价，也会因为被携带几十轮而累计成主成本。
v6（和 v7 的 step-计数思路）从根上锚错了优化对象。

### 1.3 样本构造 bug：一半正样本是空的

对全仓库 128 个 `contrastive_sample.json` 统计（`contrastive.py` 从最后一个 action 反向取依赖闭包）：

- negative（完整轨迹）平均 **54.0** 个 action；
- positive（"最小轨迹"）保留 action 的**中位数 = 1**；
- **68 / 128 个样本坍塌到 ≤ 1 个 action**；其中
  - **36 个只剩** `echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`，
  - **32 个只剩 step 0（初始状态）**。

根因：`_trace_minimal_indices` 以 `max(dependencies key)`（最后一个 action）为 sink，而最后一个 action 几乎总是 submit/echo/git status。
"最小轨迹" 名不副实——超过一半的正样本对 evolve agent 毫无信息量。

### 1.4 工具即使生成，也没有真正降本

- **SWE-bench：native tool 采用率 0%**（3346 次调用全是 `bash`，10 个 evolved tool 一个没被用）。
  原因：eval config 把 agent 接到了 `.evolve_tools_config.yaml` + 空的 `.tools_manifest.json`（v5 manifest 机制，`{"tools": []}`），
  而不是 v6 registry。**"生成了工具" ≠ "工具被部署并采用"。**
- deep-swe 采用率 78%、QA 65%，但工具**输出没有上限**：
  `read-lines` 平均 4637 / 最大 36416 chars，`git-diff` 平均 7718 / 最大 26434 chars，QA 每步 observation 平均 3127 chars。
  于是出现"turns 略降、但 context 不降"——正好呼应 §1.2：工具减少了轮数，却继续制造长 observation，被反复计费。

### 1.5 evolve 输入与产物的问题

- deep-swe 的 8 个 evolve prompt 平均 **357k / 最大 579k chars**，绝大部分是被完整序列化的 positive/negative 轨迹，而非可执行的工具证据。
- 生成的多是通用 shell wrapper（deep-swe 14 个、SWE-bench 10 个 tool），未按实际采用率/净收益淘汰。
- `instruction.md` 含高风险规则："If environment is broken, commit without running tests"、
  "If tests fail for same root cause after 2 attempts, commit best-guess fix"、"check-syntax 通过就跳过测试"。
  这些没有来自"被验证的成功子图"，直接威胁 performance 约束。

### 1.6 一句话总结

v6 缺的不是"更多图结构"，而是四件互相咬合的事：**(a) 一个对齐真实计费的成本目标；(b) 正确的优化终点；
(c) 一个能跨任务复用、且真正限制输出的工具来源；(d) 一道保证不掉点、且确保工具真被部署采用的验证闸门。**

---

## 2. 四个 high-level 技术挑战与对应技术

每个挑战都由 §1 中一个**可量化**的失败驱动，对应一个**必要且足够**的技术。不引入无法被证据支撑的模块。

### 挑战 1：成本目标锚错了——步数不是成本

**Motivation（证据）**：成本 79.8% 来自 cached context，cache/uncached = 56.8×；步数降 10% 只换来成本降 1.1%。
**技术挑战**：需要一个**可从计费日志直接测量**、并能落到图节点上的成本量，使后续所有图决策都对准真实美元。

**对应技术：DAG 上的 context-footprint 成本标注。**

沿用 v6 的 step-level DAG，但给每个 turn 节点两个可测成本标签：

1. **生成成本**：该轮 uncached-input + output 的实际计费；
2. **上下文暴露成本**：该轮产生的 observation 在其后每一轮 prompt 中被重复携带的累计计费。

设一条长度 $T$ 的轨迹，第 $t$ 轮产生大小 $s_t$（token）的 observation，则其上下文暴露成本近似：

$$
C_{\text{ctx}}(o_t)\;\approx\; s_t\,\big[p_{\text{uncached}}+(T-t)\,p_{\text{cache}}\big].
$$

这不是新费率模型，而是**忠实还原 API 计费方式**（首次进 context 按 uncached、之后每轮按 cache）。
它把"哪段 observation 真正昂贵"变成一个可计算的节点权重：**早期 + 长 + 后续轮次多**的 observation 权重最高。
后续所有图挖掘/收缩/选择都用这个权重排序，而不是用 step 计数。有真实 `cost_usd` 时优先用实测值校准 $p_{*}$。

> 技术深度：把"降本"从模糊的 step 计数，升级为轨迹图上一个可测、可优化的成本泛函；这是 v7 反复声称却从未真正落地的东西（v7 的 cost 最终退化成 token 加权和且未驱动任何决策）。

### 挑战 2：优化终点错了——一半正样本是 `echo submit`

**Motivation（证据）**：68/128 样本坍塌到 ≤1 action，36 个只剩 submit signal，32 个只剩 step 0。
**技术挑战**：sink 必须是"真正产生并验证了结果的步骤"，而不是轨迹的最后一个 action；同时不能为此引入 v7 那套 7 类节点 / 10 类边的重本体。

**对应技术：结果锚定的依赖图反向可达（outcome-anchored backward reachability）。**

- **锚点（sink）**：final patch 的 *producing writes*（追溯每个最终改动文件的最后一次成功 write）+ 验证该状态的 test step。
  这些都能从 rollout 日志恢复（改动文件 = `git diff` 解析或 per-write 记录；verifier = benchmark 结果）。
- **分析区域**：在 v6 的 `dependencies` DAG 上，只保留**能通过依赖边反向到达锚点**的节点。
  时间相邻关系（`NEXT`）只用于顺序恢复，**不当作依赖**，否则会无条件保留结果前的所有轮次。
- submit/echo/收尾 `git status` 因为不支持任何 producing write，自然被排除在分析区域之外。

这仍然是**一个简单的图操作**（正确终点集上的反向可达），不需要异构本体、也不需要 AND/OR 支持集。
它的作用是：为挑战 3 的挖掘提供一个"只包含真正相关步骤"的干净区域。

> 与 v7 的区别：v7 为了修 sink 引入了整套 provenance ontology；v9 只需换掉 sink 定义 + 一次反向可达。

### 挑战 3：单轨迹裁剪造不出"可复用且真省钱"的工具（核心图技术）

**Motivation（证据）**：v6 单轨迹依赖闭包在纯 AND 依赖下只有唯一解、根本没有"优化"可言（§1.3）；
生成的工具是 LLM 读日志猜出来的通用 wrapper，且输出无上限（read-lines 平均 4637 chars，§1.4）。
**技术挑战**：一个值得进 registry 的工具，必须对应**跨任务重复出现、且累计上下文成本高**的计算结构，并且封装后能**同时**减少轮数与限制输出。

**对应技术：跨轨迹、成本加权的频繁连通子图挖掘 + 边界保持的图收缩。**

这是 v9 的技术核心，分三步：

**(a) 规范化节点标签（deterministic，可拒绝）。**
把每个 tool call 映射到一个结构化 canonical label：`op ∈ {FIND, SEARCH, READ, WRITE, VERIFY, VCS_READ}` + 路径角色（source/test/config…）+ 参数角色（identifier/glob/range…），
具体字符串（`Foo`、`src/a.py`）不进图标签、但保留在 occurrence 里供后续推断参数。
**关键约束（避免 v8 过度设计）**：规范化用**确定性规则**（native tool 有结构化 JSON，最可靠；bash 用固定规则匹配 `find/rg/grep/cat/sed -n/head/tail/git`），
**看不懂就标 `UNKNOWN` 并退出挖掘**——宁可只在高置信步骤上挖，也不硬解析任意 shell。
仓库里 `evolve_v7.py::_tool_family / _call_op_type / _paths_from_call` 可作为起点，无需引入 v8 §5.5 那套多层 AST + tool-contract lineage 的重管线。

**(b) 成本加权的频繁连通子图挖掘。**
在多条**成功**轨迹的分析区域（挑战 2 的产物）里，找满足以下条件的**依赖连通子图**（size 2–5）：

- **跨任务重复**：在规范化标签下于 ≥ $k$ 条独立轨迹出现（去重后按独立任务计 support，防止一条轨迹反复自我增强）；
- **高成本**：occurrence 的累计 context-footprint 成本（挑战 1）位于高分位。

即优先挖 **既常见又昂贵** 的多步模式，而不是 v6 的"频率"或"单轨迹闭包"。第一版用 size≤5 的连通子图 + 规范化签名哈希即可，**不需要 gSpan**。

**(c) 边界保持的图收缩（graph contraction）→ bounded-output 工具。**
把选中的子图收缩成一个 native function tool：

```
外部输入            子图内部(隐藏)              下游使用
symbol/path ─→ [FIND → SEARCH → READ] ─→ file/line/bounded_context ─→ EDIT
```

- 子图的**入边** → 工具 inputs；**出边** → 必须暴露的 outputs；内部 observations 全部隐藏；
- 工具携带**硬输出预算**（`max_results`、`max_chars`，超限返回 continuation cursor；test 工具只回 exit code + 失败用例名 + 日志尾部）。

这一步**同时命中 §1.2 的两个成本源**：多轮 → 一次调用（降轮数）；长 observation → 有界输出（降被反复计费的 context）。
这正是 v6 缺的一环——v6 的工具减了轮数却继续吐长输出。

**工具选择**：若有多个候选，在 registry 的 prompt-token 预算下按 **"新增覆盖的高成本步骤 / schema token 开销"** 贪心选择（budgeted maximum coverage），
重叠 occurrence 只计一次。**这是一个简单的贪心覆盖问题，不需要 ILP / weighted set packing。**

> 技术深度：从"单轨迹规则裁剪"升级为"跨轨迹、成本加权的频繁子图挖掘 + 边界收缩 + 预算覆盖选择"——
> 这是真正的图算法内容，但每一步的目标函数都直接是 §1.2 的 context-footprint 成本，而非堆砌术语。

### 挑战 4：压缩可能掉点，且工具可能根本没被部署/采用

**Motivation（证据）**：SWE-bench native 采用率 0%（config 接错到空 manifest）；`instruction.md` 含 "commit without tests" 类高风险规则；正样本从未验证。
**技术挑战**：必须保证 (a) 收缩等价、(b) performance 非劣、(c) 工具确实被部署且被采用，否则前三步都是纸面收益。

**对应技术：三道闸门 + 部署/采用回执。**

候选工具必须依次通过：

1. **Replay 等价闸门**：在 occurrence 的相同 repo snapshot 上执行收缩后的工具，要求它仍返回原来的关键 `file:line` 与上下文，且不超输出预算；对含 write 的 stateful 子图，比对 changed files + diff hash。
2. **Held-out non-inferiority 闸门**：把工具临时加入 registry，在**未参与挖掘**的 held-out 任务上重新 rollout，要求 pass rate 满足预注册 margin $\epsilon$、且实际成本下降。
3. **部署/采用回执**：rollout deployment manifest 必须记录 registry hash 并写入每条 trajectory metadata；
   下一轮按 `tool_id` 聚合 `adopted_in_tasks / available_in_tasks`、`success/call`、以及"调用后是否 fallback 回 bash"。
   **采用率低或频繁 fallback 的工具进入 REMOVE 候选**——直接防止 §1.4 那种"注册了却 0 采用"和"通用 wrapper 越堆越多"。

`instruction.md` 用同样原则：**只有跨成功轨迹重复、并通过 held-out 的行为规则才能写入**；
失败轨迹只能提"重复 retry"这类待验证假设，**禁止**产生"跳过测试""直接 commit"这类高风险规则。

> 技术深度：把"生成了工具"变成"经等价性验证、非劣验证、且实测被采用的净正收益工具"，每一环都可度量。

---

## 3. 简单而足够的图模型

每条 trajectory 一个 cost-labeled step DAG：$G=(V,E,X,C)$。

- 节点 $v\in V$：一次 LLM turn（保留该轮全部 tool calls，不破坏 multi-call 原子性）；
- 边 $E$：直接复用 v6 的 step `dependencies`（prerequisite → dependent）；`NEXT` 单独存为顺序 metadata，**不参与可达性**；
- 属性 $X(v)$：canonical op / 路径角色 / 参数角色 / returncode / observation tokens / diff hash；
- 成本 $C(v)$：§2 挑战 1 的（生成成本，上下文暴露成本）二元标签；
- 结果锚点 $A\subseteq V$：final-patch producing writes + verifier（§2 挑战 2）。

**v9 明确不做**（避免 v7/v8 的膨胀）：
异构 provenance 本体、边类型完备性主张、AND/OR support sets、ILP、Steiner Tree、gSpan、
MDL bit-level 描述长度、任意 shell AST 通用解析、以及仅凭日志声称统计因果识别。

---

## 4. CASC 端到端流程

一个 cycle（复用 v6 的 rollout 与 native-tool runtime，只替换"建样本"与"evolve prompt"）：

```
rollout (v6 RolloutAgent，EVOLVE_TOOLS_MODE=registry)
  → annotate      : v6 依赖标注 + op_type（挑战2的边来源）
  → cost-label    : 给每个 turn 打 context-footprint 成本 (挑战1)
  → anchor+region : 结果锚定 + 反向可达，切出分析区域 (挑战2)
  → mine+contract : 跨轨迹成本加权子图挖掘 + 边界收缩 → 候选工具卡 (挑战3)
  → gate          : replay + held-out non-inferiority (挑战4)
  → evolve        : evolve agent 依据"已验证的工具卡"写 tools.json/executor.py/instruction.md
  → deploy+refuel : 部署 + registry hash 回执 + 采用率统计 → KEEP/REFINE/REMOVE/ADD
```

evolve agent 的输入从"两条超长轨迹"变成一张简短的**工具实现卡**：

```json
{
  "pattern": "FIND -> SEARCH -> READ_LOCAL_CONTEXT",
  "proposed_tool": "locate-symbol",
  "supported_by": {"tasks": 12, "occurrences": 19},
  "inputs": ["symbol", "search_path"],
  "outputs": ["file", "line", "bounded_context"],
  "output_budget": {"max_results": 20, "max_chars": 16000},
  "cost": {"mean_footprint_usd": 0.031, "macro_usd": 0.011, "saving_lcb_usd": 0.011},
  "validation": {"contract_replay": "19/19", "verifier_replay": "19/19"}
}
```

于是**图算法负责"该造什么工具、为什么值得、是否安全"，evolve agent 只负责把已验证的 contract 写成可执行代码**。
这同时解决 §1.5 的 prompt 膨胀（357k→一张卡）与工具随意生成的问题。

多轮 evolve 用同一 registry hash 追踪工具版本，按采用率/成功率/fallback 做 KEEP/REFINE/REMOVE/ADD；
成功证据只来自**本轮真实调用**，evolved tool 的一次调用不能增加其原始 motif 的 support，防止错误自我强化。

---

## 5. 为什么 v9 比 v7 更简单、又比 v6 更有技术性

| 设计维度 | v6 | v7（过度设计） | v9（CASC） |
|---|---|---|---|
| 成本目标 | 隐式 step / 轨迹长度 | 声称 cost-aware，实际退化成 token 加权和、未驱动决策 | **可测的 context-footprint 成本，驱动每一步图决策** |
| 优化终点 | 最后一个 action（→ 一半样本=submit） | outcome anchor + 7 类节点/10 类边本体 | **结果锚定 + 依赖反向可达（一次图操作）** |
| 样本单位 | 单轨迹规则裁剪（退化闭包） | AND/OR + ILP（未落地，空转） | **跨轨迹成本加权频繁子图 + 预算贪心覆盖** |
| 工具来源 | LLM 读日志猜 wrapper | graph-macro（依赖未实现的 solver） | **边界保持收缩 → bounded-output 工具** |
| observation | 只在 evolve prompt 截断，运行时仍长 | 讨论 bounded output，未强制 | **工具 runtime 原生硬预算 + cursor** |
| correctness | 未验证 | 4 级验证仅 Level 0 | **replay + held-out 非劣 + 采用回执** |
| 图技术 | ancestor closure | ILP/Steiner/gSpan/MDL（名词） | **成本加权子图挖掘 + 收缩 + budgeted coverage（真正用得上的最小集）** |

v9 的技术深度来自一个**闭环**：成本标注的 DAG → 结果锚定区域 → 成本加权重复子图 → 边界收缩工具 → 预算选择 → 等价性/非劣验证。
每一步都服务于"少轮次、短 observation、performance 不掉"，而不是为了丰富术语。

---

## 6. 实验设计（简版）

**RQ1**：CASC 是否在 non-inferiority 下降低每题实际 API USD？（主指标：paired cost diff + 置信区间）
**RQ2**：context-footprint 成本 vs step-only，是否更能降低 cached-context 与总 prompt tokens？（直接回应 §1.2）
**RQ3**：跨轨迹成本加权子图 vs v6 单轨迹裁剪，是否提升 tool adoption / success / 净收益？（回应 §1.3–1.4）
**RQ4**：replay + 非劣闸门是否降低回归率、并过滤掉高风险 instruction 规则？（回应 §1.5）

**关键 ablation**：v6 full；v9 但成本只用 turn 计数；v9 但不计 context 暴露；v9 用连续窗口替代依赖连通子图；
v9 去输出预算；v9 去 replay/非劣闸门；instruction-only / tools-only / tools+instruction。

**统计与防泄漏**：train/validation/test 按 repository 隔离，挖掘与阈值只在 train/validation 完成；
报告 success-only cost 与 expected cost per task（防止"更早失败来降本"）；多 seed；预注册 $\epsilon$。

---

## 7. 最小实现路线

1. 在 v6 trajectory 中补齐 per-turn 计费 token、observation token、diff hash、final-patch producing writes（挑战 1/2 的输入）。
2. 实现 context-footprint 成本标注，并先验证它能解释 §1.2 的"少 turns、cost 不降"。
3. 换掉 `contrastive.py` 的 sink：结果锚定 + 反向可达，切出分析区域（挑战 2）。
4. 实现确定性 canonicalizer（先只覆盖现有 native tools + `find/rg/grep/cat/sed -n/head/tail/git`，其余标 `UNKNOWN`）。
5. 实现 size≤5 成本加权连通子图挖掘 + 边界收缩 + budgeted coverage 选择（挑战 3）；先做 read-only 的 search/read/test-log 工具。
6. 实现 replay 等价 + held-out 非劣闸门 + registry hash 回执 + 采用率统计（挑战 4）。
7. 接回 v6 evolver：evolve agent 只消费"已验证的工具卡"，不再吞整条轨迹。

做到第 6 步已是完整算法；第 7 步只是把 contract 编译成当前仓库的 native tools。
第一版即可验证核心假设：**历史中是否存在跨任务复用的高 context-cost `FIND→SEARCH→READ` 模式，
收缩为 bounded-output 工具后能否同时降低 turns 与 cached-context 成本，且不掉 performance。**

---

## 8. 参考工作（仅保留真正用到的）

1. Korel, Laski. [Dynamic Program Slicing](https://www.sciencedirect.com/science/article/pii/0020019088900543). 1988. —— 结果反向切片的思想来源。
2. Acar et al. [A Graph Model of Data and Workflow Provenance](https://www.usenix.org/legacy/events/tapp10/tech/full_papers/buneman.pdf). 2010. —— `ACTION → ARTIFACT → ACTION` 血缘。
3. Cook, Holder. [Substructure Discovery Using MDL](https://arxiv.org/abs/cs/9402102). 1994. —— "用压缩收益而非频率选子图"的思想（v9 用实际 API cost 替代 bit 描述长度）。
4. Wang et al. [Agent Workflow Memory](https://arxiv.org/abs/2409.07429). 2024. —— 从历史轨迹归纳可复用结构（v9 产出的是可执行 bounded 工具，优化目标是 USD）。
5. Li et al. [CODESKILL: Self-Evolving Skills for Coding Agents](https://arxiv.org/abs/2605.25430). 2026. —— 高相关：v9 差异在 context-cost 目标、结果锚定切片、边界收缩与非劣验证，须作核心对比。
