# COAT v6.1 工程流程

COAT 的目标是在不更新基础模型参数的情况下，根据历史执行轨迹持续改写 code agent 的 execution harness。当前 harness 由三个可进化文件组成：

- `tools.json`：模型可见的结构化工具及参数 schema；
- `executor.py`：工具的实际执行逻辑；
- `instruction.md`：关于批处理、停止重试、提前退出和验证策略的通用行为规则。

`native_tools_v6.py` 负责初始化这三个文件，并把稳定的工具运行时和 mini-swe-agent 配置部署到 rollout 环境。真正被进化模型修改的是上述三个 harness 文件，而不是运行时本身。

## 一轮进化的主流程

### 1. 使用当前 harness 收集轨迹

在第 $t$ 轮，系统用当前 harness $H_{t-1}$ 在指定任务集合上运行 mini-swe-agent，得到一组 action--observation trajectories。第一轮也可以直接复用预先生成的 baseline trajectories，后续轮次则使用上一轮接纳后的 harness 重新 rollout。

进入后续步骤前，系统检查每个预期任务是否恰好有一个有效 trial、是否存在可解析的 `result.json` 和 `trajectory.json`，以及 agent 是否真正执行过工具调用。reward 为 0 仍然是有效轨迹；基础设施错误、空轨迹和重复/缺失任务不会被当作进化证据。

### 2. 将轨迹标注为依赖图

系统逐 action 标注三类信息：

- 当前 action 依赖哪些更早的 action；
- 操作类型：`read`、`write`、`verify` 或 `explore`；
- 执行状态：`success` 或 `fail`。

标注 action $i$ 时，LLM 只能看到 action $1,\ldots,i-1$ 的 action--observation 前缀以及当前 action--observation，不能看到未来步骤；过长 observation 会先被有界渲染。所有依赖边都从当前节点指向更早节点，因此整条轨迹被转换为一个有向无环依赖图（DAG）。标注支持逐步骤 checkpoint 和恢复，但这只改变调度方式，不改变每一步的标注输入。

### 3. 从 DAG 生成 evolving samples

这是 COAT 提取进化信号的核心步骤。系统先从最后一个 action 反向追踪依赖闭包，得到支撑最终行为的全局最小节点集合，再在局部范围内寻找几类可优化模式：

- **Skippable region**：不在最终依赖闭包中的连续操作，提示通过行为规则避免无效探索；
- **Mergeable operations**：共享相同直接依赖的成功 sibling operations，提示设计可批量完成多个操作的工具；
- **Failure-to-pivot pattern**：连续失败后紧接成功转向，提示更早停止重复尝试并切换策略；
- **Long observation**：依赖相关但输出过长的操作，提示限制、筛选或摘要工具输出；
- **Operator hotspot**：同类操作频繁出现并累计产生大量 observation，提示合并或重构工具能力；
- **Phase fallback**：没有更强信号时，按 `op_type` 提取至多 12 步的有界语义阶段，尽量让轨迹仍能提供候选证据。`min_phase_size=3` 会尽量把不足 3 步的相邻小阶段合并，但若整条轨迹本身只有 1--2 个 action，仍会保留该短阶段。

每个 evolving sample 包含任务上下文、信号类型、优化目标（tools、instruction 或 both）、证据等级，以及一对局部轨迹视图：

- `negative_sample` 是从真实 rollout 中原样截取的“优化前”局部执行。它不一定是整条失败轨迹，而是目标步骤加至多 2 个父步骤、1 个后继步骤；skippable、failure-pivot 和 hotspot 的目标区域过长时最多展示 5 个代表步骤，但 `signal.target_step_indices` 仍记录完整目标范围。phase fallback 则可保留最多 12 个连续步骤。
- `positive_sample` 是同一局部执行的“优化目标视图”。它可能由 DAG 严格支持，例如删除不在最终依赖闭包中的步骤；也可能只是把诊断目标具体化，例如把连续失败后才出现的成功 pivot 提前，或把 14,717 字符的 observation 替换为“应只返回相关摘要”的预算占位符。因此它通常不是第二次 rollout 得到的真实成功轨迹，不能将其 reward 理解为正样本 reward。

不同信号中这对视图的具体含义如下：

- `skippable`：negative 保留父节点、无关探索段（过长时显示代表步骤）和后继节点；positive 删除无关探索，只保留父节点与后继节点。
- `mergeable`：negative 保留多个分开执行的成功 sibling operations；positive 把它们表示成一个含多个 structured calls/results 的合并 operator。
- `failure_pivot`：negative 保留连续失败及其后的成功 pivot；positive 去掉重复失败，表达“更早放弃并直接 pivot”的行为目标。
- `long_observation`：两边保留同一个依赖相关 action，但 positive 将原始超长 observation 换成有界摘要/检索目标。
- `hotspot`：negative 展示同一 operator family 的多次代表性调用；positive 只保留一个代表，表达批处理或工具重构目标。
- `phase_fallback`：negative 展示一个有界语义阶段；positive 只保留该阶段中属于最终依赖闭包的节点及必要上下文。

例如，真实运行 `dab/0719-164047/cycle-2` 中，`dab__stockmarket__query3__iXFt96Z` 的一个 `mergeable` sample 找到步骤 1 和 3：两步都是成功的 `bash:python` 操作，且直接依赖都为初始节点 0。原始局部视图为：

```text
negative_sample（source_step_indices = [1, 3, 4]）
Step 1: python query_db.py dbs
Step 3: python query_db.py query stockinfo_database "SELECT * FROM stockinfo LIMIT 3"
Step 4: python query_db.py query stockinfo_database
        "SELECT DISTINCT \"Financial Status\" FROM stockinfo"
```

构造出的紧凑视图为：

```text
positive_sample（source_step_indices = ["1+3", 4]）
Step 1+3: 一个合并 operator，内部包含原 Step 1 和 Step 3 的两个 calls/results
Step 4:   原样保留
```

这里的含义是“步骤 1、3 没有相互依赖，可以由一次支持多调用的工具操作完成”，所以估计节省 1 个 action。positive 中的 `1+3` 是构造器合成的设计目标，不是 agent 曾经实际执行过的步骤。evolve prompt 为避免重复大段 action/observation，也不会再次完整打印 `positive_sample`，而是打印可从 negative 重建它的 delta：

```text
Original action count: 3; minimal action count: 2.
Minimal step order: 1+3, 4
Kept unchanged: 4
Removed from original: (none)
Transformations:
- Step 1+3 merges original steps 1, 3 into one operator; reuse their already-shown actions/observations
```

#### 信号检测和专属局部视图如何构造

检测器共享三项输入：按 1 开始编号的 `actions`、标准化依赖表 `deps`，以及从最后一个 action 反向追踪得到的全局依赖闭包 `keep`。依赖表中每个 action 都会补上初始节点 0，所有非零依赖必须小于当前 action 编号。随后按以下顺序收集候选；这里的“分数”只用于同一条 trajectory 内的候选选择，不是 reward，也不跨 trajectory 比较。

1. **Skippable region**

   - 检测：取所有 `i ∉ keep` 的 action 编号，按编号连续性切成若干段；只有长度至少为 2 的连续段才成为候选。
   - 上下文：收集该段的直接父节点，最多取编号最大的 2 个；后继优先取第一个依赖该段且属于 `keep` 的后续 action，否则取该段之后最早的一个 `keep` 节点。
   - negative：`父节点 + 无关段 + 后继`。若无关段超过 5 步，只显示首两步、末两步，再按 observation 长度补足代表步骤；完整编号仍放在 `target_step_indices`。
   - positive：`父节点 + 后继`，即删除整段不属于最终依赖闭包的操作。
   - 分数：`120 + 12 × 段长度 + min(该段 observation 字符数 // 1000, 30)`。

2. **Mergeable operations**

   - 检测范围：只检查 `keep` 内的 action。
   - 分组键：`(相同的直接依赖集合, op_type, op_state)`；每组再按编号每 4 个切成一组，因此一次最多合并 4 个 action。
   - 过滤：组内至少 2 个 action，必须全部成功；每步必须有非空结构化 `tool_calls`，且 observation 必须是含 `results` 列表的结构化对象。对于 write 操作，还要求每步能识别出非空且互不重叠的文件集合，避免合并有写冲突的操作。
   - negative：`最多 2 个共享父节点 + 分开的 sibling actions + 最多 1 个 keep 内消费者`。
   - positive：父节点和消费者不变，把组内所有 calls/results 合入一个合成 action，显示编号写成如 `1+3`，并记录 `merged_from_step_indices`。
   - 分数：`150 + 25 × (合并 action 数 - 1)`；2、3、4 个 action 分别得 175、200、225 分。

3. **Failure-to-pivot pattern**

   - 检测：先找出所有 `op_state=fail` 的 action，再切成连续失败段；要求至少连续失败 2 次，并且紧随其后的下一个 action 必须成功。这里不要求失败段或 pivot 属于 `keep`，因为它是行为诊断而非依赖证明。
   - 上下文：最多 2 个失败段外的直接父节点；如果没有依赖父节点但失败段不是从第一步开始，则补前一个 action 作为上下文。
   - negative：`上下文 + 连续失败段 + 成功 pivot`，长失败段最多展示 5 个代表步骤。
   - positive：`上下文 + 成功 pivot`，表达更早停止同一路径重试。
   - 分数：`140 + 20 × 连续失败次数`。

4. **Long observation**

   - 检测：只检查 `keep` 内的 action，原始 observation 字符数必须严格大于 `LONG_OBSERVATION_CHARS=4000`。
   - negative：`最多 2 个直接父节点 + 目标 action + 最多 1 个 keep 内消费者`，保留真实长 observation。
   - positive：action 与上下文保持不变，只把目标 observation 换成有界摘要/按需检索占位符，同时保留原来的成功或失败状态。
   - 分数：`80 + min(observation 字符数 // 1000, 80)`，最高 160 分。

5. **Operator hotspot**

   - 检测：按 operator family 对整条 trajectory 的 action 分组。普通 structured tool 以工具名为 family；bash 则进一步取首个命令动词，例如 `bash:python`。要求同一 family 至少出现 `HOTSPOT_MIN_OCCURRENCES=3` 次，且累计 observation 至少为 `HOTSPOT_MIN_OBSERVATION_CHARS=2000` 字符。
   - negative：最多展示该 family 的 5 个代表 action。
   - positive：只保留第一个代表 action，用来表达“应通过批处理或更通用工具把多次调用收敛为一次”的设计目标。
   - 分数：`70 + 8 × 出现次数 + min(累计 observation 字符数 // 2000, 40)`。

6. **Phase fallback**

   - 启用条件：只有前五类候选一个都没有时才启用；fallback 不会与前五类信号混合选择。
   - 检测：按连续相同的 `op_type` 形成原始阶段；不足 3 步的阶段尽量并入相邻阶段，超过 12 步的阶段再切成每块最多 12 步。单独存在的 1--2 步轨迹不会被丢弃。
   - negative：`最多 2 个直接父节点 + 阶段内全部节点 + 最多 1 个消费者`。
   - positive：上下文不变，只保留阶段内属于 `keep` 的节点。
   - 分数：`5 × 阶段长度 + min(阶段 observation 字符数 // 1000, 30)`。

其中 `_parents` 只取目标节点的**直接依赖**，不会递归展开整个祖先闭包；`_consumers` 也只寻找显式直接依赖目标节点的后续 action。因此 sample 是围绕信号构造的有限局部视图，而不是完整的诱导子图。

#### 多样性和分数如何选择

若前五类检测器产生了候选，`_select_diverse` 执行两轮选择：

1. 先按 `type` 建桶，并将每个桶内部按 `score` 从高到低排序。
2. 按固定优先级 `mergeable → failure_pivot → skippable → long_observation → hotspot`，从每个非空桶取最高分的 1 个。这保证在容量允许时每种已发现信号至少保留一个。固定类型优先级只决定这一轮的输出顺序，并不会让一个类型取走多个名额。
3. 将各桶剩余候选放回同一个列表，按 `score` 全局降序排序，填满剩余名额，直到达到 `MAX_SIGNALS_PER_TRAJECTORY`。

因此，在上限为 8 时，如果五种类型都存在，前 5 个名额分别给五种类型的桶内最高分候选，剩余 3 个名额完全按分数竞争；如果只发现两种类型，先各保留 1 个，再由这两类的所有剩余候选按分数竞争另外 6 个名额。不同分数相同时，Python 稳定排序保留候选原有顺序。若进入 phase fallback，因为所有候选类型相同，系统直接按 fallback 分数降序取前 8 个，不再执行类型多样性轮转。

#### 一条 trajectory 会产生多少对 sample

一个 `contrastive_v61_*.json` 就是一个 evolving sample，也恰好包含一对 `negative_sample`/`positive_sample`。当前硬上限已改为 `MAX_SIGNALS_PER_TRAJECTORY = 8`。因此一条源 trajectory 最终产生 **0--8 个 sample，即 0--8 对局部视图**：只要至少有一个 action，前五类信号或 phase fallback 至少会产生 1 对；没有 action 时产生 0 对。上面的真实 `stockmarket/query3` trajectory 是修改上限之前生成的历史产物，候选本身只有 3 个，所以仍是 `mergeable`、`long_observation` 和 `hotspot` 三对；把上限改成 8 不会凭空补足样本，只会允许候选丰富的 trajectory 最多保留 8 对。

其中，skippable 和 mergeable samples 有 DAG 依赖支持；failure pivot、long observation 和 hotspot samples 是设计诊断信号，它们的 positive view 是期望的优化方向，并不是重新执行得到的成功轨迹。每条源轨迹只保留少量、类型尽可能多样的高分信号，并仅附带有限的父节点和后继节点作为上下文。任何涉及私有答案、ground truth 或 query-specific verifier 的样本都会被丢弃。

#### 真实运行时 evolve 的初始 prompt

evolve agent 收到的不是 sample JSON 文件名或一句简短指令。`EvolvePromptBuilderV61` 会组装一份完整 Markdown，并由 `MiniSweAgentRunnerV61` 通过 `mini -t <完整文本>` 作为**初始 user task**直接传入；同一文本同时保存为 `evolve_logs/evolve_batch_N.traj.prompt.md` 供审计。mini-swe-agent 自己仍会加载 `mini.yaml` 的系统提示，这里说的 evolve prompt 是其后的首条 user prompt。

以同一真实运行的 `cycle-2/evolve_logs/evolve_batch_21.traj.prompt.md` 为例，文件共 25,927 字符，包含两个同组的 `mergeable` samples（默认 `batch_size=2`）。其实际结构和关键原文如下；仅为便于阅读省略静态规则全文、当前 `executor.py` 的长内容和 observation，运行时文件中没有这些省略号：

```markdown
# Evolve task (v6.1 — write native function tools directly)

You are evolving the COMPLETE DOWNSTREAM HARNESS for a mini-swe-agent:
tools.json, executor.py, and instruction.md. ...
Your goal is to lower total steps and tokens while preserving task success by:
1. adding, fixing, merging, or removing GENERIC structured tools ...
2. keeping tools.json schemas and executor.py behavior synchronized and robust;
3. improving instruction.md with reusable rules ...; and
4. making no change when evidence is weak or an optimization is already covered.

## You edit exactly THREE files in the current working directory
### 1. `tools.json` — the tool registry
### 2. `executor.py` — the execution logic
## Rules
### 3. `instruction.md` — HIGH-LEVEL BEHAVIORAL RULES (≤ 25 short lines)
## How to use the focused DAG evidence

The current working directory is scripts; edit tools.json, executor.py,
and instruction.md in place here.

# Current harness files in this directory
## ./tools.json
<当前 tools.json；最多 2,000 字符>
## ./executor.py
<当前 executor.py；最多 4,000 字符>
## ./instruction.md
<当前 instruction.md；最多 2,000 字符>

# Executional History 1
Source: .../dab__stockmarket__query3__iXFt96Z/agent/contrastive_v61_01_mergeable.json
Signal type: v61_mergeable
Optimization target: tools
Evidence status: dependency_validated

## Task Context
<该 rollout 的任务描述，最多 1,000 字符>

## Why This Slice Was Selected
{
  "reason": "Successful sibling operators have identical direct dependencies.",
  "target_step_indices": [1, 3],
  "shared_dependencies": [0],
  "operator_family": "bash:python",
  "estimated_steps_saved": 1
}

## Original Trajectory
<上例 Step 1、3、4 的真实 actions/observations；单个 observation 按配置最多 1,000 字符>

## Minimal Trajectory Delta
Original action count: 3; minimal action count: 2.
Minimal step order: 1+3, 4
Kept unchanged: 4
Removed from original: (none)
Transformations:
- Step 1+3 merges original steps 1, 3 into one operator; reuse their already-shown actions/observations

# Executional History 2
<同组的第二个 sample，字段结构相同>

Your task: evolve tools.json, executor.py, and instruction.md in the current
directory based on the focused DAG samples below. ... If there is no new reusable
signal, make no changes. Do not edit the prompt or contrastive-sample files.
Finish by saving the files.
```

这里“初始”指每个 evolve batch 启动时送入 agent 的第一条任务消息。每轮可能有很多 sample，因此不是整轮只发一次 prompt：samples 先按优化目标、信号类型和证据等级分组，每个 batch 默认最多放 2 个同组 sample；若完整 prompt 超过默认 50,000 字符预算则继续拆小，剩余 sample 延后到后续 batch，不会丢弃。

### 4. 根据 samples 生成候选 harness

系统按照“优化目标、信号类型、证据等级”对 samples 分组，并在不同任务之间轮转排序，避免一个 batch 被单一任务主导。每个 batch 的 evolve prompt 同时提供：

- 当前完整 harness 的必要内容；
- 每个 evolving sample 的选择原因；
- 原始局部轨迹；
- 相对于原轨迹的 minimal trajectory delta。

系统先保存 batch 开始前的 harness 快照，再让进化 agent 在可变工作副本中编辑 `tools.json`、`executor.py` 和 `instruction.md`，并把编辑后的状态另存为 candidate。工具 schema 与执行逻辑必须同步，工具应跨仓库复用并减少多步操作或 observation 开销；行为规则应保持简短、通用。若证据不足或现有 harness 已覆盖该模式，允许不做修改。若 prompt 超出预算，系统缩小 batch，而不会静默丢弃样本。

### 5. 门控、接纳或回滚

候选修改首先经过确定性检查，包括 JSON/Python 可解析性、`run_tool` 接口、工具名称和参数 schema、工具注册与 executor 的基本一致性、instruction 长度、修改文件范围和 diff 大小等。随后 LLM judge 根据以下标准审查候选 diff：

- 跨任务通用性；
- 预期净成本收益；
- 正确性与安全性；
- harness 内部一致性；
- 与 evolving evidence 的对齐程度及修改最小性。

只有所有维度均通过阈值且没有 blocking issue 时，候选的三个 harness 文件才会被提升为 $H_t$。否则系统精确恢复 $H_{t-1}$。未产生修改的 batch 被记录为 `no_change`。所有 samples 都必须在 manifest 中被处理或明确标记为 oracle-discarded，之后才会刷新运行时并进入下一轮。

## 需要与论文表述保持一致的实现边界

当前代码的 promotion gate 是“确定性验证 + LLM judge”，不是候选 harness 与旧 harness 之间的执行级 A/B test。被接纳的 $H_t$ 会在下一轮 rollout 中产生新轨迹，并由这些轨迹驱动后续进化；当前实现不会根据下一轮测得的 reward/cost 自动撤销上一轮修改。因此，论文中不宜写成“候选修改先在新执行上验证，且只有实测不降低性能时才接纳”，除非之后补充相应的执行门控代码。

## 每轮主要产物

每轮保存 rollout 快照、完整性验证报告、带依赖和 step metadata 的 trajectories、focused evolving samples、各 batch 的 prompt/diff/judge decision、接纳后的 harness 快照以及可恢复的 cycle state。最终输出是最后一轮接纳后的 harness $H_T$ 和完整的进化审计记录。
