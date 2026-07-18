## 1. Introduction

### 1.1 LLM agent 及其高昂成本

大语言模型(LLM)agent 已成为自动化软件工程的主流范式。以 ReAct 为代表的 code agent(如本文 baseline `mini-swe-agent`)在"推理 → 调用工具 → 观察结果"的循环里反复迭代,直到提交补丁或耗尽预算。这种多轮、长上下文的工作方式让 agent 能在陌生代码库中自主定位、修改和验证代码,但也让它的 API 成本远高于单轮问答。

成本高在两点。**第一,轮次多。** 一个真实代码任务常常要几十到上百次 LLM 调用。**第二,observation 不断累积在上下文里。** 每次工具返回的结果(读文件、跑测试、列目录)一旦进入对话历史,就会在之后每一轮 prompt 里被反复携带。所以一次读文件的真实代价,不只是产生它的那一轮,而是它在后续所有轮次里的累计暴露。轮次越多、观察越长,上下文就越大,每一轮的账单也越贵——两者互相放大。

### 1.2 现有方案的局限:trajectory 压缩既破坏 cache 又损失信息

面对成本问题,当前最主流的做法是 **trajectory 压缩**:当历史太长时,用摘要、滑动窗口或选择性丢弃把早期内容压短再喂回模型。虽然他能暂时降低输入的token数，但是并不能真正减少api cost。

原因有两个。**第一,它破坏 cache。** 当对前面消息进行压缩后，前缀的cache机制完全失效，而baseline的trajectory的命中率往往在 99% 以上。**第二,它有损且不可逆。** 被压掉的细节(某个函数签名、某行报错、某个路径)事后往往正是需要的,agent 却再也取不回来,只能基于失真的历史继续,导致重复探索甚至失败，大大增加了轮次。

### 1.3 本文方案:基于自进化算法的成本优化框架

本文提出一个**基于 agent 自进化(self-evolve)的成本优化框架**。在**进化阶段**改造 agent 自身:先让 baseline agent 在代码任务上跑出轨迹,再从跑出的轨迹中找到昂贵切重复的模式，通过优化agent的harness来让code agent在后续阶段避免掉这些昂贵重复的模式，从根本上让agent具有成本优化的能力。
自进化能解决压缩解决不了的问题在于**它在源头减负,而不是事后压缩。并且不破坏code agent的cache机制**。进化的本质是从已有的trajectory中提取成本优化的经验，后续基于这些经验，agent在保证performance的同时，采取成本优化的方案一步步去完成任务。

### 1.4 技术难点与解法

要把这个想法做成可靠的框架,有两个本质的技术难点。

**难点一:如何找到真正值得优化的昂贵操作？** Agent 的轨迹中包含读文件、搜索代码、运行测试等大量操作。一个操作的成本不仅来自当轮输出，还来自其内容在后续上下文中的反复出现。同时，高成本操作不一定是多余的，它也可能包含解决任务所需的关键信息。

**难点二:如何在自进化闭环里降本,同时保证正确性?** 简单减少工具调用或缩短输出，可能让 agent 遗漏重要信息，从而降低任务成功率。

为解决上述两个技术挑战，本文提出一个成本感知的 agent 自进化框架。

针对第一个挑战，框架结合任务依赖关系和 observation 的累计上下文成本，从完整轨迹中识别真正昂贵且非必要的操作，并进一步寻找能够跨任务复用的高成本模式。

针对第二个挑战，evolve agent 根据这些模式修改 code agent 的 harness，将频繁出现的多步操作合并为更高效的工具调用，并加入少量通用行为策略。每个候选 harness 都需要重新评估；只有当任务成功率基本不下降且按 token 用量与锁定配置单价计算的 cost 降低时，候选版本才会被保留，否则恢复上一版本。

# 2. Method

## 2.1 Problem Formulation

设 $h$ 为 agent harness。给定任务 $x$，agent 产生包含 $T$ 个 LLM step 的轨迹：

$$
\tau(x,h)=\{s_t\}_{t=0}^{T-1}.
$$

一个 step 对应一次模型输出。第 $t$ 个 step 可以同时发起 $m_t$ 个 tool call：

$$
s_t=\left(q_t,\{(a_{t,j},o_{t,j})\}_{j=1}^{m_t}\right),
$$

其中 $q_t$ 是模型输出，$a_{t,j}$ 是第 $j$ 个工具调用，$o_{t,j}$ 是对应 observation。所有 observation 会在下一次 LLM 调用前加入历史。

在任务集合 $D$ 上，定义成功率和可复算的 token-price cost：

$$
P(h;D)=\frac{1}{|D|}\sum_{x\in D}y(x,h),\qquad
C(h;D)=\frac{1}{|D|}\sum_{x\in D}c(x,h).
$$

本文假设 trajectory 采用稳定前缀缓存。设 $p_{\mathrm{new}}$、$p_{\mathrm{hit}}$ 和 $p_{\mathrm{out}}$ 分别为新输入、cache-hit 输入和输出 token 的价格。若第 $k$ 次模型调用包含 $N_k^{\mathrm{new}}$ 个新输入 token、$N_k^{\mathrm{hit}}$ 个缓存 token 和 $N_k^{\mathrm{out}}$ 个输出 token，则：

$$
c(x,h)=\sum_k\left(
p_{\mathrm{new}}N_k^{\mathrm{new}}
+p_{\mathrm{hit}}N_k^{\mathrm{hit}}
+p_{\mathrm{out}}N_k^{\mathrm{out}}
\right).
$$

目标是在性能约束下最小化成本：

$$
\min_h C(h;D),\qquad P(h;D)\ge P(h_0;D)-\epsilon.
$$

## 2.2 Correspondence to the Two Challenges

| 技术挑战 | 方法 |
| --- | --- |
| 找到真正值得优化的昂贵操作 | tool-call 级依赖关键性 + cache-aware observation 成本 + 跨轨迹模式聚合 |
| 降本同时保证正确性 | 相同任务上的 paired evaluation + 真实性能与 token-price cost 约束 + 回滚 |

第一个模块提出优化目标，evolve agent 将其转化为候选 harness，第二个模块决定候选是否保留。

## 2.3 Challenge I: Cost-aware Pattern Discovery

### 2.3.1 Cache-aware Future Cost

设 observation $o_{t,j}$ 含有 $n_{t,j}$ 个 token，并令 $L_t=T-t-1$ 表示它还会进入多少次后续 LLM 调用。在稳定前缀缓存下，它第一次进入下一轮 prompt 时按新输入计价，之后按 cache-hit 价格计价。因此：

$$
\hat c_{t,j}=
\begin{cases}
0, & L_t=0,\\
n_{t,j}\left[p_{\mathrm{new}}+(L_t-1)p_{\mathrm{hit}}\right], & L_t\ge1.
\end{cases}
$$

同一 step 的 observation 成本为：

$$
\hat C_t=\sum_{j=1}^{m_t}\hat c_{t,j}.
$$

这里的未来调用数按 LLM step 计算，而不是把同一 step 内的多个 tool call 当成多个轮次。价格从模型配置读取；token 数优先使用 tokenizer，字符数估计仅作为后备。该归因用于发现候选；最终验收也只使用实际 usage 中的输入、cache-hit 和输出 token 乘以锁定配置单价得到的成本。接口返回的 `cost_usd`（包括值为 0 的情况）不参与计算。

### 2.3.2 Dependency Criticality

依赖图以 tool call 为节点：

$$
v_{t,j}=(t,j,a_{t,j},o_{t,j}).
$$

若后续推理或工具调用使用了 $o_{t,j}$ 中的信息，则从 $v_{t,j}$ 指向对应节点。由最终补丁、成功测试和提交节点反向追踪，得到关键节点集合 $K_\tau$。定义可避免成本：

$$
r_{t,j}=\mathbb I[v_{t,j}\notin K_\tau],\qquad
\tilde c_{t,j}=r_{t,j}\hat c_{t,j}.
$$


实现时优先使用 `tool_call_id` 匹配完整轨迹和 minimal trajectory，其次使用 `(step_id, call_index)`。若旧数据只有 step 级标注，则将被保留 step 内的全部 tool call 视为关键，以避免误删必要 observation。

### 2.3.3 Pattern Aggregation

将冗余 tool call 按通用 action pattern 聚合。对于模式 $p$，统计：

$$
\operatorname{Support}(p)=|\{\tau:p\text{ 在 }\tau\text{ 中冗余出现}\}|,
$$

$$
\operatorname{Benefit}(p)=\sum_{(t,j):\phi(a_{t,j})=p}\tilde c_{t,j}.
$$

只有在多个任务中出现且 `Benefit > 0` 的模式进入 evolve prompt。`Support` 衡量可复用性，`Benefit` 表示稳定前缀缓存假设下可避免的估计 token-price cost。

## 2.4 Harness Evolution

Harness 包含：

```text
tools.json      tool schema
executor.py     execution logic
instruction.md  generic behavior rules
```

Evolve agent 接收当前 harness、完整/最小轨迹和量化 pattern evidence，并生成候选 harness。新增工具必须合并常见多步操作，不能包含固定仓库路径或特定测试名称。生成后检查 JSON、Python AST 以及 tool schema 与 executor 分支是否一致。

## 2.5 Challenge II: Performance-constrained Selection

发现模式和每轮候选验证可以使用同一个任务集合 $D_{\mathrm{evolve}}$。当前 harness 与候选 harness 必须在相同任务上运行，以进行 paired comparison：

$$
P'=P(h';D_{\mathrm{evolve}}),\qquad
C'=C(h';D_{\mathrm{evolve}}).
$$

候选仅在以下条件同时成立时接受：

$$
P'\ge P-\epsilon,
$$

$$
\frac{C-C'}{C}\ge\delta.
$$

gate cost 由执行框架记录的实际 token usage 与锁定配置单价重新计算，而不是读取接口的 `cost_usd`，也不是使用 tool-call 级候选归因值。若 usage 缺失、静态验证失败或 evolve 中途异常，则拒绝候选并恢复上一版本。

$D_{\mathrm{evolve}}$ 上的 gate 负责稳定自进化过程，不用于证明泛化。进化结束后，使用从未参与候选生成和选择的 $D_{\mathrm{test}}$ 进行最终报告。

## 2.6 Complete Algorithm

```text
h <- initial harness
snapshot(h)

parent_traj <- rollout(h, D_evolve)  # 原始 code-agent harness，固定且多样的 16 cases

for each cycle:
    evidence <- dependency_and_cost_analysis(parent_traj)
    h_candidate <- evolve(h, evidence)

    if static_validation_failed:
        restore(h)
        continue

    candidate_traj <- rollout(h_candidate, D_evolve)
    decision <- compare(parent_traj, candidate_traj)

    if performance_non_inferior and cost_reduced:
        h <- h_candidate
        parent_traj <- candidate_traj  # 新 harness 的 16-case trajectory 进入下一轮 evolve
        snapshot(h)
    else:
        restore(h)                    # 保留旧 h 与旧 parent_traj，下一轮重新尝试

evaluate(h, D_test)
```

在该闭环中，cache-aware tool-call 成本负责提出候选，真实成功率与按 usage 重算的成本负责最终选择。候选 rollout 同时是通过 gate 后下一轮的 parent trajectory，因此不会为同一 harness 重复做一遍无意义的 parent rollout。

## 2.7 Reporting

每轮仅需记录 parent/candidate 的任务成功率、平均 token-price cost、匹配任务数、成本降低比例、接受结果和回滚原因。正式实验同时报告最终 harness 在 $D_{\mathrm{test}}$ 上的结果。

# 3. Detailed Implementation Plan

本节给出 v9 的具体实现方案。v9 不另起一套 native-tool runtime，而是在
`src/evolve/evolve_v6_cycle.py` 和 `src/evolve/native_tools_v6.py` 的基础上演进：保留 v6
已经验证可工作的 `tools.json + executor.py + instruction.md` 注册方式、benchmark runner
和容器挂载方式，重写其证据发现、候选隔离、验证、promotion gate、回滚和实验记录逻辑。

## 3.1 Lessons from the Existing v6 Artifacts

实现前先用 `results/evolve/v6cycle` 中保留下来的中间结果确认 v6 在真实任务上的行为。
这些结果表明“直接进化 native tools”是可行的，但也暴露了 v9 必须解决的问题。

| benchmark / run | 最终 tools | `instruction.md` 行数 | 主要现象 |
| --- | ---: | ---: | --- |
| deep-swe `0713-111239` | 14 | 35 | 工具和 instruction 明显膨胀，超过 v6 prompt 声明的 25 行 |
| swe-atlas-qa `0713-170159` | 7 | 25 | read/search/list 工具使用频繁，但缺少 cost/performance gate |
| swe-atlas-tw `0713-192210` | 8 | 30 | `edit-file` 失败率很高，失败工具仍被保留 |
| swebench `0712-010936` | 10 | 23 | schema 较大，出现鼓励跳过测试的高风险 instruction |

对三组完整 v6 final-eval 轨迹的统计进一步显示：

- deep-swe 中 `read-lines` 被调用 2457 次，产生约 790 万 observation 字符；14 个 tools 的
  schema 约 13 KB，`executor.py` 约 39 KB。工具虽然被大量采用，但没有从源头消除重复读取。
- deep-swe 的 `run-script` 为 633 次调用、约 240 次非零返回；`run-tests` 为 258 次调用、
  约 103 次非零返回。v6 只验证 Python/JSON 能解析，不能发现这类运行时低可靠工具。
- swe-atlas-tw 的 `edit-file` 为 94 次调用、约 80 次非零返回。当前版本仍会把这种工具无条件
  带入最终 eval。
- v6 的 contrastive sample 存在退化标注。deep-swe 的 16 个 prompt sample 中有 4 个
  minimal trajectory 只剩 1 个 action；swe-atlas-tw 中有 9 个只剩 1 个 action。典型样本的
  “minimal trajectory”只包含最终 submit，而原轨迹有上百步，不能把这些样本中的其余调用
  直接视为因果冗余。
- v6 所有 cycle 共用 `work_dir/evolve_logs`，且 `ScriptEvolver(resume=True)` 只检查
  `evolve_batch_<id>.traj.done`。当 Cycle 2 仍有 8 个 batch 时，会复用 Cycle 1 的同名 sentinel
  并跳过全部 evolve；但 `v6_report.json` 仍可能记录 `evolved=true`。
- v6 的 batch 顺序修改同一个 live registry，前一 batch 的错误会成为下一 batch 的输入；没有
  staging、候选归因和原子回滚，无法知道最终某个 tool 或 instruction 来自哪条证据。

因此 v9 不直接把 v6 contrastive sample 喂给 evolve agent。v6 产物只用于兼容解析、回归测试
和冷启动 evidence；任何历史样本都必须先经过 v9 的结构诊断和重新计价，但不按最终 reward
过滤成本证据。

## 3.2 Scope and Code Layout

新增下列文件：

```text
src/evolve/evolve_v9.py          v9 CLI、cycle orchestration、resume/recovery
src/evolve/evidence_v9.py        trajectory 解析、cost attribution、依赖图、pattern mining
src/evolve/compiler_v9.py        evolve prompt、staging 编译、repair、candidate attribution
src/evolve/gate_v9.py            benchmark metric adapter、paired statistics、promotion gate
src/evolve/report_v9.py          中间产物、history、最终 report 和 amortization 统计
tests/test_evolve_v9.py           单元测试
tests/test_evolve_v9_integration.py  fake-runner 闭环测试
```

继续复用：

```text
src/evolve/evolve_v6_cycle.py    benchmark metadata 与 taskdir/rollout 逻辑的参考实现
src/evolve/native_tools_v6.py    seed、runtime deploy、config wiring
agent/mini-swe-agent/.../evolve_tools_v6/
                                native tool 注册、隔离 worker、bash fallback
scripts/run_deep_swe.sh          benchmark runner
scripts/run_swe_bench.sh
scripts/run_dab_harbor.sh
```

v9 不修改 v6 的 CLI 和历史结果。`scripts/run_evolve_experiment.sh` 新增独立
`EVOLVE_VERSION=v9` 分支，`scripts/run_exp.sh` 只负责传递 setting。这样 v6/v8/v9 可以在同一
代码库中复现实验，不会因为升级 v9 而改变旧版本语义。

## 3.3 Core Data Models

`evidence_v9.py` 使用显式 dataclass，避免在多个阶段传递没有 schema 的 dict。

```python
@dataclass(frozen=True)
class ToolCallNode:
    node_id: str                 # <task_id>:<step_id>:<call_index>
    task_id: str
    step_id: int
    call_index: int
    tool_call_id: str | None
    tool_name: str
    arguments: dict
    normalized_action: dict
    observation_text: str
    observation_tokens: int
    future_llm_calls: int
    estimated_future_cost: float
    dependency_state: str        # critical / low_criticality / uncertain
    returncode: int | None
    exception_info: str

@dataclass(frozen=True)
class PatternOccurrence:
    task_id: str
    node_ids: tuple[str, ...]
    estimated_benefit: float
    action_signature: str

@dataclass
class PatternCard:
    candidate_id: str
    pattern_type: str            # sequence / dependency_subgraph / retry_policy
    support_tasks: list[str]
    occurrences: list[PatternOccurrence]
    total_benefit: float
    median_benefit: float
    evidence_quality: float
    expected_replacement: str
    positive_examples: list[dict]
    negative_controls: list[dict]

@dataclass(frozen=True)
class TaskMetrics:
    task_id: str
    success: bool
    primary_score: float
    api_cost: float | None
    new_input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    llm_calls: int
    native_tool_calls: int
    native_tool_failures: int
    error: str | None

@dataclass
class GateDecision:
    promote: bool
    reasons: list[str]
    paired_task_count: int
    success_regressions: list[str]
    success_improvements: list[str]
    mean_cost_delta: float
    cost_saving_ratio: float
    bootstrap_interval: tuple[float, float]
```

所有 JSON 中间产物包含 `schema_version`。读取旧 v6 trajectory 时先转成上述内存结构，绝不
原地改写历史文件。

## 3.4 Work-directory Contract

每个实验只写自己的 `work_dir`。所有 cycle 的文件必须隔离，resume 必须验证输入哈希：

```text
work_dir/
  experiment_manifest.json
  experiment_split_manifest.json
  evolve_cases.txt
  final_eval_cases.txt
  active/
    tools.json
    executor.py
    instruction.md
    registry_meta.json
  snapshots/
    cycle-0/
    cycle-1-promoted/
  cycle-1/
    state.json
    parent_registry/
    parent_run.json
    trajectory_index.json
    annotation/
      <task_id>.dependencies.json
      quality_report.json
    graphs/
      <task_id>.json
    cost_attribution.json
    pattern_occurrences.json
    pattern_cards.json
    instruction_candidate_cards.json
    rejected_sample_report.json
    compile_prompt.md
    compile_trajectory.json
    compile_repairs.json
    staging/
      tools.json
      executor.py
      instruction.md
      change_manifest.json
    validation.json
    candidate_run.json
    parent_metrics.json
    candidate_metrics.json
    paired_metrics.json
    gate.json
    evolution_summary.md
    PROMOTED | ROLLED_BACK
  cycle-2/
  cycle-3/
  history.jsonl
  evolution_cost.json
  final_report.json
```

`state.json` 记录每个 stage 的 `pending/running/completed/failed`、输入文件 SHA256、开始结束时间
和异常。只有 stage 名和输入哈希都相同时才能 resume。不能再用 v6 的“同名 `.done` 存在即
跳过”逻辑。

## 3.5 Initialization and Dataset Split

### 3.5.1 Initial harness

`active/` 由 `native_tools_v6.seed()` 产生，但初始版本只保留稳定 runtime 和最小治理规则。
不把 v6 已进化出的 7--14 个工具直接作为正式初始 harness，否则无法区分 v9 方法收益与历史
工具收益。

允许通过 `--warm-start-registry <v6 scripts dir>` 做额外消融实验。warm start 时：

1. 复制 v6 `tools.json/executor.py/instruction.md` 到 `cycle-0-warm-start/`；
2. 运行 v9 完整静态与动态验证；
3. 将每个历史 tool 标记为 `legacy_unattributed`；
4. 正式论文主结果仍使用相同的最小 seed，warm start 只作为复用历史经验的附加实验。

### 3.5.2 Case selection

v9 CLI 接受已经选好的 case file，但必须再次验证：

- `D_evolve` 恰好为 16 个 case；
- `D_test` 恰好为 64 个 case；
- 两者 case id 交集为 0；
- SWE-Bench/DeepSWE 尽可能按 repository/codebase 分层抽样，先最大化不同 codebase 数量，再在
  同一 codebase 内补充 case；
- DAB 按 database/domain/query family 分层；
- 将候选池、随机种子、选择顺序、被排除原因写入 `experiment_split_manifest.json`。

case split 一旦生成不可在 cycle 间变化。最终 eval 不能根据 evolve 结果重新选择任务。

## 3.6 Parent Trajectory Acquisition

Cycle 1 可以复用已经完成的 no-tools baseline，但必须满足：benchmark、model config、16 个 case、
agent version 和 prompt template 全部匹配；不匹配时重新 rollout。

后续 cycle 使用以下规则减少不必要的 evolution 开销：

- 上一轮 promoted：上一轮 candidate rollout 已经使用新的 active harness，可直接作为下一轮
  parent trajectory。
- 上一轮 rolled back：active harness 没变，继续使用最近一次 active parent rollout；下一轮
  prompt 加入上一轮 rejection summary，要求针对已观察到的问题修订候选。
- 若 parent 轨迹缺文件、模型配置变化或用户启用 `--refresh-parent`，才重新 rollout。

每个 rollout 生成 `run_manifest.json`，记录 case id、registry hash、model config hash、runner
版本和实际结果目录。只比较 registry hash 明确对应的轨迹，避免把旧工具结果误当成当前 parent。

## 3.7 Trajectory Parsing and Cost Attribution

### 3.7.1 Tool-call level parsing

一个 ATIF step 可以同时包含多个 tool call。解析器按下面顺序建立稳定映射：

1. 使用 `tool_call_id` 对齐 action 与 observation result；
2. 无 id 时使用 `(step_id, call_index)`；
3. observation 数量和 call 数量不一致时，将该 step 标记为 `uncertain`，不把其中任何 call
   当成可避免操作。

`future_llm_calls` 按后续真实 LLM step 数量计算，同一 step 内多个并行 tool call 只算一次
上下文推进。若轨迹记录了 truncation/summarization 事件，则 observation 只计算到它最后一次
仍在 prompt 中的调用；旧轨迹缺少此信息时使用稳定前缀假设并标记 `cost_confidence=estimated`。

token 数优先顺序：

1. 模型对应 tokenizer；
2. API 返回的 per-message token accounting；
3. UTF-8 字符启发式估计，并在 report 中单独标记。

价格从模型配置显式读取。若缺少价格，pattern discovery 使用
`new_tokens + cache_discount * cached_tokens` 作为归一化 proxy；promotion gate 不允许伪造美元
cost，必须使用可审计的 token 组成和显式价格表计算。

### 3.7.2 Dependency graph

优先复用已有 trajectory 中的 step dependency annotation，再升级到 tool-call 粒度：

- 新 v9 annotation 输出依赖目标 `[{"step_id": j, "call_indices": [...]}]`；
- 旧 v6 只有 step 级依赖时，对依赖 step 内所有 tool call 建边，保守地将它们视为关键；
- annotation 解析失败、越界或 observation 对齐失败时，不删除整条 trajectory；只将无法对齐的
  单个节点标为 uncertain；
- write、最终 patch、成功 verifier、最后一次通过的 targeted test 和 submit 是强制 anchor；
- 失败任务同时可以贡献 cost pattern 和 retry/failure instruction evidence。其 task outcome 必须
  作为风险标签保留，不能把失败轨迹描述成已证明正确的 workflow；正确性由后续 paired
  promotion gate 检验。

这里的 `low_criticality` 只表示“值得做 counterfactual rollout 的候选”，不能在代码或论文中
直接命名为 `redundant=true`。

### 3.7.3 Cost-only evidence admission and diagnostics

成本模式发现不以 task verifier 是否通过作为准入条件。只要 trajectory 包含可解析的 action，
就可以进入 pattern mining；失败任务中的反复读取、无变化重试、超时死磕和重复验证恰恰是
instruction 优化的重要证据。

每条 trajectory 仍运行以下诊断，但诊断结果只写入 report 和 card 风险标签，不再硬淘汰样本：

```text
dependency coverage == 100%
all referenced indices exist
minimal action count >= max(2, ceil(0.05 * original action count))
minimal path contains at least one non-submit causal action
successful task should retain a patch/write anchor or a verification anchor
```

完全没有 action 的 trajectory 自然无法贡献 pattern，并写入 `rejected_sample_report.json`；其余
样本保留在 evidence pool。退化 minimal annotation 不再让整条轨迹失效：连续序列仍可用于成本
模式发现，dependency subgraph 只使用能够解析的边。最终工具是否兼顾正确性和成本，由 staging
validation 与 paired promotion gate 决定。

## 3.8 Cross-task Pattern Mining

### 3.8.1 Action normalization

`normalize_action()` 保留操作语义，去掉任务特定常量：

- 绝对路径替换为 `<repo>/<path-class>`；
- 文件名按 source/test/config/doc 分类，不保留 repository 名；
- 行号、输出上限、测试 node id 等数值归一化为区间；
- shell command 解析为 pipeline/operator 序列，例如
  `rg -> read range -> read follow-up range`；
- native tool 与等价 bash action 映射到共同的 semantic op，如 `search/read/edit/verify/git`；
- 保留成功/失败、输出 token 桶、是否发生范围缩小等结果特征。

不预先限定只能生成哪几类 tool，也不提供固定 tool-name 白名单。pattern 类型由轨迹产生；安全
约束作用于执行能力和副作用，不限制 agent 发现新的 workflow。

### 3.8.2 Pattern candidates

同时枚举两类结构：

1. 长度 2--5 的相邻 semantic-op sequence；
2. 2--5 个节点的 dependency-connected subgraph。

典型候选可以是“搜索后读取命中片段”“读取多个文件并返回匹配上下文”“编辑后运行一个范围受限的
检查”，但这些只是轨迹可能产生的实例，不是硬编码类别。

默认 `min_support=2`，support 按不同 task 数计算，同一任务重复 20 次仍只提供 1 个 support。
对重叠 occurrence 使用 node-id union 计价：同一个高成本 call 不能同时给多个候选重复贡献完整
Benefit。实现可按候选 score 排序后做 greedy non-overlap assignment，并把未分配 occurrence 留在
原始文件中供审计。

候选排序：

$$
Score(p)=Benefit(p)\cdot\log(1+Support(p))\cdot Quality(p)
-SchemaOverhead(p)-FailurePenalty(p).
$$

其中 `SchemaOverhead` 估算新增 tool schema/instruction 每轮反复进入 prompt 的固定成本；这能避免
v6 为了节省 observation 却注册 13 KB schema 的问题。

每轮最多选择 15 张 pattern card，目标范围为 8--15 张；如果满足 `min_support=2` 的候选不足
8 张，宁可少于 8 张，也不能用单任务 pattern 补数。任务失败和退化 minimal annotation 只作为
风险标签，不再阻止包含 action 的轨迹贡献成本证据。完整候选池与最终选入的 cards 分别保存。

### 3.8.3 Instruction candidates

instruction evidence 与 tool pattern 分开生成。只从跨任务重复出现的行为信号提取，例如：

- 相同失败调用在参数不变时重复；
- 搜索/读取范围持续扩大并产生大量无关输出；
- targeted check 已经提供足够证据后仍重复运行相同检查；
- evolved tool 超时后没有缩小范围或回退 bash。

每条 instruction card 必须包含 support task、正例、negative control、期望改变的可观测行为和
潜在性能风险。禁止生成“环境坏了就直接提交”“语法检查通过就永远跳过测试”等无条件规则。
对于 destructive/security/data-integrity/public API/schema 等高风险修改，instruction 必须保留
必要验证。最终 `instruction.md` 默认不超过 12 条有效规则和 800 tokens，并保持 tool-agnostic；
tool 名称和参数用法只存在于 `tools.json`。

## 3.9 Candidate Harness Compilation

### 3.9.1 Staging and atomicity

每轮先把 `active/` 完整复制到 `cycle-i/staging/`。evolve agent 只能修改 staging 中：

```text
tools.json
executor.py
instruction.md
change_manifest.json
```

编译和 canary 期间绝不能修改 `active/`。`change_manifest.json` 记录：

```json
{
  "tools_added": [{"name": "...", "candidate_ids": ["p-003"]}],
  "tools_modified": [{"name": "...", "candidate_ids": ["p-006"]}],
  "tools_removed": [{"name": "...", "reason": "high failure / unused"}],
  "instruction_changes": [{"candidate_ids": ["i-002"], "rule": "..."}],
  "rejected_cards": [{"candidate_ids": ["p-009"], "reason": "not safely generalizable"}],
  "expected_effect": {"llm_calls": -2, "observation_tokens": -1800}
}
```

`rejected_cards` 使用上述对象数组格式，不使用裸 candidate-id 字符串。每个 batch 当前及此前出现过的
card 都必须归因到一次修改或一次明确拒绝，且不能覆盖此前 batch 已累积的 attribution。候选
attribution 不写进公开 tool schema，避免增加 rollout prompt 成本。

### 3.9.2 Serial compiler batches

不把本轮全部 cards 放进一次 compiler 调用。默认 `evolve_batch_size=2`，pattern cards 和
instruction cards 分别按排序切片并配对：每个 batch 最多包含 2 张 pattern card 和 2 张
instruction card。15+15 张 cards 因此产生 8 个串行 compiler batch，而不是一个约 50K-token
的大 prompt。

所有 batch 串行修改同一个 `cycle-i/staging/`：batch 2 读取 batch 1 修改后的 harness，以此类推。
每个 batch 后立即运行 registry validation，保存 registry snapshot、prompt、trajectory、repair 和
batch report；若某个 batch 两次 repair 后仍失败，整个 cycle rollback。`change_manifest.json` 必须
累积合并先前 batch 的 attribution，不能被后续 batch 覆盖。全部 batch 完成后才运行一次 16-case
candidate canary 和 promotion gate。

每个 batch prompt 由以下部分组成：

1. v6 registry/executor contract；
2. 当前 staging harness 的完整 `tools.json`、完整 `instruction.md` 和摘要化 executor API；
3. 最多 2 张 pattern card 和 2 张 instruction card；
4. 最近被拒绝候选的 `evolution_summary`；
5. registry schema budget、runtime timeout/output contract；
6. 要求写 `change_manifest.json` 并运行本地验证。

不再把 16 条数百步 original/minimal trajectory 或全部 cards 整体塞入 prompt。需要查看原始证据时，card 提供
对应文件和 node id，由 evolve agent 使用 bash 定点读取。这既减少 evolve 自身成本，也避免
长 prompt 中退化 minimal sample 主导生成。

### 3.9.3 Repair

编译后最多进行 2 次定向 repair。repair prompt 只包含 validator 错误、相关文件片段和原始
candidate attribution，不重新发送全部 evidence。每次保存 prompt、trajectory、diff 和验证结果。
两次仍失败则本轮立即 rollback，不进入昂贵 canary。

## 3.10 Validation Pipeline

验证分四层，任何一层失败都不能进入 promotion gate。

### Layer 1: structural validation

- `tools.json` 必须为 list；name 唯一且符合 native function naming；
- JSON schema 只使用 runtime 支持的类型，property/required 一致；
- description、property 数量、单 tool schema 和总 registry token 数不超过 budget；
- `instruction.md` 行数/token 数、重复规则和 tool-name leakage 检查；
- `change_manifest.json` 的 candidate id 必须存在于本轮 cards。

### Layer 2: executor validation

- Python AST 可解析，顶层存在 `run_tool`；
- schema names 与 executor dispatch 一一对应；
- stdlib-only import；
- 禁止硬编码 benchmark case id、固定 repository 路径和外部网络 endpoint；
- 禁止 module import 时执行命令；
- 对 subprocess 强制 timeout，禁止吞掉 TimeoutExpired 后无限等待；
- 返回值必须包含 `output/returncode/exception_info`。

### Layer 3: isolated smoke tests

在临时 repository fixture 中为每个 tool 自动构造：

- 最小合法参数；
- 缺失参数；
- 不存在路径；
- 小型正常输入；
- 超输出输入；
- 超时命令（如果工具可执行子进程）。

每个调用使用独立 worker、5 秒 smoke timeout、512 MB memory。测试确认 worker crash、OOM、异常
和 timeout 都只变成普通 observation，不会终止 agent。

### Layer 4: runtime budget validation

正式 rollout 的 evolved tool 默认：

```text
EVOLVE_TOOLS_V6_TIMEOUT_SECONDS=30
EVOLVE_TOOLS_V6_MEMORY_MB=1024
EVOLVE_TOOLS_V6_OUTPUT_TOKENS=1000
```

runtime 在父进程设置 hard deadline，并杀死整个子进程组。输出超过 budget 时保留开头、结尾和
`next_offset`，不能把无限输出注入上下文。错误 observation 使用建议而非强制措辞：推荐缩小
path/query/operation 范围，或回退到等价 bash；禁止原样重复同一失败调用。

现有 v6 runtime 已具有隔离 worker、memory limit、30 秒 hard timeout 和 bash fallback，v9 主要
补充统一 output-token cap、进程组清理测试和结构化 telemetry，不让 evolve agent 重写 runtime。

## 3.11 Canary Rollout and Metric Extraction

候选通过 validation 后，在同一 16 个 `D_evolve` case 上运行一次 candidate canary。parent 与
candidate 的 case id 必须完全一致；缺失 case 不静默丢弃。

新增 `BenchmarkMetricAdapter`：

- SWE-Bench 使用官方 resolved/reward 字段；
- DeepSWE 使用 benchmark 官方 primary score 判定成功，`partial/f2p/p2p` 只作次级诊断，不能因
  通用 `reward` 字段恒为 0 就把所有 case 错判失败；
- DAB 使用其正式 verifier primary reward；
- adapter 将原始字段和值完整写入 `TaskMetrics`，不得只保留 bool。

cost 一律按每次 API usage 重新计算：

```text
new_input_tokens = prompt_tokens - cached_tokens
cached_input_tokens = cached_tokens
cost = new_input_tokens * p_new
     + cached_input_tokens * p_hit
     + output_tokens * p_out
```

其中价格来自实验启动时锁定的模型配置，并按 per-million-token 单位换算。不得读取或回退到
接口/agent result 中的 `cost_usd`、`instance_cost` 等成本字段；即使它们为正值也忽略。这样
`cost_usd=0` 不会再被误认为零成本，benchmark rollout 与 compiler rollout 使用同一计算口径。

同时记录 tool 维度 telemetry：调用次数、非零返回次数、timeout、输出 tokens、bash fallback 和
同参数重复调用。这些指标用于解释 gate 结果和下一轮 rejection feedback，但不替代上述按 usage
重算的成本。

## 3.12 Relaxed Performance-constrained Gate

LLM rollout 有随机性，所以 gate 不要求“之前成功的每个 case 当前都必须成功”。默认参数：

```text
max_success_drop_rate       = 0.10
max_regression_rate         = 0.20
min_cost_saving_rate        = 0.00
max_candidate_error_rate    = 0.10
bootstrap_samples           = 2000
```

对 16 个 case，gate 同时检查：

1. candidate 有足够完整结果；candidate error 计为失败，不能从分母删除；
2. 总成功数下降不超过 `ceil(0.10 * 16)`；
3. parent success、candidate failure 的 regression 数不超过 `ceil(0.20 * parent_success)`；
4. paired mean token-price cost 不增加，并报告 paired bootstrap interval；
5. 没有 destructive side effect、registry runtime crash、输出 cap 违例等 hard failure；
6. registry 固定开销已经包含在真实 token/cost 中。

bootstrap interval 在 16-case setting 下主要用于报告不确定性，不设置不现实的“95% 下界必须大于
0”硬条件。promotion 的结论应表述为“在 evolve set 上满足预定义非劣与成本约束”，不能写成
统计意义上的性能保证。

如果用户希望成本必须有实质改善，可设置 `--min-cost-saving-rate 0.03`。论文主实验应预先固定
该阈值，不能看到结果后调整。

## 3.13 Failure, Rollback, and Next-cycle Feedback

任一轮未通过 gate 时必须：

1. 保留 staging、compile trajectory、canary 全部结果；
2. 写 `evolution_summary.md`，包括采用了哪些 evidence、实际 tool adoption、失败率、cost 变化、
   success regressions 和最可能的问题；
3. 将 `staging/` 标记为 `ROLLED_BACK`，不覆盖 `active/`；
4. 把失败摘要加入 `history.jsonl`；
5. 下一轮继续使用当前 active parent，并把失败摘要加入 compile prompt；
6. 不把失败 candidate 的 trajectory 当成“成功依赖图”证据，但可以使用其 tool failure/retry
   telemetry 修复同一候选。

通过 gate 时先将 active snapshot 写入 `snapshots/`，再用同一文件系统内 rename 原子替换
`active/`。替换后重新计算 registry hash；candidate rollout 成为下一轮 parent。

进程异常后 resume 时：

- `staging` 已生成但 validation 未结束：从 validation 恢复；
- canary 只有部分 case：runner 只补缺失 case，已完成 case 不重跑；
- gate 文件已写但 active hash 未更新：根据 `gate.promote` 完成或撤销原子提交；
- 任何状态不一致时保守 rollback，并在 report 中记录 recovery decision。

## 3.14 Time Limits and Hung-case Handling

v9 为每个阶段设置独立 deadline：

```text
annotation_timeout_per_step
compiler_timeout
repair_timeout
native_tool_timeout
case_agent_timeout
case_verifier_timeout
rollout_job_timeout
```

runner 优先把 agent/verifier timeout 传给 Pier/Harbor。若底层 runner 不提供 per-case timeout，则
启动 watchdog：轮询 trial 的 `started_at`、agent/verifier log 和 `result.json`；超过 deadline 后
终止该 trial 对应 compose project，写 `timeout_record.json`，analyzer 将其作为 error case。不能
因为第 16 个 verifier 死锁让整个 cycle 永远停在 15/16。

job-level timeout 到达时杀死整个 process group，但保留所有 partial result。gate 把缺失结果视为
candidate error；final eval 则明确报告 completed/error/missing 数量，不伪造 reward。

## 3.15 Evolution-cost Accounting and Break-even

`evolution_cost.json` 累计以下成本：

- parent rollout（若不是复用 baseline）；
- dependency annotation；
- compiler 与 repair；
- candidate canary；
- 因异常产生的重试。

最终报告同时给出：

$$
E_{evolve},\quad \Delta C=C(h_0)-C(h_{final}),\quad
N_{break-even}=\frac{E_{evolve}}{\Delta C}.
$$

若 $\Delta C\le0$，break-even 记为不可达到。这样论文不会只报告 deployment 阶段省钱，却隐藏
self-evolve 本身的 API 开销。

## 3.16 Final 64-case Evaluation

三轮结束后，只使用最终 `active/` 在预先固定、从未进入 candidate generation/gate 的 64 个
`D_test` case 上运行一次正式 eval：

```text
evolve cases       = 16
cycles             = 3
final eval cases   = 64
n_concurrent       = 16
min_support        = 2
```

no-evolve baseline 必须使用完全相同的 64 个 case、model config、agent version、并行度和任务
timeout。运行脚本不再重新采样 final eval，而是从指定（默认对应 benchmark 下最新）的
`results/no_evolve/<results-subdir>/noevolve-*/` 中读取每个 trial 的 `config.json:task.path`，锁定
这 64 个 case；缺失、重复、数量不是 64 或与 evolve set 相交都会在启动正式实验前报错。
正式比较保存逐 case join table，至少报告：

- success、success regression/improvement；
- 按锁定配置单价重算的 cost 及 new/cache-hit/output token；
- LLM steps；
- observation tokens；
- native tool adoption/failure/timeout；
- wall-clock latency 和 verifier error；
- paired bootstrap interval；
- evolution break-even。

final eval 只报告结果，不能再据此修改 harness，否则 64 个 case 就不再是 test set。

结果目录遵循项目现有分层：evolve 的 cards、prompt、registry、gate 及原始 canary rollout 保存在
`results/evolve/v9cycle/<benchmark>/<timestamp>/`（原始 rollout 位于其 `rollouts/` 子目录）；正式
64-case rollout 保存在 `results/eval/<results-subdir>/evolve-v9cycle-*/`；no-evolve 对照保持在
`results/no_evolve/<results-subdir>/noevolve-*/`。不得再向 `results/<results-subdir>/` 顶层直接写
`v9-final-*` 或 `v9c*-canary-*`。

## 3.17 CLI and Script Integration

v9 CLI：

```bash
python -m src.evolve.evolve_v9 experiment \
  --benchmark swebench \
  --config _config/deepseekv4_flash.yaml \
  --evolve-cases-file <16-cases.txt> \
  --final-eval-cases-file <64-cases.txt> \
  --baseline-dir <no-tools-baseline> \
  --scripts-dir <work-dir>/active \
  --work-dir <work-dir> \
  --n-cycles 3 \
  --n-concurrent 16 \
  --min-support 2 \
  --max-pattern-cards 15 \
  --output-token-cap 1000 \
  --tool-timeout-seconds 30 \
  --max-success-drop-rate 0.10 \
  --max-regression-rate 0.20 \
  --min-cost-saving-rate 0.00 \
  --bootstrap-samples 2000
```

`scripts/run_evolve_experiment.sh` 对 `EVOLVE_VERSION=v9` 做以下事情：

1. 构建并验证 16/64 严格互斥的 taskdir；
2. 调用 v9 `experiment` 完成三轮；
3. 将 final active registry hash 写入 run manifest；
4. 调用现有 benchmark runner 完成 64-case eval；
5. 核验实际运行 case 与 `final_eval_cases.txt` 完全一致。

`scripts/run_exp.sh` 只增加 v9 参数透传，不在脚本中复制 gate 算法。所有 threshold 和 decision
logic 只有 `gate_v9.py` 一个实现来源。

## 3.18 Test Plan

### Unit tests

1. 同一 LLM step 多个 tool call 只增加一次 future-call depth；
2. new/cache-hit/output cost 公式；
3. `tool_call_id` 与 `(step_id, call_index)` fallback；
4. observation 数量不匹配时标记 uncertain；
5. step-level v6 dependency 保守映射到全部 calls；
6. 126-step 到 1-step 的退化 minimal sample 被保留用于连续成本模式，并记录诊断；
7. support 按不同 task 计数；
8. 重叠 motif 不重复计算 node benefit；
9. action normalization 删除 repository/path/test-name 泄漏；
10. pattern card 和 change manifest attribution 校验；
11. schema/executor dispatch 一致性；
12. instruction budget、重复规则和 tool-name leakage；
13. worker timeout/OOM/output cap 返回普通 observation；
14. relaxed gate 允许少量 success regression，但拒绝总体超阈值下降；
15. candidate error 不从 gate 分母消失；
16. rollback 不改变 active hash；
17. promote 使用原子替换并产生 snapshot；
18. cycle-scoped resume 不会误用上一 cycle sentinel；
19. evolve/eval case overlap 非零时拒绝启动；
20. evolution cost 和 break-even 计算。

### Integration tests

使用 fake benchmark runner 构造 4 个轻量 task，覆盖：

- candidate 降本且性能持平，正常 promote；
- candidate 成本下降但回归过多，rollback 后进入下一轮；
- compiler 第一次生成坏 JSON，repair 后通过；
- 最后一个 verifier 挂起，被 watchdog 标记 timeout；
- 进程在 canary 中断后 resume，只补缺失 task；
- 最终 eval 使用独立 case 且不再更新 registry。

再在 Conda `0622` 中执行真实 smoke test：每个 benchmark 2 个 evolve case、1 cycle、2 个 final
eval case、并行度 2。smoke 通过后才允许启动正式 16/3/64/16 实验。

## 3.19 Implementation Order

建议按以下顺序实现，确保每一步都可独立验证：

1. **Compatibility layer**：复制 v6 runner/registry contract，建立 v9 dataclass、manifest、
   cycle-scoped state 和 hash resume。
2. **Evidence layer**：ATIF tool-call parser、cost attribution、旧 v6 dependency adapter、质量过滤。
3. **Pattern layer**：normalization、sequence/subgraph mining、support/benefit 去重、instruction cards。
4. **Compiler layer**：staging、card-based prompt、change manifest、两次 repair。
5. **Validation layer**：四层 validator、runtime output cap 和 telemetry。
6. **Gate layer**：benchmark adapter、paired metrics、relaxed gate、rollback/promotion。
7. **Recovery layer**：stage deadline、watchdog、partial-result resume。
8. **Reporting layer**：history、evolution cost、break-even、final report。
9. **Script integration**：`EVOLVE_VERSION=v9`、16/64 split 检查、final eval。
10. **Experiment validation**：unit tests、fake integration、2-case smoke、正式 benchmark。

## 3.20 Definition of Done

v9 代码完成需同时满足：

- v6 的 native registry 能直接加载 v9 promoted harness，bash fallback 保持可用；
- 不存在固定 tool 类别或 tool-name 白名单；
- 所有 candidate 都能追踪到具体 evidence card；
- 退化 dependency sample 不参与 avoidable-cost pattern mining；
- 每轮 staging 与 active 隔离，失败轮自动总结、rollback 并进入下一轮；
- tools 有 hard timeout、memory limit、output cap 和推荐缩小范围/回退 bash 的错误 observation；
- 16 个 evolve case 与 64 个 final eval case 可机器验证为零交集；
- 三轮所有 graphs、cards、prompt、compile/repair、validation、canary、paired metrics、gate、history
  和 registry snapshot 都被保存；
- final report 同时给出 performance、按 usage 重算的 cost、置信区间、evolution 总成本和 break-even；
- Conda `0622` 下 unit/integration/smoke tests 全部通过后才运行正式实验。
