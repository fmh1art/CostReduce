# Evolve V7:面向代码 Agent 成本优化的结果锚定溯源图压缩

## 1. 背景与论文定位

本文档服务于一篇学术论文,研究方向是**基于 agent 自进化算法的成本优化**。

- **Baseline**:ReAct-based code agent,即 `mini-swe-agent`(位于 `agent/mini-swe-agent`)。它交替进行"推理 → 调用工具 → 观察结果",在代码任务上产生 trajectory。
- **目标**:在指定 benchmark 上,**保持成功率基本不变的前提下,降低 API 成本**。
- **Evolve 流程**:
  1. 让 baseline agent 在 code agent task 上跑出 trajectory;
  2. 对 trajectory 的每个 step 标注依赖,得到一个 step-level DAG;
  3. 在该 DAG 上构建 evolve samples,作为 evolve agent 的输入;
  4. evolve agent 据此生成 skills(本框架中即 native function tools:`tools.json` + `executor.py`)和 `instruction.md`,优化 code agent 的后续迭代;
  5. 用优化后的 agent 重新 rollout,验证"表现不变 + 成本下降"。

**v7 是最新的 evolve 框架**。本文档描述 v7 的设计,**并与当前 `evolve_v7.py` 的实现对齐**:已实现的部分详写,未实现的部分明确标注为 roadmap,不混为一谈。

v7 的总名称是 **Cost-aware Outcome-anchored Provenance Graph Compression**(成本感知、结果锚定的执行溯源图压缩),四个词的含义:

- **Cost-aware**:优化目标是 API token 或美元成本,而非 step 数。
- **Outcome-anchored**:图的裁剪从 final patch、测试证据、verifier result 等任务结果出发,而不是从 trajectory 的最后一个 action 出发。
- **Provenance graph**:图同时表示 action、observation、环境状态版本与验证证据之间的来源关系。
- **Compression**:既包括删除不必要节点,也包括把重复子图收缩为一个 macro/tool 节点。

整体优化目标:

$$
\min \; \mathbb{E}[C(\pi)]
\quad
\text{s.t.}\quad
P_{\mathrm{success}}(\pi)
\geq
P_{\mathrm{success}}(\pi_{\mathrm{baseline}})-\epsilon
$$

**直觉**:不是要求有限测试集上成功率数值绝对相等,而是一个**非劣性约束(non-inferiority)**——优化后的 agent 允许在预先声明的很小范围 $\epsilon$(如 1%–2%)内波动,但不能发生有统计意义的性能退化。$C(\pi)$ 是 API 成本,优先用实际 `cost_usd`;无账单时用按模型单价折算的 token 成本近似(见 §5.5 的成本模型说明)。

---

## 2. v6 的问题(v7 要修什么)

### 2.1 v6 在做什么

v6 的 evolve agent 直接写 `tools.json` + `executor.py`(native function tools)。一个 cycle:rollout → 标注 step 依赖 → 反向构造"最小路径"作为 positive sample → evolve agent 据此改写 tools → 校验。

v6 的"最小路径"= `Ancestors(n) ∪ {n, 0}`,其中 $n$ 是**最后一个 action step**;完整 trajectory 作为 negative sample。

### 2.2 三个问题

**问题 1:Sink 选错(最严重,是真实 bug)。** Trajectory 的最后一个 action 经常只是 submit signal、`git status`、summary、重复测试或失败后的环境探查,并不等于任务结果。在仓库当前结果快照中,共扫描到 79 个带 `minimal_step_indices` 的非空 contrastive sample,其中 21 个只保留一个 action;一个 `reward=1`、含 78 个 action 的 SWE-bench trajectory 被裁剪成 `[0, 78]`,而 action 78 仅执行 submit signal。这直接造出无效 positive sample。

**问题 2:"最小路径"名不副实。** 当 `dependencies[i]` 全是强制 AND 依赖时,ancestor closure 是唯一闭包,不存在"选哪条更短路径"的优化决策;当依赖中混有多个可替代信息来源时,v6 又把它们全部保留,得不到真正的 minimum-cost support。因此 v6 那套既不是 critical path,也不是优化求得的 minimal path,只是 **final-action-anchored dependency closure**。

**问题 3:依赖语义被压扁 + positive sample 未验证。** 信息依赖、状态依赖、证据依赖、控制依赖被压进同一个 `dependencies` list,图算法无法区分哪些边必须严格保留、哪些只是替代信息、哪些只解释失败分支。而且从原轨迹删除某些 read steps 后仍保留原 edit action,只证明"固定 action 可被重放",不证明"agent 没看到那些 observation 时仍能自主生成该 edit"。

### 2.3 v7 的应对(概览)

- sink 从 last-action 换成 **outcome anchor**(final patch 的 write + verifier result);
- 把扁平 dependencies 升级为**带类型的溯源图**，并区分边的来源可信度与切片角色;
- 用**成本加权**替代 step 计数;
- 把"positive sample"降级为 `candidate_slice`,通过**分级验证**才升级;
- 从跨轨迹重复子图挖 **macro**,用 MDL 思想选择 tool。

---

## 3. v7 总体流程

v7 复用 v6 的 rollout 与 native-tool runtime(`EVOLVE_TOOLS_MODE=registry`),只替换"构建 sample"与"evolve prompt"两步的内部实现。一个 cycle 实际执行:

```
rollout (v6 RolloutAgent)
   → annotate        (v6 TrajectoryAnnotator:扁平 dependencies + op_type)
   → build samples   (V7SampleBuilder:溯源图 + outcome-anchored slice + macro catalog)
   → evolve          (V7ScriptEvolver + EvolvePromptBuilderV7:compact prompt)
   → refresh         (v6 runtime 重部署 + 校验 tools.json/executor.py)
```

概念上 v7 分 8 个 stage,实现状态如下:

| Stage | 内容 | 状态 | 对应代码 |
|---|---|---|---|
| 0. Rollout instrumentation | 记录 per-turn token/cost、per-write diff hash、test 时 repo state hash | ❌ roadmap(当前 best-effort 从 trajectory 推断) | — |
| 1. Provenance graph construction | 构造带类型的 observed/inferred、support/order 图（当前字段名仍是 `hard`） | ✅ | `ProvenanceGraphBuilderV7._build_graph` |
| 2. Soft dependency annotation | 标注 alternative support sets + 置信度 | ⚠️ 部分(复用 v6 扁平 dependencies,无 alternative) | `TrajectoryAnnotator` + `_support_sets` 回退 |
| 3. Outcome-anchored slicing | hard backward slice + AND-OR 成本优化 | ⚠️ 部分(hard slice ✅;AND-OR 退化成 closure) | `CostAwareSupportSolver` |
| 4. Slice validation | 4 级验证 | ⚠️ 仅 Level 0 | `build()` 中 `validation_level` |
| 5. Cross-trajectory motif mining | 规范化 + 重复子图 | ⚠️ 部分(2–3 连续窗口,非 gSpan) | `MacroMinerV7.mine` |
| 6. Macro selection | MDL gain + registry budget | ✅(简化版 budgeted greedy) | `MacroMinerV7` |
| 7. Evolve tools/instruction | compact prompt + 硬预算 | ✅ | `EvolvePromptBuilderV7` |
| 8. Downstream evaluation | pass rate + cost + non-inferiority | 📋 实验设计(见 §10) | — |

后续章节按 stage 展开,每个 stage 都会标明"已实现"还是"roadmap"。

---

## 4. Provenance 图模型

对每条 trajectory $\tau$,构造一个带属性的异构有向无环多重图 $G_\tau=(V,E,\phi_V,\phi_E,X,C)$:$V$ 节点,$E$ 有向边,$\phi_V/\phi_E$ 类型函数,$X(v)$ 节点属性(命令、文件、状态、token),$C(v)$ 节点成本(无直接 API 成本者取 0)。"多重图"指同一对节点可有不同类型边;通过事件时间与 artifact version 保持无环。

### 4.1 节点类型

| 类型 | 含义 | 关键属性 | 成本归属 |
|---|---|---|---|
| `TASK` | 任务节点:system prompt、task desc、初始 commit | summary | — |
| `TURN` | 一次 LLM 请求及响应 | prompt/cached/completion tokens、api_cost、message | **成本主要落这里** |
| `ACTION` | 一个 tool call(一个 TURN 可产生多个) | tool_family、op_type、files、arguments_summary | — |
| `OBSERVATION` | action 的环境反馈 | chars、returncode、content_hash | — |
| `ARTIFACT_VERSION` | 被读/改文件的一个版本 | path、version、(hash/diff) | — |
| `EVIDENCE` | 验证证据(test/lint/build) | kind、success、validated_state_hash | — |
| `OUTCOME` | 任务结果:`FINAL_PATCH`/`VERIFIER_RESULT`/`SUBMISSION`/虚拟 `SUCCESS` | anchor_mode、pass、reward | — |

**关键改进**:v6 把"含多个 tool call 的一轮"近似成一个 action step;v7 把 `TURN` 与 `ACTION` 分开建模——**API 成本发生在 turn 上,环境状态变化发生在具体 tool call 上**。这样既能把成本精确归因到 turn,又能显式识别 agent 已做的 batching,并分析同一 turn 内 calls 是否冲突。

### 4.2 边类型：这不是“完备枚举”

下表是当前 code-agent runtime 所需的**最小工程 schema**，不是对所有 agent 依赖的完备本体论。新 benchmark（例如 browser/DB agent）可以通过 adapter 增加节点或边；无法归类的关系先记为 `OTHER` 并保留原始 provenance，不强行塞入现有类别。因此，类型表本身仍有人工设计成分；论文应报告 `OTHER` 比例、终点覆盖率和依赖边抽样 precision/recall，而不应声称边类型已经完备。

更重要的修正是：**“能否直接观测”和“切片时是否必须沿该边闭包”是两个维度**。旧版 hard/soft 列把二者混在了一起。

| 边类型 | 方向 | 来源可信度 | 在 outcome slice 中的角色 | 含义 |
|---|---|---|---|---|
| `CONTEXT` | TASK→TURN | observed | support | 任务描述是决策上下文 |
| `DECIDES` | TURN→ACTION | observed | structural | 某轮响应生成该 tool call |
| `RETURNS` | ACTION→OBSERVATION | observed | structural | observation 是 action 的执行结果 |
| `OBSERVES` | OBSERVATION→TURN | inferred | candidate support | 后续决策使用了该 observation |
| `CONSUMES_STATE` | ARTIFACT_VERSION→ACTION | observed/inferred | support | action 读取或依赖某文件版本 |
| `PRODUCES_STATE` | ACTION→ARTIFACT_VERSION | observed | support | write action 产生新文件状态 |
| `VALIDATES` | ARTIFACT_VERSION/ACTION→EVIDENCE | observed | support | 测试在特定代码状态上产生证据 |
| `SUPPORTS_OUTCOME` | PATCH_HUNK/EVIDENCE/FINAL_PATCH→OUTCOME | observed/policy | support | artifact 或证据支持最终结果 |
| `CONTROL_TRIGGER` | OBSERVATION(error)→TURN(retry) | inferred | explanation only | 错误触发 retry 或策略切换 |
| `NEXT` | TURN→TURN | **observed** | **order only** | 两个 turn 在日志中相邻 |

### 4.3 Observed/inferred 与 support/order 分开

- `NEXT` 确实可从日志直接观测，所以它是 hard fact；但它只约束 replay 顺序，不证明前一轮是结果的必要支持。反向切片不沿 `NEXT` 扩展，否则会无条件保留结果之前的所有 turns。
- `OBSERVES` 可能是真正的决策支持边，但它通常需要 LLM 猜测，因此是 inferred candidate，需置信度或后续验证。
- `PRODUCES_STATE` 同时是 observed 和 support，切片时必须保留。

当前 `evolve_v7.py` 已把 `NEXT` 记为 `hard=False`，这个字段实际表示“是否参与 support closure”，而不是“是否可观测”。为避免误解，后续实现应将其拆成 `provenance=observed|inferred` 和 `role=support|order|explanation`。

### 4.4 为什么图仍然是 DAG

文件可能被反复修改,看似"回到旧状态",但 v7 为每次修改创建新的 artifact version:

```
file_v0 -> edit_1 -> file_v1 -> edit_2 -> file_v2
```

即使 `file_v2` 内容与 `file_v0` 相同,它们仍是不同时间的状态节点,不会产生环。Retry 也通过新的 turn/action 节点表示,不连回历史节点。

### 4.5 Turn-call 原子性

一个 TURN 可能一次生成多个 tool calls。v7 为分析 state effects 把它们拆成多个 ACTION,但**从原始 trajectory 抽子轨迹时,不能在不改写模型响应的情况下只删其中一个 call**。因此区分两类输出:

1. **Trace-preserving slice**:保留所有 retained turns 的原始响应内容;保留一个 turn 就保留并执行其全部 actions,可做忠实 replay。
2. **Transform candidate**:允许删同一 turn 中的某个 call、合并多个 turns、或用 macro 替换子图;不再是原 trajectory 的字面子序列,必须记录 transformation 并至少做 state/verifier 验证,涉及"模型能否自主生成新 turn"时还需 counterfactual rollout。

当前 py 产出的 `candidate_kind` 为 `trace_preserving`。

---

## 5. Outcome-anchored 成本感知切片

### 5.1 "causal" 的口径(避免误解)

v7 标题里的 "causal" 指两种**可操作**关系,不是统计学意义上的因果识别:

1. **执行因果关系**:某 action 确实产生了后续使用的文件状态或验证证据;
2. **决策充分性关系**:给定某组历史 observations,agent 有足够信息生成后续 action。

**v7 不声称仅凭 observation logs 就完成因果识别**;soft information edges 只是待反事实验证的因果假设。统计因果识别通常需要干预、随机化或额外假设,而 v7 只有 Level 3 counterfactual rollout 接近真实干预。(本文档只在统一这一处说明,不再重复。)

### 5.2 Outcome anchor 的构造（用一句话先说清楚）

**Outcome anchor 就是“哪些 write 真正产生了最后提交的 patch”。** v6 用最后一个 action，容易选中 submit/git status；v7 改为选最终改动文件对应的 write。

`instrumented` 不是新算法，只表示**在 rollout runtime 中加了日志记录钩子**：每次 write 后记录 step id、changed files 和 diff hash，结束时就能直接知道哪些 steps 产生了 final diff。当前 runtime 没有这些字段，所以 py 只能用后两种 fallback。

当前 `evolve_v7.py::_outcome_anchors()` 的实际逻辑可简化成：

```text
if 日志已直接记录 final-diff 来自哪些 write steps:   # instrumented，尚未实现
    anchors = 这些 write steps
elif 轨迹里有 git diff/status 输出:                       # 当前主路径
    changed_files = 从输出中用正则解析文件名
    anchors = 每个 changed file 的最后一次成功 write
else:                                                        # 最后退路
    anchors = 每个被修改文件的最后一次成功 write
```

然后再处理测试证据：有 benchmark 外部 verifier 时，不强制保留轨迹内部 test；没有外部 verifier 时，在 anchor 之后保留最后一个成功 verify。所以最终终点只是：

$$Terminals = \{\text{producing writes}\} \cup \{\text{required successful test}\}.$$

虚拟 `SUCCESS` 节点只是为了让反向遍历有一个统一起点，不代表又执行了一个 agent step。对问答类任务，benchmark adapter 需要把最终答案定义为 terminal，而不是强行找 patch。

⚠️ 当前 mode 2/3 仍是 heuristic：它们可能把临时/后来被覆盖的 write 当成 anchor。而且 `eligible_for_evolve` 暂时没有对 fallback anchor 降权。因此它们只是“比 last action 更好的工程修补”，不是已解决的技术贡献。

### 5.3 第一阶段：Support backward slice（已实现）

从 `SUCCESS` 沿参与 support closure 的边反向遍历（不沿 `NEXT`）:

$$
S_{\mathrm{hard}}=\mathrm{Ancestors}_{\mathrm{hard}}(SUCCESS)\cup\{SUCCESS\}
$$

该集合保证保留:final patch 的状态来源、产生相关 artifact 的 write actions、outcome policy 要求的 correctness evidence、对状态重放必需的 setup actions。**这一阶段不依赖 LLM dependency annotation,因此比 v6 的 final-action closure 更可靠。**

### 5.4 第二阶段:AND/OR 信息支持(roadmap 的完整版)

对 hard slice 中每个需要模型决策的节点 $v$,标注器输出 alternative support sets,而非扁平 list:

```json
{
  "target": "turn_9",
  "hard_predecessors": ["file_v2"],
  "support_sets": [
    {"nodes": ["obs_3"], "confidence": 0.86, "reason": "obs_3 directly locates the buggy function"},
    {"nodes": ["obs_5", "obs_6"], "confidence": 0.72, "reason": "the two observations jointly identify the same function"}
  ]
}
```

含义:`file_v2` 是不可替代的状态前驱;信息方面可只保留 `obs_3`,或同时保留 `obs_5`+`obs_6`;不需三者全留。一个 set 内部是 AND,多个 sets 之间是 OR;一个 OR 分支内部仍可含多个 AND dependencies。JSON 中 `support_group` 标识某个 AND set,把多条普通 edges 组合成一条逻辑 hyperedge。

### 5.5 Minimum-cost support subgraph（可选 roadmap；当前 py 没有跑 ILP）

这一节想解决的其实只是一个问题：**当同一个 edit 有多组可替代的信息来源时，选哪一组最便宜？**

例如，edit 前要么保留“search + read source”，要么保留“read source + read test”。两组都能支持 edit，一组内的步骤必须一起留（AND），两组之间只选一组（OR）。Minimum-cost support subgraph 就是在所有合法 AND/OR 选择中，找总 token/USD 成本最低的那个子图。

ILP（integer linear programming，整数线性规划）只是一种**可选求解器**：用 0/1 变量表示“保留/不保留某节点”与“选/不选某支持组”。它不是 v7 当前实现的必要组成，更不是为了增加技术名词而必须引入的模块。当前 annotator 没有产出 OR alternatives，因此根本无选项可优化；`evolve_v7.py` 实际使用 beam search，且在当前数据上退化为普通 ancestor closure。所以下面的 ILP 只是“未来如果真有 alternative support sets，可如何精确求解”的形式化说明。

定义二进制变量:$x_v=1$ 表示保留节点 $v$;$z_{v,k}=1$ 表示为节点 $v$ 选第 $k$ 个 support set。

$$
\min
\sum_{v\in V} c_v x_v
+\lambda\sum_{v,k}(1-q_{v,k})z_{v,k}
+\mu R(x)
$$

- $c_v$:节点 API 成本。第 $i$ 个 turn 的成本 $c_i = p_{in}(T^{prompt}_i-T^{cached}_i)+p_{cache}T^{cached}_i+p_{out}T^{completion}_i$($p_{*}$ 为每 token 单价);有可靠 `cost_usd` 时优先用实际值。
- $q_{v,k}\in[0,1]$:support set 的标注置信度;第二项惩罚低置信分支。
- $R(x)$:风险项,如删除 write 前的关键 read、跳过所有验证、依赖失败 action;$\mu$ 控制风险权重。

主要约束:

- terminals 必须保留:$x_r=1,\;\forall r\in Terminals$
- evidence coverage(无外部 verifier 时):$\sum_{e\in Evidence(g)} x_e \ge b_g$(通常 $b_g=1$;有可靠外部 verifier 时可令 $b_g=0$ 删冗余内部测试)
- trace-preserving 原子性:$x_a=x_u,\;\forall a\in EmittedActions(u)$
- 选节点必留其 hard 前驱:$x_h\ge x_v,\;\forall h\in HardPred(v)$
- 选需信息支持的节点必至少选一个 support set:$\sum_k z_{v,k}\ge x_v$
- 选 support set 必留其中所有节点:$x_u\ge z_{v,k},\;\forall u\in S_{v,k}$
- 未选目标节点时不选其 support set:$z_{v,k}\le x_v$

**直觉**:在"必须保留 terminals、必须保留状态前驱、每个决策点至少挑一组信息支持"的前提下,挑总成本最低的子图;$\lambda$ 让低置信替代方案只有在足够便宜时才被选,$\mu$ 阻止危险删除。

**Worked example**:一条 6-turn 轨迹

| Turn | 操作 | op_type | 成本 |
|---|---|---|---|
| T1 | search "Foo" | read | 2 |
| T2 | read src/a.py | read | 3 |
| T3 | read tests/test_a.py | read | 2 |
| T4 | edit src/a.py | write | 4 |
| T5 | pytest tests/test_a.py | verify | 5 |
| T6 | git status | explore | 1 |

anchors={T4}(产生 final patch),evidence={T5}(无外部 verifier,取 anchor 后最后一个成功 verify),terminals={T4,T5}。不同方法的结果:

| 方法 | 保留节点 | 删除 | 成本 | 说明 |
|---|---|---|---|---|
| v6(last-action sink, $n$=T6, deps={}) | {T0,T6} | T1–T5 | 1 | **§2.2 的 bug**:只剩 git status |
| v7 hard slice | {T4,T5} | T1,T2,T3,T6 | 9 | 丢了决策所需的 reads(不完整) |
| v7 当前(flat AND,T4 deps={1,2,3}) | {T1,T2,T3,T4,T5} | T6 | 16 | 退化成 closure |
| v7 roadmap(AND-OR,T4: A={1,2}@0.86 / B={2,3}@0.72) | {T1,T2,T4,T5} | T3,T6 | 14 | OR 选更便宜的等价支持 |

这个例子展示了三件事:v6 的 sink bug(可能只留 submit);v7 的 anchor 修正;以及**当前退化版与 roadmap AND-OR 的差距——正是 OR 替代选择让 T3 可被安全删除**。

### 5.6 当前实现状态

`CostAwareSupportSolver` 是 beam-search K-best 求解器,但 `support_sets` 全仓库只有 `evolve_v7.py` 在**读**、没有任何模块在**写**(annotator 只产扁平 `dependencies`)。于是 `_support_sets()` 永远走 `flat_dependency_compat` 回退(单个 AND option),**求解器退化成确定性 outcome-anchored closure**——等价于只实现到 §5.3 的 hard slice,再补上扁平依赖闭包。OR 选择、$\lambda$/$\mu$ 惩罚要等 §5.4 的 alternative support-set 标注上线才生效;K-best 机制已就位但暂无 alternative 可比。

### 5.7 为什么不直接 shortest path / 与 Steiner Tree 的关系

Shortest path 只保留一条 source→sink 路径,但代码任务常需多分支:

```
read implementation --+
                      +--> construct correct patch
read tests ----------+
```

只选其一会破坏 AND 依赖,因此不适合求最小 execution subgraph。

与 Directed Steiner Tree 的共同点:都有带成本的候选节点、都要求连接一组 terminals、都希望低成本连接子图。区别:标准 Steiner Tree 用普通路径表达连接,而 v7 的一个 action 可能同时需要多个 inputs(AND hyperedge);v7 有多个可替代 support sets(OR);v7 包含 artifact version 与 correctness evidence,不只是拓扑连通;v7 的解必须通过 replay/diff/verifier 验证。因此可把 ILP/Steiner/shortest-hyperpath 当求解技术,但 v7 的贡献是**图语义 + 成本目标 + 验证机制**的组合,不是新 Steiner 算法。

---

## 6. Slice 验证

### 6.1 为什么必须验证

图优化只保证解满足图模型中的约束。如果图缺边或 soft dependency 标错,得到的 slice 仍可能错误。因此算法输出首先叫 `candidate_slice`,不能直接叫 positive sample。

### 6.2 四级验证

- **Level 0 Structural**:terminals 齐全、hard dependencies 闭合、artifact version 顺序合法、不存在"test 验证旧状态却支持新 patch"。→ `structurally_valid`
- **Level 1 State replay**:在 clean repo 中按拓扑顺序重放 slice 中的 state-changing actions,比 final diff hash / changed files / setup state。→ `state_valid`。注意:只证明固定 actions 可重放,不证明 agent 能自主生成。
- **Level 2 Verifier**:在重放后的代码状态上运行 evidence policy 选中的 targeted tests 或 benchmark verifier。→ `verifier_valid`。测试策略优先用 baseline 已通过且与修改相关的 tests,避免全量测试让 sample 构造成本失控。
- **Level 3 Decision sufficiency**:反事实 agent rollout——只向 agent 提供 slice 中保留的信息,检查它是否仍能产生等价 patch 并通过 verifier("等价"= final diff 完全一致,或 verifier 通过且修改范围未异常扩大)。→ `decision_sufficient`。最强但最贵,可只在训练子集或 top candidates 上执行。

### 6.3 当前实现状态

⚠️ **仅 Level 0 实现**:py 中 `validation_level` 只取 `structurally_valid / unvalidated`,而 `state_replay_valid / verifier_replay_valid / decision_sufficient` 全部硬编码 `None`。

分级 positive sample 的升级策略是 roadmap:`structurally_valid`→只用于图诊断;`state_valid`→tool/state macro 候选发现;`verifier_valid`→高置信 contrastive sample;`decision_sufficient`→最高质量 evolve sample。

---

## 7. Cost-aware Graph Macro Mining

### 7.1 为什么单条 pruning 不够生成 tool

删除一条 trajectory 中未被依赖的节点,只说明某些操作可能不必要;它不直接说明该建什么可复用 tool。一个 tool 值得加入 registry 通常需同时满足:相似多步模式跨任务重复、每次都产生较高 API/observation 成本、模式有稳定输入输出与状态效果、不过度 task-specific、收益大于 schema 长期占 prompt 的成本。这些条件更适合跨轨迹图挖掘建模。

### 7.2 图标签归一化(canonicalization)

直接用完整 command 和 path 会让语义相同的操作被判为不同。挖掘前先归一化:

```
rg "Foo" src/a.py
rg "Bar" lib/b.py
```

可归一化为 `{"tool_family":"search","query_role":"symbol","path_role":"source_subtree","op_type":"read"}`。路径抽象为 repo root / source subtree / test subtree / config file / changed file / unknown path;参数抽象为 symbol、error message、test name 等 semantic role(参数在任务中的功能,而非原始字符串)。

### 7.3 候选 motif 生成

- **完整版**:gSpan 等 frequent subgraph mining;inexact matching 允许节点标签受控差异(不同文件路径仍被识别为同一 search-read pattern)。
- **当前实现**:固定 size 2–3 的**连续窗口**(`turns[start:start+size]`),用 canonical 后的 `op:tool_family` signature 串做 key,要求 `support(trajectories)≥2 或 occurrence≥3`。

⚠️ **局限**:连续窗口找不到非相邻重复模式(如 search → ... → read → edit 中,search+edit 这个 motif 会被中间节点打断)。升级到 gSpan + inexact 是 roadmap。

### 7.4 Macro contract

每个候选 motif 必须转化为显式 contract:

```json
{
  "name": "locate-and-read-symbol",
  "inputs": {"symbol": "string", "path": "string"},
  "outputs": {"matches": "bounded text with file and line context"},
  "preconditions": ["path exists"],
  "state_effects": [],
  "failure_behavior": "return nonzero with a bounded error message"
}
```

preconditions 是调用前必成立条件;state_effects 是对 repo state 的修改;failure_behavior 是参数错误/执行失败的稳定返回约定;bounded output 表示输出有明确上限,避免工具自身制造长 observation。py 的 `_contract_hint` 给出简化版(inputs/output/preconditions/state_effects/tool_families)。

### 7.5 MDL-style cost gain

对 motif $m$ 定义:

$$
Gain(m)=
\sum_{o\in Occ(m)}
\left[C(G_o)-\widehat{C}(m,o)\right]
-\alpha L_{schema}(m)
-\beta R_{failure}(m)
-\gamma R_{specificity}(m)
$$

- $Occ(m)$:motif 的 occurrence 集合;$G_o$:一次原始多步 occurrence;$\widehat{C}(m,o)$:用 macro 执行该 occurrence 的估计成本。
- $L_{schema}(m)$:tool name/description/JSON schema 被反复加入 prompt 的 token 开销。
- $R_{failure}(m)$:历史失败风险;$R_{specificity}(m)$:惩罚过度绑定特定 repo/path/framework 的工具。
- $\alpha,\beta,\gamma$:超参,在 held-out evolve cases 上选择。

**直觉**:只有当"用 macro 执行所有 occurrence 省下的成本"超过"schema 反复进 prompt 的开销 + 失败风险 + 过度特化惩罚"时,macro 才值得保留。这借用了 MDL 的模型选择思想(一个模式只有在"描述模式本身的代价 + 用它压缩数据后的代价"小于直接描述原始数据时才值得保留),但把 bit-level 描述长度替换为实际 API cost + tool prompt overhead。

**Worked example**:motif `search-symbol -> read-file`(size 2),出现在 12 条 trajectory、共 19 次 occurrence。每次 occurrence 原成本约 \$0.03(2 turns),用 macro 执行约 \$0.012(1 turn + bounded output):

- gross saving = $19 \times (0.03-0.012) = \$0.342$
- $L_{schema}$ = 55 + 2×22 = 99 tokens,投影到 100 个 registry turn × input price = overhead 成本
- net gain = gross − overhead − failure risk − specificity penalty

对照 py 的 `MacroMinerV7`:`schema_tokens = 55 + size×22`;`estimated_saving = original_cost - window[0].cost`(粗略假设 macro 成本=窗口首步成本,即上式 $\widehat{C}(m,o)\approx C(\text{window}[0])$)。这是 §9 列出的已知简化之一。

### 7.6 Macro selection

不能把所有正收益 motif 都加入,因为 tool descriptions 持续占 prompt、多个 motif 可能覆盖同一批 steps、相似 tools 增加选择难度、registry 过大会抵消节省的成本。定义 $y_m$ 表示是否选 motif $m$:

$$
\sum_m L_{schema}(m)\,y_m\le B_{registry}
$$

可用 weighted set packing(选互不重叠且总收益最大的 occurrence 集合)、budgeted maximum coverage(预算内覆盖尽量多高成本 steps)或 ILP 同时表示 registry/overlap/数量约束。

**当前实现:budgeted greedy coverage**——重叠 occurrence 只对未覆盖的 turn 计边际收益,按 `边际收益 / schema_tokens` 排序贪心选,直到 registry 预算($B_{registry}$,默认 1200 tokens)或 `max_candidates`(默认 8)用完;每选一个就把它覆盖的 step_keys 加入 `covered` 集合。

### 7.7 Graph contraction sample

选定 motif 后,不再把"删除后的原 trajectory"直接作为 positive,而是构造显式收缩样本:

```json
{
  "type": "graph_macro",
  "negative_subgraph": {"nodes": [], "edges": []},
  "positive_macro": {"contract": {}, "external_inputs": [], "external_outputs": [], "state_effects": []},
  "support": {"trajectory_count": 12, "occurrence_count": 19},
  "cost": {"observed_original_usd": 0.42, "estimated_macro_usd": 0.11, "schema_overhead_tokens": 86},
  "validation": {"state_replay_rate": 1.0, "verifier_pass_rate": 0.95}
}
```

evolve agent 的工作从"阅读日志并猜一个工具"变为"根据已有 contract、成本证据和验证结果实现工具"。

---

## 8. Evolve prompt 与 sample 结构(已实现)

### 8.1 Compact prompt

`EvolvePromptBuilderV7` 用硬预算控制 prompt 膨胀(v6 的 prompt 随 cycle 增长是一大成本来源):

- `V7_MAX_PROMPT_CHARS = 32000`:prompt 字符硬上限。
- `V7_MAX_STEPS_PER_SAMPLE = 8`:每条 sample 最多展示 8 个 outcome-support step;**outcome anchor 与 evidence terminal 不受此配额影响**(必展示)。
- `V7_EVOLVE_CASES_PER_PROMPT = 4`:每个 evolve prompt 默认放 4 条 sample。
- 完整 provenance graph 仍保存在磁盘 sample 中,prompt 里只放摘要。

结构:`HEADER`(工具契约规则)→ current registration files(`tools.json`/`executor.py`/`instruction.md`,各自截断)→ 每条 sample 的 evidence block → `FOOTER`(校验指令)。每条 evidence block 包含:eligible/verifier_pass/write_anchors/validation_level/anchor_mode/turn_count、成本(original/selected/saving/ratio)、outcome-support slice steps、removed-cost aggregate、selected graph-macro evidence、safe batching candidates、repeated failure motifs。当 sample 不 eligible 时,只给 failure motif 作为 instruction 证据,并明确禁止从中学 tool/patch 策略。

`instruction.md` 仍限制 ≤25 行 tool-agnostic 规则,只在 batching/failure 证据有重复支持时才加规则。

### 8.2 与 v6 prompt 的对比

`prompt-dry-run` 子命令(`build_prompt_comparison`)可量化对比 v6/v7 prompt:字符数、行数、按字符/4 估计的 token 数,以及 `v7_to_v6_character_ratio` / `character_reduction` / `estimated_token_reduction`。v6 prompt 序列化完整 positive/negative trajectory;v7 只放结构化 evidence + 硬预算截断。

### 8.3 产出的文件

每条 trajectory 产出 `provenance_graph_v7.json`(完整图,磁盘保留)+ `v7_evolve_sample.json`(摘要 + slice + macros);整个 run 产出 `v7_macro_catalog.json`(跨轨迹选定的 macros + cost_model + failed_trajectories)。

---

## 9. 已实现 vs Roadmap 对照

### 9.1 对照表

| 模块 | 已实现 | Roadmap |
|---|---|---|
| Outcome anchor | git-diff 正则解析 / last-write-per-file fallback | Stage 0 instrumentation 产出精确 `write_steps` |
| Provenance 图 | hard 边 + soft 边(来自扁平 deps) | per-write snapshot、文件访问记录自动获得 CONSUMES/PRODUCES |
| Soft 标注 | 复用 v6 扁平 `dependencies`(单 AND option) | alternative support sets + 置信度 + reason |
| Slicing 求解 | 退化成 outcome-anchored closure(beam/K-best 空转) | AND-OR ILP 真正比较替代支持;λ/μ 生效 |
| 验证 | 仅 Level 0 structural | Level 1 replay / Level 2 verifier / Level 3 counterfactual |
| Motif 挖掘 | size 2–3 连续窗口 + 粗估成本 | gSpan + inexact matching + 精确 $\widehat{C}(m,o)$ |
| Macro 选择 | budgeted greedy coverage | weighted set packing / ILP |
| Evolve prompt | compact + 32k 硬预算 | — |
| 评估 | — | §10 的 RQ1–RQ4 + ablation |

### 9.2 已知简化与待改进点

1. **AND-OR 退化**:`support_sets` 无人写,求解器等价于 closure。要让 §5.5 的 ILP 真正生效,需先实现 alternative support-set annotator。
2. **仅 Level 0 验证**:`state_replay_valid` 等字段硬编码 `None`,positive sample 分级升级未落地。
3. **Anchor 的 best-effort 与未降权**:`observed_git_change_summary` / `best_effort_last_successful_write_per_file` 是 heuristic,而 `eligible_for_evolve` 对三种 anchor mode 同等对待。应至少在 best-effort mode 下降权或标注。
4. **Macro 成本估计粗糙**:`estimated_saving = original_cost - window[0].cost` 假设 macro 成本=窗口首步;应替换为实测或更细的 $\widehat{C}(m,o)$。
5. **成本模型可能退化**:`PriceModel.from_config` 在 config 无 pricing 时回退到 `weighted_tokens`(in=1/cache=0.1/out=3),此时 cost 是 token 加权和而非真实 USD——可用于相对比较,但主指标仍应是真实 USD。

### 9.3 Roadmap 优先级

1. **Stage 0 instrumentation**:让 anchor 精确、让 per-write state 可重放(解锁 Level 1 验证)。
2. **alternative support-set annotator**:让 AND-OR 真正生效(解锁 §5.5 的核心贡献)。
3. **Level 1–2 验证**:state replay + verifier(让 positive sample 可信)。
4. **gSpan motif + 精确成本**:补齐 macro mining。
5. **Level 3 counterfactual rollout**:最强验证,最后做。

这个顺序避免在 provenance graph 尚不可靠时过早投入复杂图搜索算法。

---

## 10. 实验设计与评估

### 10.1 Research questions

- **RQ1:Outcome anchor 是否改善 positive sample 正确性?** 比较 v6 final-action closure / final-write anchor / v7 artifact-evidence anchor。指标:state replay pass rate、verifier preservation rate、单 action 异常 slice 比例。
- **RQ2:AND-OR cost-aware slicing 是否优于 hard closure?** 比较 hard ancestor closure / flat LLM dependencies / AND-OR ILP / K-best+validation。指标:**cost compression ratio** $=C(\text{validated slice})/C(\text{original trajectory})$(越低压缩越大)、invalid slice rate。
- **RQ3:Graph motif selection 是否产生更有效的 tools?** 比较 v6 LLM-only tool generation / frequency-only motif / MDL-style cost gain / MDL+validation+registry budget。指标:tool adoption rate、tool success rate、actual API cost saving、pass rate。
- **RQ4:成本降低是否满足 correctness non-inferiority?** 在相同 cases 上做 paired evaluation(baseline 与 evolved agent 跑同一组任务以减少任务难度噪声),报告 pass-rate difference 的置信区间与每题 API cost difference。

### 10.2 Ablation

至少包含:去 artifact nodes / 去 evidence terminals / 去 soft-edge confidence / AND-OR 改 flat dependencies / 去 slice validation / motif selection 去 schema overhead / 去 specificity penalty / instruction-only / tools-only / tools+instruction。

### 10.3 评估指标

主指标:**实际 API USD 或按模型单价折算的 token cost**(step 数仅辅助,见 §11.4)。配套:uncached/cached/completion tokens、agent turns、tool call 数、tool success rate、tool registry prompt tokens、wall-clock time(辅助)。

### 10.4 避免数据泄漏

train split(图构造 + motif mining + tool evolution)/ validation split(选 $\lambda,\mu,\alpha,\beta,\gamma$ 与方法版本)/ test split(只做最终下游评估)严格分离。对 SWE-bench 进一步按 repository 划分,验证跨 repo 泛化,避免工具只记住某个项目结构。抽取一小部分 trajectory 做人工双人标注,评价 hard edge precision/recall、soft support-set precision、terminal coverage、op_type accuracy、annotator agreement。

---

## 11. 定位与相关工作

- **vs PDG / Dynamic Program Slicing**:借鉴"显式依赖表示 + 从结果 backward traversal + 分析一条实际执行而非枚举全部可能"。区别:分析对象是 LLM decisions/tool calls/repo state 而非程序语句;LLM 信息依赖不可完全观察,需标注 + 反事实;节点带 API cost、目标是降本;需把重复子图进一步转化为 agent tools。更准确的说法:v7 是一种**面向 LLM agent 的、带成本与不确定信息依赖的 outcome-anchored dynamic slicing**。
- **vs Workflow Provenance**:借鉴 `ACTION → ARTIFACT_VERSION → ACTION`。区别:code agent 步骤在线生成;除 artifact lineage 还要表示 observation 对 LLM decision 的 epistemic influence;provenance 用于 cost-aware slicing / tool synthesis / agent evolution,而非仅审计或可复现。
- **vs Frequent Subgraph Mining / MDL(gSpan / SUBDUE)**:借鉴"发现重复 pattern + 用压缩收益而非仅频率 + 把子图替换为 macro 节点"。区别:节点边带 code-agent 语义;模式需参数化(不保留 task-specific path);压缩收益用实际 API cost + tool prompt overhead;macro 须生成可执行 contract 并通过下游 rollout 验证。
- **vs Agent Workflow Memory / AFlow**:前者从历史 trajectory 归纳可检索文本 workflow,v7 则显式构造 artifact-aware 溯源图、优化目标是 API cost、产物是可执行 tool+instruction 而非可检索 memory;AFlow 在 workflow program space 用 MCTS 搜索,v7 先分析真实执行溯源,显式建模 dollar cost + registry overhead + non-inferiority。
- **vs CODESKILL(高度相关,须重点对比)**:CODESKILL 从 trajectory 文本抽多粒度 procedural skill,用 learnable management policy 维护 skill bank,目标是 skill 质量/成功率。v7 区别:中间表示是 typed 溯源图(非直接从文本抽 skill);用 outcome-anchored slicing 处理无效步骤与错误 sink;用 graph motif + MDL-style cost gain + registry budget 显式选 tool;**主优化对象是 API cost under non-inferiority**;对每条压缩 sample 区分 state/verifier/counterfactual validity。由于与"coding agent 自进化 skills"高度重叠,论文须把上述差异作为核心方法与实验对比,不能只把"生成 skills"当创新点。

---

## 12. 结论边界

1. **不宣称全局最优 trajectory**:v7 找到的是"在已观测 actions、已构建 provenance graph、给定 support candidates 与目标函数下的 minimum-cost validated subgraph",不搜索所有可能的新 action 序列,因此不是全局最优 agent policy。
2. **replay ≠ 决策充分性**:state replay 证明固定 actions 可复现 final state;只有 counterfactual rollout 才更有力地说明删 observation 后 agent 仍能产生正确 action。
3. **soft edges ≠ ground-truth causality**:soft edges 是带置信度的假设,须报告人工标注质量或反事实验证结果,不能默认一次 LLM 标注就是 ground truth。
4. **step reduction ≠ API cost reduction**:一个新 tool 可能减少 turns,但 schema 长、输出大,最终 token 成本反增。主指标必须是实际 USD 或按价格折算的 token cost,step 数仅辅助。
5. **不用绝对"性能不变"表述**:有限 benchmark 只支持带置信区间的 non-inferiority 结论,应预先声明 $\epsilon$、统计方法与失败处理方式。

---

## 13. 参考工作

1. Ferrante, Ottenstein, Warren. [The Program Dependence Graph and Its Use in Optimization](https://bears.ece.ucsb.edu/class/ece253/papers/ferrante87.pdf). 1987.
2. Korel, Laski. [Dynamic Program Slicing](https://www.sciencedirect.com/science/article/pii/0020019088900543). 1988.
3. Acar et al. [A Graph Model of Data and Workflow Provenance](https://www.usenix.org/legacy/events/tapp10/tech/full_papers/buneman.pdf). 2010.
4. Yan, Han. [gSpan: Graph-Based Substructure Pattern Mining](https://experts.illinois.edu/en/publications/gspan-graph-based-substructure-pattern-mining/). 2002.
5. Cook, Holder. [Substructure Discovery Using Minimum Description Length and Background Knowledge](https://arxiv.org/abs/cs/9402102). 1994.
6. Wang et al. [Agent Workflow Memory](https://arxiv.org/abs/2409.07429). 2024.
7. Zhang et al. [AFlow: Automating Agentic Workflow Generation](https://arxiv.org/abs/2410.10762). 2024.
8. Li et al. [CODESKILL: Learning Self-Evolving Skills for Coding Agents](https://arxiv.org/abs/2605.25430). 2026.
