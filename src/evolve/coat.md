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
- **Phase fallback**：没有更强信号时，按 `op_type` 提取一个有界语义阶段，保证轨迹仍能提供候选证据。

每个 evolving sample 包含任务上下文、信号类型、优化目标（tools、instruction 或 both）、证据等级，以及一对局部轨迹视图：

- `negative_sample` 保留原始的低效局部执行；
- `positive_sample` 表示由依赖关系或诊断目标给出的更紧凑局部视图。

其中，skippable 和 mergeable samples 有 DAG 依赖支持；failure pivot、long observation 和 hotspot samples 是设计诊断信号，它们的 positive view 是期望的优化方向，并不是重新执行得到的成功轨迹。每条源轨迹只保留少量、类型尽可能多样的高分信号，并仅附带有限的父节点和后继节点作为上下文。任何涉及私有答案、ground truth 或 query-specific verifier 的样本都会被丢弃。

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
