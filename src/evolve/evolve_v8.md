# Evolve V8：Validated Cost-Aware Graph Contraction

## 1. 一句话方案

v8 回到 v6 的简单主干：**trajectory → step-level DAG → evolve samples → tools/instruction → rollout evaluation**。唯一的核心变化是，不再用规则从单条轨迹裁出“最小 positive trajectory”，而是在多条成功轨迹的 DAG 中发现**重复、高成本、可验证的子图**，把子图收缩为一个 native function tool。

一次收缩同时解决两类成本：子图中的多轮 agent 决策被一个 tool call 替代；子图内部的长 observations 不再进入 LLM context，只向下游暴露有上限的必要输出。

本文将该方法称为 **Validated Cost-Aware Graph Contraction（VCGC）**。

### 优化目标（双向而非单向）

v6/v7 的目标是“成功率非劣（non-inferiority）前提下降成本”。v8 在此基础上**把成功率也当作可以主动改进的目标**，而不只是一个不能退步的约束：

$$
\min \; \mathbb{E}[C(\pi)]
\quad\text{s.t.}\quad
P_{\text{success}}(\pi)\ \ge\ P_{\text{success}}(\pi_{\text{baseline}})-\epsilon,
\qquad\text{并鼓励 } P_{\text{success}}(\pi)\uparrow.
$$

理由有二，且都由 §2.1 的实证数据支撑：

1. **成本与成功率可以同向改善。** baseline 在长任务上大量 turns 花在重复 read/长 observation 上，context 迅速逼近 window（deep-swe peak context 已达 162k tokens）。把定位类工作收缩成 bounded-output tool，既省 token，又给真正的推理/修改留出 context 预算，从而**降低“因上下文膨胀或步数耗尽而失败”的比例**。
2. **本项目 benchmark 的 baseline 成功率很低**（§2.1：deep-swe 严格 `reward==1` 只有 1/16），因此“成功率不能下降”不是有意义的强约束——真正有价值的是让 evolved agent 在同等成本甚至更低成本下**多解出几道题**。

因此 v8 的成功判据（success）与训练数据筛选（见 §5.4）采用**放宽定义**，并在实验中同时报告：严格 pass rate、放宽后的 partial/f2p 成功比例、以及每题成本，观察“成本↓ 且 成功比例↑”是否同时成立。

---

## 2. 对 v6 的实证诊断

### 2.1 结果快照说明

本节检查了 `results/` 中已恢复的 64-case no-evolve/eval、prep trajectories、contrastive samples，以及最终生成的 `tools.json`、`executor.py`、`instruction.md`。不同 run 存在采样随机性，下面的数据用于定位设计问题，不能替代最终的 paired non-inferiority 实验。

| Benchmark | 方法 | 平均 LLM turns | 平均 observation chars | 平均 prompt tokens | Performance |
|---|---:|---:|---:|---:|---:|
| Deep-SWE | baseline | 118.16 | 198,994 | 9,030,352 | reward 3.13% |
| Deep-SWE | v6 | 106.61 | 195,583 | 8,202,824 | reward 6.25% |
| SWE-Atlas QA | baseline | 48.16 | 172,366 | 2,151,618 | 18.75% |
| SWE-Atlas QA | v6 | 46.42 | 194,710 | 2,478,018 | 23.44% |
| SWE-Atlas TW | baseline | 45.44 | 127,763 | 2,370,757 | 18.75% |
| SWE-Atlas TW | v6 | 48.00 | 113,447 | 2,306,401 | 18.75% |
| SWE-bench Verified | baseline | 56.30 | 67,145 | 1,432,636 | 75.00% |
| SWE-bench Verified | v6 | 51.20 | 56,789 | 1,220,604 | 79.69% |

这组结果说明 v6 有时有效，但改进并不稳定地对齐两个成本来源：

- QA 的 turns 下降 3.6%，但 observation 增长 13.0%，prompt tokens 增长 15.2%；
- TW 的 observation 下降 11.2%，但 turns 反而增长 5.6%；
- 因此“减少 step 数”不能作为总成本或 observation 成本的代理指标；
- 当前 `cost_usd` 在多个结果中为 0 或 null，论文必须使用模型价格重算，不能把 0 当成免费。

### 2.2 v6 sample 构造的问题

恢复的 64 个 `contrastive_sample.json` 中：

- baseline 平均有 54.98 个 action，v6 positive 平均保留 32.72 个，但**中位数只有 1 个**；
- 18/64 个 sample 只保留一个 action；Deep-SWE 中可直接看到多个 sample 只剩 `echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`；
- 原因是 v6 从最后一个 action 反向取 dependency ancestors，而最后一个 action 经常只是 submit/status；
- 即使 sink 正确，当 `dependencies` 表示全部必要前驱时，ancestor closure 也只有一个确定答案，并不存在“最短路径优化”；
- 裁剪后的 action 可以被重放，不等于 agent 在看不到被删除 observations 时仍能自主生成同一 action。因此它不能未经验证就叫 positive sample。

### 2.3 evolve 输入与产物的问题

- Deep-SWE v6 的 8 个 evolve prompts 平均约 357k chars，最大约 579k；大量篇幅来自完整 positive/negative trajectory，而不是可执行的 tool 证据。
- 最终 tools 多数是 `read/search/edit/git/test` 的通用 shell wrapper。Deep-SWE v6 一次注册了 14 个 tools，它们每轮都产生 schema prompt overhead，但没有基于实际采用率和净收益淘汰。
- 工具会被大量采用，却仍可返回很长 observation。例如 eval 日志中一次 `read-lines` 直接返回大段源文件；这解释了“turns 下降但 context 不降”的现象。
- SWE-bench v6 eval 的 trajectory 中只观察到 `bash` 调用，虽然 registry 中存在 10 个 evolved tools，说明“生成了工具”不等于“工具被部署并采用”。
- `instruction.md` 中出现“测试失败后直接 commit”“小改动可不测试”等高风险规则。**需澄清根因**：这些规则并非全部由 evolve LLM“凭日志学出”，其中相当一部分直接来自框架的 seed 模板（`native_tools_v6.py::SEED_INSTRUCTION_MD` 里就硬编码了 “commit without running tests”“submit best-effort without full validation”）。因此它们既是 evolve 未验证的问题，也是一个可以**立即修复的 baseline 注入 bug**——v8 应先把 seed instruction 中未经验证的高风险规则删除或降级，再谈从验证子图学习行为规则。

结论是：v6 真正缺少的不是更多图结构，而是一个把**图证据、真实成本、可复用 tool 和 correctness 验证**连起来的单一优化对象。

### 2.4 可行性预实验（直接在 `results/prep` 上验证，无需重跑）

在投入完整 v8 实现前，先用一个只读脚本（`scripts/v8_feasibility.py`，直接消费 `results/prep/runs/<bench>/` 的 baseline trajectory，不重新 rollout）验证 v8 的三条核心假设。**成本一律用真实单价** `_config/deepseekv4_flash.yaml`：input=1、output=2、**cached=0.02** 元/百万 token（即 cache 命中比未命中 input 便宜 **50×**，而非 v7 `PriceModel` fallback 假设的 10×）。每 benchmark 16 条 prep 轨迹，结果见 `results/v8_feasibility_result.json`：

| Benchmark | 可用轨迹 | 平均 turns | 多-op turns 占比 | 可收缩 turns 占比 | 成本占比 unc/cache/out | observation exposure 占总成本 | READ 占 observation tokens |
|---|---:|---:|---:|---:|---:|---:|---:|
| Deep-SWE | 16 | 120.9 | 43.4% | **17.7%** | 19/50/31 | 35.9% | **63.6%** |
| SWE-Atlas TW | 16 | 33.3 | 66.8% | **17.4%** | 39/24/38 | 37.9% | 49.0% |
| SWE-bench Verified | 16 | 61.8 | 46.4% | **14.7%** | 30/31/39 | 34.1% | 48.2% |
| SWE-Atlas QA | 0（16 条全部在 agent 阶段崩溃，仅 system+user 两步 + `exception.txt`） | — | — | — | — | — | — |

三条假设的结论：

**假设 A（把多轮收缩成一轮能省 turn）——只成立于少数场景，不能当主杠杆。** 只有 **14.7%–17.7%** 的 turns 属于“连续 read/search burst 中可被收缩掉的多余轮次”（把长度 $b\ge2$ 的 burst 收成 1 轮）；与此同时 **43%–67%** 的 turns 本身已经是多-op（agent 已经用 `A && B | head` 在一轮里 batch 了 find/search/read）。因此“3 轮→1 轮”是乐观上界；turn 节省和 observation 节省在这个单-bash agent 上高度解耦，论文不能用 turn 数代理 observation 成本，也不能把 turn-collapse 当作主要收益来源。

**假设 B（observation 长期暴露是主要成本，值得 bounded output）——成立，且比重比预期更靠 READ。** 即使按真实的 50× cache 折扣，observation 的累计 context exposure 仍占总成本的 **34%–38%**；而 observation tokens 中 **READ 类占 48%–64%（Deep-SWE 高达 63.6%）**，SEARCH/FIND 合计通常 <12%。**这直接修正了 v8 原先“先做 read-only search/read 宏工具、把 test-log/READ 排后”的优先级**：真正的成本大头是**读文件的长输出**（以及 SWE-bench 上的 WRITE/patch 与各 benchmark 的 VERIFY 日志），而不是 search 结果。因此 v8 的首要动作应是**给 READ / VERIFY 这类现有高频操作加 bounded output + cursor**，而不是优先去挖 `locate-symbol` 这种 search motif。

**假设 C（有足够成功轨迹供跨任务 motif mining）——严重不足，必须放宽成功定义。** 严格 `reward==1` 的轨迹数：Deep-SWE **1/16**、SWE-Atlas TW 4/16、SWE-bench 11/16。从 1–4 条成功轨迹里挖“跨多个 task 重复”的 motif 在统计上不成立。放宽后（Deep-SWE 的 `reward.json` 提供 `partial`/`f2p` 分量）：`partial≥0.5` 有 **14/16**、`f2p>0` 有 **13/16**，训练样本量提高一个数量级。这直接支撑 §5.4 采用**放宽的成功定义**。此外 SWE-Atlas QA 的 prep 数据当前**完全不可用**（全部崩溃），任何依赖它的分析都要先重新 rollout。

预实验的总结论：**v8 的方向（bounded-output + 验证 + 跨轮 registry 管理）成立，但重心要调整**——(1) 优先 bounded READ/VERIFY 输出而非优先挖 search motif；(2) 成功定义必须放宽，否则 motif mining 无米下锅；(3) turn-collapse 只是次要收益，不能作为主叙事或主指标。这些调整已写入后续章节（§5.4 成功定义、§5.5.12 与 §5.6 的优先级、§7 的成本口径、§8 的实现顺序）。

---

## 3. 四个 high-level 技术挑战

### 挑战 1：怎样把成本归因到真正产生结果的执行片段？

最后一个 action 不是任务结果；同时，LLM 标注的 dependency 也可能缺边或多边。若 outcome 定错，后续任何图算法都会优化错误目标。

**对应技术：final-outcome anchoring + dependency-connected subgraph。**

只在 verifier 成功的训练轨迹上，从 final patch 的 producing writes 和必要验证步骤出发。候选模式必须通过 dependency edges 与这些 outcome steps 连通；时间相邻 `NEXT` 只用于顺序恢复，不当作 dependency。这样避免把 submit/status 当作成功模式，也不需要枚举复杂的异构边类型。

### 挑战 2：怎样统一优化“轮次多”和“observation 长”？

一个 observation 不只在产生它的当轮付费；它进入历史后会在后续多轮 prompt 中反复暴露。只给 step 计数或只给当轮 output tokens 计费，会系统性低估早期长输出的代价。

**对应技术：context-exposure node cost。**

给 DAG 中每个 turn 两个可测成本标签：该轮实际 API cost，以及该轮 observation 在后续 context 中的累计暴露成本。图算法用二者定位候选，再用“收缩前 vs 收缩后”的反事实 token 账本计算净节省，而不是用 step 数做代理。

### 挑战 3：怎样从 task-specific 轨迹得到可复用 tool，而不是凭 prompt 猜工具？

单条轨迹中删除了某些 steps，并不能说明应该创建什么 tool。一个 tool 应对应跨任务重复的计算结构，并有稳定的输入、输出和 state effect。

**对应技术：cost-weighted dependency motif mining + graph contraction。**

在多条成功 DAG 中挖掘重复的 dependency-connected 子图，用规范化图签名合并不同文件名下的同构模式。一个候选 tool 就是一次图收缩：子图的入边定义 tool inputs，出边定义必须暴露的 outputs，内部节点和 observations 被隐藏。

### 挑战 4：怎样保证压缩不会降低 performance？

图中缺边、错误参数化或输出截断都可能让一个看似省钱的 tool 失败。仅靠结构分数无法满足“performance 不下降”。

**对应技术：replay equivalence gate + held-out non-inferiority gate。**

候选先在历史 occurrence 的相同 repo snapshot 上重放，再在 held-out tasks 上 rollout。只有通过 state/output equivalence 且满足预注册 non-inferiority margin 的候选才能进入最终 registry。

---

## 4. 简单而足够的图模型

每条 trajectory 构成一个 cost-labeled step DAG：

$$G=(V,E,X,C).$$

- 一个节点 $v_i\in V$ 对应一次 LLM turn，保留该轮的全部 tool calls，避免破坏 multi-call 原子性；
- $E$ 直接复用 v6 的 step dependency，方向为 prerequisite → dependent；
- `NEXT` 单独存为 order metadata，不参与 dependency reachability；
- $X(v)$ 只保留挖掘所需属性：operation class、read/write/verify、参数角色、文件角色、return code、observation tokens、repo diff hash；
- outcome anchors 是节点集合 $A\subseteq V$，不再额外构造一套 TASK/ACTION/OBSERVATION/ARTIFACT 异构本体。

边类型不需要声称完备。v8 只问两个问题：两步是否存在 dependency；一个候选子图能否在 outcome-connected 区域中重复出现。dependency 标注质量通过人工抽样和 replay gate 报告，而不是默认其为 ground truth causality。

---

## 5. 核心方法 VCGC

### 5.1 先用一句话理解 VCGC

VCGC 做的事情是：**在历史成功轨迹中找到经常重复的多步操作，把它封装成一个新工具，并验证这个新工具确实能以更少轮次、更短输出完成同样的工作。**

这里的“封装”就是 graph contraction（图收缩）：原来图中有三个 step 节点，封装后用一个 tool 节点替换它们。

```text
收缩前：SEARCH → READ MATCHES → READ CONTEXT → EDIT
                    三轮 LLM 决策

收缩后：       LOCATE-SYMBOL ───────────────→ EDIT
                    一轮 LLM 决策
```

`EDIT` 没有被封装：agent 仍然阅读定位结果并决定如何修改代码。因此新工具只替代确定性的搜索、读取工作，不替 agent 作 task-specific 的修改决策。

### 5.2 本节术语

| 术语 | 本文中的含义 | 简单例子 |
|---|---|---|
| **motif（重复模式）** | 在多条 trajectory 图中反复出现的相似小子图 | 很多任务都有 `搜索符号 → 读取匹配代码` |
| **occurrence（一次实例）** | 某个 motif 在一条具体 trajectory 中的一次出现 | 在 task A 的第 3–5 轮出现了一次 |
| **macro / 宏工具** | 将 motif 封装后得到的一个新 function tool | `locate-symbol` |
| **graph contraction（图收缩）** | 用一个宏工具节点替换原来的多个 step 节点 | 三个 read/search turns 变成一个 turn |
| **boundary（边界）** | 这个子图从外部需要什么，以及下游需要它输出什么 | 输入 symbol/path，输出 file/line/code context |
| **contract（工具契约）** | 宏工具的输入、输出、状态影响、失败行为和输出上限 | `max_results=20`，失败返回 nonzero |
| **registry** | downstream agent 能看到和调用的 tools 集合 | `bash`、`locate-symbol`、`run-tests` |

本文中的 macro 不是一种额外文件格式，也不是 shell macro。它最终就是当前框架中的一个 native function tool，即 `tools.json` 中的一项及 `executor.py` 中对应的执行逻辑。后文统一称为“宏工具”。

### 5.3 一个贯穿全节的例子

假设三条成功轨迹分别修复 Python、TypeScript 和 Go 项目。文件名和命令不同，但 agent 都做了类似操作：

| Turn | Task A | Task B | Task C |
|---|---|---|---|
| $T_1$ | `find` 找源文件 | `find` 找源文件 | `find` 找源文件 |
| $T_2$ | `rg Foo` | `grep Foo` | `rg Foo` |
| $T_3$ | 读取匹配位置附近代码 | 读取匹配位置附近代码 | 读取匹配位置附近代码 |
| $T_4$ | 修改代码 | 修改代码 | 修改代码 |
| $T_5$ | targeted test | targeted test | targeted test |

依赖图都是：

```text
T1 FIND → T2 SEARCH → T3 READ → T4 EDIT → T5 TEST
```

其中 $T_4$ 产生 final patch，$T_5$ 验证 patch，所以二者是 outcome steps。$T_1$–$T_3$ 虽然不是结果本身，但通过 dependency path 支持 $T_4$。

假设每条轨迹中：

- $T_1$–$T_3$ 需要 3 次 LLM 调用；
- 三次 observation 共 25,000 tokens，其中包含大量无关文件名和完整文件内容；
- 真正供 $T_4$ 使用的只有 3 个匹配位置及附近代码，约 4,000 tokens。

VCGC 要发现的就是重复子图 $T_1\rightarrow T_2\rightarrow T_3$，并把它变成：

```json
{
  "name": "locate-symbol",
  "inputs": ["symbol", "search_path"],
  "outputs": ["file", "line", "bounded_context"],
  "limits": {"max_results": 20, "max_chars": 16000}
}
```

以后 agent 可以用一轮调用 `locate-symbol(symbol="Foo", search_path="src")`，工具在内部完成 find、search 和局部读取，只返回有界的相关上下文。于是该片段从 3 轮变成 1 轮，进入 LLM context 的 observation 也从约 25,000 tokens 降为约 4,000 tokens。

下面五个阶段只是在回答五个顺序问题：

1. 哪些历史轨迹是成功且可信的？
2. 哪种多步操作跨任务重复？
3. 如何把它定义成一个工具？
4. 它是否真的省钱且值得放入 registry？
5. 替换后是否仍能正确完成任务？

### 5.4 Stage A：先找对结果，再记录成本

#### 要解决的问题

如果从最后一个 action 开始分析，可能选中 submit 或 `git status`。因此首先要知道轨迹中的哪些 steps 真正产生并验证了 final patch。

#### 训练轨迹的“成功”采用放宽定义

v8 的 motif mining 只在“成功”轨迹上进行，但 §2.4 表明严格 `reward==1` 的轨迹太少（Deep-SWE 仅 1/16），不足以支撑跨任务重复挖掘。因此 v8 对**训练数据筛选**采用放宽的成功定义（downstream 评估仍分别报告严格与放宽两套指标，见 §7）：

一条轨迹进入 motif-mining 训练集，需满足下面任一条，并按可信度分层：

| 层级 | 判据（deep-swe 用 `reward.json`；其他 benchmark 用 verifier `reward`/`f2p`/`partial`） | 用途 |
|---|---|---|
| `full_pass` | `reward==1`（全部 fail-to-pass 通过） | 最高置信；tool + instruction 都可学 |
| `partial_pass` | `f2p>0` 且 `p2p` 未回归（如 `partial≥0.5`）：至少修好了一部分目标测试，且没打破原本通过的测试 | 可用于 tool/motif 挖掘；其锚点必须是**通过了对应测试的 write** |
| `reject` | `f2p==0` 或引入 p2p 回归 | 只能贡献 §5.8 的“重复失败”待验证假设，不产 tool |

关键约束：`partial_pass` 轨迹的 outcome anchor **只锚定到通过了测试的那部分改动**（把 final diff 与 `f2p_passed` 的测试对应起来），而不是整条 patch。这样即使一条轨迹只解出一半，被挖出的 motif 仍来自“确实产生了可验证正确结果”的子片段。放宽的是“哪些轨迹可用”，不是“哪些 step 可当结果”——后者仍严格由 verifier 证据锚定。

这样做同时服务于 §1 的第二个目标：训练集里包含更多“部分成功”的真实定位/修改片段，evolve 出的工具与 instruction 更可能帮后续 agent **把部分成功推成完全成功**，从而在降本的同时提高成功比例。

#### 做法

rollout 时增加两类日志：

1. 每次修改代码后记录 step id、changed files 和 `git diff` hash；
2. 每轮记录 prompt、cached、completion 和 observation tokens。

任务结束后，把（通过测试的）final patch 追溯到产生它的最后一次 write，并加入验证该版本的 test step。这些 steps 是 outcome anchors。

在上例中：

```text
anchors = {T4 EDIT, T5 TEST}
```

`T1–T3` 能通过 dependency path 到达 $T_4$，所以它们属于与成功结果相关的区域；最后的 submit/status 因为不支持 patch，不进入候选模式。

#### observation 为什么要单独关注

假设 $T_1$ 返回了 10,000 tokens，而且之后还有 4 次 LLM 调用。该输出可能在后续 4 轮 prompt 中被重复携带，所以它的影响远大于“产生时的 10,000 tokens”。v8 将这个累计影响称为 **observation exposure**。

若 observation $o_i$ 在后续 $r_i$ 轮仍然可见，可用下面的量定位高成本节点：

$$
C_{exposure}(o_i)=|o_i|_{tok}\left(p_{in}+r_i p_{cache}\right).
$$

它是成本归因信号，不与日志中的 API cost 直接相加，避免重复计费。最终节省量会在 Stage D 中通过“原多轮调用”和“宏工具调用”的完整 token 账本比较得到。

**必须用真实 `p_cache` 标定。** §2.4 的预实验显示，$p_{in}$ 与 $p_{cache}$ 的比值直接决定“缩短 observation”相对“减少 turn”的价值。本项目实际单价是 input=1、cached=0.02（比值 **50×**），远大于 v7 `PriceModel.from_config` 在缺省时回退的 10×。用错折扣会系统性高估或低估 exposure。即便按 50× 折扣，exposure 仍占总成本 34%–38%（§2.4），说明 bounded output 是真实有效的杠杆；但论文的主指标必须用**真实账单/真实单价**换算，不能用 fallback 权重。

### 5.5 Stage B：从多条成功轨迹中找重复模式

#### 要解决的问题

task A 使用 `rg`，task B 使用 `grep`；路径也完全不同。若直接比较命令字符串，它们不会被识别为同一模式。

#### 核心原则：不是让 LLM 自由概括命令

canonicalization（规范化）不应通过 prompt 让 LLM 自由生成一个标签。那样既昂贵，也难以复现。v8 使用一个**确定性的、可拒绝的分层解析器**：

```text
原始 tool call
   ↓
1. 读取结构化 tool name / arguments
   ↓ 如果是 bash
2. shell AST 解析命令、参数、管道和重定向
   ↓
3. command rule registry 判定操作类型
   ↓
4. path resolver 判定路径角色
   ↓
5. argument abstractor 删除具体值、保留参数用途
   ↓
CanonicalCall + confidence
   ↓
confidence 不足或出现未知副作用 → UNKNOWN，不参与 motif mining
```

“可拒绝”很重要：本方法不要求覆盖所有 shell 命令。宁可只在 70% 的高置信 steps 上挖 motif，也不把 `rm`、复杂脚本或无法理解的管道错误合并成某个通用模式。

#### 5.5.1 规范化后的数据结构

每个 tool call 被转换成下面的结构，而不是一个随意拼接的字符串：

```json
{
  "op": "SEARCH",
  "targets": [{
    "location": "repo",
    "granularity": "file",
    "semantic_role": "source",
    "outcome_relation": "unchanged",
    "language": "python"
  }],
  "arguments": {
    "query_role": "identifier",
    "pattern_mode": "literal_or_plain",
    "context": "small",
    "bounded": true
  },
  "effects": "read_only",
  "parser_source": "bash_ast_rule",
  "confidence": 0.95
}
```

原始字符串 `Foo`、`src/a.py`、行号 `30,80` 不进入图标签。它们仍保存在 occurrence 的原始记录中，后续用于推断宏工具参数，但不参与“两个 step 是否同类”的判断。

实现时应同时保存 template 与 bindings：template 用于跨任务比较，bindings 保存该次调用的真实参数。

```json
{
  "template": {
    "op": "SEARCH",
    "query_slot": {"role": "identifier"},
    "target_slot": {"semantic_role": "source", "granularity": "file"}
  },
  "bindings": {
    "query_slot": "Foo",
    "target_slot": "src/a.py"
  }
}
```

motif grouping 只比较 template。分组后，按“节点位置 + 参数角色”对齐 bindings：如果 `query_slot` 在不同 tasks 中分别是 `Foo/Bar/Baz`，它就成为宏工具的输入参数；如果某个 flag 在所有 occurrences 中都是 `-n`，则可作为工具内部固定行为；如果 slots 无法稳定对齐，则该组不能共享一个 contract，应拆组或拒绝。这一步可以理解为受 template 约束的参数 anti-unification，不需要 LLM 猜测参数。

一个 turn 可能包含多个 tool calls。v8 不拆掉 turn，而是先分别规范化 calls，再形成 turn label：

```json
{
  "turn_kind": "MULTI_CALL",
  "calls": [
    {"op": "SEARCH", "target_role": "source_subtree"},
    {"op": "READ", "target_role": "source_file"}
  ]
}
```

这样既保留“一轮产生多个 calls”的原子性，也能比较 batching pattern。

后续示例为便于阅读，会用 `source_file` 作为 `{semantic_role: source, granularity: file}` 的简写；落盘数据仍使用完整的多轴结构。

#### 5.5.2 第一层：优先解析 native function tools

native tool 已经有结构化 `function_name` 和 JSON arguments，因此最可靠。例如：

```json
{
  "function_name": "read-files",
  "arguments": {
    "files": ["src/a.py:30-80", "src/b.py:1-100"]
  }
}
```

通过一个显式 registry 映射：

```python
NATIVE_TOOL_RULES = {
    "read-file":  {"op": "READ",   "path_fields": ["file", "path"]},
    "read-files": {"op": "READ",   "path_fields": ["files"]},
    "search-code":{"op": "SEARCH", "path_fields": ["path"],
                    "query_fields": ["pattern", "query"]},
    "find-files": {"op": "FIND",   "path_fields": ["path"],
                    "query_fields": ["glob", "pattern"]},
    "run-tests":  {"op": "VERIFY", "path_fields": ["cwd", "test_path"]},
    "edit-file":  {"op": "WRITE",  "path_fields": ["file", "path"]}
}
```

该 registry 是版本化配置，不由 LLM 临时生成。未知 native tool 先根据其 JSON schema 尝试匹配；仍无法识别则标为 `UNKNOWN`。结构化工具规则的默认置信度可设为 1.0。

仓库现有 `evolve_v7.py` 中的 `_tool_family`、`_call_op_type` 和 `_paths_from_call` 可以作为这一层的起点，但需要把当前字符串 signature 升级为上述结构化对象。

这里必须区分两类 native tools：

1. **primitive tool**：`read-file`、`search-code` 等单一操作，可以直接通过固定 rule 映射为 `READ`、`SEARCH`；
2. **evolved macro tool**：上一 cycle 生成的 `locate-symbol` 等组合工具，不能只根据名字映射。它需要携带生成时的 contract 和 lineage，说明自己语义上收缩了哪些 primitive operations。

例如第一轮生成 `locate-symbol` 时，同时记录：

```json
{
  "tool_id": "locate-symbol",
  "tool_version": "sha256:...",
  "created_in_cycle": 1,
  "semantic_expansion": [
    {"op": "FIND", "target_role": "source_subtree"},
    {"op": "SEARCH", "query_role": "identifier"},
    {"op": "READ", "range": "local_context"}
  ],
  "inputs": ["symbol", "search_path"],
  "outputs": ["file", "line", "bounded_context"],
  "effects": "read_only",
  "output_budget": {"max_results": 20, "max_chars": 16000},
  "derived_from_motif": "wl:..."
}
```

该 metadata 不建议塞入 function schema，以免无关字段持续进入 downstream prompt。框架额外维护 `tool_contracts_v8.json`；`tools.json` 仍只包含模型需要看到的 name、description 和 parameters。部署时用 `tools.json + executor.py` 的 hash 绑定 contract version，任一文件变化后 contract cache 都必须重新验证。

第二轮遇到：

```json
{
  "function_name": "locate-symbol",
  "arguments": {"symbol": "Foo", "search_path": "src"}
}
```

canonicalizer 先按 `tool_id + tool_version` 查找 contract，而不是把名字猜成 `SEARCH`。它会同时产生两个视图：

```json
{
  "execution_view": {
    "op": "MACRO_CALL",
    "tool_id": "locate-symbol",
    "actual_turns": 1,
    "actual_observation_tokens": 3800
  },
  "semantic_view": {
    "expands_to": ["FIND", "SEARCH", "READ_LOCAL_CONTEXT"],
    "effects": "read_only"
  }
}
```

- **execution view** 忠实表示第二轮只发生了一次 LLM turn，用于实际成本、采用率和失败率统计；
- **semantic view** 是只用于跨 cycle pattern matching 的虚拟展开，用来判断它与第一轮的 `FIND → SEARCH → READ` 属于同一类工作；虚拟节点不计 turn cost，也不伪装成真实执行日志。

如果 evolved tool 缺少 contract、version 对不上、或 executor validation 失败，则标为 `UNKNOWN_EVOLVED_TOOL`，不能仅凭 tool name 参与 motif mining。

#### 5.5.3 第二层：将 bash 解析成 AST，而不是用字符串 split

对 `bash` tool 的 `arguments.command` 使用 shell parser（建议固定版本的 `tree-sitter-bash`；也可以使用 `bashlex`），得到 command、argument、pipeline、redirect 等 AST 节点。不能只用 `command.split()`，因为它无法正确处理引号、变量、管道和重定向。

例如：

```bash
cd repo && rg -n -C 3 'Foo' src | head -50
```

解析结果应近似为：

```text
AND
├── cd(repo)
└── PIPELINE
    ├── rg(flags=[-n,-C,3], query=Foo, path=src)
    └── head(limit=50)
```

处理规则：

- `cd repo && X`：更新该 call 的 effective cwd，再解析 `X`；`cd` 不单独成为 motif 节点；
- `A && B` / `A ; B`：保留为同一 turn 内的有序 `COMPOSITE` calls；
- `A | head -N`：`head` 被记录为输出限制 modifier，主操作仍是 `A`；
- `cat file | sed -n '30,80p'`：合并成一个 `READ(file, explicit_range)`；
- 含 `eval`、动态生成命令、无法展开的复杂变量、process substitution 或未知写副作用时：标为 `UNKNOWN`；
- 解析器只分析日志，不执行命令。

#### 5.5.4 第三层：command rule registry

AST 给出了命令结构，但仍需把具体 executable 映射成有限操作集合。第一版只支持与 code agent 成本最相关的六类：

| Canonical op | 可识别命令 | 关键参数 |
|---|---|---|
| `FIND` | `find`, `fd`, `git ls-files` | root、name/glob、type、limit |
| `SEARCH` | `rg`, `grep`, `git grep` | query、paths、glob、context、limit |
| `READ` | `cat`, `sed -n`, `head`, `tail`, read native tools | paths、range、limit |
| `WRITE` | edit/write native tools、`sed -i`, `apply_patch` | target paths、write mode |
| `VERIFY` | `pytest`, `go test`, `cargo test`, `npm test` | test target、runner、scope |
| `VCS_READ` | `git status`, `git diff`, `git log`, `git show` | query kind、paths、limit |

`rm`、`mv`、安装、构建、网络请求、任意 Python/Node inline script 首先标为 `OTHER` 或 `UNKNOWN`，不强行归入六类。后续只有当某类在数据中频繁且成本高时，才新增显式规则。

rule registry 中每条规则包含：executable aliases、参数语法、read/write effects 和置信度。例如：

```json
{
  "executables": ["rg", "ripgrep"],
  "op": "SEARCH",
  "query": "first_positional_after_flags",
  "paths": "remaining_positionals",
  "modifiers": {
    "-F": {"pattern_mode": "literal"},
    "-i": {"case_sensitive": false},
    "-C": {"context_from_next_arg": true},
    "-g": {"glob_from_next_arg": true},
    "--max-count": {"limit_from_next_arg": true}
  },
  "effects": "read_only"
}
```

#### 5.5.5 第四层：路径角色抽象

`src/a.py` 不能简单变成 `source_file`；首先要相对当前 cwd 和 repo root 解析路径，再用 repo metadata 判断角色。流程如下：

1. 结合 turn 的 effective cwd，将相对路径转成 repo-relative path；
2. 只做词法规范化，不访问 repo 外部路径，不跟随逃逸出 repo 的 `..`；
3. 从几个相互独立的轴描述路径，而不是强迫每个路径只有一个互斥标签；
4. 删除原始 basename，只保留角色描述。

路径描述包含以下轴：

| 字段 | 判断证据 | 可能取值 |
|---|---|---|
| `location` | 是否位于 repo root 内 | `repo`, `external`, `unknown` |
| `granularity` | 参数指向文件还是目录/集合 | `file`, `subtree`, `repo_root`, `unknown` |
| `semantic_role` | 测试约定、manifest、source roots、扩展名 | `test`, `source`, `config`, `docs`, `generic`, `unknown` |
| `outcome_relation` | 是否出现在 final diff changed-files 中 | `changed`, `unchanged`, `unknown` |
| `language` | 扩展名与 repo manifest | `python`, `typescript`, `go`, `mixed`, `unknown` |

例如一个被本次 patch 修改的测试文件不是在 `changed_file` 和 `test_file` 中二选一，而是：

```json
{
  "location": "repo",
  "granularity": "file",
  "semantic_role": "test",
  "outcome_relation": "changed",
  "language": "python"
}
```

其中 source roots 通过轻量 repo adapter 获得。例如 Python 常见 `src/` 和 package dirs，Go 从 `go.mod` 根开始，Node/TS 结合 `package.json`、workspace 和 `tsconfig.json`。不要仅凭目录名中含 `src` 就给高置信角色。

例子：

```text
/workspace/proj/src/a.py       → repo/file/source/unchanged/python
/workspace/proj/tests/a.py     → repo/file/test/changed/python
/workspace/proj/pyproject.toml → repo/file/config/unchanged/python
/workspace/proj                → repo/repo_root/generic/unchanged/mixed
/tmp/error.log                 → external/file/unknown/unknown/unknown
```

#### 5.5.6 第五层：参数角色抽象

参数抽象只保留“参数在操作中的作用”，不保留具体值。第一版采用可复现的规则：

| 原始参数 | 判断规则 | 抽象值 |
|---|---|---|
| `Foo` | 匹配语言 identifier 形式，如 `^[A-Za-z_][A-Za-z0-9_:.]*$` | `identifier` |
| `connection refused` | 含空格且类似错误文本 | `text_fragment` |
| `foo.*bar` | 含未转义 regex metachar，且未使用 literal flag | `regex` |
| `*.py` | glob 语法 | `extension_glob` |
| `30,80` | `sed/head/tail` 的显式范围 | `explicit_range` |
| `-C 3` | context 行数 1–5 | `small_context` |
| `--max-count 20` / `head -50` | 存在结果上限 | `bounded_output` |
| 未提供 limit | 可能返回无界结果 | `unbounded_output` |

不需要判断 `Foo` 在语义上是否真的是某个类名；稳定地标成 `identifier` 已足够让跨 repo 模式对齐。“symbol”只作为宏工具参数名，不作为必须由分类器推断的 ground-truth 语义。

数值也不保留原值，而是分桶，例如 range 长度为 `small(≤100 lines)`、`medium(101–500)`、`large(>500)`。这样 `sed -n 30,80p` 与 `sed -n 100,160p` 会得到相同结构，但读取 5,000 行不会与它们错误合并。

#### 5.5.7 三条示例如何逐步得到标签

示例一：

```bash
find src -name '*.py'
```

```text
AST executable      = find
rule                = FIND
root path src       = source_subtree
-name '*.py'        = extension_glob
没有 head/max count = unbounded_output

CanonicalCall = FIND(
  target_role=source_subtree,
  selector=extension_glob,
  bounded=false,
  effects=read_only
)
```

示例二：

```bash
rg -n -C 3 'Foo' src/a.py
```

```text
AST executable = rg
rule           = SEARCH
query Foo      = identifier
path src/a.py  = source_file
-C 3           = small_context
单文件搜索      = cardinality_one

CanonicalCall = SEARCH(
  query_role=identifier,
  target_role=source_file,
  context=small,
  effects=read_only
)
```

示例三：

```bash
sed -n '30,80p' src/a.py
```

```text
AST executable = sed
-n + address p = READ，而不是 WRITE
path src/a.py  = source_file
30,80          = explicit_range, small

CanonicalCall = READ(
  target_role=source_file,
  range=explicit_small_range,
  effects=read_only
)
```

对照例：`sed -i 's/a/b/' src/a.py` 带 `-i`，必须得到 `WRITE(source_file)`；如果规则遗漏 `-i` 并标成 READ，会造成危险碰撞，因此此类 effect flags 必须拥有最高解析优先级。

#### 5.5.8 置信度、拒绝与 LLM fallback

每个标签记录来源和置信度：

| 来源 | 建议置信度 | 使用方式 |
|---|---:|---|
| 已知 native tool + schema rule | 1.00 | 可直接参与 mining |
| shell AST + 完整 command rule | 0.90–0.98 | 可参与 mining |
| shell AST + 部分参数未知 | 0.70–0.89 | 只用于粗粒度统计，默认不生成 tool |
| regex/string fallback | ≤0.60 | 不参与 mining |
| parse failure / unknown effects | 0 | `UNKNOWN` |

第一版可以完全不使用 LLM fallback。若后续为了提高 coverage 加入 LLM，它只能在固定 label schema 中选择，并且输出仍标为低置信候选；没有 rule/replay 支持时不能进入工具生成阶段。

motif 的置信度取其所有节点/边的最低置信度，而不是平均值。这样一个包含危险未知 step 的 5-node motif 不会被另外四个高置信节点“平均洗白”。

#### 5.5.9 从 CanonicalCall 到 motif

完成上述解析后，每个 turn 都有稳定 label。接下来只在成功轨迹的 outcome-connected DAG 中枚举 1–5 node 的 dependency-connected 子图。

对小子图使用 Weisfeiler–Lehman（WL）hash 生成图指纹：初始标签是 `CanonicalCall`，迭代时把 dependency 邻居标签合入。相同 hash 只是“可能属于同一 motif”的快速分组；分组后仍需逐项比较结构化 labels、effects 和 boundary types，不能把 hash 相同直接当成语义等价。

候选 motif 还必须：

- 在多个不同 tasks 中出现，而不只是在同一条轨迹里重复；
- 所有 occurrences 来自 verifier 成功的轨迹；
- 通过 dependency path 与 final edit/test 相连；
- 不包含 `UNKNOWN`、固定 repo 名、绝对路径或 task answer；
- 具有兼容的 inputs、outputs 和 state effects。

#### 5.5.10 如何验证 canonicalization 本身

该模块需要独立评估，不能只报告最终 benchmark：

1. **Golden tests**：为每种支持命令维护输入命令与期望 `CanonicalCall`；特别覆盖引号、空格、管道、`cd &&`、`sed -i` 和失败解析。
2. **Invariance tests**：把 `Foo/src/a.py/30,80` 替换为 `Bar/lib/b.py/100,150` 后，角色相同则 canonical label 应不变。
3. **Separation tests**：READ 与 WRITE、targeted test 与 full test、bounded 与 unbounded output 必须得到不同标签。
4. **人工抽样**：在 train trajectories 中随机抽取 steps，报告 op、path role、argument role、effect 的 precision/recall。
5. **Coverage**：报告高置信可解析 steps 比例和 `UNKNOWN` 比例。coverage 低意味着可挖模式较少，不应通过降低阈值隐藏。
6. **Collision audit**：抽样检查被分到同一 motif 的 occurrences 是否真的可以共享一个 contract。

最重要的边界是：canonicalization 只负责提出“这些 steps 可能是同类”的候选，**不证明它们可安全互换**。真正的可替换性仍由 Stage C 的 contract 和 Stage E 的 replay/held-out rollout 验证。因此，即使规范化规则不完美，也不会未经验证直接生成并部署工具。

#### 5.5.11 代码模块与执行伪代码

建议新增独立模块 `src/evolve/canonicalize.py`，不要把规则继续堆入 motif miner。模块职责如下：

```text
RepoProfiler
  └── 从 repo root、manifest、final diff 构造 RepoProfile

NativeToolParser
  └── 解析已知 function tool 的结构化 arguments

ToolContractResolver
  └── 按 tool_id/version 解析上一 cycle evolved tool 的 semantic expansion

ShellCallParser
  └── 用 tree-sitter-bash 将 bash command 转成 primitive commands

CommandRuleRegistry
  └── 将 executable + flags 映射为 FIND/SEARCH/READ/WRITE/VERIFY/VCS_READ

PathAbstractor
  └── 将具体路径转成 location/granularity/semantic_role/outcome_relation/language

ArgumentAbstractor
  └── 将 query、glob、range、limit 转成参数角色

TurnCanonicalizer
  └── 合并同一 turn 的多个 CanonicalCall，并给出 confidence/reject_reason
```

核心流程伪代码：

```python
def canonicalize_call(call, context):
    # context: repo_profile, cwd, final_changed_files
    if call.function_name != "bash":
        evolved_contract = tool_contracts.lookup(
            tool_id=call.function_name,
            deployed_registry_hash=context.registry_hash,
        )
        if evolved_contract is not None:
            return CanonicalMacroCall(
                execution_view=actual_call_metrics(call),
                semantic_view=evolved_contract.semantic_expansion,
                bindings=evolved_contract.bind_arguments(call.arguments),
                tool_version=evolved_contract.tool_version,
            )

        rule = native_rules.match(call.function_name)
        if rule is None:
            return Unknown(reason="unknown_native_tool")
        primitives = rule.parse_json_arguments(call.arguments)
        source = "native_schema_rule"
        base_confidence = 1.0
    else:
        ast = shell_parser.parse(call.arguments["command"])
        if ast.has_parse_error:
            return Unknown(reason="shell_parse_error")
        primitives = lower_shell_ast(ast, context.cwd)
        source = "bash_ast_rule"
        base_confidence = 0.95

    canonical_calls = []
    for primitive in primitives:
        rule = command_rules.match(primitive.executable)
        if rule is None or rule.has_unknown_side_effect(primitive):
            return Unknown(reason="unknown_command_or_effect")

        parsed = rule.parse(primitive.arguments)
        canonical_calls.append(CanonicalCall(
            op=rule.op,
            targets=[path_abstractor.apply(p, context.repo_profile)
                     for p in parsed.paths],
            arguments=argument_abstractor.apply(parsed),
            effects=rule.effects(parsed),
            parser_source=source,
            confidence=base_confidence * parsed.completeness,
        ))

    if min(c.confidence for c in canonical_calls) < MIN_MINING_CONFIDENCE:
        return Rejected(canonical_calls, reason="low_confidence")
    return canonical_calls
```

对 trajectory 的处理：

```python
repo_profile = RepoProfiler.build(
    repo_root=trajectory.repo_root,
    manifests=trajectory.repo_manifests,
    final_changed_files=trajectory.outcome.changed_files,
)

for turn in trajectory.turns:
    calls = [canonicalize_call(call, Context(repo_profile, turn.cwd))
             for call in turn.tool_calls]
    turn.canonical_label = canonicalize_turn(calls)
```

规范化结果单独写入 `agent/canonical_steps_v8.json`，避免每次 mining 重复解析：

```json
{
  "schema_version": "v8-canonical-1",
  "rule_registry_version": "2026-07-1",
  "steps": [
    {
      "step_index": 7,
      "raw_call_hash": "...",
      "canonical_calls": [
        {
          "op": "SEARCH",
          "targets": [{
            "location": "repo",
            "granularity": "file",
            "semantic_role": "source",
            "outcome_relation": "unchanged",
            "language": "python"
          }],
          "arguments": {
            "query_role": "identifier",
            "context": "small",
            "bounded": true
          },
          "effects": "read_only",
          "confidence": 0.95
        }
      ],
      "rejected": false,
      "reject_reason": null
    }
  ]
}
```

缓存 key 至少包含 `raw_call_hash + effective_cwd + repo_profile_hash + rule_registry_version + tool_contract_registry_version`。规则或 evolved tool contract 升级后 version 变化，旧缓存自动失效，保证实验可复现。

#### 5.5.12 第一版 MVP 的边界

第一版不需要解决任意 shell 程序理解。建议只实现：

- 所有当前 `tools.json` 中的 native read/search/find/test tools；
- 第一轮生成工具的 `tool_contracts_v8.json` 读写、version 校验和虚拟 semantic expansion；
- bash 的 `find/fd/rg/grep/git grep/cat/sed -n/head/tail`；
- `cd && command`、简单 pipeline 和 `head/tail` 输出限制；
- Python、TypeScript/JavaScript、Go 三类 repo profile；
- READ/SEARCH/FIND 三类 read-only motif mining。

以下情况全部返回 `UNKNOWN`：inline Python/Node、`eval`、下载命令、复杂变量展开、未知重定向、修改系统环境和无法确定副作用的脚本。WRITE/VERIFY 可以先作为 motif 的下游边界节点，而不进入被收缩子图。

**MVP 的第一步不是挖 motif，而是给现有高频 READ/VERIFY 加 bounded output（由 §2.4 数据决定）。** 预实验显示 observation tokens 里 READ 占 48%–64%、SEARCH/FIND 合计 <12%，且 42%–67% 的 turns 已经是多-op（`FIND → SEARCH → READ` 常常本来就在一条 bash 里，收缩它省不了 turn）。因此“把 `FIND → SEARCH → READ` 收缩成 `locate-symbol`”不是最高收益动作。MVP 的最小可验证闭环应是：

1. **先做 bounded-output 版的 READ 与 VERIFY 工具**（受控 `max_chars` + continuation cursor；test 只返回 exit code + 失败用例名 + log tail，完整日志落盘按需读取），这不需要跨任务 motif mining，直接针对最大成本源；
2. 用 §5.7 的反事实 token 账本，在 prep 轨迹上离线估算“把每次长 READ/VERIFY observation 截断到预算”能省多少 exposure 成本，验证 §2.4 的 34%–38% exposure 确实可回收；
3. **之后**再做 read-only 的 `FIND → SEARCH → READ` motif 收缩，作为“额外减少 turn”的次要收益，而不是主线。

这样 MVP 首先验证的是“bounded READ/VERIFY 是否能在不伤 performance 的前提下回收 observation exposure 成本”，这是本项目数据下**成本占比最大、且不依赖成功轨迹数量**的假设；motif 收缩作为第二阶段，只有在成功轨迹放宽后（§5.4）数量足够时才展开。

### 5.6 Stage C：将重复模式定义为宏工具

发现 motif 后，还不能直接生成工具。必须先回答：工具需要什么输入？应该返回什么？会不会修改 repo？

图的边界给出这些答案：

```text
外部输入                         子图内部                         下游使用
symbol/path ──→ [FIND → SEARCH → READ] ──→ file/line/context ──→ EDIT
```

- 左侧进入子图的信息形成工具输入：`symbol`、`search_path`；
- 右侧下游 edit 所需的信息形成工具输出：`file`、`line`、局部代码；
- 该子图只有 read 操作，所以 `state_effects=[]`，不会修改代码；
- 三个中间 observations 属于工具内部，不分别返回给 LLM。

由此得到宏工具契约：

```json
{
  "name": "locate-symbol",
  "inputs": {
    "symbol": "string",
    "search_path": "string"
  },
  "outputs": "matching file, line number, and bounded local context",
  "state_effects": [],
  "failure_behavior": "return nonzero and a short error message",
  "output_budget": {
    "max_results": 20,
    "max_chars": 16000
  }
}
```

输出上限是降低 observation 成本的关键。搜索结果超过限制时，工具返回 continuation cursor，agent 需要时再取下一页；test 工具只返回 exit code、失败用例名和日志尾部，完整日志保存到文件供按需读取。

**注意：bounded output 是 gate 强制的契约，不是 runtime 自带能力。** 当前 v6/v8 共用同一个 `evolve_tools_v6` runtime（`registry.py` / `agent.py` / `executor.py`），它对工具输出**不做任何截断**——observation 长短完全取决于 evolve agent 在 `executor.py` 每个分支里怎么写（这正是 §2.3 里 `read-lines` 返回整段源码的原因）。因此 v8 不能声称“工具 runtime 原生 bounded”；`output_budget` 只有当 §5.8 的 replay gate **实际检查并拒绝超预算输出**、并把 max_chars/cursor 逻辑写进 executor 分支时才生效。实现上有两条路：(a) 在 runtime 的 `run_tool` 外层加一个通用 output-clamp（更稳，推荐）；(b) 要求每个 executor 分支自带 clamp 并由 gate 校验。无论哪条，都必须在验证阶段强制，而非依赖 prompt 纪律。

对于包含 edit 的 stateful motif，还必须在 contract 中声明 changed files 等 state effects。但第一版应优先实现 read-only 的 search/read/test-log 宏工具，因为更容易验证，也不容易破坏代码。

### 5.7 Stage D：判断这个工具是否真的值得加入

创建工具并不是免费的：它的 name、description 和 JSON schema 会在每轮 prompt 中占 token；工具太多还会增加 agent 选错工具的概率。

因此对每次 occurrence 比较两个真实场景：

```text
原方案：3 次 LLM 调用 + 3 次长 observation + 后续 context 重复暴露
新方案：1 次 LLM 调用 + 1 次 bounded observation + tool schema overhead
```

净收益定义为：

$$
Saving = Cost_{original}-Cost_{macro}-Cost_{schema}.
$$

这里的 $Cost_{macro}$ 指使用宏工具后的完整 API 成本，不是只计算 executor 的运行时间。系统根据 occurrence 当时的真实 context 重建两边的 prompt/cached/completion tokens，再按模型价格换算。

继续使用上例，下面用一组示意数字展示计算方式（不是对现有 results 的实测结论）。假设一次 occurrence 的 token 账本为：

| 项目 | 原三轮操作 | `locate-symbol` |
|---|---:|---:|
| LLM turns | 3 | 1 |
| observation tokens | 25,000 | 4,000 |
| 折算 API cost | $0.030 | $0.011 |
| 分摊 schema cost | $0 | $0.002 |
| 总成本 | $0.030 | $0.013 |

则一次约节省 $0.017。如果该模式在 20 个 tasks 中稳定出现，才有充分理由实现它。

不能只看平均值：如果工具在 2 个任务中节省很多、在其余任务中经常失败，平均数会掩盖风险。因此对跨任务 saving 做 bootstrap，取保守的置信下界 LCB（lower confidence bound）。直观上，LCB 回答的是：**即使考虑样本波动，我们仍有多大把握认为它能省钱？** 只有 LCB 仍大于 0 的工具才保留。

若同时发现很多候选工具，则在 registry token budget 下按“新增节省 / schema tokens”依次选择。两个工具若覆盖同一批 steps，重叠部分只计算一次，避免注册大量功能重复的工具。这是一个 budgeted weighted coverage 问题，不需要 ILP。

### 5.8 Stage E：先验证替换成立，再交给 evolve agent

图中存在错误 dependency 时，宏工具可能漏掉下游需要的信息。因此成本为正还不够，候选必须依次通过三道 gate：

1. **Contract replay**：在历史 occurrence 的相同 repo state 上执行宏工具。上例要求它仍找到原来的关键 `file:line` 和代码上下文，同时不超过输出上限。
2. **State/verifier replay**：在宏工具之后重放历史中已经确定的 downstream state-changing actions，检查最终 diff 和 targeted test 是否仍然一致。它验证固定 action 的执行等价性，但不声称 agent 一定能自主生成该 edit。对 stateful 工具，还要直接比较 changed files 和 diff hash。
3. **Held-out rollout**：将工具临时加入 registry，在没有参与 motif mining 的新 tasks 上重新运行 agent，检查 pass rate 是否满足 non-inferiority，同时实际成本下降。

只有通过验证的候选才形成 evolve sample。样本不再包含两条很长的 positive/negative trajectories，而是一张简短的“工具实现卡”：

```json
{
  "pattern": "FIND -> SEARCH -> READ_LOCAL_CONTEXT",
  "proposed_tool": "locate-symbol",
  "supported_by": {"tasks": 12, "occurrences": 19},
  "inputs": ["symbol", "search_path"],
  "outputs": ["file", "line", "bounded_context"],
  "output_budget": {"max_results": 20, "max_chars": 16000},
  "saving": {"mean_usd": 0.017, "lcb_usd": 0.011},
  "validation": {
    "contract_replay": "19/19",
    "verifier_replay": "19/19"
  }
}
```

evolve agent 根据这张卡实现 `tools.json + executor.py`。也就是说，图算法负责回答“应该实现什么、为什么值得实现、是否安全”，evolve agent 只负责把已验证的 contract 写成可执行代码。

`instruction.md` 采用同样原则：只有跨成功轨迹重复、并通过 held-out rollout 的行为规则才能写入。失败轨迹可以提出“重复 retry”之类的待验证假设，但不能直接产生“跳过测试”“直接 commit”等高风险规则。

### 5.9 完整例子的最终变化

| 阶段 | 例子中发生的事 |
|---|---|
| Outcome anchoring | 确认 $T_4$ 产生 final patch，$T_5$ 验证 patch |
| Motif discovery | 在多个成功 tasks 中发现 $T_1\rightarrow T_2\rightarrow T_3$ |
| Contract construction | 输入为 symbol/path，输出为有界 file/line/context |
| Graph contraction | 用一个 `locate-symbol` 节点替换三个 turns |
| Cost selection | 3 turns → 1 turn，25k observation tokens → 4k |
| Validation | replay 找到同一代码位置，原 edit/test 仍通过 |
| Evolution | evolve agent 将 contract 实现为 native function tool |

所以，VCGC 并不是“先用复杂图算法求一条最短路径”。它做的是：**从成功经验中找出重复且昂贵的局部工作流，将其编译成输出受控的新工具，并用 replay 和新任务 rollout 验证该替换。**

### 5.10 多轮 evolve 如何工作

#### 为什么不能把每个 cycle 当成独立数据

cycle 1 的 trajectory 主要包含 `bash` 或 primitive tools；生成工具后，cycle 2 会包含三种混合步骤：

```text
bash / primitive tool
上一轮生成的 evolved tool
evolved tool 与额外 bash/tool 组成的新流程
```

因此 cycle 2 的目标不是从头再生成一套工具，而是根据真实使用结果决定：保留、改进、合并、删除旧工具，或增加新工具。

#### 两个图视图

在构图前，工具 contract 按以下流程进入系统：

1. validated contraction card 已经给出 `proposed_tool`、semantic expansion、inputs/outputs/effects；
2. evolve agent 根据 card 修改 `tools.json + executor.py`；
3. validator 检查生成的 schema 是否符合 card，并执行 smoke/replay tests；
4. 验证通过后，框架计算对应 tool schema 与 executor implementation 的 hash，写入 `tool_contracts_v8.json`；
5. rollout deployment manifest 记录整个 registry hash，并把它写入每条 trajectory 的 metadata。

```json
{
  "tool_registry": {
    "registry_hash": "sha256:...",
    "contracts_version": "v8-contracts-1",
    "tools": {
      "locate-symbol": "sha256:tool-version-..."
    }
  }
}
```

因此，即使多个 cycles 都有同名 `locate-symbol`，也能根据 trajectory 的 registry hash 找回当时实际部署的 contract。若旧日志没有 registry hash，只能将该调用标为 legacy/unknown version，不能用于严格的跨 cycle 收益比较。

每轮 trajectory 构建两张逻辑视图，但共享同一份日志：

| 视图 | 节点表示 | 用途 |
|---|---|---|
| Execution graph | 实际发生的 turns；一次 evolved tool call 就是一个节点 | 计算真实 turns、tokens、observation、失败率 |
| Semantic expansion graph | 根据 `tool_contracts_v8.json` 将 evolved tool 虚拟展开为 primitive pattern | 与早期 cycles 对齐 motif、判断已有工具覆盖范围 |

例如：

```text
Cycle 1 execution: FIND → SEARCH → READ → EDIT
Cycle 2 execution: LOCATE-SYMBOL → EDIT

Cycle 1 semantic:  FIND → SEARCH → READ → EDIT
Cycle 2 semantic: [FIND → SEARCH → READ] → EDIT
                  └── locate-symbol 的虚拟 expansion
```

两条 semantic paths 可以匹配；但成本计算仍使用 execution graph，所以 cycle 2 被正确计为 2 个 turns，而不是虚构成 4 个。

#### 每个工具维护跨 cycle 统计

每次 rollout 后，按 `tool_id + tool_version` 聚合：

```json
{
  "tool_id": "locate-symbol",
  "version": "sha256:...",
  "cycle": 2,
  "available_in_tasks": 64,
  "adopted_in_tasks": 31,
  "call_count": 47,
  "success_count": 44,
  "mean_observation_tokens": 4200,
  "fallback_after_call_count": 9,
  "validated_cost_saving_lcb": 0.008
}
```

其中：

- `adopted_in_tasks / available_in_tasks` 衡量 agent 是否愿意使用该工具；
- `success_count / call_count` 衡量 executor 是否稳定；
- `fallback_after_call_count` 统计调用工具后是否紧接着用 bash 重做相同工作，是输出不足或工具失败的信号；
- saving 必须按实际 execution graph 重算，不能沿用创建时的估计。

#### 第二轮可能发现什么

假设 cycle 2 中经常出现：

```text
LOCATE-SYMBOL → READ_MORE_LINES → EDIT
```

semantic expansion 为：

```text
[FIND → SEARCH → READ_LOCAL] → READ_MORE_LINES → EDIT
```

这说明 `locate-symbol` 虽然被采用，但输出上下文经常不够。系统不应该再创建一个功能重复的 `locate-and-read-symbol-v2`，而应生成 **REFINE** 候选：扩大可配置 `context_lines`、加入 continuation cursor，或修正默认 output budget。

反之，如果 cycle 2 中 `locate-symbol` 几乎无人调用，或者调用后大量 fallback 到原 bash 流程，则它可能产生负 schema overhead，应成为 **REMOVE** 候选。

#### 五类 registry 更新决策

每个 cycle 的候选不再只有“新增工具”，而有五类：

| 决策 | 触发证据 | 操作 |
|---|---|---|
| `KEEP` | adopted、稳定、saving LCB > 0 | 保持当前版本 |
| `REFINE` | 工具被采用，但经常出现固定补充步骤或 bounded-output fallback | 修改同一 tool contract/executor，生成新 version |
| `MERGE` | 两个已有工具的 semantic expansions 高度重叠，且 registry overhead 过高 | 合并为一个参数化工具 |
| `REMOVE` | 长期不采用、失败率高、saving LCB ≤ 0 | 从 registry 删除 |
| `ADD` | 出现未被现有 semantic expansion 覆盖的新高收益 motif | 新增工具 |

registry selection 计算 marginal coverage 时，现有工具已经覆盖的 semantic nodes 不再为新 `ADD` 候选重复计收益。只有新候选能覆盖旧工具未解决的步骤，或能以更低成本替换旧工具时，才有正的边际收益。

#### 多轮数据流

```text
Cycle k registry + contracts
        ↓
rollout，记录 primitive/evolved tool calls
        ↓
构建 execution graph（真实成本）
        +
构建 semantic expansion graph（跨轮模式对齐）
        ↓
更新每个 tool version 的 adoption/success/fallback/saving
        ↓
产生 KEEP / REFINE / MERGE / REMOVE / ADD candidates
        ↓
replay + held-out validation
        ↓
生成 Cycle k+1 registry + 新版本 contracts
```

#### 防止错误自我强化

多轮 evolve 容易把第一轮错误工具当成“历史常见模式”，然后不断强化。v8 使用以下约束：

1. evolved tool 的一次调用不能增加其原始 motif support；support 仍按独立 tasks 和原始/虚拟 expansion 去重统计；
2. semantic expansion 只用于对齐，不能作为新成功证据；成功证据来自本轮真实 call、真实 observation 和 verifier；
3. 工具新版本与旧版本分别统计，不能用旧版本的成功率替新版本背书；
4. 每轮保留固定 baseline/holdout cases，防止 registry 在自身 rollout 分布上闭环过拟合；
5. `REFINE/MERGE/ADD` 仍必须重新通过 replay 和 non-inferiority gate。

因此，多轮 evolve 不是“工具越积越多”，而是一个带 lineage 和实测反馈的 registry 更新过程。

---

## 6. 为什么该方法比 v6 更有技术性，比 v7 概念更简单

| 设计问题 | v6 | v8 |
|---|---|---|
| sample 单位 | 单条轨迹的规则裁剪 | 跨任务重复的 dependency subgraph |
| sink | 最后一个 action | final-patch producing writes + verifier |
| 成本 | 主要隐式用 step/trajectory 长度 | LLM cost + observation context exposure（按真实 50× cache 折扣）+ schema overhead |
| tool 来源 | evolve LLM 阅读日志后猜测 | validated graph contraction contract |
| observation | 截断主要发生在 evolve prompt | gate 强制的 bounded output + cursor（runtime 需加 output-clamp，见 §5.6） |
| correctness | positive sample 未验证 | replay + verifier + held-out non-inferiority |
| 图技术 | ancestor closure | cost-weighted motif hashing、boundary contraction、budgeted coverage |

v8 的技术深度来自一个闭环：**成本标注的图 → 重复子图 → 边界保持的图收缩 → 有预算的选择 → 等价性验证**。每一步都服务于“少轮次、短 observation、performance 不下降”，而不是为了丰富术语。

**关于“比 v7 简单”的诚实限定。** v8 相对 v7 更简单，仅指**算法/理论层**：不引入异构 provenance 本体、边类型完备性主张、AND/OR support sets、ILP、Steiner Tree、gSpan、未验证的 minimal positive trajectory，以及仅凭日志做 causal identification 的主张。在**工程实现层，v8 并不比 v7 轻**：它新增了 5 层确定性 canonicalizer、repo profiler、`tool_contracts_v8.json` 版本管理、execution/semantic 双视图、WL hash、boundary contraction、budgeted coverage、replay/held-out gate 与 5 类 registry 决策。其中最重的前置依赖是 **per-step repo snapshot instrumentation**（replay gate 的先决条件，v7 §9 至今未落地）。因此实现顺序（§8）必须把便宜、不依赖成功轨迹数量的 bounded-output 先做，把重型 motif/replay 机制往后放。

---

## 7. 实验设计

### 7.1 Research questions

- **RQ1：** VCGC 是否在 non-inferiority 条件下降低每题实际 API cost？
- **RQ2：** context-exposure cost 相比 step-only cost，是否更能降低 observation tokens 和累计 prompt tokens？
- **RQ3：** dependency-connected motifs 相比 v6 trajectory pruning，是否提高 tool adoption、tool success 和净收益？
- **RQ4：** replay/verifier gates 是否显著降低 evolved agent 的回归率？
- **RQ5：** execution/semantic 双视图与 tool lineage 是否减少跨 cycle 的重复工具，并支持有效的 REFINE/REMOVE？
- **RQ6（新增，对应 §1 的第二目标）：** 在成本不升的前提下，VCGC 是否**提高**成功比例？分别报告严格 pass rate 与放宽成功比例（partial/f2p），验证“成本↓ 且 成功↑”能否同时成立，而不仅是“成本↓ 且 成功不降”。
- **RQ7（对应 §2.4 假设 B）：** 单独给现有高频 READ/VERIFY 加 bounded output（不挖任何 motif）能回收多少 observation exposure 成本，且是否伤 performance？这是最便宜、不依赖成功轨迹数量的杠杆，应作为独立结论。

### 7.2 必要 ablations

1. v6 full method；
2. v8，但 node cost 只用 turn count；
3. v8，但不计 observation exposure；
4. v8，用连续窗口替代 dependency-connected motif；
5. v8，不计 schema overhead；
6. v8，去掉 replay/verifier gate；
7. v8，将上一轮 evolved tool 当成 opaque tool name，不使用 semantic expansion；
8. instruction-only、tools-only、tools + instruction；
9. **bounded-output-only**（只做 §5.6 的 READ/VERIFY output-clamp，不做任何 motif 收缩）——用于隔离 §2.4 假设 B 的独立贡献；
10. **严格成功 vs 放宽成功训练集**（§5.4）——验证放宽成功定义是否真的提高 tool 质量与最终成功比例，而非引入噪声。

### 7.3 指标与统计

主指标是同一批 case、同一模型配置下的 paired API cost difference，**用真实单价换算**（input=1、output=2、cached=0.02 元/百万 token；cache 折扣 50×），不使用 v7 fallback 的 10× 权重（§2.4）。次指标包括 uncached/cached/completion tokens、LLM turns、observation tokens（并按 op 类型分解，重点看 READ/VERIFY，见 §2.4）、peak context、tool adoption rate、tool failure rate、schema tokens 和 wall time。多轮实验还报告 registry size、重复 semantic coverage、tool version churn、KEEP/REFINE/MERGE/REMOVE/ADD 数量，以及工具创建后各 cycle 的净收益变化。

Performance 用**双口径**同时报告：(1) 严格 `reward==1` 的 paired non-inferiority（预注册 margin $\epsilon$ + 置信区间）；(2) 放宽成功比例（`f2p>0` 且无 p2p 回归 / `partial≥0.5`）的**改进量**，对应 RQ6。同时报告 success-only cost 与 expected cost per task，避免通过更早失败来“降低成本”。至少使用多个随机 seeds，train/validation/test 按 repository 隔离，所有 motif mining 和阈值选择只在 train/validation 完成。**数据可用性前提**：SWE-Atlas QA 的 prep 轨迹当前全部崩溃（§2.4），必须先重新 rollout 才能纳入；否则该 split 仅用于最终 held-out 评估、不参与 mining。

---

## 8. 最小实现路线

顺序按“先便宜、先验证成本大头、先不依赖成功轨迹数量”重排（依据 §2.4 预实验）：

0. **（已完成）可行性预实验**：`scripts/v8_feasibility.py` 在 `results/prep` 上量化了 turn-collapse 比例、真实 50× cache 折扣下的 exposure 占比、各 benchmark 成功轨迹数（结果落 `results/v8_feasibility_result.json`）。它确立了下面的优先级：bounded READ/VERIFY 是最大杠杆、turn-collapse 是次要收益、成功定义必须放宽。
1. **修 baseline 注入 bug**：删除/降级 `SEED_INSTRUCTION_MD` 中未验证的高风险规则（“skip tests then commit / submit best-effort”），作为 evolve 前的干净起点（§2.3）。
2. **在 runtime 加通用 output-clamp + cursor**，并把现有 READ / VERIFY 类工具改成 bounded output（§5.6）；这是不依赖 motif mining 的最大成本源。
3. **用真实单价的 per-turn token 账本 + exposure 成本**（input=1/cache=0.02/out=2），在 prep 轨迹上离线估算 bounded READ/VERIFY 可回收多少 exposure（对应 RQ7 / ablation #9）。per-turn tokens 现有 trajectory 已具备（`metrics.{prompt,cached,completion}_tokens`），无需新 instrumentation。
4. **补齐 outcome instrumentation**：per-write diff hash、final-hunk anchors，并按 §5.4 的放宽成功定义把 `partial_pass` 轨迹的 anchor 对齐到通过测试的改动；这是 replay gate 与 motif mining 的前置。
5. 实现 canonicalizer、`tool_contracts_v8.json` 和 execution/semantic 双视图；
6. 只实现 size 1–5 的 WL motif hash 与 boundary contract，不实现通用子图挖掘器；先做 read-only 的 `FIND→SEARCH→READ` 收缩，作为“额外减 turn”的次要收益，再扩展有 state effects 的 edit 宏工具；
7. 实现 replay/verifier gate（依赖第 4 步的 per-step snapshot）、held-out non-inferiority、registry budget 和 KEEP/REFINE/MERGE/REMOVE/ADD；
8. 最后接回 v6 evolver，让 evolve agent 只消费 validated contraction cards。

第 2–3 步就能独立发一个“bounded-output 降本、performance 非劣”的最小结论（RQ7）；第 4–7 步形成完整 VCGC 算法；第 8 步只是把 contraction contract 编译成当前仓库所需的 native tools。

---

## 9. Introduction（论文引言草稿）

### 9.1 LLM agent 及其高昂成本

大语言模型(LLM)agent 已成为自动化软件工程的主流范式。以 ReAct 为代表的 code agent(如本文 baseline `mini-swe-agent`)在"推理 → 调用工具 → 观察结果"的循环里反复迭代,直到提交补丁或耗尽预算。这种多轮、长上下文的工作方式让 agent 能在陌生代码库中自主定位、修改和验证代码,但也让它的 API 成本远高于单轮问答。

成本高在两点。**第一,轮次多。** 一个真实代码任务常常要几十到上百次 LLM 调用。**第二,observation 不断累积在上下文里。** 每次工具返回的结果(读文件、跑测试、列目录)一旦进入对话历史,就会在之后每一轮 prompt 里被反复携带。所以一次读文件的真实代价,不只是产生它的那一轮,而是它在后续所有轮次里的累计暴露。轮次越多、观察越长,上下文就越大,每一轮的账单也越贵——两者互相放大。**降本的本质,就是同时压住"轮次"和"observation 暴露"这两条曲线。**

### 9.2 现有方案的局限:trajectory 压缩既破坏 cache 又损失信息

面对成本问题,当前最主流的做法是 **trajectory 压缩**:当历史太长时,用摘要、滑动窗口或选择性丢弃把早期内容压短再喂回模型。它让名义 token 数变少,但在带 prompt cache 的现代 API 上,既省不到钱,还常常损害性能。

原因有两个。**第一,它破坏 cache。** 推理服务对未改变的 prompt 前缀提供极低价的 cache 命中(命中价可低到未命中的几十分之一),而真实轨迹的命中率往往在 99% 以上。摘要一旦改写历史前缀,后面所有 token 就从"缓存价"退回"全价",省下的那点 token 远远抵不上失去缓存的涨价。**第二,它有损且不可逆。** 被压掉的细节(某个函数签名、某行报错、某个路径)事后往往正是需要的,agent 却再也取不回来,只能基于失真的历史继续,导致重复探索甚至失败。归根结底,压缩是在"已经付过费"的历史上做事后裁剪——救不回花掉的钱,还削弱了 agent 的信息基础。**真正该做的,是从源头少产生昂贵操作,而不是事后压缩它们。**

### 9.3 本文方案:基于自进化算法的成本优化框架

本文提出一个**基于 agent 自进化(self-evolve)的成本优化框架**。我们不在推理时压历史,而是在**进化阶段**改造 agent 自身:先让 baseline agent 在代码任务上跑出轨迹,再从这些轨迹里找出反复出现、成本高昂的操作,把它们自动进化成两类产物——**native function tools**(本框架中即 `tools.json` + `executor.py`)和 **instruction**。进化后的 agent 用更少的轮次、更短的 observation 完成同样的工作。整个过程是一个闭环:rollout → 分析 → 进化 → 再 rollout。

自进化能解决压缩解决不了的问题,原因很直接。**它在源头减负,而不是事后压缩。** 进化出的工具在**产生 observation 之前**就限定了输出:读文件只回相关片段,跑测试只回结果和关键日志,长输出落盘、按需再取。昂贵的内容从一开始就没进上下文,因此根本不需要改写历史前缀,**天生不破坏 cache**。而且工具和 instruction 是跨任务持久的资产,一次进化的收益能摊到之后所有任务上。更进一步,把定位、读取这类确定性工作交给受控工具,既省 token,又为真正的推理和修改腾出上下文预算,从而**降低"因上下文膨胀或步数耗尽而失败"的比例**。所以我们的目标是双向的:在成功率不下降的底线上,**主动争取成功率提升**。

### 9.4 技术难点与解法

要把这个想法做成可靠的框架,有两个本质难点。本文把整套方法称为 **Validated Cost-Aware Graph Contraction(VCGC)**。

**难点一:如何发现自进化过程中值得优化的昂贵操作?** 难点在于,同一个意图在不同任务里长得很不一样(有的用 `rg`、有的用 `grep`,路径参数各不相同),直接比命令字符串认不出它们是同一模式;而让 LLM 自由概括又贵、又不可复现。我们的解法是**确定性归一化 + 图挖掘**:先把每次调用规范化成统一的语义标签,抹平表面差异,再在执行图上挖出跨任务反复出现的昂贵子图,作为值得沉淀成工具的候选。

**难点二:如何在自进化闭环里降本,同时保证正确性?** 我们省钱的手段,是把一串操作收缩成一个工具,工具内部照常干活,但只返回必要的结果、把中间又长又贵的 observation 藏起来不进上下文(因为没有改写历史前缀,这样做天生不破坏 cache,这也是它区别于事后压缩、能真正省钱的原因)。可是"藏信息"本身就有风险:工具可能恰好藏掉了后续真正需要的内容,悄悄改变 agent 的行为、导致失败——**省得越狠,越容易出错**。而自进化是个闭环,这样一个"看着省钱、其实有害"的工具一旦被采纳,就会在之后每一轮里被反复复用,错误被不断放大。我们的解法是给每个候选工具加一道**验证门**,过了才准进入工具库:第一步,把它放回原本出现的场景里重放一遍,检查产出结果与原来等价(找到的文件、最终的 diff 一致);第二步,在一批**没参与挖掘的新任务**上带着这个工具重新跑,确认成功率不下降。两关都过才收录,而且工具库可回退——某一轮发现变差就撤掉。正因为有这道门兜底,我们才敢把目标定成双向的:在成功率不降的底线上,主动争取它变得更高。

综上,VCGC 是一个自洽的闭环:**成本标注的执行图 → 跨任务重复子图 → 边界保持的收缩 → 有预算的工具选择 → 等价性与非劣性验证**。它从源头削减轮次和 observation 暴露,不破坏 cache、不损失信息,并为成功率的提升留出空间。我们在多个代码 benchmark 上验证该框架,并用 ablation 分离出各组件的独立贡献。

---

## 10. Method

### 10.1 Overview

我们的框架 **VCGC(Validated Cost-Aware Graph Contraction)** 是一个进化闭环:baseline agent 先在代码任务上 rollout 出一批轨迹,框架从这些轨迹里挖出"值得优化的昂贵操作",把它们编译成新的 native function tool,验证通过后交给下一轮 agent 使用,如此循环。

整个方法围绕 §9.4 的两个难点组织,可以分成两个部分:

- **发现(对应难点一)**:把每条轨迹表示成一张带成本标签的执行图,在图上找出跨任务反复出现、又贵又确定的多步操作(§10.2、§10.3)。
- **安全降本(对应难点二)**:把这些操作收缩成一个"在源头限定输出"的工具,用净收益筛掉不划算的候选,再用一道验证门确保它不损害正确性,才允许进入工具库;多轮之间用 lineage 和固定 held-out 防止错误自我强化(§10.4–§10.7)。

一个贯穿全章的例子:很多任务都要"搜索一个符号 → 读取它周围的代码 → 再动手改"。搜索和读取是确定性的、跨任务重复的,而且返回的 observation 又长又贵。VCGC 会把"搜索 + 读取"这两三步收缩成一个 `locate-symbol` 工具:agent 一次调用就拿到有界的相关上下文,把 3 轮 LLM 决策压成 1 轮、把约 25k tokens 的中间输出压到约 4k;而"怎么改代码"仍留给 agent 自己决定。

### 10.2 成本标注的执行图

每条轨迹被表示成一张 cost-labeled step DAG $G=(V,E,X,C)$:一个节点是一次 LLM turn(保留该轮所有 tool call,不破坏原子性),边是 step 之间的依赖(prerequisite → dependent),时间顺序单独存为 metadata、不当作依赖。每个节点带上挖掘所需的属性 $X$(操作类别、读/写/验证、参数与文件角色、返回码、observation token 数、repo diff hash),以及两类成本标签 $C$。

**只在真正产生结果的地方记成本(outcome anchoring)。** 如果从轨迹的最后一个 action 往回找,常常选中 submit 或 `git status` 这类不产生结果的收尾动作。因此我们先定位"真正产生并通过测试的改动步骤",把它们标为 outcome anchor,成本与收益都锚定到这些 anchor 及其依赖闭包。为缓解严格全通过轨迹太少的问题,训练集的成功定义放宽到**部分通过**(修好了此前失败的目标测试、且没有打破原本通过的测试),但此时 anchor **只对齐到确实通过了测试的那部分改动**——放宽的是"哪些轨迹可用",不是"哪些 step 算结果"。

**两类成本都要计,收益用反事实账本算。** 只数轮次会低估长输出的代价,只算当轮 output token 又忽略它在后续每一轮 prompt 里的重复暴露。所以每个节点带两类成本:该轮的实际 API 成本,以及它的 observation 在后续轮次里的 **exposure 成本**(按真实的 prompt-cache 折扣计价)。任何"收缩能省多少"都通过重建"收缩前 vs 收缩后"的完整 token 账本得到,而不是用轮次数当代理。

### 10.3 发现值得优化的昂贵操作

**先归一化,再挖掘。** 同一个意图在不同任务里表面形态差异很大(`rg` 与 `grep`、路径与参数各不相同),直接比命令字符串认不出同一模式;而让 LLM 自由概括又贵、不可复现、难以审计。我们用一个**确定性、可拒绝的分层规范化器**把每次调用映射成统一的语义标签:结构化 tool schema → shell AST(而非字符串 split)→ command rule registry → 路径角色 → 参数角色。每个标签带一个置信度,**低置信或副作用未知的调用直接拒绝、不参与挖掘**,LLM 只在少数低置信情形做兜底、且其判定同样要过后续验证。这样表面差异被可复现地抹平,`rg Foo` 与 `grep Foo` 都归一化成同一个 `SEARCH(symbol)`。

**在归一化后的图上挖跨任务重复子图。** 我们用 Weisfeiler–Lehman hash 给小子图(实际只需 size 1–5)分组,把在**多个独立任务**里都出现的依赖连通子图作为 motif 候选。support 按去重后的独立 task 计,避免同一任务里的多次出现虚增频次。得到的每个 motif,就是一个"跨任务重复、又贵又确定"的操作模式——它才是值得沉淀成工具的东西。

### 10.4 边界保持的收缩:在源头省钱且不破 cache

发现 motif 后,子图的**边界**直接给出工具契约:进入子图的信息是工具输入,下游真正需要的信息是工具输出,内部的中间 observation 被封装、不再逐条返回给 LLM。对上例,`FIND → SEARCH → READ` 收缩成 `locate-symbol(symbol, search_path) → {file, line, bounded_context}`,`state_effects=[]`(只读、不改代码)。

**关键在于工具在产生 observation 之前就限定输出**(bounded output + continuation cursor):读文件只回相关片段、跑测试只回 exit code + 失败用例名 + 日志尾部,超出上限的部分落盘、返回一个游标供按需再取。昂贵的中间结果从一开始就没进上下文,因此**根本不涉及改写历史前缀,天生不破坏 prefix cache**——这正是它区别于事后压缩、能真正省钱的原因。

需要强调,bounded output 是**由验证门强制的契约,而非 runtime 自带能力**:当前 runtime 对工具输出不做截断,输出长短取决于 executor 分支的实现。因此我们在 runtime 外层加一个通用的 output-clamp,并由 §10.6 的 gate 实际检查、拒绝超预算输出,而不依赖 prompt 纪律。第一版优先实现只读的 search/read/test-log 类工具,因为更易验证、也不易破坏代码;带 edit 的 stateful 工具必须在契约里声明 changed files 等 state effect 后才引入。

### 10.5 有预算的工具选择

造一个工具不是免费的:它的 name/description/schema 会在每轮 prompt 里占 token,工具太多还会增加 agent 选错工具的概率。因此对每个候选算净收益

$$Saving = Cost_{original} - Cost_{macro} - Cost_{schema},$$

其中 $Cost_{macro}$ 是改用工具后的完整 API 成本(按 occurrence 当时的真实 context 重建两边账本),$Cost_{schema}$ 是 schema 的长期占用。**不能只看均值**:一个工具若在少数任务省很多、在其余任务经常失败,均值会掩盖风险,所以我们对跨任务 saving 做 bootstrap,只保留置信下界 LCB 仍大于 0 的候选。若同时有多个候选,则在固定的 registry token 预算下按"边际新增节省 / schema tokens"贪心选择,覆盖同一批 step 的重叠部分只计一次——这是一个 budgeted weighted coverage 问题,不需要 ILP。

### 10.6 验证门:保证收缩不损害正确性

净收益为正还不够。图里可能有缺失或错误的依赖,导致工具漏掉下游真正需要的信息,悄悄改变 agent 行为。因此每个候选必须依次通过三道 gate,才能进入工具库:

1. **Contract replay**:在该 motif 历史 occurrence 的相同 repo 状态上执行工具,要求它仍能复现原来的关键 `file:line` 与代码上下文,且输出不超预算。
2. **State/verifier replay**:在工具之后重放历史中已确定的下游改动步骤,检查最终 diff 与目标测试是否仍一致(stateful 工具还要直接比对 changed files 与 diff hash)。
3. **Held-out rollout**:把工具临时加入 registry,在**没有参与 motif mining 的新任务**上重新跑 agent,确认 pass rate 满足预注册的 non-inferiority 约束、同时成本确实下降。

只有全部通过的候选才形成一张简短的"工具实现卡"(pattern、inputs/outputs、output budget、saving 的均值与 LCB、各 replay 的通过率),交给 evolve agent 去实现 `tools.json + executor.py`。也就是说,**图算法负责回答"该实现什么、为什么值得、是否安全",evolve agent 只负责把已验证的契约写成可执行代码**,而不是自己凭 prompt 猜工具。`instruction.md` 遵循同一原则:只有跨成功轨迹重复、并通过 held-out rollout 的行为规则才能写入;失败轨迹只能提出待验证假设,不能直接变成"跳过测试""直接 commit"这类高风险规则。

正因为有这道门兜底,我们才把优化目标设成**双向**的:在成功率非劣的底线之上,主动争取它变得更高——放宽的成功定义让训练集包含更多"部分成功"的真实定位/修改片段,进化出的工具与 instruction 更可能帮后续 agent 把部分成功推成完全成功。

### 10.7 多轮进化与防止错误自我强化

进化是闭环:上一轮生成的工具会出现在下一轮的轨迹里。因此从第二轮起,候选不再只有"新增",而是对现有工具做 **KEEP / REFINE / MERGE / REMOVE / ADD** 五类决策(依据是真实使用中的 adoption、成功率、fallback 频率与 saving LCB)。为了跨轮对齐模式,我们维护两个图视图:**execution graph** 记真实成本,**semantic expansion graph** 把一次工具调用虚拟展开回它代表的原始 motif,仅用于对齐、不作为新的成功证据。

一个"看着省钱、其实有害"的工具一旦进库,可能在后续每轮被反复复用、错误被放大。我们用几条约束把它挡住:(1) 一次工具调用不增加其原始 motif 的 support,support 仍按去重后的独立 task 统计;(2) semantic expansion 只用于对齐,成功证据只来自本轮真实调用与 verifier;(3) 工具的新旧版本分别统计,不能用旧版本成绩为新版本背书;(4) 每轮保留**固定的 baseline/held-out 任务**,防止 registry 在自己产生的 rollout 分布上闭环过拟合;(5) REFINE/MERGE/ADD 一律要重新过 §10.6 的门。因此多轮进化不是"工具越积越多",而是一个带 lineage、可回退、由实测反馈驱动的受控更新过程。
