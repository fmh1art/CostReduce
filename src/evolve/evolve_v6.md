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

针对第一个挑战，

v6 首先对 rollout 轨迹中的步骤依赖进行标注，并将依赖关系组织为 DAG；随后沿依赖关键路径裁剪轨迹，得到保留任务必要信息的最小轨迹。原始轨迹与最小轨迹组成一组 contrastive sample：两者之间被消除的重复搜索、分散读取和反复测试等步骤，揭示了真正值得优化的高成本模式；最小轨迹保留的步骤则约束优化不能丢失解决任务所需的信息。

针对第二个挑战，v6 不直接改写历史，也不改变 rollout agent 的推理循环，而是进化它的 harness。evolve agent 从 contrastive samples 中归纳可复用模式，直接更新结构化工具注册 `tools.json`、统一执行器 `executor.py` 和高层行为规则 `instruction.md`。新 harness 在下一轮 rollout 中立即生效，再由新轨迹继续驱动下一轮进化，从而形成“执行—分析—进化—再执行”的闭环。该方法在源头合并多步操作、限制冗长输出并减少无效尝试，因此既能降低轮次和累计上下文成本，也保留原始对话前缀及其 cache。

## 2. Method：基于 v6 的 Harness 自进化

### 2.1 问题定义与总体框架

给定第 $k$ 轮 harness $H_k$ 和一组代码任务，v6 先用 $H_k$ 运行 agent 得到原始轨迹，再从轨迹中提取“完成最终操作真正依赖了哪些步骤”，最后据此生成 $H_{k+1}$。优化对象不是任务补丁或历史文本，而是由三部分组成的通用 harness：`tools.json` 描述模型可见的结构化工具，`executor.py` 实现统一执行入口，`instruction.md` 保存高层成本优化策略。其目标是在保留任务关键信息和原始推理循环的同时，减少调用轮次与进入上下文的 observation。

整个闭环为 **Rollout → Dependency Annotation → Contrastive Construction → Harness Evolution → Validation and Deployment**。v6 默认执行四轮；第一轮可复用已有 baseline 轨迹，之后每轮都使用上一轮产生的 harness 重新 rollout。

### 2.2 轨迹依赖标注

v6 将包含 `tool_calls`、`action` 或 `observation` 的记录依次编号为 action step 1 到 $n$，并把任务初始状态记为 step 0。对每个 step $i$，标注 LLM 会看到此前的完整步骤历史，以及当前 action 和截断后的 observation，然后输出：

- `dependencies[i]`：生成 step $i$ 所必需的所有前序步骤，只允许取 0 到 $i-1$；
- `op_type`：该步属于 `read`、`write`、`verify` 或 `explore`。该标签与依赖在同一次 LLM 调用中生成，主要作为步骤元信息，不参与当前的轨迹裁剪。

解析器会去重依赖、过滤越界索引并补入 step 0。只有所有 action step 都得到依赖记录时，轨迹才视为完成标注；文件级和步骤级标注可以并行，失败样本会重试一次。最终依赖关系形成一个只从后续步骤指向前序步骤的 DAG。

### 2.3 Contrastive Construction

Contrastive construction 不按固定比例截断轨迹，也不求图上的单条最短路径，而是计算**最终 action 的依赖反向可达闭包**。设最后一个 action 为 $n$，从 $n$ 开始递归访问 `dependencies`：

\[
S=\{n\}\cup Ancestors(n)\cup\{0\}.
\]

集合 $S$ 中的 action 及其传递依赖被完整保留。首个 action 之前的 system prompt、任务描述等非 action 内容也始终保留；轨迹中间不属于 action 的消息默认删除。裁剪后的轨迹同时保存保留索引及其依赖子图。

每条标注轨迹由此产生一组样本：未经裁剪的完整轨迹作为 `negative_sample`，表示 baseline 的高成本执行；依赖闭包对应的子轨迹作为 `positive_sample`，表示到达同一最终 action 所需的 dependency-critical 执行。二者的差集展示可被合并或避免的探索、读取、测试和重试，而共同保留的步骤约束进化不能盲目删除关键信息。

### 2.4 Harness Evolution

所有 `contrastive_sample.json` 按路径排序，并以小批次依次交给 evolve agent；v6 默认每批两个样本，并用完成标记支持断点续跑。送入 prompt 的每个样本都包含 Original Trajectory 与 Minimal Trajectory，其中 observation 会被限长，避免进化过程自身引入过大上下文。prompt 还提供当前 harness，使 evolve agent 能在已有能力上持续修改。

evolve agent 只有 `bash` 编辑能力，但直接维护以下三个产物：

- `tools.json`：每个工具包含唯一名称、单句描述和 JSON Schema 参数；
- `executor.py`：按 `action["tool"]` 分发，并统一返回 `output`、`returncode` 和 `exception_info`；
- `instruction.md`：不描述具体工具，只记录 batching、失败后放弃或转向、early exit 和 risky moves 四类通用策略，限制在 25 个短行以内。

`instruction.md` 会作为高层规则注入下一轮 rollout 的任务 prompt。进化并非简单增加工具，而是根据正负轨迹的差异新增、修复、合并或删除能力。候选工具应把高频多步操作压成一次调用，并能跨任务和仓库复用；硬编码路径、项目专用命令、重复实现 `bash` 以及 build/package/install 工具会被排除。测试工具还必须尊重调用时的 `cwd`，自动区分 Django 原生测试入口与 `pytest`。

### 2.5 原生工具执行与安全机制

`tools.json` 和 `executor.py` 就是 rollout agent 实际加载的注册文件，不需要额外转换。模型每轮同时看到稳定的 `bash` 和进化出的原生函数工具；已注册调用进入 `run_tool`，其他调用仍走原始环境。为避免缺少参数直接触发格式错误，运行时放宽 schema 的 `required` 限制，让执行器以普通 observation 返回可修正的错误。

每次原生工具调用都在独立 worker 进程中执行，并受到超时、内存和输出长度限制；默认硬超时为 30 秒，默认输出上限约为 1000 tokens。worker 崩溃、超时、返回格式异常或工具自身报错都被转换为统一 observation，并提示 agent 缩小操作范围或回退到 `bash`，不会终止主 agent。执行器加载失败时，所有进化工具会被隐藏，系统自动退化为 bash-only。

### 2.6 校验、部署与跨轮反馈

初始化时，v6 在文件缺失的情况下写入带 `run-tests` 的种子 harness，并部署稳定的工具运行时和 agent 配置。一轮 evolve 的所有批次处理完成后，框架先刷新运行时与 agent 配置，再软校验 `tools.json` 是否为合法工具列表、`executor.py` 是否能通过 AST 解析并定义 `run_tool`。软校验只保证结构和接口有效，不直接证明工具语义正确；工具是否真正降低成本且不影响求解，由下一轮真实 rollout 的轨迹继续反馈。

每轮的 annotate、contrastive 和 evolve 状态都会写入报告。单个阶段异常会被记录而不会直接中止整个循环，使部分任务失败时仍可利用其余有效轨迹继续进化。由此，v6 将一次性的轨迹分析变成持续反馈过程：新工具改变 agent 的执行方式，新执行方式产生新轨迹，新轨迹再决定下一轮应保留、修复或淘汰哪些工具与行为规则。
