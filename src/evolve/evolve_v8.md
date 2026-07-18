## 9. Introduction（论文引言草稿）

### 9.1 LLM agent 及其高昂成本

大语言模型(LLM)agent 已成为自动化软件工程的主流范式。以 ReAct 为代表的 code agent(如本文 baseline `mini-swe-agent`)在"推理 → 调用工具 → 观察结果"的循环里反复迭代,直到提交补丁或耗尽预算。这种多轮、长上下文的工作方式让 agent 能在陌生代码库中自主定位、修改和验证代码,但也让它的 API 成本远高于单轮问答。

成本高在两点。**第一,轮次多。** 一个真实代码任务常常要几十到上百次 LLM 调用。**第二,observation 不断累积在上下文里。** 每次工具返回的结果(读文件、跑测试、列目录)一旦进入对话历史,就会在之后每一轮 prompt 里被反复携带。所以一次读文件的真实代价,不只是产生它的那一轮,而是它在后续所有轮次里的累计暴露。轮次越多、观察越长,上下文就越大,每一轮的账单也越贵——两者互相放大。**降本的本质,就是同时压住"轮次"和"observation 暴露"这两条曲线。**

### 9.2 现有方案的局限:trajectory 压缩既破坏 cache 又损失信息

面对成本问题,当前最主流的做法是 **trajectory 压缩**:当历史太长时,用摘要、滑动窗口或选择性丢弃把早期内容压短再喂回模型。它让名义 token 数变少,但在带 prompt cache 的现代 API 上,既省不到钱,还常常损害性能。

原因有两个。**第一,它破坏 cache。** 推理服务对未改变的 prompt 前缀提供极低价的 cache 命中(命中价可低到未命中的几十分之一),而真实轨迹的命中率往往在 99% 以上。摘要一旦改写历史前缀,后面所有 token 就从"缓存价"退回"全价",省下的那点 token 远远抵不上失去缓存的涨价。**第二,它有损且不可逆。** 被压掉的细节(某个函数签名、某行报错、某个路径)事后往往正是需要的,agent 却再也取不回来,只能基于失真的历史继续,导致重复探索甚至失败。归根结底,压缩是在"已经付过费"的历史上做事后裁剪——救不回花掉的钱,还削弱了 agent 的信息基础。**真正该做的,是从源头少产生昂贵操作,而不是事后压缩它们。**

### 9.3 本文方案:基于自进化算法的成本优化框架

本文提出一个**基于 agent 自进化(self-evolve)的成本优化框架**。我们不在推理时压历史,而是在**进化阶段**改造 agent 自身:先让 baseline agent 在代码任务上跑出轨迹,再从轨迹中形成两条并行但共用验证门的候选链。第一条把跨任务重复的昂贵执行子图收缩为 **native function tools**(`tools.json` + `executor.py`);第二条把可识别的无效决策尾部收缩为经过验证的 **instruction policy**(`instruction.md`),例如何时早退、何时用低成本替代验证、何时尝试有边界且可回滚的风险动作。进化后的 agent 用更少的轮次、更短的 observation 完成同样的工作。整个过程是一个闭环:rollout → 分析 → 候选干预 → 验证 → 再 rollout。

自进化能解决压缩解决不了的问题,原因很直接。**它在源头减负,而不是事后压缩。** 进化出的工具在**产生 observation 之前**就限定了输出:读文件只回相关片段,跑测试只回结果和关键日志,长输出落盘、按需再取。昂贵的内容从一开始就没进上下文,因此根本不需要改写历史前缀,**天生不破坏 cache**。而且工具和 instruction 是跨任务持久的资产,一次进化的收益能摊到之后所有任务上。更进一步,把定位、读取这类确定性工作交给受控工具,既省 token,又为真正的推理和修改腾出上下文预算,从而**降低"因上下文膨胀或步数耗尽而失败"的比例**。所以我们的目标是双向的:在成功率不下降的底线上,**主动争取成功率提升**。

### 9.4 技术难点与解法

要把这个想法做成可靠的框架,有两个本质难点。本文把整套方法称为 **Validated Cost-Aware Graph Contraction(VCGC)**。

**难点一:如何发现自进化过程中值得优化的昂贵操作?** 难点在于,同一个意图在不同任务里长得很不一样(有的用 `rg`、有的用 `grep`,路径参数各不相同),直接比命令字符串认不出它们是同一模式;而让 LLM 自由概括又贵、又不可复现。我们的解法是**确定性归一化 + 图挖掘**:先把每次调用规范化成统一的语义标签,抹平表面差异,再在执行图上挖出跨任务反复出现的昂贵子图,作为值得沉淀成工具的候选。

**难点二:如何在自进化闭环里降本,同时保证正确性?** 我们省钱的手段,是把一串操作收缩成一个工具,工具内部照常干活,但只返回必要的结果、把中间又长又贵的 observation 藏起来不进上下文(因为没有改写历史前缀,这样做天生不破坏 cache,这也是它区别于事后压缩、能真正省钱的原因)。可是"藏信息"本身就有风险:工具可能恰好藏掉了后续真正需要的内容,悄悄改变 agent 的行为、导致失败——**省得越狠,越容易出错**。instruction policy 的风险更隐蔽:失败轨迹只能说明“这里可能浪费”,不能直接证明应该早退、跳过检查或承担风险。我们的解法是给两类候选都加**验证门**:工具先做原场景/下游一致性重放,policy 则进入隔离的单候选 paired canary;二者最后都必须在没参与挖掘的新任务上满足 non-inferiority 与成本条件。验证通过才收录,registry 可随时回退。正因为有这道门兜底,我们才敢把目标定成双向的:在成功率不降的底线上,主动争取它变得更高。

综上,VCGC 是一个自洽的双通道闭环。执行通道是**成本标注的执行图 → 跨任务重复子图 → 边界保持的工具收缩**;决策通道是**决策 episode → 正负样本 → policy hypothesis → 单候选干预**。二者最终都经过 candidate-specific paired canary 和 held-out non-inferiority 门,再编译进同一个 registry。它从源头削减轮次和 observation 暴露,不破坏 cache,并为成功率的提升留出空间。

---

## 10. Method

### 10.1 Overview

我们的框架叫 **VCGC(Validated Cost-Aware Graph Contraction)**,是一个进化闭环:baseline agent 先在代码任务上跑出一批轨迹,框架从这些轨迹里挖出"值得优化的昂贵操作",把它们编译成新的 native function tool,验证通过后交给下一轮 agent 使用,如此循环。

整章就回答 §9.4 的两个难点:**§10.2 讲怎么发现值得优化的昂贵操作,§10.3 讲怎么在闭环里安全地降本。**

先看一个贯穿全章的例子。很多任务都要做同一件事:**搜索一个符号 → 读它周围的代码 → 再动手改**。前两步是确定性的、跨任务重复的,而且返回的 observation 又长又贵。VCGC 会把"搜索 + 读取"收缩成一个 `locate-symbol` 工具——agent 一次调用就拿到有界的相关上下文,3 轮 LLM 决策压成 1 轮,约 25k tokens 的中间输出压到约 4k;而"怎么改代码"仍然留给 agent 自己决定。下面两节,就是让这件事既能自动发现、又能保证不出错。

### 10.2 应对挑战一:发现值得优化的昂贵操作

要发现"值得优化的昂贵操作",得先回答两个问题:成本到底记在哪(§10.2.1),以及怎么在成千上万次调用里认出跨任务重复的那些(§10.2.2)。

#### 10.2.1 为什么这样定义图,以及怎么把它建出来

**为什么用图。** 我们的目标是找出"一串经常一起出现、又贵的操作",而"一起出现"本质上是操作之间的依赖关系,"贵"要能落到具体的某几步上。一张带成本标签的依赖图恰好能同时表达这两件事,所以我们把每条轨迹建成一张 DAG $G=(V,E,X,C)$。

**节点和边怎么定。** 一个节点 $v\in V$ 就是**一次 LLM turn**,并且保留该轮里的所有 tool call——因为同一轮的多个 call 是一个原子决策,拆开会破坏语义。一条边 $e\in E$ 是两步之间的**依赖**(前置 → 后继),表示"后一步用到了前一步的产物"。这里有一个刻意的选择:**轨迹的先后顺序(谁在谁之后发生)只作为 metadata 存着,不当成依赖边**。因为时间相邻不等于有因果关系,把"下一步"当依赖会挖出大量假模式;我们只让真实的数据依赖进入 $E$,依赖标注的质量再由后面的 replay(§10.3.3)和人工抽样来检验,而不是默认它就是对的。每个节点还带一组属性 $X$:操作类别(读/写/验证)、参数与文件角色、返回码、observation 的 token 数、以及该轮之后的 repo diff hash——这些是后面归一化和挖掘要用的。

**成本标在哪:一次 observation 贵在"之后"。** 每个节点带两类成本标签 $C$:一是**这一轮自己的 API 成本**;二是这一轮的 observation 的**暴露成本**——它一旦进入对话历史,就会在**之后每一轮 prompt 里被反复携带**,按真实的 prompt-cache 折扣累计计价。这一点是整套成本观的关键:一次读文件真正贵的地方,往往不是产生它的那一轮,而是它在后续所有轮次里的长期驻留。所以后面凡是算"收缩能省多少",都用重建"收缩前 vs 收缩后"的完整 token 账本来算,而不是简单数省了几轮。

**建图时只把成本锚到"真正产生结果"的步骤。** 如果偷懒从轨迹最后一个 action 往回找,常常锚到 submit、`git status` 这类不产生结果的收尾动作上,挖出来的东西没有意义。所以我们先在图上标出 **outcome anchor**——真正改出代码、并且通过了测试的那些步骤,成本和后续分析都围绕这些 anchor 及其依赖闭包展开。为了让可用轨迹多一些,我们把训练轨迹的"成功"放宽到**部分通过**(修好了此前失败的目标测试、又没打破原本通过的测试);但即便如此,anchor **只对准确实通过了测试的那部分改动**——放宽的是"哪条轨迹能用",绝不是"哪一步算结果"。

#### 10.2.2 怎么归一化,以及怎么挖出重复的昂贵操作

**先归一化:让不同写法的同一操作能对上。** 同一个意图在不同任务里表面差别很大——有的用 `rg`、有的用 `grep`,路径、参数各不相同。直接比命令字符串,认不出它们是一回事;而把整段日志丢给 LLM 让它自由概括,又贵、又不可复现、还没法审计。我们的做法是一个**确定性的、可以拒绝的分层归一化器**,一层层把一次调用翻译成统一的语义标签:

1. **结构化 tool schema**:如果这一步本来就是个 native function tool,直接读它的 schema,最可靠;
2. **shell 语法树**:普通 shell 命令先解析成 AST,而不是按空格 split,避免管道、引号、子命令把字符串切错;
3. **命令规则表**:用一张 `grep/rg/find/pytest/...` 的规则表,把命令识别成 `SEARCH / FIND / READ / TEST` 等操作类别;
4. **路径角色**:把具体路径抽象成角色(源码目录、测试文件、临时文件……),抹掉与任务绑定的具体名字;
5. **参数角色**:把参数抽象成角色(要搜的符号、行号范围、超时……)。

每个标签都带一个**置信度**,**拿不准或副作用不明的调用直接拒绝、不进入挖掘**,只在少数低置信情形才退回让 LLM 兜底,而且它的判断同样要过后面的验证门。归一化之后,`rg Foo` 和 `grep Foo` 就都变成同一个 `SEARCH(symbol=Foo, path=<src>)`。

**再挖掘:找跨任务反复出现的重复子图。** 在归一化后的图上,我们找那些**在多个不同任务里都出现**的依赖连通子图。为了高效判断"两个小子图是不是同一个模式",我们给每个小子图(实际只需 size 1–5)算一个 **Weisfeiler–Lehman hash**,hash 相同的归为同一个 motif。这里有个关键的计数规则:**support(支持度)按去重后的独立任务数算**,同一个任务里出现很多次也只算一次——这样才不会被某条啰嗦的轨迹刷出一堆假高频。最后再叠加 §10.2.1 的成本标签:一个 motif 值不值得关注,取决于它**既跨任务重复、又确实贵**(内部 observation 暴露成本高)。挑出来的这些"重复且昂贵的多步操作",就是下一步要收缩成工具的候选。

### 10.3 应对挑战二:在闭环里安全地降本

拿到候选之后,降本要闯四关:把它收缩成一个"从源头就省"的工具(§10.3.1),判断它是不是真划算(§10.3.2),验证它没把行为改坏(§10.3.3),以及在多轮里不让坏工具越滚越大(§10.3.4)。

#### 10.3.1 收缩:让省钱发生在"产生 observation 之前"

**边界直接定义工具长什么样。** 一个重复子图的**边界**天然告诉我们工具的接口:从外面进入子图的信息就是**输入**,子图下游真正要用的信息就是**输出**,而中间那些又长又贵的 observation 属于工具**内部**、不再逐条塞回对话。拿贯穿全章的例子:`FIND → SEARCH → READ` 这个子图,输入是 `symbol` 和 `search_path`,输出是 `file / line / 有界的局部代码`,内部三次读取的完整内容全部封在工具里。它只有读操作,所以标记 `state_effects=[]`(不改代码)。

**关键:输出在"产生之前"就被限定住,所以不破 cache。** 这一步是整个框架能真正省钱的核心。工具在**生成输出之前**就把它卡在一个上限内——读文件只回相关片段,跑测试只回 exit code、失败用例名和日志尾部,超出的部分落盘、返回一个游标供 agent 按需再取。于是昂贵内容**从一开始就没进入上下文**,自然也就**不需要事后去改写对话历史**;而不改写历史前缀,就**天生不破坏 prefix cache**——这正是它和 trajectory 压缩的根本区别:压缩是在已经付过费、已经进了 cache 的历史上做有损裁剪,而收缩是让昂贵内容压根不产生。需要如实说明一点:这个输出上限是我们在 runtime 外层加的一个统一 output-clamp、并由 §10.3.3 的验证门强制执行的**契约**,不是底层 runtime 自带的能力,所以不能靠 prompt 自觉,必须在验证阶段真的去检查、拒绝超预算的输出。第一版我们只做只读的 search/read/test-log 类工具(容易验证、不会改坏代码);带 edit 的有状态工具必须先在契约里声明它会改哪些文件,才允许引入。

#### 10.3.2 筛选:只留下真的划算的工具

**造工具本身是有代价的。** 一个工具的 name/description/schema 会在**每一轮 prompt 里**都占 token,工具太多还会让 agent 更容易选错。所以对每个候选算一笔净账:

$$Saving = Cost_{\text{原方案}} - Cost_{\text{用工具}} - Cost_{\text{schema占用}},$$

其中 $Cost_{\text{用工具}}$ 是改用工具后的**完整** API 成本(按这次出现时的真实上下文,把两边的 prompt/cached/completion token 都重建出来再折算),$Cost_{\text{schema占用}}$ 是它长期挂在 registry 里的开销。

**不能只看平均值。** 一个工具要是在少数任务里省很多、在其余任务里经常失败,平均数会把风险盖住。所以我们对它跨任务的 saving 做 bootstrap,取一个保守的**置信下界**,只有下界仍然大于 0 的工具才留下——直观说就是:即便考虑样本波动,我们仍有把握它是省钱的。如果一次挖出很多候选,就在固定的 registry 预算下按"**边际新增节省**"贪心地挑,两个工具覆盖同一批步骤时,重叠部分只算一次,避免收进一堆功能重复的工具。

#### 10.3.3 验证:确保省钱没有偷偷把行为改坏

**省钱靠"藏信息",而藏信息天然有风险。** 我们省钱的手段就是把中间的 observation 藏进工具内部;可万一图里有条依赖标错了、工具恰好藏掉了下游真正需要的东西,agent 的行为就被悄悄带偏——**省得越狠,越容易出错**。所以净收益为正还远远不够,每个候选必须依次闯过三道门,才准进工具库:

1. **原场景重放**:把工具放回它当初出现的那些 occurrence、在相同的 repo 状态上跑一遍,要求它仍能复现出原来的关键 `file:line` 和代码上下文,而且输出不超预算;
2. **下游一致性重放**:在工具之后,把历史里已经确定的下游改动步骤重放一遍,检查最终 diff 和目标测试是否还和原来一致(有状态的工具还要直接比对改了哪些文件);
3. **新任务实测**:把工具临时装进 registry,在一批**完全没参与过挖掘的新任务**上重新跑 agent,确认成功率满足预先声明的 non-inferiority 底线、同时成本确实下降。

**三关全过,才写成一张"工具实现卡"交给 evolve agent。** 这张卡很短:它记录这个模式是什么、输入输出、输出上限、saving 的均值和下界、以及各道 replay 的通过率。evolve agent 只负责照着这张已验证的卡去实现 `tools.json + executor.py`。也就是说,**判断"该做什么、值不值、安不安全"由图算法完成,evolve agent 不再凭 prompt 猜工具**,它只把已经验证过的契约翻译成可执行代码。

#### 10.3.3.1 Instruction policy:样本只提出假设,干预才能签发规则

`instruction.md` 不能把失败轨迹里的相关性直接写成因果规则。一个失败且很长的轨迹不证明“更早退出”一定更好;一次没跑测试但最终成功不证明“跳过验证”安全;一次高风险动作碰巧成功也不证明值得推广。因此 instruction 使用独立的数据链:

1. `InstructionSampleBuilder` 从 discovery trajectory 抽取三类 **decision episode**。`early_exit` 记录重复无新证据/硬阻塞的窗口以及后来又取得进展的反例;`verification_skip` 记录修改后验证确实抓到问题的负例,以及外部 verifier 成功但 agent 内部没有验证的待干预假设;`bounded_risk` 只识别保存前态、随后验证的可回滚动作,缺少任一边界就作为负例。
2. `InstructionCandidateBuilder` 按独立 task 聚合。样本中的 `hypothesis` 不是 positive label;只有跨任务重复 support 且存在相似触发条件下的 negative control,候选才有资格进入一个**单独的 policy-only canary arm**。
3. canary arm 一次只放一个 instruction candidate,并保持 `tools.json`、`executor.py` 字节不变,避免把工具收益错误归因给 instruction。框架要求 policy 在轨迹行为指标上被真实触发,而不是仅仅出现在 prompt 中。
4. `InstructionValidationGate` 重新计算 discovery/held-out 的 paired 成功率和成本。跳过验证的 treatment 仍必须通过框架外部 verifier;bounded-risk treatment 还必须证明回滚路径可用且没有 external side effect。也就是说,**下游 agent 可以跳过一次低价值验证,框架绝不跳过对“允许跳过”这条规则的验证**。
5. 只有 candidate fingerprint、触发证据、成本下降、零 correctness regression 和 held-out 隔离全部满足,才签发 `InstructionCard`;compile agent 只负责把 card 翻译成短的 tool-agnostic contract。

三条 baseline governance 不宣称来自样本,它们是永远存在的安全边界:没有新证据时不要重复同一思路;无法执行相关验证时必须使用最低成本替代并披露;风险动作必须有界、可逆、可立即检查,不可逆或外部副作用必须先取得授权。样本只负责学习这些边界内更具体的触发条件,不得学习固定的无条件 step 上限、泛化的“跳过测试”或不可逆动作。

#### 10.3.4 闭环:别让一个坏工具越滚越大

**进化是循环,坏工具会自我强化。** 这一轮造出来的工具,会出现在下一轮的轨迹里被继续使用;一个"看着省钱、其实有害"的工具一旦进库,就可能被后面每一轮反复复用、错误被不断放大,甚至新工具还建在它之上。所以从第二轮起,我们要做的不是从头再造一套,而是根据真实使用情况对已有工具做**保留 / 改进 / 合并 / 删除 / 新增**的决策(依据是它被采用得多不多、成功率高不高、是否老要打补丁、以及 saving 下界还正不正)。

**几条硬规矩挡住自我强化:** 一个工具的成功证据只能来自它**本轮真实的调用和 verifier 结果**,不能拿它所代表的"历史老模式"给自己背书;工具的新旧版本分开统计,不能用旧版本的成绩替新版本担保;每一轮都留一批**固定的 held-out 任务**,防止 registry 在自己产生的数据分布上闭环过拟合;任何改进、合并、新增,都必须**重新过一遍 §10.3.3 的三道门**。因此多轮进化不是"工具越堆越多",而是一个带版本血缘、能实测反馈、也随时能回退删除的受控更新过程。

---

## 11. v8 工程实现方案

### 11.1 范围与不可违反的约束

v8 复用 v6 的 benchmark rollout、`tools.json + executor.py + instruction.md` 注册方式和 native-tool runtime，但替换“直接把 contrastive sample 交给 evolve agent 猜产物”的中间过程。新的主链路是：

```text
                         ┌─ execution subgraph → tool candidate → replay gates → ToolCard
rollout → annotate/graph ┤
                         └─ decision episode → policy hypothesis → paired canary → InstructionCard

ToolCard + InstructionCard → compile → held-out registry gate → promote/rollback
```

第一版只自动接纳 `SEARCH / FIND / READ / TEST` 这类只读候选。无法确定副作用、归一化置信度不足、没有 outcome anchor、saving 置信下界不为正、三道验证任一道缺证据的候选均 fail-closed：可以写入审计报告，但不能进入 evolve prompt 或正式 registry。时间相邻只记录为 `turn_index`，绝不自动生成依赖边。

新增 instruction 链使用 `vcgc.v8.2` schema，是对 v8.1 的加法扩展：原有 `graphs/motifs/candidates/validation/cards` 字段与 tool runtime 不变；旧 cycle 没有 instruction artifacts 时按空集合处理，仍可读取、验证和继续 tool-only evolve。

### 11.2 模块与数据产物

实现文件为 `src/evolve/evolve_v8.py`，继续调用 v6 的 `RolloutAgent` 和 native-tool 部署逻辑，新增以下确定性组件：

1. `ShellNormalizer`：优先读取结构化 function call；对 bash 使用 `shlex` 解析管道/复合命令，再用白名单规则归一化为语义操作。输出 `op / path_role / arg_roles / confidence / rejected_reason / state_effects`。
2. `CostLedger`：从每轮 usage 中读取 prompt、cached prompt 和 completion token；从 observation 估算其 token 量，并按它在后续轮次中的 cache/non-cache 比例计算 exposure cost。所有价格均为 CLI 参数，不把某家模型价格写死。
3. `ExecutionGraphBuilder`：一轮一个节点、该轮所有 tool calls 保持原子性；只消费 trajectory 中显式的 `dependencies`；识别通过 verifier/测试支撑的 write 作为 anchor，并取其依赖闭包。输出每任务一个 `graph.json`。
4. `MotifMiner`：仅枚举 anchor 闭包内、弱连通、大小 1–5 的只读子图；用确定性的 WL refinement + canonical edge serialization 生成 hash；support 按独立 task 去重，保存 occurrence 边界和覆盖步骤。
5. `CandidateSelector`：重建每个 occurrence 的“原轨迹 vs 收缩后”账本，扣除 schema 常驻成本；以固定随机种子的 task-level bootstrap 得到 saving 下界；在 registry token 预算内按去重覆盖后的边际 LCB 贪心选择。
6. `ValidationGate`：显式记录 `scenario_replay / downstream_replay / heldout` 三关。它只读取真实产生的证据文件并校验 diff、关键 `file:line`、测试、输出上限、成功率 non-inferiority 和成本下降；缺文件或字段就是 pending/failed，绝不默认通过。
7. `InstructionSampleBuilder / InstructionCandidateBuilder`：从 discovery 图提取 early-exit、verification-skip、bounded-risk episode，保留 hypothesis/negative 证据角色并按独立 task 聚合；观察到成功本身不能生成 positive label。
8. `InstructionValidationGate / InstructionCardCompiler`：每次只验证一个 policy-only arm；要求行为真实触发、paired cost 下降、无 correctness regression、held-out 不与 support 重叠。verification-skip 额外要求外部 verifier，bounded-risk 额外要求 rollback 与 external-side-effect 证据。
9. `ToolCardCompiler`：只把三关均通过、版本与本轮证据一致的候选写成短 tool card；evolve agent 只看到卡片并负责翻译为 `tools.json + executor.py`。生成后继续使用 v6 parser 校验，并额外执行 schema/executor 同步检查和 output-clamp smoke test。
10. `RegistryManager`：每轮保存版本、父版本、采用次数、saving LCB、验证证据和状态；新 registry 先写 staging，held-out 通过后原子 promote，否则保留上一版并记录 rollback。

每轮目录固定为：

```text
cycle-N/
  split.json                 # discovery / held-out，首次生成后固定
  graphs/<task>.json
  motifs.json
  candidates.json
  validation/<candidate>.json
  cards.json                 # 只含 fully validated tool 候选
  instruction_samples.json   # hypothesis / negative decision episodes
  instruction_candidates.json
  instruction_validation/<policy>.json
  instruction_cards.json     # 只含 paired-canary validated policy
  staging/                   # 待验证 registry
  registry.json              # 版本血缘与 promote/rollback 决策
  report.json
```

这些 JSON 都带 `schema_version`，并用排序 key 和稳定 hash 保证同一输入可复现。

### 11.3 三道验证门的文件契约

工具候选 `candidate_id` 的验证证据是 `validation/<candidate_id>.json`。`scenario_replay` 必须逐 occurrence 给出关键位置集合、输出字符数和复现结果；`downstream_replay` 必须给出原/新 diff hash、目标测试结果和声明的 state effects；`heldout` 必须给出彼此独立的 baseline/treatment task id、成功数、总数和完整成本。验证器同时要求 held-out task 不与 discovery support 相交，并用单侧 non-inferiority 下界检查 `success_treatment - success_baseline > -margin`。

instruction 候选的证据是 `instruction_validation/<policy_id>.json`，包含 candidate-specific discovery/held-out baseline 与 treatment 行。treatment 必须显式记录 `policy_triggered`；verification-skip 记录 `external_verifier_passed`；bounded-risk 记录 `rollback_verified` 与 `external_side_effects`。任一门的 `passed` 都不能由输入文件直接宣称，而由 gate 根据原始字段和 candidate fingerprint 重算。

由于真正的原 repo 状态重放和 held-out rollout 需要 benchmark 容器，CLI 将验证拆为两个动作：`prepare` 产出候选与待运行的证据模板；外部 runner 填充原始结果；`validate` 重算并签发 card。`run` 串联完整闭环。`--dry-run` 只展示计划和产物路径，不会把候选标为已验证。

### 11.4 CLI 与闭环行为

提供四个入口：

```bash
python -m src.evolve.evolve_v8 prepare --run-dir <rollout> --work-dir <cycle-dir>
python -m src.evolve.evolve_v8 validate --work-dir <cycle-dir>
python -m src.evolve.evolve_v8 compile --work-dir <cycle-dir> --scripts-dir <registry> \
  --config <yaml> --execute
python -m src.evolve.evolve_v8 promote --work-dir <cycle-dir> --scripts-dir <registry> \
  --postcompile-evidence <json>
python -m src.evolve.evolve_v8 run --benchmark swebench --config <yaml> \
  --eval-cases-file <cases> --scripts-dir <registry> --work-dir <work>
```

`prepare` 可独立用于离线检查图、motif 和 decision episode；`validate` 在证据不全时返回非零并列出缺口；`compile` 在 tool/instruction card 都为空时拒绝修改现有 registry；`run` 固定 discovery/held-out 切分，cycle 1 可复用 baseline，此后只 rollout 已 promote 的 registry。instruction canary 与 tool canary 不在同一实验臂中出现；两类候选同时存在时，多轮实验默认 tool-first 交替，失败过的稳定 `policy_id` 会被后续 cycle 隔离。policy-only compile 后若模型误改工具文件，框架从 canary 前快照强制恢复 `tools.json/executor.py`。每一轮失败都保留上一版可运行 registry，不做半完成覆盖。

### 11.5 验收标准

- 单元测试覆盖 shell 归一化/拒绝、显式依赖而非时间边、anchor 闭包、task 去重 support、WL hash 稳定性、exposure 账本、bootstrap 可复现、重叠预算选择和三门 fail-closed。
- 集成测试用最小 synthetic trajectory 跑通 `prepare → validate`，证明无证据不能生成 card，三门真实字段满足时才能生成。
- 对生成 registry 运行 JSON、AST、schema/executor 同步和输出上限检查；任何失败均不 promote。
- 报告同时给出 raw token、按 cache 价格折算的成本、成功率、候选各门状态和 rollback 原因，使论文中的每个结论都能回溯到任务级证据。

---

## 12. 基于 `results/prep` 的实现审计与效果预估

### 12.1 数据画像

本节只读取 `results/prep` 下的原始 `trajectory.json`、`result.json`、`verifier/reward.json` 和 log，不使用其中已有的 v7/contrastive 产物。价格按本次 rollout 的 `deepseekv4_flash.yaml`：输入 1 元/M token、输出 2 元/M token、cache 0.02 元/M token。

| benchmark | case | 有效 action turns | baseline 成功 | prompt / cached / completion token | cache ratio | observation chars | 实际 API cost |
|---|---:|---:|---:|---:|---:|---:|---:|
| deep-swe | 16 | 1,934 | 1/16 (6.25%) | 154.16M / 152.97M / 0.97M | 99.23% | 3.48M | 6.175 元 |
| swebench-verified | 16 | 988 | 11/16 (68.75%) | 26.81M / 26.30M / 0.32M | 98.11% | 1.40M | 1.681 元 |
| swe-atlas-tw | 16 | 533 | 4/16 (25.00%) | 18.52M / 17.93M / 0.29M | 96.84% | 1.67M | 1.520 元 |
| swe-atlas-qa | 16 | 0 | 不可用 | 0 | — | 0 | 0 |

swe-atlas-qa 的 trajectory 只有 system/user 两步和 `dependencies={"0":[]}`，说明 rollout 本身没有留下 agent action，不能拿来挖 motif 或估计效果。其余 48 个 case 合计 3,455 个 turn、约 9.376 元。三个有效集合的 median turn 分别是 115、56、31.5；observation 单次最大分别达到 33,650、25,234、32,242 chars，符合“长 observation + 多轮累计暴露”这一目标场景。

cache ratio 达到 96.84%–99.23%，所以 introduction 对 trajectory rewrite 的担忧在本数据上成立：历史前缀非常便宜，重写它容易得不偿失。v8 应优先减少**未来 LLM call 数**以及 observation 第一次进入 prompt 时的未缓存 token，而不是把已经缓存的旧前缀摘要掉。

### 12.2 真实命令分布带来的实现调整

原始日志不是简单的 `rg Foo`，而是大量 `cd /repo && ...`：deep-swe 1,426 次、SWE-bench 711 次、swe-atlas-tw 208 次；pipeline 分别有 585、394、264 次。最常见的有效主体是 `cat/nl/sed/grep/find/ls`，验证则同时包含 `python -m pytest`、`go test`、`cargo test`、`yarn test`。因此代码允许无副作用的 `cd/export` 前缀、stderr 丢弃/合并和只读 pipeline，并补齐上述命令；heredoc、重定向、任意 Python/Node 脚本、install/build 和未知副作用仍拒绝。

调整后，确定性 normalizer 能安全接纳为只读/验证操作的 turn 比例约为 deep-swe 50.1%、SWE-bench 44.1%、swe-atlas-tw 58.5%；被拒绝比例仍有 44.3%、55.0%、40.7%。这个 recall 不算高，但符合 v8 第一版 fail-closed 的定位：复杂脚本宁可不挖，也不能误当只读工具收缩。

### 12.3 在真实数据上运行 `prepare` 的结果

固定 75% discovery / 25% held-out、`min_support=2` 的诊断运行结果如下（当前正式默认值为 2）：

| benchmark | discovery / held-out | 可用图（含 held-out） | motif | 入选候选 | 历史非重叠 saving 上界 |
|---|---:|---:|---:|---:|---:|
| deep-swe | 12 / 4 | 12/16 | 247 | 10 | 约 discovery cost 的 6.4% |
| swebench-verified | 12 / 4 | 11/16 | 31 | 10 | 约 6.8% |
| swe-atlas-tw | 12 / 4 | 4/16 | 0 | 0 | 0% |

这里的 6.4%/6.8% 是“历史 occurrence 全被未来 agent 采用、按非重叠步骤重算、已扣 schema”的**上界**，不是线上收益承诺。deep-swe 的 247 个 motif 中 213 个 support 只有 2；SWE-bench 的 31 个里 23 个 support 只有 2。当前默认 `min_support=2` 允许在两个不同 case 中重复的模式进入候选阶段，随后仍由候选筛选、编译校验和 paired canary 淘汰偶然模式。swe-atlas-tw 在 held-out 切分后没有跨 discovery task 重复 motif，说明当前 16-case 样本仍可能不足，不能把候选数量直接视为有效工具数量。

deep-swe 有 6 个 discovery task 触发每图 5,000 个子图的确定性枚举上限。这避免了组合爆炸和 OOM，但也意味着 motif 排名带有截断偏差；正式实验应改成闭合/最大 motif、beam search 或两阶段频繁边扩展，而不是简单提高上限。

若理想化地把**所有** observation 都压到 1,000 token、且 agent 从不需要回取，三套有效数据的 token 账本上界分别可省约 20.3%、11.8%、24.0%。真实工具只覆盖其中一部分，而且 cursor 回取会吃掉收益；结合实际 motif 覆盖，第一轮更可信的预期是：deep-swe / SWE-bench 降 2%–5%，swe-atlas-tw 目前 0%–2%，三套按现有成本加权约 2%–4%。随着 case 数增加、support≥3 的 motif 变多并真正压掉 LLM turn，目标可向 5%–10% 靠近；当前数据不支持宣称 20% 以上。

### 12.4 代码对设计方案的兑现程度

已兑现且有测试覆盖的部分：

- 一 turn 一节点、显式 dependency edge、时间顺序不造边；
- 确定性归一化、置信拒绝、路径/参数角色和只读白名单；
- outcome anchor、partial-pass 且不破坏 P2P 的轨迹准入、anchor 依赖闭包；
- cache-aware direct/exposure ledger、task-level support、稳定 WL hash、size 2–5 连通 motif；
- task bootstrap LCB、schema 成本、registry token budget 和重叠覆盖去重；
- 固定 discovery/held-out split、三门证据重新计算、缺证据 fail-closed；
- fully validated card、staging registry、静态 schema/executor/clamp 检查、post-compile 证据后原子 promote 和版本血缘。

仍是部分实现、不能在论文里写成“已经解决”的部分：

1. shell 层使用 `shlex` tokenization + 白名单语法规则，不是真正覆盖 Bash 全语法的 AST；复杂 heredoc/子 shell 会拒绝。
2. 原始 trajectory 没有每 turn 的 repo diff hash；当前 anchor 用 annotator 的 `op_type`、内部通过测试和 external partial verifier 组合推断，尚不能把每个 F2P 测试精确归因到某次 write。
3. observation token 用 chars/4 估算；cost counterfactual 考虑首次未缓存、后续 cache ratio，但没有调用模型 tokenizer 精确重放每个 prompt。
4. `experiment` 已能执行 16-case paired canary、完整性检查、逐 case 回归检查、成本检查、promotion/rollback 和最多三轮闭环；但“恢复每个 occurrence 的原 repo 状态并独立重放 downstream diff”仍只有 evidence contract，尚未成为独立的 container producer。因此线上 canary 是最终硬门，离线三门仍不能冒充已全部自动化。
5. output clamp 不再只做静态检查：validator 会真实构造溢出输入，验证顶层整数游标、续读推进、4000 字符上限、`max_matches=1` 的提前停止，并用 AST 拒绝 `shell=True`、任意命令参数和不受限 `read/readlines`。不同工具的业务语义仍需要 paired canary 验证。

对应代码测试为 `tests/test_evolve_v8.py`，当前 17 个测试在 conda `0622`（Python 3.12.11）中全部通过。

### 12.5 能否“保证 performance，同时降低 API cost”

严格答案是：**设计有能力把它变成一个可检验目标，但仅凭当前 prep 数据和当前代码，还不能给出统计意义上的保证。** 当前实现最安全的性质是证据不足时不生成 card、不 promote，所以它能保证“不冒险上线”，代价是可能完全不降本；它不能凭离线历史保证新任务的成功率。

performance 风险相对可控的原因是第一版只收只读工具、保留 bash fallback、三门验证、固定 held-out 和 rollback。潜在正收益来自少读长输出、少耗尽 turn budget，deep-swe 的 median 115 turn 最可能受益。但现有 held-out 每套只有 4 个 task：即使 treatment 4/4 不退化，也只能说明这 4 个 case 上没有观察到回归，远不足以支撑 5 percentage points non-inferiority 的强结论。按不同 baseline success rate，通常至少需要约 150–500 个 paired held-out case（配对设计可降低实际需求），并应顺序累计而不是每轮换一批。

因此建议论文/实验把目标分两档报告：

- **Pilot gate**：当前 4–16 个 held-out 要求逐 case 无新增失败且成本下降，只允许 staging/canary；
- **Promotion gate**：累计足够多 paired case 后报告 success-rate difference 的预注册单侧下界、API cost difference 的 bootstrap 下界，再允许正式 promote。

在补齐 container replay producer、扩大 held-out、把 deep-swe 的 motif 截断改成更稳健的搜索后，“成功率统计非劣、API cost 下降”仍是可以继续研究的目标；但 §13 的真实 SWE canary 已否定了当前工具化方案能稳定达到 3%–8% 的原预估。对“成功率显著提升”目前也只有机制上的可能，没有数据证据。以现状直接宣称既保证 performance 又显著降本，会超过代码和实验能支持的结论。

---

## 13. 真实 benchmark 实验与中间产物分析（2026-07-14）

### 13.1 统一设置与执行环境

所有正式 driver、测试和 compile agent 均在 conda `0622` 中运行（Python 3.12.11、mini-swe-agent 2.4.2）。SWE evolve 使用 prep 中固定的 16 个 astropy case，baseline 为 `prep-swebench-deepseek-v4-flash-0708-144910`，目标轮数 3，并行度 16。每轮固定 12 discovery / 4 held-out；cycle 1 的 `prepare` 均得到 16 张图、11 张 eligible graph、4 个 motif、4 个 selected candidate，未触发枚举上限。

严格遵守“先 SWE，有效后再 deep-swe/DAB”：由于下面两个有效 SWE paired canary 均未通过 promotion gate，**deep-swe、DAB 和 64-case eval 没有启动**。这不是漏跑，而是 fail-closed 顺序门的预期行为；在 SWE 已观测到 performance 回归时继续花费另外两个 benchmark 的 API cost 会违反实验约束。

### 13.2 编译阶段为何经历多次失败

编译失败产物被保留而不是覆盖，它们揭示了 validator 必须检查的真实边界：

| work dir | 中间产物/问题 | 闸门行为与修复 |
|---|---|---|
| `0714-1748` | evolve agent 生成名为 `bash` 的通用 `shell=True` wrapper | canary 前人工终止；随后禁止保留工具名、任意 command/cmd/script/code 参数、`shell=True`、`os.system/popen` |
| `0714-1810` | 生成 `read_file/search_file/list_directory` 三个 primitive，而非 2+ turn contraction | schema/AST 通过但 16-case canary 暴露成本 +25%；compiler 改为只允许 card 对应的 `batch_read/search_context` |
| `0714-1835` | `batch_read` 的 schema 声明 integer offset，executor 却返回字符串 cursor；`search_context` 忽略 offset | 新增真实两页 smoke；两项均在 canary 前被拒绝 |
| `0714-1843` | 生成 `tool_name` 而不是运行时 `name`，且 validator 在排序空 name 时异常 | validator 改为畸形 schema 稳定 fail-closed；prompt 固定 OpenAI function schema |
| `0714-1846` | staging 起初没有 `.runtime`，16 个容器无法 import `evolve_tools_v6` | 完整性门得到 0 trajectory 并拒绝统计；canary 前显式向 staging 部署 runtime/config |
| `0714-1846` 后续 | `search_context` 先读取全仓库、收集全部 path/match，16 并发出现 exit 137 | 增加禁止整文件 `read/readlines`、lazy path、bounded deque、`max_matches=1` 最多打开 2 个文件的动态测试 |
| `0714-1900` | memory-bounded 版本最后一页没有返回 `next_offset` | 自动 compile-repair 回路收到具体 warning 并修复；最终 staging 全部语义 smoke 通过 |

这条链路说明“生成代码自称测试通过”不能作为证据。最终 validator 同时检查 JSON schema、候选 ID 覆盖、工具名精确一致、AST 安全性、flat arguments、结果类型、真实溢出分页和提前停止。代码级测试最终为 17/17 passed。

### 13.3 SWE canary A：`batch_read + search_context`

有效 run 为 `results/swebench-verified/v8c1-canary-swebench-212165`。16/16 trajectory、16/16 verifier、0 exception，耗时 15m38s。结果：

- success：10/16，baseline 11/16；新增通过 `13236`，回归 `13033`、`14369`；
- API cost：1.704018 元，baseline 1.680608 元，**增加 1.39%**；
- turns：1026，baseline 988；
- native calls：96；实际 native output 均不超过 4000 字符；
- gate：rollback，原因同时包含 baseline-success regression 和 cost 未下降。

逐 case 结果如下：

| case | success B→T | turns B→T | cost B→T（元） | cost 变化 | native calls |
|---|---:|---:|---:|---:|---:|
| 12907 | 1→1 | 23→40 | .0302→.0596 | +97.7% | 3 |
| 13033 | 1→0 | 57→61 | .0908→.0756 | -16.7% | 6 |
| 13236 | 0→1 | 110→106 | .1419→.1497 | +5.5% | 12 |
| 13398 | 0→0 | 52→129 | .1138→.2773 | +143.8% | 6 |
| 13453 | 1→1 | 78→53 | .1121→.0840 | -25.0% | 14 |
| 13579 | 1→1 | 55→36 | .0997→.1519 | +52.4% | 2 |
| 13977 | 1→1 | 75→62 | .1082→.0813 | -24.9% | 9 |
| 14096 | 1→1 | 72→66 | .1555→.1097 | -29.5% | 7 |
| 14182 | 0→0 | 97→111 | .1952→.1544 | -20.9% | 6 |
| 14309 | 1→1 | 39→47 | .0539→.0671 | +24.6% | 5 |
| 14365 | 0→0 | 42→44 | .0795→.0532 | -33.1% | 0 |
| 14369 | 1→0 | 48→59 | .0829→.1050 | +26.7% | 3 |
| 14508 | 1→1 | 66→54 | .1140→.0888 | -22.1% | 4 |
| 14539 | 1→1 | 45→47 | .0495→.0486 | -1.7% | 5 |
| 14598 | 0→0 | 92→67 | .1885→.1312 | -30.4% | 11 |
| 14995 | 1→1 | 37→44 | .0651→.0665 | +2.2% | 3 |

`13398` 单独增加 77 turns，是总成本反转的最大来源；排除该 case，treatment 成本比 baseline 约低 8.9%，说明 contraction 确实在一部分任务中省钱，但长尾策略漂移足以吞掉全部收益。更关键的是 `13033` 在早期 primitive canary 和最终 contraction canary 中都回归，不能把所有 performance 下降解释为随机方差。其原生调用显示 agent 先对完整文件做 `batch_read`，只看到 4000 字符首屏且没有继续 cursor，随后形成错误修复方向；“存在 cursor”不等于模型会正确回取。

### 13.4 SWE canary B：隔离 `batch_read` 的 search-only 消融

为检验回归是否主要由整文件首屏锚定导致，第二个 staging 用审计参数 `--exclude-tool-name batch_read`，只保留严格合并 SEARCH+READ 的 `search_context`。两次 16 并发启动各有 2 个容器在任何模型请求之前随机 exit 137；宿主仍有约 89 GiB available，且两次失败 case 不同。最终保留同配置 run `v8c1-canary-swebench-1670414` 的 14 个有效 trial，只对基础设施失败的 `12907/13236` 用相同 registry 补跑，并把来源写入 `composite_treatment_stats.json`。这是 infrastructure-retry composite，不把失败启动计为模型失败。

合成的 16-case 结果：

- success：9/16，baseline 11/16；新增通过 `13236`，回归 `13033/13977/14369`；
- API cost：2.058369 元，baseline 1.680608 元，**增加 22.48%**；
- native calls：45；
- 主要长尾：`13398` 52→123 turns、`14369` 48→108、`14539` 45→125；
- gate：rollback。旧统计曾把 JSON observation wrapper 长度误当 native output，误报 `13977` cap violation；现已改为解析 result payload 的真实 `output`，该项不是实际越界，但不影响 performance/cost 两项失败结论。

该消融否定了“只删除 batch_read 就能稳定恢复 performance/cost”的假设。即使只有一个只读、有界、可提前停止的 search 工具，工具 schema、首轮 instruction 和工具选择本身仍会改变 agent 的搜索路径；少数 case 的巨大 turn 膨胀决定了总成本。

### 13.5 阶段产物是否符合预期

| 阶段 | 是否符合 | 证据与结论 |
|---|---|---|
| graph construction | 基本符合 | 16 graph、11 eligible；显式 dependency、anchor closure 可复现 |
| motif mining | 符合工程契约 | 4 motif、support≥3、无枚举截断；但“历史重复”不足以预测新策略效果 |
| offline saving selection | 数学上符合，预测力不足 | LCB 为正且扣 schema，但反事实假设 agent 会用工具替换原步骤、且不改变后续策略；线上长尾证明该假设过强 |
| compiler | 最终符合 | 精确 card、无通用 shell、schema/executor 同步；自动 repair 能根据 validator warning 收敛 |
| semantic validation | 符合 | 游标、输出上限、early-stop、memory bound 都真实执行；成功拦截多批无效产物 |
| runtime wiring | 修复后符合 | staging runtime/config 可导入，16-case 有效 run 0 exception |
| paired pilot | 符合且发挥作用 | 两个 treatment 都因真实回归/增费 rollback，没有污染 active registry |
| 三轮 evolve | 未进入第 2/3 轮 | cycle 1 promotion gate 失败；继续基于失败 registry 进化会违反“坏工具不能自我强化” |
| 64-case eval | 未运行 | 只有通过 16-case pilot 的 registry 才有资格 eval；当前没有候选版本满足条件 |
| deep-swe / DAB | 未运行 | 用户要求 SWE 有效后再跑，SWE 未有效 |

### 13.6 最终判断

当前 v8 **实现了安全发现、编译、语义验证和回滚**，但没有实现“保证 performance 的同时降低 API cost”的效果目标。安全目标达成：所有已知不安全/畸形/不可分页/高内存实现均在付 canary 成本前被挡住，两次有效但有害的 registry 也没有 promote。效果目标未达成：最佳完整 canary 仍是 success -1、cost +1.39%，search-only 更差。

下一步不应直接扩大到 deep-swe/DAB，而应修改实验因果结构：对每个 candidate 做 factorial/逐工具 canary；将“agent 是否继续 cursor、后续 turn 是否增加”纳入 candidate saving，而不是只重建历史 observation 账本；给长尾设置 paired sequential stop；并用重复 seed/多次 paired run 区分策略方差与工具因果效应。在这些改变之前，v8 应被描述为一个**可靠拒绝坏 registry 的研究原型**，而不是已经验证的降本方案。

---

## 14. 放松效果门与失败后继续进化

§13 记录的是修改前的历史实验；当时 pilot 任意 baseline-success case 回归或成本没有严格下降就立即停止全部循环。考虑到 LLM rollout 的随机性，当前实现将 gate 拆成两类。

### 14.1 仍然严格的工程安全门

以下条件不属于统计波动，继续 fail-closed，不能通过提高容忍度绕过：

- baseline/treatment case 覆盖必须与固定 split 完全一致；
- tool arm 的 staging 工具必须至少被真实采用一次；instruction arm 则必须在 paired trajectory 的行为指标上被观测到触发；
- native output 不得突破字符上限；
- registry schema、executor、安全 AST、分页、early-stop 和 memory-bound smoke 必须通过；
- rollout 不完整时不能用缺失 case 计算 performance/cost。

### 14.2 默认放松的效果门

默认阈值为：

| 参数 | 默认值 | 含义 |
|---|---:|---|
| `--max-regression-rate` | 0.20 | baseline 成功 case 中，允许最多 20% 在当前随机 rollout 失败 |
| `--max-heldout-regression-rate` | 0.25 | held-out baseline 成功 case 中允许的回归比例 |
| `--max-success-drop-rate` | 0.10 | treatment 总成功数相对 baseline 成功数最多下降 10% |
| `--max-cost-increase-rate` | 0.03 | 中间进化轮允许最多 3% 成本上涨，以换取后续轮修复空间 |

只有所有效果指标都在阈值内，gate 才返回 `passed=true`；若存在被容忍的波动，状态为 `passed_with_tolerance`，并在 `tolerated_variations` 中逐项记录，而不是把回归隐藏掉。例如历史 full-tool SWE canary 的 2/11 回归率为 18.18%、成功数下降 9.09%、成本上涨 1.39%，在新默认值下属于 `passed_with_tolerance`。阈值均写入 gate JSON，避免实验后改口径。

### 14.3 第 i 轮失败后的行为

当前循环不再在第一个失败轮次 `break`。对于 compile validation 失败、rollout 不完整或 pilot gate 失败，执行：

1. 不调用 `RegistryManager.promote`，将本轮 staging 标记为 `staging_abandoned=true`；
2. active registry 和 `current_run` 保持为最近一次成功 promote 的版本；如果从未 promote，则继续使用原始 baseline；
3. 写出 `cycle-i/evolution_summary.json`，记录 baseline/treatment 成功数与成本、回归/改善 case、每个工具的调用数、回归 case 的工具归因、cost/turn 增长最大的五个 case、gate 原因和下一轮建议；
4. 将摘要追加到根目录 `evolution_history.json`；
5. 把最近三轮摘要放入第 i+1 轮 compile prompt，明确说明失败实现已放弃，禁止原样复制，要求在不削弱 tool card 安全契约的前提下修复问题；
6. 进入第 i+1 轮，直到达到 `--n-cycles`。

因此下一轮使用的是“上一版 active registry + 失败经验”，而不是用失败 treatment 的 trajectory 自我强化。`no candidates` 也会记录并继续到轮数上限，便于审计每一轮为什么没有产生可用变化。

### 14.4 验证

conda `0622` 下项目 `tests/` 回归当前为 35/35 tests passed。新增测试覆盖：

- 20% regression、10% success drop、2% cost increase能以 `passed_with_tolerance` 通过；
- 5% cost increase超过默认阈值时失败；
- 连续三轮 pilot 失败时，三轮均执行、均 rollback-and-continue、无版本被 promote，并产生三条 evolution history。
- 缺少内部验证的成功轨迹只能形成 hypothesis，不能直接签发“跳过验证”；
- instruction candidate 必须有跨任务 support、negative control、单候选 paired evidence 和真实 policy adoption；
- verification-skip 缺少外部 verifier、bounded-risk 缺少 rollback 证据时均 fail-closed。

---

## 15. 跨 codebase 采样与 native-tool 运行隔离

SWE final64 暴露出两个此前 gate 没覆盖的系统问题：字典序采样使 16 个 evolve case 全部来自 Astropy；promoted executor 又在 agent 主进程内运行，导致异常工具调用可以直接杀死整个 agent。当前实现增加以下约束。

### 15.1 Codebase-diverse evolve set

`scripts/select_evolve_cases.py` 从 Harbor flat task 目录提取 codebase identity。SWE-bench 使用 `owner/repository`，deep-swe 优先读取 `task.toml` 的 `metadata.repository_url`，DAB/DataMind 按 dataset 分组。默认 `EVOLVE_CASE_SELECTION=diverse`：对稳定哈希排序后的 group 做 round-robin，每个 codebase 先取一个 case，再开始第二轮。选择结果同时写入 `case_selection.json`，记录每个 case 的 codebase 和 selection policy。`EVOLVE_CASE_SELECTION=sorted` 仅用于复现旧实验。

最终 eval 使用同一个 selector 从排除了 evolve set 的剩余 pool 中选择 64 个 case，并构建只包含这 64 个 case 的 exact Harbor taskdir。`final_eval_cases.txt`、`final_eval_case_selection.json` 和 `experiment_split_manifest.json` 在 rollout 前写入，且交集非零时 fail-fast；rollout 后再从 trial `config.json` 提取 `eval_cases_used.txt` 与预选集合核对。因此 evolve 的 16 个 case 只用于挖掘和 gate，不能进入 final64。

### 15.2 Hard timeout 与进程隔离

evolved executor 不再由 agent 进程直接调用。稳定 runtime 为每次调用启动 disposable stdlib worker：

- 默认 hard timeout 为 30 秒，可用 `EVOLVE_TOOLS_V6_TIMEOUT_SECONDS` 调整，上限 300 秒；
- 默认 worker address-space limit 为 1024 MiB，可用 `EVOLVE_TOOLS_V6_MEMORY_MB` 调整；
- timeout 返回 `returncode=124` 和 `ToolTimeoutError` observation；
- worker crash、MemoryError、协议错误返回 `returncode=125`；
- observation 建议不要原样重试；优先缩小 evolved-tool 的 path/query 或操作范围，也可以回退到等价 bash command，由 LLM 根据上下文选择；
- pagination 的 `next_offset` 会跨 worker 边界保留；
- executor 顶层代码也只在 worker 内 import，agent 侧只做不执行代码的 AST validation。

因此 evolved tool 超时或资源异常只损失一次 tool call，不再把整个 case 变成无 trajectory 的 `exit 137`。

### 15.3 Compile gate 加强

compile prompt 现在要求路径 containment、有限的 context/offset/file/byte bounds，并禁止 `list/tuple/set` 物化递归发现结果。validator 会静态拒绝 `list(_sorted_files_recursive(...))`、`list(os.walk(...))` 等实现，避免小型 smoke 文件掩盖大仓库风险。

---

## 16. 移除固定工具类型限制

早期实现中的 `ToolCardCompiler.prototype_cards` 把所有 `SEARCH+READ` motif 强制合并为 `search_context`，其余候选强制合并为 `batch_read`。这使 registry 在 compiler agent 运行前就被人为限制为最多两个工具，违背从执行图自由发现 compound tool 的目标。该限制现已删除。

当前链路遵循以下规则：

1. 每个 selected candidate 先形成独立 evidence card，保留原始 node labels、edge signature、support tasks、representative calls 和 saving 估计；代码不再预先指定工具名或工具 family。
2. compiler agent 根据 evidence card 自由选择描述性工具名和最窄的结构化 schema。
3. 只有 input/output/execution contract 完全等价的 cards 才允许合并；不能仅因为都包含 READ 或 SEARCH 就合并。
4. 每个 candidate ID 必须在 registry 工具描述中恰好出现一次。validator 检查缺失、重复和未授权 candidate ID，但不检查固定工具名。
5. semantic smoke 根据生成后的 JSON schema 构造通用测试输入；生产 validator 不再包含 `batch_read/search_context` 名称白名单或专用分支。

这项修改移除的是工具类型上限，不等于允许无证据地任意发明工具。候选行为仍必须能收缩已观察到的多轮 motif，并继续受到只读、结构化参数、路径 containment、输出上限、timeout、memory bound、canary 和 promotion gate 的约束。

---

## 17. 开放式 instruction 候选发现

旧实现只从 `early_exit`、`verification_skip`、`bounded_risk` 三个预写模板中选择规则，导致真实轨迹即使包含稳定的工作流经验，也无法形成候选。当前实现保留这三类安全规则的兼容处理，但不再把它们作为候选类型白名单。

新增流程从执行图中归一化并聚合实际出现的行为信号，包括操作阶段转换（例如 `SEARCH→READ`、`TEST→READ`）、修改前的证据收集以及修改后的检查行为。候选名称、触发条件和规则内容由实际信号派生；新的信号可以形成新的候选类型，不需要预先加入固定类别集合。

成功轨迹只提供 hypothesis，包含相同行为的失败轨迹提供 negative control。候选仍要求至少两个不同 discovery case 的成功支持和至少一个失败对照，随后必须通过独立 paired canary。为控制 prompt 和验证预算，每轮最多保留按支持度、对照数量和风险排序后的 15 个候选；这是数量预算，不是策略类型限制。

每个候选携带 `adoption_signal`。baseline/treatment 分析会从轨迹重新计算相同信号，因此 canary 能判断开放式规则是否被实际执行，而不再只认识三个旧策略名称。

完整候选池写入 `instruction_candidate_cards.json`。为保持因果归因，`prototype_instruction_cards.json` 仍只记录当轮被隔离测试的一张规则；工具轮不会偷偷改写 instruction，instruction 轮也会保持工具文件不变。
