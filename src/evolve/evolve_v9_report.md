# Evolve v9 三 benchmark 深度实验与错误分析

分析日期：2026-07-16（Asia/Shanghai）

## 1. 结论先行

这组三 benchmark 结果目前不能支持“v9 在保证 performance 的同时降低 API cost”这一结论。更严格地排除 Harbor 异常后，SWE-Bench 和 DeepSWE 的 v9 成本都高于 no-evolve；DAB 根本没有完成 evolve，所谓降本主要来自安装、额度和 Docker 构建失败导致的提前退出。

最重要的结论如下。

1. **SWE-Bench 明确变贵。** 62 个有双方 trajectory 的 case 上，成本从 5.4406 增至 5.8809（+8.09%）。排除双方 Harbor 异常后，61 个干净配对 case 从 5.1738 增至 5.7980（+12.06%）。严格按预期 64 case 计，成功数从 48 降为 47，因为 v9 有两个 agent 安装失败的 case。
2. **DeepSWE 的 -0.60% 是不稳定且被异常 case 驱动的表面收益。** 原始 64 case 从 23.2864 降到 23.1475，bootstrap 区间跨 0；排除双方 verifier timeout 后，61 个干净配对 case 反而从 22.2665 增至 22.4221（+0.70%）。成本下降和上升的 case 分别为 31/33，中位数 `Δcost=+0.0060`，即典型 case 实际更贵。
3. **DAB 实验无效。** 三轮都在 evidence copy 前失败，最终 harness 仍为 0 个工具。final eval 的 64 个 trial 中只有 38 个有 trajectory，Harbor 记录了 41 个异常；成功数从 no-evolve 的 54/64 降至 17/64。不能把缺失或提前退出产生的低成本解释为 v9 节省。
4. **存在 P0 级 staging 隔离漏洞。** DeepSWE 三轮 promotion gate 全部拒绝，但 cycle 3 的 repair agent 用 `cp` 把 staging 文件复制进 active `scripts/`。最终 eval 的 registry hash 与 cycle 3 batch 3 被拒绝的中间 registry 完全一致，因此 DeepSWE final eval 实际使用了一个从未 promotion 的 harness。
5. **主要成本根因不是 step 数本身，而是工具没有真正压缩决策轮次。** SWE-Bench 虽然平均 action step 少 3.76%，但平均每 step observation 增加 5.03%，cached context 增加 20.29%；新增成本的 80.4% 来自 cached-context。DeepSWE 的总工具调用反而比 baseline 多 10.45%，平均 step 多 7.83%。
6. **初始 evolve prompt 并不缺现有 harness，也没有预设只能生成某几类工具。** 它包含完整 `tools.json`、完整 `instruction.md`、`executor.py` 摘要及本地文件路径，也明确写了没有固定工具类别/名称。真正的问题是 card 把具体命令、参数、路径和 observation 数据流压成了 `read -> read` 一类抽象标签，compiler 没有足够信息设计真正能替代多个 LLM 决策的 compound tool。
7. **`batch_size=2` 只限制了 card 数，没有真正解决 prompt 过长。** 每张 card 又序列化了跨多个任务的全部 occurrence。SWE 每个 batch 平均仍涉及约 14–15/16 个任务、约 8.0k–9.0k prompt tokens；DeepSWE 约 7.9k–8.6k。换言之，样本仍以聚合 occurrence 的形式几乎全塞进每个 prompt。

因此，优先级最高的工作不是扩大 evolve samples 或增加轮次，而是先修复隔离、错误计量和 DAB task 选择，再让 pattern card 保留可执行的数据流证据，并用 tool-level telemetry 验证“一个新工具是否真的替代了至少两个 LLM 轮次”。在这些问题修好前，继续增加 evolve case、cycle 或 compiler call 只会扩大成本和选择偏差。

## 2. 分析范围、数据与口径

### 2.1 实验目录

| Benchmark | v9 evolve work dir | v9 final eval | no-evolve |
|---|---|---|---|
| SWE-Bench Verified | `results/evolve/v9cycle/swebench/0715-235050` | `results/eval/swebench-verified/evolve-v9cycle-swebench-0715-235050` | `results/no_evolve/swebench-verified/noevolve-swebench-0703-031441` |
| DeepSWE | `results/evolve/v9cycle/deep-swe/0716-023159` | `results/eval/deep-swe/evolve-v9cycle-deep-swe-0716-023159` | `results/no_evolve/deep-swe/noevolve-deep-swe-0702-205240` |
| DAB | `results/evolve/v9cycle/dab/0716-063558` | `results/eval/dab/evolve-v9cycle-dab-0716-063558` | `results/no_evolve/dab/noevolve-dab-0713-174201` |

辅助复算脚本为 `scripts/analyze_v9_results.py`。它只读现有结果，不重新运行 benchmark。

### 2.2 成本计算

本报告**不使用 provider/framework 的 `cost_usd` 字段**。每个 case 的成本均由 trajectory/result 中持久化的 token usage 按模型配置重新计算：

```text
cost = uncached_input_tokens × 1 / 1e6
     + cached_input_tokens   × 0.02 / 1e6
     + output_tokens         × 2 / 1e6
```

配置文件字段为 `price_yuan_per_million_token`，因此下文数值的实际单位是配置中的人民币成本单位，不应叫 USD。代码内仍有 `api_cost`、`cost_saving_per_64_cases` 等通用字段名，但计算来源是 `token_usage_x_configured_price`。

### 2.3 统计定义与限制

- `steps`：trajectory 中包含 tool call 的 action step 数；一个 step 可以并行包含多个 call，因此 tool-call 总数可能大于 step 数。
- `observation tokens`：原始结果未为每个 observation 单独保存 tokenizer 计数，本报告使用 `ceil(chars/4)` 估算。成本本身使用真实 usage token，不使用该估算。
- `raw paired`：双方都有 trajectory 即配对，即便 Harbor root result 记录 verifier/agent 异常。
- `clean paired`：双方都有 trajectory，且双方 Harbor root exception map 都没有该 case。
- 缺失 trajectory、agent 安装失败、agent 中途异常不能作为节省；performance 的严格统计按预期 64 case 将其视为失败。
- no-evolve 与 v9 虽然 case 集、模型配置一致，但不是同时运行：SWE 间隔约 12 天，DeepSWE 约 14 天，DAB 约 3 天，且没有固定 rollout seed/多次重复。因此单 case 差异不能直接归因于 harness；报告只在聚合、工具采用和错误证据一致时讨论机制。

## 3. 实验有效性审计

### 3.1 Case 选择本身是正确的

| Benchmark | Evolve 选择 | 多样性 | Final eval | 与 evolve 重叠 | 与 no-evolve case set |
|---|---:|---:|---:|---:|---:|
| SWE-Bench | 16 | 12 个 codebase | 64 | 0 | 完全一致 |
| DeepSWE | 16 | 16 个 codebase/task family | 64 | 0 | 完全一致 |
| DAB | 16 | 14 个 dataset | 64 | 0 | 完全一致 |

三个 benchmark 的 `final_eval_cases.txt` 都与对应 no-evolve 的 64 个 trial case set 完全一致；v9 final eval 创建出的 64 个 trial config 也都对应这 64 个 case。因此，**静态 split 与目录结构满足要求**。问题发生在运行完整性和 DAB prep 阶段，而不是 final case 文件选择。

### 3.2 Final eval 完整性

| Benchmark | 预期 | 有 trajectory | Harbor root 异常 | v9 strict success | no-evolve success | 是否可直接比较 |
|---|---:|---:|---:|---:|---:|---|
| SWE-Bench | 64 | 62 | 3（2 agent install，1 verifier timeout） | 47 | 48 | 只能做 61-case clean 或错误插补比较 |
| DeepSWE | 64 | 64 | 2 verifier timeout | 2 | 2 | raw 可看，但 clean 结论更可信 |
| DAB | 64 | 38 | 41（其中 26 无 trajectory） | 17 | 54 | 无效，不能形成 v9 效果结论 |

当前 `final_report.json` 的 `error_cases` 对三组实验都为空，这是错误的：它只从有 trajectory 的 per-trial `result.json` 读取 `error/exception`，没有合并 Harbor root `result.json -> stats.evals.*.exception_stats`。结果是：

- SWE 的两个安装失败被记为 `missing_cases`，同一 run 的 verifier timeout 未进入 `error_cases`；
- DeepSWE 两个 verifier timeout 未进入 `error_cases`；
- DAB 明明有 41 个 Harbor exception，仍报告 `error_cases=[]`；
- 有部分空/短 trajectory 的 DAB case token usage 为 0，却被当成真实低成本进入 paired saving。

这会系统性高估节省，应视为框架报告层的 P0/P1 问题。

## 4. 三轮 evolve 过程

### 4.1 SWE-Bench cycle/gate

| Cycle | Parent success/cost | Candidate success/cost | 点估计 saving | 异常 | 决策 |
|---|---:|---:|---:|---:|---|
| 1 | 10 / 1.5648 | 11 / 1.3346 | +14.71% | 0 | promoted |
| 2 | 11 / 1.3346 | 11 / 1.3728 | -2.86% | 0 | rollback |
| 3 | 11 / 1.3346 | 8 / 1.5178 | -13.72% | 2/16 missing，error rate 12.5% | rollback |

Cycle 1 的 bootstrap mean-saving 区间为 `[-0.0090, 0.0497]`，已经跨 0，但 gate 只使用 aggregate 点估计，不要求置信区间下界大于 0，因此仍 promotion。最终 64-case 泛化时方向反转，说明 16 case、单 rollout 的 gate 存在明显 winner's curse/选择偏差。

流程 lineage 在 SWE 上大体符合预期：cycle 1 从原始 code-agent rollout 进化；promotion 后，cycle 2 的 evidence 来自新 harness 在同一 16 case 上生成的 candidate trajectory。Cycle 2 失败后，cycle 3 正确地没有使用被拒绝 harness 作为 parent，而是继续使用 cycle 1 的 promoted trajectory。但是，cycle 2 的具体失败工具、截断、timeout 和多余 step 没有形成结构化 negative evidence，只把一条聚合 summary 放进 history；因此 cycle 3 仍容易重复类似设计。

### 4.2 DeepSWE cycle/gate

| Cycle | Parent success/cost | Candidate success/cost | 点估计 saving | 决策 |
|---|---:|---:|---:|---|
| 1 | 0 / 4.8712 | 0 / 5.5920 | -14.80% | rollback |
| 2 | 0 / 4.8712 | 0 / 5.1543 | -5.81% | rollback |
| 3 | 0 / 4.8712 | 0 / 5.2917 | -8.63% | rollback |

三轮 parent 都是同一份原始 rollout，三轮的 15 张 pattern cards 和 15 张 instruction cards **完全相同**，pattern pool 都是 565 个、occurrence 都是 1,587 个。也就是说，三轮没有获得新 evidence，只让 compiler 对完全相同的证据进行随机重写，累计消耗 17.9340 成本单位。

所有 DeepSWE evolve sample 的 full success 都为 0，但它们的 partial score 往往很高。当前 gate 只以 full success 计非退化约束，parent/candidate 都为 0 时几乎没有 performance 分辨率。应同时使用 paired `primary_score/partial` non-inferiority；否则一个 candidate 可以大幅破坏部分测试但仍显示 0→0。

### 4.3 DeepSWE 被拒绝 harness 泄漏到 active registry

证据链是完整的：

1. 三轮 `experiment_report.json` 均为 `rollback-and-continue`，没有 promotion。
2. 每轮 `registry_before` 都是 baseline hash `25d4a06ac8eb9d0400abbe4f3288f069`。
3. Cycle 3 的 `ROLLED_BACK` 文件却记录 `cda9f7b73181a7415a7117a894ace349`，已经与 parent hash 不同。
4. `cycle-3/compiler_batches/batch-03/repair-1/compile_trajectory.json` 的 repair agent 明确执行：

```bash
cd .../0716-023159 && \
cp cycle-3/staging/tools.json scripts/tools.json && \
cp cycle-3/staging/executor.py scripts/executor.py && \
cp cycle-3/staging/instruction.md scripts/instruction.md && \
cp cycle-3/staging/change_manifest.json scripts/change_manifest.json
```

5. Final active registry hash `cda9f7...` 与 `cycle-3/compiler_batches/batch-03/registry_after` 完全相同。
6. Final eval 64/64 case 都采用了 `search_code`/`read_file_range`，证明最终确实加载了该被拒绝 registry。

根因有两层：

- `run_compile_agent` 虽以 staging 为 cwd，compiler 仍有不受限制的本机 bash/filesystem 权限，可以 `cd ..`、搜索整个 repository，并写 active path；prompt 中“Never edit active registry”不是安全边界。
- `_rollback()` 只写 `ROLLED_BACK` marker 和 summary，不恢复 cycle 开始时的 active snapshot，也不验证 active hash 是否仍等于 `registry_before`。

所以当前 DeepSWE final eval 既不能代表 baseline harness，也不能代表合法 promotion 的 v9 harness。修复必须是技术隔离：compiler 进程只挂载 staging 为可写、active 和历史结果不可见或只读；每次 compiler/repair 后检查 active hash；发现变化立即从 snapshot 原子恢复并使 cycle hard-fail。仅加强 prompt 不足以修复。

### 4.4 DAB 三轮为何完全没有 evolve

DAB 选出的 16 个 evolve ID 与 prep run 实际生成的 16 个 trajectory **交集为 0**。每轮 `_annotation_copy` 都报告同一组 16 个 trajectory 缺失，所以 cards、compiler、canary、gate 均未运行。

具体根因在 DAB task adapter 的交互：

1. `run_evolve_experiment.sh` 先把选定的 16 个 task 软链进临时 `prep_taskdir/`。
2. 该临时目录没有 `manifest.json`。
3. `scripts/run_dab_harbor.sh` 看到 `manifest.json` 不存在，调用 DAB adapter 用 `--overwrite` 在同一目录重新生成全量 task。
4. 原来的精确 16-case 目录被扩成全量集合；`--n-tasks 16` 随后运行 Harbor 自己选到的 16 个任务，而不是 `eval_cases.txt` 中的 16 个。

正确修复可任选其一：对已有 `task.toml` 目录不再触发 adapter；在精确 taskdir 中写一个只含选中任务的 manifest；或者让 Harbor 接收显式 case filter。启动 evolve 前还应 assert prep trajectory ID set 与 evolve ID set 完全一致，不一致时停止整个 benchmark，而不是重复三轮同一异常后继续 final eval。

## 5. Final eval 成本与 performance

### 5.1 原始、干净配对与严格 performance

| Benchmark | Raw paired cost（no → v9） | Raw 变化 | Clean paired cost（no → v9） | Clean 变化 | Strict success（no → v9） |
|---|---:|---:|---:|---:|---:|
| SWE-Bench | 5.4406 → 5.8809（62） | **+8.09%** | 5.1738 → 5.7980（61） | **+12.06%** | 48 → 47 |
| DeepSWE | 23.2864 → 23.1475（64） | -0.60% | 22.2665 → 22.4221（61） | **+0.70%** | 2 → 2 |
| DAB | 6.1757 → 2.6620（38） | -56.89%（无效） | 2.0455 → 1.7470（23） | -14.59%，但 success 20→17，且 0 新工具 | 54 → 17 |

SWE 与 Deep 的 final bootstrap interval 都跨 0。SWE 的 `baseline-candidate` mean interval 是 `[-0.0342, 0.0147]`；Deep 是 `[-0.0353, 0.0367]`。DAB raw interval虽为正，但样本选择被异常严重污染，没有解释意义。

### 5.2 SWE-Bench trajectory 统计

统计基于 62 个 raw valid pairs。

| 指标（每 case 均值） | no-evolve | v9 | 变化 |
|---|---:|---:|---:|
| API cost | 0.08775 | 0.09485 | +8.09% |
| Action steps | 56.23 | 54.11 | -3.76% |
| Observation tokens / step（估算） | 300.7 | 315.8 | +5.03% |
| Observation tokens / case（估算） | 15,150.9 | 15,609.7 | +3.03% |
| New input tokens | 25,576.6 | 26,309.7 | +2.87% |
| Cached input tokens | 1,407,671.7 | 1,693,231.5 | **+20.29%** |
| Output tokens | 17,010.8 | 17,339.1 | +1.93% |
| First-action prompt tokens | 1,400.5 | 2,930.6 | +1,530.1（+109.3%） |
| User-message chars | 4,225.7 | 6,752.7 | +2,527（+59.8%） |
| Bash calls | 56.81 | 39.55 | -30.38% |
| Native calls | 0 | 16.26 | +16.26 |

成本 delta 分解：

| 成本分量 | 62-case 总 Δ（v9-no） | 占净增量 |
|---|---:|---:|
| New input | +0.04545 | 10.3% |
| Cached context | **+0.35409** | **80.4%** |
| Model output | +0.04072 | 9.2% |
| 合计 | +0.44027 | 100% |

虽然 bash 从 3,522 次降到 2,452 次，但又增加 1,008 次 native call，总 tool calls 只从 3,522 降到 3,460（-1.76%）。新增工具大多是一对一替代 bash，并没有形成大规模的多轮合并；固定 schema/instruction 则在后续每轮上下文中持续暴露。`Δcost` 与 `Δsteps` 的 case-level 相关系数为 0.902，说明真正重要的是是否减少模型决策轮次，而不是是否把 bash 换成 native tool。

SWE 的总体变贵也具有重尾特征：

- `django__django-11734`：成本 +0.6389、step +122、native 64 次，performance 0→1；这是昂贵的性能改善，不是降本。
- `scikit-learn__scikit-learn-9288`：成本 +0.3160、step +127、performance 1→1；这是纯粹的效率退化。
- 两个 case 合计 +0.9549，超过全体净增量；但它们正是小规模 16-case gate 很容易漏掉的长尾风险，不能在总体结论中删除。
- 成本上升/下降 case 为 32/30，中位数 `Δcost=+0.00061`，大多数 case 没有稳定收益。

Performance 在 62 个 raw pair 上为 46→47（5 个 regression、6 个 improvement），但两个缺失 case 在 no-evolve 都成功；按完整 64 case 是 48→47。五个有 trajectory 的 regression 为 `django__django-13512`、`django__django-16642`、`mwaskom__seaborn-3069`、`pylint-dev__pylint-6386`、`pytest-dev__pytest-10051`。

### 5.3 SWE-Bench 工具采用与错误

| Tool | Calls | 平均返回 tokens（估算） | 非零 returncode | 含截断结果 | 主要问题 |
|---|---:|---:|---:|---:|---|
| `bash` | 2,452 | 185.3 | 512 | 2 | baseline 能力，失败包含正常测试失败等 |
| `read_file` | 723 | 544.0 | 2 | 150 | 最常用但只是 primitive read；无 range 时截断却常无可用 continuation |
| `search_files` | 166 | 257.6 | 60 | 21 | 31 timeout、22 把 file 当 directory、6 cwd 外路径、1 路径不存在 |
| `list_directory` | 54 | 664.2 | 4 | 27 | 50% 截断，4 次 cwd 外路径 |
| `search_and_read` | 43 | 515.5 | 12 | 11 | 3 timeout、8 file-as-directory、1 路径不存在 |
| `batch_read` | 22 | 842.1 | 0 | 14 | 63.6% 截断；采用率很低 |

62/62 case 都使用了新工具，说明“没有采用”不是 SWE 失败原因；问题是采用质量。1,008 次 native call 中只有 22 次 `batch_read` 和 43 次 `search_and_read` 是明确 compound 形态，主体是 `read_file` 这类已有 bash 能做的 primitive wrapper。Native failure 为 78 次，含截断的 native result 约 223 次。工具使模型更愿意频繁读取，却没有可靠减少后续推理。

实现层还有几个具体缺陷：

- `search_files`/`search_and_read` 用 Python `os.walk` 扫 repository，缺少 `.git`、vendor、build 等完整排除和真正的全局早停，最终被 runtime 30s hard cap 杀掉 34 次。
- `search_files` schema 要求 `path` 是目录，但实际 agent 经常传具体文件；executor 不做 file/dir 双态处理，产生 30 次 file-as-directory 错误。
- `read_file` 在未指定 `end_line` 时会逐行收集整个文件后再 clamp；若因 4,000 chars clamp 截断，`has_more` 仍可能为 false，不返回 `next_offset`。
- `search_files`、`search_and_read`、`batch_read` 会返回 `next_offset`，但 schemas 没有 `offset` 参数且 executor 不读取 offset；instruction 要求“使用 next_offset 继续”，实际协议无法执行。
- `list_directory` 先构造完整 entries 再 clamp，没有 continuation；这解释了 27/54 截断。

### 5.4 DeepSWE trajectory 统计

统计基于 64 个 raw pairs；需牢记 final harness 来自 staging 泄漏。

| 指标（每 case 均值） | no-evolve | v9 | 变化 |
|---|---:|---:|---:|
| API cost | 0.36385 | 0.36168 | -0.60% |
| Action steps | 118.16 | 127.41 | **+7.83%** |
| Observation tokens / step（估算） | 479.7 | 388.3 | -19.06% |
| Observation tokens / case（估算） | 44,653.0 | 40,865.8 | -8.48% |
| New input tokens | 73,191.8 | 69,430.0 | -5.14% |
| Cached input tokens | 8,957,160 | 9,073,034 | +1.29% |
| Output tokens | 55,757.7 | 55,394.8 | -0.65% |
| First-action prompt tokens | 1,527.0 | 2,338.0 | +811（+53.1%） |
| User-message chars | 5,129.8 | 6,930.8 | +1,801（+35.1%） |
| Bash calls | 126.89 | 100.36 | -20.91% |
| Native calls | 0 | 39.80 | +39.80 |

Deep 的 baseline tool calls 为 8,121；v9 是 6,423 bash + 2,547 native = 8,970，总调用 **增加 849（+10.45%）**。这直接说明 `read_file_range` 没有压缩 workflow，而是把原本一个 bash/sed/cat 操作拆成更多分页和 LLM 决策。

成本分量总 delta 为：new input -0.24075、cached context +0.14832、model output -0.04645，合计 -0.13888。表面上更短的 observation 抵消了更多 step 和固定 schema，但 margin 极小；去掉异常 case 就反转为 +0.70%。

Deep 的 primary partial score 均值为 0.8381→0.8472（+0.0091），但中位数 0.9618→0.9568，64 case 中 26 上升、26 下降、12 不变，没有一致方向。Full success 为 2→2，同时有 2 regression 和 2 improvement。该结果加上跨 0 的 cost CI，不构成 performance-preserving saving 证据。

### 5.5 DeepSWE 工具采用与实现缺陷

| Tool | Calls | 平均返回 tokens（估算） | 非零 returncode | 含截断结果 | 主要问题 |
|---|---:|---:|---:|---:|---|
| `bash` | 6,423 | 182.9 | 797 | 15 | baseline 能力 |
| `read_file_range` | 2,259 | 587.2 | 5 | 269 | 88.7% native calls 是 primitive read，推动多轮分页 |
| `search_code` | 288 | 392.3 | 16 | 0 | 4 timeout、11 invalid regex、1 pattern 被当 grep option |

两个工具 64/64 case 全部采用，native failure 仅 21 次，但 observation 截断和额外轮次仍然很多。可靠不等于省成本：primitive read 的低 failure rate 不能补偿 2,259 次额外 schema-guided call。

具体实现问题包括：

- `search_code` 用 `grep -rn -m N`。GNU grep 的 `-m` 是每文件上限，不是全局上限；递归搜索仍可产生大结果并扫描无关目录。
- 命令没有在 pattern 前加入 `--`，以 `-` 开头的 pattern 被解释为 option；regex 默认开启，agent 常把代码片段当 regex，产生 11 次 unmatched-regex。
- executor 实现了 `offset`，但 `tools.json` schema 未声明，模型不知道该参数；pagination contract 再次断裂。
- `read_file_range` 整文件模式截断时没有结构化 `next_offset`；range 模式 clamp 后也不提示截断位置。
- `_resolve_path` 用字符串 `resolved.startswith(cwd)` 判断边界，`/testbed2` 会被错误视为 `/testbed` 内路径，存在安全边界缺陷。
- Final instruction 写了“Prefer structured tool calls over raw bash”，这不是 evidence 证明的条件，而是 compiler 自行加入的强 adoption bias；64/64 全采用正是其结果。

### 5.6 DAB 错误细分

DAB final 41 个 Harbor exception 可分为：

| 错误 | 数量 | 阶段 | 解释 |
|---|---:|---|---|
| Agent install `curl` HTTP 504 | 22 | setup | 每个容器在线下载 uv/mini-swe-agent，在 concurrency=64 下网络服务失败 |
| `Insufficient Balance` 导致 mini-swe agent exit 1 | 15 | rollout | API 额度/并发侧外部失败；其中部分留下短 trajectory 和低/零 token cost |
| Docker build `DeadlineExceeded` | 4 | environment build | 大 build context（示例达 4.26GB）和并发构建超过 deadline |

26 个 case 没有 trajectory；另有 15 个异常 case 留下部分 trajectory，因此 raw 38-pair 集合仍被错误污染。`dab__imdb__query6`、`dab__patents__query2` 等 case 甚至有 0 token/0 step 记录，这不是“免费解决”，而是 agent 未运行完成。

即使只看双方无 Harbor exception 的 23 case，成本虽然下降 14.59%，performance 仍从 20/23 降为 17/23，而且 active registry 是 0 个工具，只增加了 3 条 v9 baseline instruction。该差异只能归因于 rollout 随机性/短 prompt 行为偏置，不能归因于 evolved tools。

## 6. Pattern cards、instruction cards 与初始 evolve prompt 审计

### 6.1 Card 数量与质量

| Benchmark/cycle | Pattern pool | Occurrences | 选中 pattern | Instruction cards | Evidence role |
|---|---:|---:|---:|---:|---|
| SWE c1 | 382 | 1,097 | 15 | 15 | 全部 mixed-outcome hypothesis |
| SWE c2 | 329 | 972 | 15 | 15 | 全部 mixed-outcome hypothesis |
| SWE c3 | 347 | 992 | 15 | 15 | 全部 mixed-outcome hypothesis |
| Deep c1/c2/c3 | 每轮 565 | 每轮 1,587 | 每轮 15 | 每轮 15 | 全部 failure-only waste signal |
| DAB | 0 | 0 | 0 | 0 | evidence copy 前失败 |

“去掉质量筛选”已按此前要求实现：SWE 和 Deep 每轮 16/16 parseable trajectory 都进入 mining，不按 task success 过滤。SWE c1/c2/c3 分别有 12/13/8 个任务带 diagnostic issue；Deep 每轮 16/16 都有 issue、其中 7 个是 degenerate minimal path，但它们仍全部被赋予 `quality_score=1.0`。

不做 hard filter 是合理的，因为失败 trajectory 的确包含浪费模式；但把所有诊断异常都当成满质量也过于激进。更合适的是：不丢弃 sample，但用 soft weight 区分“可用于生成工具的成功数据流”“只能用于生成停止/回退 instruction 的失败信号”“dependency 不可信的低权重证据”。

### 6.2 Pattern abstraction 丢失了 compiler 最需要的信息

SWE 的高频 cards 是：

```text
read|command|source,test -> read|command|source,test
search|command|source,test -> read|command|source,test -> read|command|source,test
interpreter|command|source,test -> interpreter|command|source,test
```

Deep 的 15 张 cards 几乎都是 2–5 个 `read|command|source` 串联。

这些 signature 只保留 operation、argument key 类型和粗粒度 path role；对 bash 来说参数 key 永远只是 `command`，路径角色又从整条命令里扫描 `.py`/`test` 得到。因此它丢失了：

- 实际 executable 与 flags；
- 搜索 query/regex；
- 是同一文件分页还是不同文件的独立读取；
- 前一 observation 是否决定了后一 call 的路径或参数；
- 输出长度、命中率、错误类型；
- 哪些 turn 真能在调用前一次性确定参数，哪些必须等上一轮 observation 后才能决定。

“相邻两次 read”并不等价于“可以 batch”：第二次路径可能来自第一次内容，属于真实的 LLM 决策依赖。Card 却统一写成“Design one structured workflow which can replace N turns”，compiler 只能猜出通用 read/search/list wrapper。SWE 最终 5 个工具、Deep 最终 2 个工具正与这种信息贫化一致。

### 6.3 Instruction cards 多数只是机械 phase transition

代表性 rule 是：

```text
Move from read to interpreter ...
Move from search to read ...
Move from read to version_control ...
Move from version_control to read ...
```

支持度高只是因为这些 phase 在 coding trajectory 中普遍相邻，不代表它们能降低成本。相反，`read→search` 与 `search→read`、`read→version_control` 与 `version_control→read` 可以同时被选中，形成近似同义的“观察后进入下一阶段”规则。这类规则很难验证，也增加 instruction 长度和行为偏置。

`failed_call_change_or_fallback` 在 SWE/Deep 都达到 16-task support，但“失败”包含正常的测试失败、grep 无匹配等不同语义；support 不能直接等同为一个统一策略的因果证据。

### 6.4 Prompt 是否包含现有 tools、executor、instruction 与 v6 式 evolve 指令

答案是：**包含，而且不是此次失败的主要缺失项。** 以用户指定的 `cycle-2/compiler_batches/batch-01/compile_prompt.md` 为例：

- 包含完整 current `tools.json`；
- 包含完整 current `instruction.md`；
- 包含 `executor.py` 的 path、hash、line count、imports、dispatch 等摘要，并明确允许检查 staging 中的本地实现；
- 包含目标：降低 future API cost、保持 correctness；
- 明确没有固定工具类别/名称；
- 要求 compound tool 至少替代两个 observed LLM turns；
- 包含 tools/executor schema、stdlib、path safety、timeout、output cap、fallback、smoke validation；
- 要求 add/merge/narrow/repair/remove，而不只是新增。

相较 v6，v9 增加了 staging、manifest attribution、gate 和安全/输出 contract，也去掉了 v6 中鼓励无条件跳过验证的危险表述。这些是进步。

但 v9 丢掉了 v6 prompt 最有用的部分：v6 为每个 batch 展示 `Original Trajectory` 与 `Minimal Trajectory` 的具体序列化，compiler 至少能看到操作和 observation；v9 只给抽象 card 和 source path/node id。Compiler 虽可自行打开 source trajectory，但 prompt 没要求它针对代表 occurrence 读取对应 call/observation，实际 compiler 更多时间花在 repository 探索和 manifest 修补上。

### 6.5 `batch_size=2` 为什么 prompt 仍长

| Benchmark | Cycle | 8 个 batch 平均 chars | 约 tokens（chars/4） | 最大约 tokens | 每 batch 平均涉及任务数 |
|---|---:|---:|---:|---:|---:|
| SWE | 1 | 31,841 | 7,960 | 9,235 | 14.8/16 |
| SWE | 2 | 34,355 | 8,589 | 9,861 | 14.2/16 |
| SWE | 3 | 35,834 | 8,959 | 10,716 | 14.2/16 |
| Deep | 1 | 31,596 | 7,899 | 9,870 | 15.4/16 |
| Deep | 2 | 32,170 | 8,043 | 10,001 | 15.4/16 |
| Deep | 3 | 34,518 | 8,629 | 10,456 | 15.4/16 |

每个 batch 的确只有 2 pattern + 2 instruction cards；问题是每张 pattern card 带所有 `occurrences`，每张 instruction card 又带 support task、positive examples 和 negative controls。一张 card 就能覆盖 10–16 个任务。Batching 因而只解决了“card 数量”，没有解决“sample evidence 序列化数量”。

另一个问题是 prompt 要求“所有当前和历史 batch card 都必须在 manifest 中被 change 或 rejection account”。这会把 compiler 的目标从设计少量高价值工具变成完成 attribution bookkeeping，也会让后续 batch 保留/修改早期随机决定。可以保留审计，但应由框架自动维护候选状态，不应让 LLM 手工背负全量 manifest 一致性。

### 6.6 Compiler 自身成本与迭代次数

| Benchmark | Compile invocations | 其中 repair | Compiler 内模型 API calls | Compiler cost | Canary cost | 已知 evolve 总成本 |
|---|---:|---:|---:|---:|---:|---:|
| SWE | 27 | 3 | 532 | 1.7966 | 3.9857 | 5.7823 + 两轮 annotation 未计 |
| Deep | 29 | 5 | 627 | 1.8960 | 16.0380 | 17.9340 |
| DAB | 0 | 0 | 0 | 0 | 0 | 0 |

所以“一轮 evolve 只启动 agent 一次、只改 harness 一次”并不符合 v9 实际行为。每 cycle 有 8 个串行 compiler agent invocation，每个 invocation 内又会产生十几到几十个模型 API call，并可能有 repair。SWE 三轮总 27 次 compiler invocation；Deep 总 29 次。

SWE final 每 64 case 不但没有 saving，因而永远无法 amortize 5.7823 的 evolve 成本。Deep raw 结果给出的 break-even 约 129 次 64-case eval，但该计算建立在被拒绝 harness 泄漏和不显著的 0.1389 表面 saving 上；clean paired 已变为成本上升，所以实际也没有 break-even。DAB 没有执行 evolve，break-even 不适用。

## 7. 框架实现层的根因排序

### P0：必须先修复

1. **Compiler staging 无 filesystem 隔离，rollback 不恢复 active。** 已导致 DeepSWE 被拒绝 harness 用于 final eval。
2. **Final/gate 错误检测没有合并 Harbor root exception map。** 部分/空 trajectory 被当作正常低成本；`error_cases=[]` 与真实 3/2/41 个异常矛盾。
3. **DAB 精确 taskdir 被 adapter 重新生成覆盖。** 16 个 selected evolve cases 与 prep trajectories 零交集。
4. **实验遇到 cycle-level infrastructure/input exception 仍机械重试三轮并继续 final eval。** Gate failure 应总结并继续，但 case-set mismatch、registry mutation、无完整 baseline 属于 hard failure，不能当普通 candidate rollback。

### P1：直接决定能否降本

1. **Pattern card 丢失参数和数据依赖。** `read→read` 无法告诉 compiler 什么能安全合并。
2. **Gate 在同一 16 个 discovery case 上做单次 stochastic selection。** 无独立 heldout evolve-canary、无重复 seed、bootstrap 区间不参与 promotion，造成 cycle 1 winner's curse。
3. **Gate 不验证工具是否真的替代轮次。** Structural validator 只验证 JSON/Python/smoke，不要求 tool adoption、`Δsteps<0`、输出变小或一个 compound call 替代 ≥2 个模型决策。
4. **Schema/instruction recurring overhead 没用实际序列化成本扣除。** Miner 使用固定 `schema_tokens=160` 粗估，不按最终 schema、instruction、trajectory turn 数和 cache 价格做 tool-level净收益。
5. **Pagination/output contract 只做表面验证。** 多个工具返回模型不可调用的 `next_offset`，或发生截断却没有 continuation；validator 未做 round-trip property test。
6. **失败 cycle 的细粒度 telemetry 没进入下一轮。** Deep 三轮 cards 完全相同；SWE 也只传 aggregate history，没有把 native failure、timeout、truncation、重复 bash/native、case regression 变成 rejected signature。

### P2：实验质量与成本效率

1. Compiler prompt 每 batch 仍约 8k–10k tokens，并产生 532/627 次模型 API call；需要压缩 card，而不仅是减 card 数。
2. Final no-evolve 基线不是同时间、同 seed 复跑，难以区分 harness effect 与 rollout/service drift。
3. `--n-concurrent 64` 对 DAB 的在线安装、API quota、Docker build 明显超过稳定容量；SWE 也出现两个相同 HTTP 504 安装失败。
4. Prompt 写 120s tool timeout，但环境自身 timeout 使实际 hard timeout 为 `min(120, 30)=30s`。运行时错误 observation 的“缩小范围或回退 bash”措辞符合要求，但 compiler 看到的 timeout contract 与真实值不一致。
5. Final report 字段名 `api_cost` 容易被误读成 USD，应持久化 `currency`/`price_config_hash` 并在报告中显示。

## 8. 不大改框架前提下的具体优化方案

### 8.1 先修正确性和可审计性

1. Cycle 开始时保存 active 三文件 snapshot/hash；compiler container 只挂载 staging 为 writable，active/results/history 不挂载或只读。
2. 每次 compile/repair 后 assert active hash 未变；异常则原子恢复 snapshot，标记 `isolation_violation` 并终止 benchmark。
3. `_rollback` 必须真的恢复 snapshot，而不是只写 marker；final deploy 只能接受最后一个 `PROMOTED` hash。
4. 从 Harbor root result 合并 per-case exception。Agent/setup error：candidate cost 按 baseline 插补且 performance 失败；verifier-only error：agent cost可以单列，但 performance 未知，不能作为 promotion 证据。
5. Final eval 不是 64/64 complete 或 error rate 非零时，输出 `valid_for_claim=false`，禁止计算/展示 break-even 主结论。
6. DAB 精确目录写 manifest，并在 prep 后 assert `trajectory_ids == evolve_ids`。

### 8.2 只改 card/prompt 的核心内容

每张 pattern card 不再序列化全部 occurrence，而保留最多 2 个代表正例 + 1 个反例，每个例子包含：

```json
{
  "calls": [
    {"tool": "bash", "normalized_command": "rg <QUERY> <PATH>"},
    {"tool": "bash", "normalized_command": "sed -n <RANGE> <MATCHED_FILE>"}
  ],
  "data_dependency": "call_2.file comes from call_1 output",
  "observations": [
    {"chars": 420, "returncode": 0, "summary": "3 paths"},
    {"chars": 1900, "returncode": 0, "summary": "target function body"}
  ],
  "contractible_before_first_call": false,
  "reason": "second path is unknown until search result; do not batch blindly"
}
```

只有当多个未来参数在第一次 call 前都已知，或 compound tool 内部能用确定规则从前一结果选择下一步时，才允许生成工具；否则只能生成 instruction（例如 narrow/stop/fallback）。这能直接避免把任意 `read→read` 当 compound candidate。

Prompt 中加入以下 v6 风格但更可验证的指令：

- 不要把一个 bash primitive 简单重命名成 native tool；候选必须展示至少两个被替代的历史 LLM action step。
- 对每个新增工具写 `replaced_steps`, `expected_output_bound`, `fallback`, `non_contractible_counterexample`。
- Failure-only card 默认只能生成 avoid/stop/recovery instruction；若没有至少一个成功或高 partial-score 支持，不生成自动化该 workflow 的工具。
- 只保留能在当前 batch 证据中证明净收益的修改；框架自动维护 rejected-card ledger，compiler 无须手工 account 所有历史 ID。
- 当前 harness 仍完整提供，但 card payload 控制在约 1,200–1,800 chars；整个 compiler prompt 目标不超过 4k–5k tokens。

### 8.3 Validator 和 tool-level gate

对每个工具自动生成并执行以下 contract test：

1. file/dir/path traversal、空参数、invalid regex、pattern 以 `-` 开头；
2. 4,000-char 边界；若截断，schema 必须声明 continuation 参数，使用 `next_offset` 后结果前进且无无限重复；
3. timeout 后 observation 必须推荐“缩小范围或回退等价 bash”，不能强制某一个选择；
4. repository scope 搜索必须跳过 `.git`、build、vendor/node_modules 等，且全局早停；
5. smoke 之外增加 recorded-occurrence replay：比较原两步/多步 bash 与 compound tool 的必要信息保真度和 output token。

Canary 必须输出每个工具的：adoption cases、call count、failure、timeout、truncation、被替代 bash 数、`Δsteps`、`Δobservation`、`Δcost`、success regression。未采用或一对一替代的工具应自动删除，而不是仅凭 aggregate gate 一起 promotion。

### 8.4 Gate 与实验设计

在仍只有 16 个 evolve case 的预算下，可以使用 2-fold cross-fitting：8 个用于 mining，另 8 个做 gate，然后交换；或 12 discovery + 4 heldout，并在三 cycle 轮换。每个 candidate 至少跑 2–3 个 seed/replicate，使用 contemporaneous parent rollout，而不是两周前的 no-evolve 单次结果。

Promotion 至少要求：

- root-exception-aware coverage 100%；
- full success 和 Deep partial score 均满足 paired non-inferiority；
- cost saving 的 bootstrap lower bound 大于一个小的负容忍阈值，或在重复 seed 中方向一致；
- 至少一个候选工具达到预设 adoption 和 `steps_replaced >= 2 × compound_calls`；
- 扣除实际 schema/instruction recurring cost 后仍为正；
- 对 performance regression case 做 targeted replay，不能仅允许 20% regression 后依靠别的 stochastic improvement 抵消。

Gate fail 后可以放弃 candidate 并进入下一 cycle，但应把 candidate 的细粒度 failure telemetry 变成 `rejected_candidate_evidence.json`，下一轮 miner 屏蔽相同 signature/实现。若 cards 与上一轮完全相同且没有新 evidence（Deep 当前情况），应直接停止后续 compiler，避免重复花费。

### 8.5 运行基础设施

- Benchmark concurrency 与 annotation/compiler concurrency 分离。DAB 建议先以 16 或容量探测值运行，不要 64 个容器同时下载 agent、构建大镜像、访问同一 API 额度。
- 预装/缓存 uv 和 mini-swe-agent，禁止每个 trial 在线从 GitHub 下载。
- DAB 镜像预构建并检查 build context，避免数 GB context；设置 `.dockerignore`。
- 开跑前做 API balance/preflight，遇 `Insufficient Balance` 停止 launch 新 trial，并在恢复后只重跑 infrastructure-failed cases。
- Final 比较应同一时间交错运行 no-evolve/evolved（例如 ABBA 顺序）并保存 seed、runtime version、image digest、price config hash。

## 9. 对论文叙述的影响

当前结果不适合写成“v9 显著降低成本并保持性能”。可靠的学术表述只能是一个诊断性负结果：

> 将高频轨迹 motif 直接编译为 native tools，会因为抽象丢失、schema/context 暴露、primitive-tool 过度采用和 stochastic gate selection 而抵消理论节省；只有验证真实决策轮次替代、异常完整性和 heldout 泛化后，cost-aware harness evolution 才成立。

修复后的论文主线仍然是合理的：

```text
token-level cost attribution
→ dependency-aware, counterexample-bearing contractible cards
→ isolated staged harness compiler
→ tool-level replay/ablation
→ paired risk-aware promotion
→ untouched final evaluation + amortized evolution cost
```

这个故事比“挖高频 pattern → 生成更多工具”更有说服力，因为它回答了三个审稿人必问的问题：为什么该序列能合并、如何证明工具真的减少了模型决策、怎样保证节省不是错误/提前退出/小样本随机性。

## 10. 逐 case 对照说明

表中格式：`success/cost/steps/平均 observation tokens`；v9 额外给出 `native calls/native failures`。Observation token 为 chars/4 估算。每一行都同时读取并比较对应 no-evolve 与 v9 trajectory；“原因”是从 token 成本分量、step、observation、工具采用、截断、运行异常和 performance 变化生成的诊断，不把单次 rollout 共现误写成因果。

### 10.1 SWE-Bench 64 case

| Case | no-evolve: success/cost/steps/obs-step | v9: success/cost/steps/obs-step/native/fail | Δcost | 原因 |
|---|---:|---:|---:|---|
| `astropy__astropy-13579` | 1/0.1671/84/248.4 | 1/0.1129/41/320.4/9/2 | -0.0543 | 成本降低，最大负贡献=cached-context；少43步；新工具9次与轮次下降同时出现，但单次 rollout 不能证明因果；新工具失败2次，产生恢复/回退开销；含截断 observation 的 step 0→4 |
| `astropy__astropy-14369` | 0/0.1233/59/445.3 | 0/0.0962/50/477.7/14/0 | -0.0271 | 成本降低，最大负贡献=model-output；少9步；新工具14次与轮次下降同时出现，但单次 rollout 不能证明因果；含截断 observation 的 step 0→7 |
| `django__django-11099` | 1/0.0217/19/366.1 | 1/0.0226/25/318.6/5/1 | 0.0009 | 成本增加，最大正贡献=cached-context；多6步导致历史重复计费；用了5次新工具但未替代 LLM 轮次；新工具失败1次，产生恢复/回退开销；含截断 observation 的 step 0→1 |
| `django__django-11141` | 0/0.0672/55/273.9 | 0/0.0870/66/306.0/24/1 | 0.0198 | 成本增加，最大正贡献=cached-context；多11步导致历史重复计费；用了24次新工具但未替代 LLM 轮次；新工具失败1次，产生恢复/回退开销；含截断 observation 的 step 0→4 |
| `django__django-11400` | 0/0.0730/49/356.1 | 0/0.0696/50/362.9/13/2 | -0.0034 | 成本降低，最大负贡献=model-output；新工具失败2次，产生恢复/回退开销；含截断 observation 的 step 0→3 |
| `django__django-11734` | 0/0.2319/94/326.3 | 1/0.8708/216/318.4/64/3 | 0.6389 | 成本增加，最大正贡献=cached-context；多122步导致历史重复计费；用了64次新工具但未替代 LLM 轮次；新工具失败3次，产生恢复/回退开销；含截断 observation 的 step 1→10；失败→成功 |
| `django__django-11749` | 1/0.0605/44/234.6 | 1/0.0528/39/289.9/10/0 | -0.0077 | 成本降低，最大负贡献=model-output；少5步；新工具10次与轮次下降同时出现，但单次 rollout 不能证明因果；含截断 observation 的 step 0→2 |
| `django__django-12050` | 1/0.0173/23/152.4 | 1/0.0330/32/265.3/4/1 | 0.0157 | 成本增加，最大正贡献=model-output；多9步导致历史重复计费；平均 observation +74%；用了4次新工具但未替代 LLM 轮次；新工具失败1次，产生恢复/回退开销；含截断 observation 的 step 0→1 |
| `django__django-12155` | 1/0.0354/30/328.9 | 1/0.0536/27/414.0/8/1 | 0.0182 | 成本增加，最大正贡献=model-output；步数未增但每轮上下文/固定 harness 更重；平均 observation +26%；新工具失败1次，产生恢复/回退开销；含截断 observation 的 step 0→3 |
| `django__django-12262` | 1/0.0457/26/510.8 | 无 trajectory | NA | 无有效 trajectory（NonZeroAgentExitCodeError），不能把缺失成本当节省 |
| `django__django-12708` | 1/0.0989/62/375.7 | 1/0.0957/54/251.6/13/3 | -0.0032 | 成本降低，最大负贡献=new-input；少8步；平均 observation -33%；新工具13次与轮次下降同时出现，但单次 rollout 不能证明因果；新工具失败3次，产生恢复/回退开销；含截断 observation 的 step 0→3 |
| `django__django-12858` | 1/0.1992/105/283.0 | 1/0.0921/63/347.0/19/2 | -0.1072 | 成本降低，最大负贡献=cached-context；少42步；新工具19次与轮次下降同时出现，但单次 rollout 不能证明因果；新工具失败2次，产生恢复/回退开销；含截断 observation 的 step 0→8 |
| `django__django-13023` | 0/0.0496/43/210.3 | 1/0.0466/37/378.1/11/1 | -0.0030 | 成本降低，最大负贡献=model-output；少6步；新工具11次与轮次下降同时出现，但单次 rollout 不能证明因果；新工具失败1次，产生恢复/回退开销；含截断 observation 的 step 1→5；失败→成功 |
| `django__django-13128` | 1/0.0936/64/290.9 | 1/0.0702/46/394.6/16/1 | -0.0234 | 成本降低，最大负贡献=cached-context；少18步；新工具16次与轮次下降同时出现，但单次 rollout 不能证明因果；新工具失败1次，产生恢复/回退开销；含截断 observation 的 step 0→4 |
| `django__django-13279` | 1/0.0531/29/634.8 | 1/0.0822/48/430.0/17/1 | 0.0291 | 成本增加，最大正贡献=model-output；多19步导致历史重复计费；用了17次新工具但未替代 LLM 轮次；新工具失败1次，产生恢复/回退开销；含截断 observation 的 step 0→5 |
| `django__django-13343` | 1/0.0496/42/272.5 | 1/0.0625/48/299.9/10/1 | 0.0130 | 成本增加，最大正贡献=model-output；多6步导致历史重复计费；用了10次新工具但未替代 LLM 轮次；新工具失败1次，产生恢复/回退开销；含截断 observation 的 step 0→3 |
| `django__django-13512` | 1/0.0524/40/479.9 | 0/0.0340/28/378.9/14/2 | -0.0184 | 成本降低，最大负贡献=new-input；少12步；平均 observation -21%；但 performance 回退，属于无效节省；新工具失败2次，产生恢复/回退开销；含截断 observation 的 step 0→5；成功→失败 |
| `django__django-13670` | 1/0.0226/21/236.5 | 1/0.0141/16/151.2/3/1 | -0.0085 | 成本降低，最大负贡献=new-input；少5步；平均 observation -36%；新工具3次与轮次下降同时出现，但单次 rollout 不能证明因果；新工具失败1次，产生恢复/回退开销 |
| `django__django-14155` | 0/0.0471/36/328.6 | 0/0.0852/51/274.3/13/1 | 0.0381 | 成本增加，最大正贡献=model-output；多15步导致历史重复计费；用了13次新工具但未替代 LLM 轮次；新工具失败1次，产生恢复/回退开销；含截断 observation 的 step 0→3 |
| `django__django-14311` | 1/0.1087/56/430.9 | 1/0.0644/41/301.2/15/3 | -0.0443 | 成本降低，最大负贡献=new-input；少15步；平均 observation -30%；新工具15次与轮次下降同时出现，但单次 rollout 不能证明因果；新工具失败3次，产生恢复/回退开销；含截断 observation 的 step 0→2 |
| `django__django-14500` | 1/0.1101/47/688.9 | 1/0.1385/68/409.1/22/2 | 0.0284 | 成本增加，最大正贡献=model-output；多21步导致历史重复计费；用了22次新工具但未替代 LLM 轮次；新工具失败2次，产生恢复/回退开销；含截断 observation 的 step 0→12 |
| `django__django-14792` | 0/0.1391/62/381.0 | 0/0.1206/50/334.5/15/0 | -0.0185 | 成本降低，最大负贡献=cached-context；少12步；新工具15次与轮次下降同时出现，但单次 rollout 不能证明因果；含截断 observation 的 step 1→5 |
| `django__django-14855` | 1/0.0547/52/295.3 | 1/0.0685/53/304.0/10/1 | 0.0139 | 成本增加，最大正贡献=model-output；多1步导致历史重复计费；用了10次新工具但未替代 LLM 轮次；新工具失败1次，产生恢复/回退开销；含截断 observation 的 step 0→1 |
| `django__django-15382` | 1/0.1332/91/215.4 | 无 trajectory | NA | 无有效 trajectory（NonZeroAgentExitCodeError），不能把缺失成本当节省 |
| `django__django-15569` | 1/0.1275/97/213.4 | 1/0.0784/66/243.2/19/2 | -0.0491 | 成本降低，最大负贡献=cached-context；少31步；新工具19次与轮次下降同时出现，但单次 rollout 不能证明因果；新工具失败2次，产生恢复/回退开销；含截断 observation 的 step 0→1 |
| `django__django-16032` | 1/0.3026/151/244.4 | 1/0.1958/92/286.6/39/2 | -0.1068 | 成本降低，最大负贡献=cached-context；少59步；新工具39次与轮次下降同时出现，但单次 rollout 不能证明因果；新工具失败2次，产生恢复/回退开销；含截断 observation 的 step 0→5 |
| `django__django-16145` | 1/0.0546/33/340.0 | 1/0.0586/42/367.8/3/2 | 0.0040 | 成本增加，最大正贡献=cached-context；多9步导致历史重复计费；用了3次新工具但未替代 LLM 轮次；新工具失败2次，产生恢复/回退开销；含截断 observation 的 step 0→1 |
| `django__django-16256` | 0/0.0693/56/308.5 | 0/0.0889/65/328.0/24/2 | 0.0196 | 成本增加，最大正贡献=model-output；多9步导致历史重复计费；用了24次新工具但未替代 LLM 轮次；新工具失败2次，产生恢复/回退开销；含截断 observation 的 step 0→2 |
| `django__django-16493` | 1/0.0361/31/307.4 | 1/0.0406/36/303.4/12/2 | 0.0045 | 成本增加，最大正贡献=cached-context；多5步导致历史重复计费；用了12次新工具但未替代 LLM 轮次；新工具失败2次，产生恢复/回退开销；含截断 observation 的 step 0→3 |
| `django__django-16569` | 1/0.0219/27/125.3 | 1/0.0177/18/187.6/4/1 | -0.0042 | 成本降低，最大负贡献=model-output；少9步；新工具4次与轮次下降同时出现，但单次 rollout 不能证明因果；新工具失败1次，产生恢复/回退开销 |
| `django__django-16642` | 1/0.0325/27/408.5 | 0/0.0431/31/406.1/15/1 | 0.0106 | 成本增加，最大正贡献=model-output；多4步导致历史重复计费；用了15次新工具但未替代 LLM 轮次；新工具失败1次，产生恢复/回退开销；含截断 observation 的 step 0→4；成功→失败 |
| `matplotlib__matplotlib-13989` | 1/0.0284/25/371.9 | 1/0.0466/41/297.0/13/0 | 0.0182 | 成本增加，最大正贡献=cached-context；多16步导致历史重复计费；用了13次新工具但未替代 LLM 轮次；含截断 observation 的 step 0→4 |
| `matplotlib__matplotlib-14623` | 1/0.2192/104/364.1 | 1/0.1171/70/305.1/27/3 | -0.1021 | 成本降低，最大负贡献=cached-context；少34步；平均 observation -16%；新工具27次与轮次下降同时出现，但单次 rollout 不能证明因果；新工具失败3次，产生恢复/回退开销；含截断 observation 的 step 0→1 |
| `matplotlib__matplotlib-23314` | 1/0.0634/61/235.9 | 1/0.0527/46/282.3/15/1 | -0.0107 | 成本降低，最大负贡献=model-output；少15步；新工具15次与轮次下降同时出现，但单次 rollout 不能证明因果；新工具失败1次，产生恢复/回退开销；含截断 observation 的 step 0→2 |
| `matplotlib__matplotlib-24570` | 1/0.0530/37/297.9 | 1/0.0555/31/280.0/10/1 | 0.0025 | 成本增加，最大正贡献=model-output；步数未增但每轮上下文/固定 harness 更重；新工具失败1次，产生恢复/回退开销；含截断 observation 的 step 0→3 |
| `mwaskom__seaborn-3069` | 1/0.2319/127/296.0 | 0/0.1747/102/287.7/40/2 | -0.0572 | 成本降低，最大负贡献=cached-context；少25步；但 performance 回退，属于无效节省；新工具失败2次，产生恢复/回退开销；含截断 observation 的 step 3→4；成功→失败 |
| `pydata__xarray-4094` | 1/0.0504/44/213.1 | 1/0.1108/57/332.7/19/0 | 0.0604 | 成本增加，最大正贡献=model-output；多13步导致历史重复计费；平均 observation +56%；用了19次新工具但未替代 LLM 轮次；含截断 observation 的 step 0→3 |
| `pylint-dev__pylint-6386` | 1/0.1070/56/526.6 | 0/0.1718/87/403.3/35/2 | 0.0648 | 成本增加，最大正贡献=cached-context；多31步导致历史重复计费；用了35次新工具但未替代 LLM 轮次；新工具失败2次，产生恢复/回退开销；含截断 observation 的 step 0→8；成功→失败 |
| `pytest-dev__pytest-10051` | 1/0.0575/51/305.0 | 0/0.0511/37/335.4/15/2 | -0.0064 | 成本降低，最大负贡献=new-input；少14步；但 performance 回退，属于无效节省；新工具失败2次，产生恢复/回退开销；含截断 observation 的 step 0→3；成功→失败 |
| `pytest-dev__pytest-5631` | 1/0.0351/30/267.5 | 1/0.0262/23/279.4/4/0 | -0.0090 | 成本降低，最大负贡献=model-output；少7步；新工具4次与轮次下降同时出现，但单次 rollout 不能证明因果；含截断 observation 的 step 0→2 |
| `pytest-dev__pytest-5840` | 0/0.2016/78/244.3 | 0/0.0609/42/446.8/16/1 | -0.1407 | 成本降低，最大负贡献=model-output；少36步；新工具16次与轮次下降同时出现，但单次 rollout 不能证明因果；新工具失败1次，产生恢复/回退开销；含截断 observation 的 step 0→5 |
| `scikit-learn__scikit-learn-10908` | 1/0.1005/107/140.8 | 1/0.1268/93/222.4/19/1 | 0.0264 | 成本增加，最大正贡献=model-output；步数未增但每轮上下文/固定 harness 更重；平均 observation +58%；新工具失败1次，产生恢复/回退开销；含截断 observation 的 step 0→3 |
| `scikit-learn__scikit-learn-12585` | 1/0.0284/25/209.7 | 1/0.0350/34/286.1/6/1 | 0.0066 | 成本增加，最大正贡献=new-input；多9步导致历史重复计费；平均 observation +36%；用了6次新工具但未替代 LLM 轮次；新工具失败1次，产生恢复/回退开销；含截断 observation 的 step 0→2 |
| `scikit-learn__scikit-learn-13124` | 1/0.1084/57/314.1 | 1/0.1164/61/416.6/24/1 | 0.0080 | 成本增加，最大正贡献=cached-context；多4步导致历史重复计费；平均 observation +33%；用了24次新工具但未替代 LLM 轮次；新工具失败1次，产生恢复/回退开销；含截断 observation 的 step 0→8 |
| `scikit-learn__scikit-learn-13328` | 1/0.1097/72/311.2 | 1/0.0876/76/259.5/21/2 | -0.0220 | 成本降低，最大负贡献=model-output；平均 observation -17%；新工具失败2次，产生恢复/回退开销；含截断 observation 的 step 0→5 |
| `scikit-learn__scikit-learn-14087` | 0/0.1111/67/279.8 | 1/0.1229/59/253.1/21/1 | 0.0118 | 成本增加，最大正贡献=model-output；步数未增但每轮上下文/固定 harness 更重；新工具失败1次，产生恢复/回退开销；含截断 observation 的 step 0→2；失败→成功 |
| `scikit-learn__scikit-learn-14710` | 0/0.2668/151/227.9 | 0/0.0829/69/205.5/19/1 | -0.1839 | 成本降低，最大负贡献=cached-context；少82步；新工具19次与轮次下降同时出现，但单次 rollout 不能证明因果；新工具失败1次，产生恢复/回退开销；含截断 observation 的 step 0→1；同时存在运行异常=VerifierTimeoutError；no-evolve 运行异常=VerifierTimeoutError |
| `scikit-learn__scikit-learn-14983` | 1/0.0753/63/227.1 | 1/0.1023/74/275.8/26/1 | 0.0270 | 成本增加，最大正贡献=cached-context；多11步导致历史重复计费；平均 observation +21%；用了26次新工具但未替代 LLM 轮次；新工具失败1次，产生恢复/回退开销；含截断 observation 的 step 0→1 |
| `scikit-learn__scikit-learn-15100` | 1/0.0197/24/135.0 | 1/0.0374/33/176.2/9/1 | 0.0177 | 成本增加，最大正贡献=model-output；多9步导致历史重复计费；平均 observation +31%；用了9次新工具但未替代 LLM 轮次；新工具失败1次，产生恢复/回退开销 |
| `scikit-learn__scikit-learn-9288` | 1/0.1641/87/438.4 | 1/0.4800/214/317.5/14/2 | 0.3160 | 成本增加，最大正贡献=cached-context；多127步导致历史重复计费；用了14次新工具但未替代 LLM 轮次；新工具失败2次，产生恢复/回退开销；含截断 observation 的 step 0→5 |
| `sphinx-doc__sphinx-7889` | 1/0.0389/28/445.6 | 1/0.0570/34/461.8/7/0 | 0.0180 | 成本增加，最大正贡献=model-output；多6步导致历史重复计费；用了7次新工具但未替代 LLM 轮次；含截断 observation 的 step 1→3 |
| `sphinx-doc__sphinx-7985` | 0/0.1442/74/439.0 | 0/0.0979/53/528.5/24/0 | -0.0463 | 成本降低，最大负贡献=model-output；少21步；新工具24次与轮次下降同时出现，但单次 rollout 不能证明因果；含截断 observation 的 step 0→9 |
| `sphinx-doc__sphinx-8265` | 1/0.0807/57/341.6 | 1/0.1050/60/407.8/28/1 | 0.0243 | 成本增加，最大正贡献=cached-context；多3步导致历史重复计费；平均 observation +19%；用了28次新工具但未替代 LLM 轮次；新工具失败1次，产生恢复/回退开销；含截断 observation 的 step 0→5 |
| `sphinx-doc__sphinx-9281` | 0/0.0532/46/218.3 | 1/0.0760/50/347.7/18/1 | 0.0229 | 成本增加，最大正贡献=cached-context；多4步导致历史重复计费；平均 observation +59%；用了18次新工具但未替代 LLM 轮次；新工具失败1次，产生恢复/回退开销；含截断 observation 的 step 0→5；失败→成功 |
| `sphinx-doc__sphinx-9658` | 1/0.1135/62/289.1 | 1/0.1077/42/485.7/18/0 | -0.0058 | 成本降低，最大负贡献=cached-context；少20步；新工具18次与轮次下降同时出现，但单次 rollout 不能证明因果；含截断 observation 的 step 0→7 |
| `sympy__sympy-11618` | 1/0.0477/54/144.2 | 1/0.0506/46/246.6/14/1 | 0.0029 | 成本增加，最大正贡献=new-input；步数未增但每轮上下文/固定 harness 更重；平均 observation +71%；新工具失败1次，产生恢复/回退开销；含截断 observation 的 step 0→2 |
| `sympy__sympy-13798` | 0/0.0575/51/140.2 | 0/0.0551/43/197.4/11/2 | -0.0023 | 成本降低，最大负贡献=model-output；少8步；新工具11次与轮次下降同时出现，但单次 rollout 不能证明因果；新工具失败2次，产生恢复/回退开销 |
| `sympy__sympy-15017` | 0/0.0570/55/210.7 | 1/0.1012/71/274.2/27/0 | 0.0443 | 成本增加，最大正贡献=cached-context；多16步导致历史重复计费；平均 observation +30%；用了27次新工具但未替代 LLM 轮次；含截断 observation 的 step 0→1；失败→成功 |
| `sympy__sympy-15345` | 1/0.0727/55/251.1 | 1/0.1002/69/230.6/21/2 | 0.0275 | 成本增加，最大正贡献=model-output；多14步导致历史重复计费；用了21次新工具但未替代 LLM 轮次；新工具失败2次，产生恢复/回退开销；含截断 observation 的 step 0→6 |
| `sympy__sympy-15349` | 1/0.0328/30/155.7 | 1/0.0203/17/116.9/4/0 | -0.0125 | 成本降低，最大负贡献=model-output；少13步；平均 observation -25%；新工具4次与轮次下降同时出现，但单次 rollout 不能证明因果 |
| `sympy__sympy-16886` | 1/0.0154/16/389.8 | 1/0.0157/19/227.3/3/1 | 0.0003 | 成本增加，最大正贡献=model-output；多3步导致历史重复计费；用了3次新工具但未替代 LLM 轮次；新工具失败1次，产生恢复/回退开销；含截断 observation 的 step 0→1 |
| `sympy__sympy-19637` | 1/0.0221/27/201.9 | 1/0.0218/21/221.6/6/1 | -0.0003 | 成本降低，最大负贡献=new-input；少6步；新工具6次与轮次下降同时出现，但单次 rollout 不能证明因果；新工具失败1次，产生恢复/回退开销；含截断 observation 的 step 0→2 |
| `sympy__sympy-23950` | 0/0.0720/61/252.4 | 1/0.0678/45/426.1/20/3 | -0.0041 | 成本降低，最大负贡献=cached-context；少16步；新工具20次与轮次下降同时出现，但单次 rollout 不能证明因果；新工具失败3次，产生恢复/回退开销；含截断 observation 的 step 0→2；失败→成功 |
| `sympy__sympy-24661` | 1/0.0807/66/170.4 | 1/0.0493/37/297.1/9/1 | -0.0314 | 成本降低，最大负贡献=model-output；少29步；新工具9次与轮次下降同时出现，但单次 rollout 不能证明因果；新工具失败1次，产生恢复/回退开销；含截断 observation 的 step 0→2 |

### 10.2 DeepSWE 64 case

| Case | no-evolve: success/cost/steps/obs-step | v9: success/cost/steps/obs-step/native/fail | Δcost | 原因 |
|---|---:|---:|---:|---|
| `abs-module-cache-flags` | 0/0.2756/109/317.6 | 0/0.3040/149/234.8/23/0 | 0.0284 | 成本增加，最大正贡献=cached-context；多40步导致历史重复计费；用了23次新工具但未替代 LLM 轮次；含截断 observation 的 step 0→4 |
| `adaptix-name-mapping-aliases` | 0/0.6386/170/471.2 | 0/0.3619/129/451.4/56/0 | -0.2767 | 成本降低，最大负贡献=cached-context；少41步；新工具56次与轮次下降同时出现，但单次 rollout 不能证明因果；含截断 observation 的 step 0→5 |
| `arcane-drift-detection-baselines` | 0/0.2516/103/453.3 | 1/0.2984/150/310.1/49/0 | 0.0468 | 成本增加，最大正贡献=cached-context；多47步导致历史重复计费；用了49次新工具但未替代 LLM 轮次；含截断 observation 的 step 0→9；失败→成功 |
| `arktype-json-schema-refs-dependencies` | 0/0.3196/89/767.9 | 0/0.5923/184/371.6/25/1 | 0.2727 | 成本增加，最大正贡献=cached-context；多95步导致历史重复计费；用了25次新工具但未替代 LLM 轮次；新工具失败1次，产生恢复/回退开销；含截断 observation 的 step 0→6 |
| `bandit-incremental-cache-control` | 0/0.1910/83/404.9 | 0/0.2197/90/368.4/30/0 | 0.0287 | 成本增加，最大正贡献=cached-context；多7步导致历史重复计费；用了30次新工具但未替代 LLM 轮次；含截断 observation 的 step 0→3 |
| `bandit-interprocedural-taint-checks` | 0/0.1753/63/658.2 | 0/0.4044/140/457.1/29/1 | 0.2291 | 成本增加，最大正贡献=cached-context；多77步导致历史重复计费；用了29次新工具但未替代 LLM 轮次；新工具失败1次，产生恢复/回退开销；含截断 observation 的 step 0→7 |
| `boa-hierarchical-evaluation-cancellation` | 0/0.4034/149/371.4 | 0/0.5710/192/344.7/67/1 | 0.1676 | 成本增加，最大正贡献=cached-context；多43步导致历史重复计费；用了67次新工具但未替代 LLM 轮次；新工具失败1次，产生恢复/回退开销；含截断 observation 的 step 0→3 |
| `cattrs-partial-structuring-recovery` | 0/0.1950/83/522.3 | 0/0.3841/148/322.6/39/0 | 0.1892 | 成本增加，最大正贡献=cached-context；多65步导致历史重复计费；用了39次新工具但未替代 LLM 轮次；含截断 observation 的 step 0→1 |
| `clack-async-autocomplete-options` | 0/0.3668/95/524.4 | 0/0.2155/53/633.2/18/0 | -0.1513 | 成本降低，最大负贡献=cached-context；少42步；新工具18次与轮次下降同时出现，但单次 rollout 不能证明因果；含截断 observation 的 step 0→2 |
| `claude-code-by-agents-recursive-delegation` | 0/0.1730/51/1126.6 | 0/0.1811/49/993.5/30/0 | 0.0081 | 成本增加，最大正贡献=model-output；步数未增但每轮上下文/固定 harness 更重；含截断 observation 的 step 0→4 |
| `dateutil-rfc5545-timezone-interop` | 0/0.3392/120/355.5 | 0/0.2863/110/387.9/35/0 | -0.0528 | 成本降低，最大负贡献=cached-context；少10步；新工具35次与轮次下降同时出现，但单次 rollout 不能证明因果 |
| `drizzle-orm-window-function-builders` | 0/0.6421/246/250.8 | 0/0.3261/143/287.5/56/1 | -0.3160 | 成本降低，最大负贡献=cached-context；少103步；新工具56次与轮次下降同时出现，但单次 rollout 不能证明因果；新工具失败1次，产生恢复/回退开销；含截断 observation 的 step 0→1 |
| `dynamodb-toolbox-lazy-recursive-schemas` | 0/0.3366/132/506.7 | 0/0.6632/187/299.8/62/0 | 0.3266 | 成本增加，最大正贡献=cached-context；多55步导致历史重复计费；用了62次新工具但未替代 LLM 轮次；含截断 observation 的 step 0→7 |
| `eicrud-keyset-pagination-cursor` | 1/0.3155/111/503.8 | 0/0.2453/113/335.6/36/1 | -0.0702 | 成本降低，最大负贡献=cached-context；平均 observation -33%；但 performance 回退，属于无效节省；新工具失败1次，产生恢复/回退开销；含截断 observation 的 step 0→1；成功→失败 |
| `etree-xml-diff-patch` | 0/0.2480/103/220.6 | 0/0.2161/105/227.3/35/0 | -0.0319 | 成本降低，最大负贡献=model-output |
| `expr-try-catch-errors` | 0/0.7352/205/247.8 | 0/0.7466/257/223.4/60/0 | 0.0114 | 成本增加，最大正贡献=cached-context；多52步导致历史重复计费；用了60次新工具但未替代 LLM 轮次；含截断 observation 的 step 0→7 |
| `fastapi-deprecation-response-headers` | 0/0.3809/164/357.7 | 0/0.3578/181/291.7/62/0 | -0.0231 | 成本降低，最大负贡献=model-output；平均 observation -18% |
| `fastapi-implicit-head-options` | 0/0.7140/221/342.5 | 0/0.8838/236/324.2/60/1 | 0.1698 | 成本增加，最大正贡献=model-output；多15步导致历史重复计费；用了60次新工具但未替代 LLM 轮次；新工具失败1次，产生恢复/回退开销 |
| `geo-shapeindex-serialization` | 0/0.2438/88/541.1 | 0/0.1714/83/337.0/48/1 | -0.0724 | 成本降低，最大负贡献=cached-context；少5步；平均 observation -38%；新工具48次与轮次下降同时出现，但单次 rollout 不能证明因果；新工具失败1次，产生恢复/回退开销；含截断 observation 的 step 0→4 |
| `go-genai-streamed-function-args` | 0/0.5640/205/338.3 | 0/0.3911/176/311.3/61/0 | -0.1729 | 成本降低，最大负贡献=cached-context；少29步；新工具61次与轮次下降同时出现，但单次 rollout 不能证明因果；含截断 observation 的 step 0→4 |
| `go-git-worktree-merge-conflicts` | 0/0.4025/113/588.3 | 0/0.4475/114/428.8/48/1 | 0.0450 | 成本增加，最大正贡献=model-output；多1步导致历史重复计费；用了48次新工具但未替代 LLM 轮次；新工具失败1次，产生恢复/回退开销；含截断 observation 的 step 0→4 |
| `gql-incremental-graphql-delivery` | 0/0.2819/106/537.2 | 0/0.3889/178/307.7/55/0 | 0.1070 | 成本增加，最大正贡献=cached-context；多72步导致历史重复计费；用了55次新工具但未替代 LLM 轮次；含截断 observation 的 step 0→7 |
| `happy-dom-abort-pending-body-reads` | 0/0.3353/102/638.5 | 0/0.4148/96/567.0/50/0 | 0.0795 | 成本增加，最大正贡献=model-output；步数未增但每轮上下文/固定 harness 更重；含截断 observation 的 step 0→5 |
| `happy-dom-deterministic-intersectionobserver` | 0/0.2112/57/375.2 | 0/0.1326/62/419.2/19/0 | -0.0786 | 成本降低，最大负贡献=model-output；含截断 observation 的 step 0→3 |
| `httpx-deterministic-cookie-store` | 0/0.2717/133/269.6 | 0/0.2339/104/250.2/34/0 | -0.0378 | 成本降低，最大负贡献=cached-context；少29步；新工具34次与轮次下降同时出现，但单次 rollout 不能证明因果；含截断 observation 的 step 1→4 |
| `igel-persist-feature-schema` | 0/0.2915/82/815.5 | 0/0.2986/118/505.1/17/0 | 0.0071 | 成本增加，最大正贡献=cached-context；多36步导致历史重复计费；用了17次新工具但未替代 LLM 轮次；含截断 observation 的 step 0→2 |
| `ipython-session-bundle-replay` | 0/0.1666/66/419.0 | 0/0.1716/80/366.7/30/0 | 0.0050 | 成本增加，最大正贡献=cached-context；多14步导致历史重复计费；用了30次新工具但未替代 LLM 轮次；含截断 observation 的 step 0→3 |
| `katex-multicolumn-array-spans` | 0/0.3851/126/576.7 | 0/0.2375/105/416.4/34/1 | -0.1476 | 成本降低，最大负贡献=cached-context；少21步；平均 observation -28%；新工具34次与轮次下降同时出现，但单次 rollout 不能证明因果；新工具失败1次，产生恢复/回退开销；含截断 observation 的 step 0→1 |
| `kcp-go-multiplexed-kcp-streams` | 0/0.3465/75/259.7 | 0/0.3380/112/220.3/10/0 | -0.0085 | 成本降低，最大负贡献=model-output；平均 observation -15%；含截断 observation 的 step 0→1 |
| `kgateway-consistent-hash-policy` | 0/0.3427/140/382.2 | 0/0.3522/149/351.3/64/0 | 0.0095 | 成本增加，最大正贡献=cached-context；多9步导致历史重复计费；用了64次新工具但未替代 LLM 轮次；含截断 observation 的 step 0→4 |
| `kombu-single-active-consumer-priority` | 0/0.3524/117/486.9 | 0/0.1727/82/507.7/41/0 | -0.1796 | 成本降低，最大负贡献=cached-context；少35步；新工具41次与轮次下降同时出现，但单次 rollout 不能证明因果；含截断 observation 的 step 0→2 |
| `kombu-virtual-queue-dead-lettering` | 0/0.2543/88/546.7 | 0/0.3860/106/383.6/22/0 | 0.1317 | 成本增加，最大正贡献=model-output；多18步导致历史重复计费；用了22次新工具但未替代 LLM 轮次 |
| `koota-composite-trait-aspects` | 0/0.3965/100/714.2 | 0/0.1854/64/597.8/34/0 | -0.2110 | 成本降低，最大负贡献=cached-context；少36步；平均 observation -16%；新工具34次与轮次下降同时出现，但单次 rollout 不能证明因果；含截断 observation 的 step 0→5 |
| `koota-entity-snapshot-rollback` | 0/0.3138/79/698.6 | 0/0.2358/81/553.7/35/0 | -0.0781 | 成本降低，最大负贡献=model-output；平均 observation -21%；含截断 observation 的 step 0→4 |
| `langchain-request-coalescing` | 0/0.5057/149/295.9 | 0/0.2542/122/234.8/33/1 | -0.2515 | 成本降低，最大负贡献=cached-context；少27步；平均 observation -21%；新工具33次与轮次下降同时出现，但单次 rollout 不能证明因果；新工具失败1次，产生恢复/回退开销；同时存在运行异常=VerifierTimeoutError |
| `mobly-grouped-test-barriers` | 0/0.2902/87/650.8 | 0/0.2289/85/479.5/33/0 | -0.0613 | 成本降低，最大负贡献=cached-context；少2步；平均 observation -26%；新工具33次与轮次下降同时出现，但单次 rollout 不能证明因果；含截断 observation 的 step 0→4；同时存在运行异常=VerifierTimeoutError |
| `numba-stencil-boundary-modes` | 0/0.2469/80/418.8 | 0/0.3166/105/389.4/31/1 | 0.0697 | 成本增加，最大正贡献=cached-context；多25步导致历史重复计费；用了31次新工具但未替代 LLM 轮次；新工具失败1次，产生恢复/回退开销；含截断 observation 的 step 0→1 |
| `obsidian-linter-auto-table-of-contents` | 0/0.3236/97/450.2 | 0/0.3316/122/377.0/33/0 | 0.0080 | 成本增加，最大正贡献=cached-context；多25步导致历史重复计费；用了33次新工具但未替代 LLM 轮次；含截断 observation 的 step 0→4 |
| `obsidian-linter-link-format-conversion` | 1/0.1773/63/712.7 | 0/0.3589/116/451.2/31/0 | 0.1815 | 成本增加，最大正贡献=cached-context；多53步导致历史重复计费；用了31次新工具但未替代 LLM 轮次；含截断 observation 的 step 0→6；成功→失败 |
| `obsidian-linter-scoped-ignore-markers` | 0/0.5192/127/464.9 | 0/0.8443/172/396.3/37/0 | 0.3251 | 成本增加，最大正贡献=cached-context；多45步导致历史重复计费；用了37次新工具但未替代 LLM 轮次；含截断 observation 的 step 0→6 |
| `opa-template-string-reconstruction` | 0/0.4345/162/299.5 | 0/0.4226/133/383.8/65/4 | -0.0119 | 成本降低，最大负贡献=model-output；少29步；新工具65次与轮次下降同时出现，但单次 rollout 不能证明因果；新工具失败4次，产生恢复/回退开销 |
| `optique-conditional-option-dependencies` | 0/0.9342/232/433.7 | 0/0.6381/210/336.5/61/1 | -0.2960 | 成本降低，最大负贡献=cached-context；少22步；平均 observation -22%；新工具61次与轮次下降同时出现，但单次 rollout 不能证明因果；新工具失败1次，产生恢复/回退开销；含截断 observation 的 step 0→3 |
| `oxvg-structural-selector-preservation` | 0/0.6150/147/592.1 | 0/0.4968/153/514.4/48/0 | -0.1182 | 成本降低，最大负贡献=cached-context；含截断 observation 的 step 0→9 |
| `pebble-durability-wait-apis` | 0/0.5714/236/279.8 | 0/0.5886/263/260.1/45/2 | 0.0172 | 成本增加，最大正贡献=cached-context；多27步导致历史重复计费；用了45次新工具但未替代 LLM 轮次；新工具失败2次，产生恢复/回退开销 |
| `prometheus-typed-label-sorting` | 0/0.2008/79/445.3 | 0/0.2621/98/274.0/19/1 | 0.0612 | 成本增加，最大正贡献=cached-context；多19步导致历史重复计费；用了19次新工具但未替代 LLM 轮次；新工具失败1次，产生恢复/回退开销；含截断 observation 的 step 0→1 |
| `psd-tools-blend-range-api` | 0/0.1647/66/524.0 | 0/0.2275/104/286.5/31/0 | 0.0627 | 成本增加，最大正贡献=cached-context；多38步导致历史重复计费；用了31次新工具但未替代 LLM 轮次；含截断 observation 的 step 0→4 |
| `pwntools-tube-multiplexing` | 0/0.2240/51/367.4 | 0/0.2423/70/296.2/27/0 | 0.0183 | 成本增加，最大正贡献=cached-context；多19步导致历史重复计费；用了27次新工具但未替代 LLM 轮次；含截断 observation 的 step 0→3；no-evolve 运行异常=VerifierTimeoutError |
| `python-statemachine-state-data-scoping` | 0/0.3317/129/515.7 | 0/0.4108/147/388.3/39/0 | 0.0791 | 成本增加，最大正贡献=model-output；多18步导致历史重复计费；用了39次新工具但未替代 LLM 轮次；含截断 observation 的 step 0→9 |
| `returns-validated-error-accumulation` | 0/0.2448/98/440.1 | 0/0.2020/75/423.2/35/0 | -0.0427 | 成本降低，最大负贡献=cached-context；少23步；新工具35次与轮次下降同时出现，但单次 rollout 不能证明因果；含截断 observation 的 step 0→5 |
| `scc-bounded-memory-spilling` | 0/0.3679/122/399.8 | 0/0.2497/111/363.8/33/0 | -0.1182 | 成本降低，最大负贡献=cached-context；少11步；新工具33次与轮次下降同时出现，但单次 rollout 不能证明因果；含截断 observation 的 step 0→5 |
| `sql-formatter-bigquery-pipe-formatting` | 0/0.5889/156/484.9 | 0/0.4307/168/317.8/49/0 | -0.1582 | 成本降低，最大负贡献=cached-context；平均 observation -34%；含截断 observation 的 step 0→7 |
| `sqlfmt-create-table-ddl-formatting` | 0/0.4590/120/487.1 | 0/0.3836/127/388.1/35/0 | -0.0754 | 成本降低，最大负贡献=cached-context；平均 observation -20%；含截断 observation 的 step 0→8 |
| `sqlite-utils-safe-import-checkpoints` | 0/0.4292/155/280.9 | 0/0.5898/204/222.3/54/0 | 0.1606 | 成本增加，最大正贡献=cached-context；多49步导致历史重复计费；用了54次新工具但未替代 LLM 轮次 |
| `task-task-graph-export` | 0/0.4274/171/393.3 | 0/0.3023/114/595.5/17/0 | -0.1252 | 成本降低，最大负贡献=cached-context；少57步；新工具17次与轮次下降同时出现，但单次 rollout 不能证明因果 |
| `tengo-destructuring-bindings` | 0/0.4827/152/330.9 | 0/0.7233/187/258.6/75/0 | 0.2406 | 成本增加，最大正贡献=cached-context；多35步导致历史重复计费；用了75次新工具但未替代 LLM 轮次；含截断 observation 的 step 0→3 |
| `termenv-preserve-ansi-resets` | 0/0.2303/62/694.3 | 0/0.2483/79/443.6/15/1 | 0.0180 | 成本增加，最大正贡献=cached-context；多17步导致历史重复计费；用了15次新工具但未替代 LLM 轮次；新工具失败1次，产生恢复/回退开销 |
| `testem-bail-on-test-failure` | 0/0.2345/68/993.9 | 0/0.2835/92/676.5/61/0 | 0.0490 | 成本增加，最大正贡献=cached-context；多24步导致历史重复计费；用了61次新工具但未替代 LLM 轮次；含截断 observation 的 step 0→4 |
| `textual-kitty-key-phases` | 0/0.2274/75/547.4 | 0/0.2402/109/265.1/28/0 | 0.0128 | 成本增加，最大正贡献=cached-context；多34步导致历史重复计费；用了28次新工具但未替代 LLM 轮次；含截断 observation 的 step 0→2 |
| `textual-richlog-follow-state` | 0/0.2873/100/434.0 | 0/0.2495/104/380.8/36/0 | -0.0378 | 成本降低，最大负贡献=model-output；含截断 observation 的 step 0→3 |
| `tomlkit-toml-table-converters` | 0/0.3577/104/426.1 | 0/0.3442/91/422.6/42/0 | -0.0135 | 成本降低，最大负贡献=cached-context；少13步；新工具42次与轮次下降同时出现，但单次 rollout 不能证明因果；含截断 observation 的 step 0→2 |
| `true-myth-iterable-collection-combinators` | 0/0.2176/64/673.9 | 0/0.6057/181/377.5/54/0 | 0.3882 | 成本增加，最大正贡献=cached-context；多117步导致历史重复计费；用了54次新工具但未替代 LLM 轮次；含截断 observation 的 step 0→2 |
| `updo-policy-alerting` | 0/0.1594/60/570.1 | 0/0.1475/62/642.0/30/0 | -0.0119 | 成本降低，最大负贡献=model-output；含截断 observation 的 step 0→4 |
| `yaegi-go-embed-directives` | 0/0.8036/236/297.0 | 0/0.4859/171/280.3/64/1 | -0.3178 | 成本降低，最大负贡献=cached-context；少65步；新工具64次与轮次下降同时出现，但单次 rollout 不能证明因果；新工具失败1次，产生恢复/回退开销；含截断 observation 的 step 0→4 |
| `ytt-jsonpath-query-api` | 0/0.3680/140/205.2 | 1/0.2225/53/334.6/12/0 | -0.1455 | 成本降低，最大负贡献=cached-context；少87步；新工具12次与轮次下降同时出现，但单次 rollout 不能证明因果；失败→成功 |

### 10.3 DAB 64 case

| Case | no-evolve: success/cost/steps/obs-step | v9: success/cost/steps/obs-step/native/fail | Δcost | 原因 |
|---|---:|---:|---:|---|
| `dab__agnews__query1` | 1/0.0909/48/466.9 | 1/0.0332/25/322.8/0/0 | -0.0577 | 成本降低，最大负贡献=model-output；少23步；平均 observation -31%；主要来自 rollout 随机性/路径变短，非工具采用 |
| `dab__agnews__query2` | 1/0.2021/66/549.8 | 无 trajectory | NA | 无有效 trajectory（NonZeroAgentExitCodeError），不能把缺失成本当节省 |
| `dab__agnews__query4` | 1/0.1551/80/234.8 | 无 trajectory | NA | 无有效 trajectory（NonZeroAgentExitCodeError），不能把缺失成本当节省 |
| `dab__civic_unstructured__query10` | 0/0.3636/88/413.5 | 0/0.2736/57/973.2/0/0 | -0.0900 | 成本降低，最大负贡献=model-output；少31步；主要来自 rollout 随机性/路径变短，非工具采用 |
| `dab__civic_unstructured__query1` | 1/0.1957/48/1004.8 | 无 trajectory | NA | 无有效 trajectory（NonZeroAgentExitCodeError），不能把缺失成本当节省 |
| `dab__civic_unstructured__query2` | 1/0.1280/34/1066.4 | 无 trajectory | NA | 无有效 trajectory（RuntimeError），不能把缺失成本当节省 |
| `dab__civic_unstructured__query5` | 1/0.4069/72/989.1 | 无 trajectory | NA | 无有效 trajectory（NonZeroAgentExitCodeError），不能把缺失成本当节省 |
| `dab__civic_unstructured__query8` | 1/0.1513/48/638.0 | 无 trajectory | NA | 无有效 trajectory（NonZeroAgentExitCodeError），不能把缺失成本当节省 |
| `dab__civic_unstructured__query9` | 1/0.2000/53/651.1 | 1/0.2440/66/532.3/0/0 | 0.0440 | 成本增加，最大正贡献=cached-context；多13步导致历史重复计费；新工具零采用，差异来自固定 instruction、rollout 路径或运行噪声 |
| `dab__crmarenapro__query10` | 1/0.1103/35/594.6 | 1/0.0581/26/491.5/0/0 | -0.0522 | 成本降低，最大负贡献=model-output；少9步；平均 observation -17%；主要来自 rollout 随机性/路径变短，非工具采用 |
| `dab__crmarenapro__query13` | 1/0.0447/19/839.1 | 1/0.0958/25/1109.5/0/0 | 0.0510 | 成本增加，最大正贡献=new-input；多6步导致历史重复计费；平均 observation +32%；新工具零采用，差异来自固定 instruction、rollout 路径或运行噪声 |
| `dab__crmarenapro__query1` | 1/0.0169/10/867.5 | 无 trajectory | NA | 无有效 trajectory（NonZeroAgentExitCodeError），不能把缺失成本当节省 |
| `dab__crmarenapro__query2` | 1/0.1018/28/1222.4 | 0/0.0611/31/716.8/0/0 | -0.0407 | 成本降低，最大负贡献=model-output；平均 observation -41%；但 performance 回退，属于无效节省；成功→失败 |
| `dab__crmarenapro__query5` | 1/0.0467/19/703.8 | 1/0.0673/31/471.0/0/0 | 0.0206 | 成本增加，最大正贡献=model-output；多12步导致历史重复计费；新工具零采用，差异来自固定 instruction、rollout 路径或运行噪声 |
| `dab__crmarenapro__query7` | 1/0.1778/59/973.4 | 无 trajectory | NA | 无有效 trajectory（NonZeroAgentExitCodeError），不能把缺失成本当节省 |
| `dab__crmarenapro__query8` | 1/0.1070/24/1105.6 | 1/0.0793/22/931.9/0/0 | -0.0278 | 成本降低，最大负贡献=model-output；少2步；平均 observation -16%；主要来自 rollout 随机性/路径变短，非工具采用 |
| `dab__crmarenapro__query9` | 1/0.0733/23/479.2 | 1/0.0567/21/597.4/0/0 | -0.0165 | 成本降低，最大负贡献=model-output；少2步；主要来自 rollout 随机性/路径变短，非工具采用 |
| `dab__cve__query10` | 1/0.6887/144/401.0 | 无 trajectory | NA | 无有效 trajectory（NonZeroAgentExitCodeError），不能把缺失成本当节省 |
| `dab__cve__query2` | 1/0.4010/87/553.0 | 0/0.1017/31/604.5/0/0 | -0.2993 | 成本降低，最大负贡献=cached-context；少56步；但 performance 回退，属于无效节省；同时存在运行异常=NonZeroAgentExitCodeError；成功→失败 |
| `dab__cve__query4` | 1/0.4251/104/375.6 | 0/0.0861/27/834.2/0/0 | -0.3390 | 成本降低，最大负贡献=cached-context；少77步；但 performance 回退，属于无效节省；同时存在运行异常=NonZeroAgentExitCodeError；成功→失败 |
| `dab__cve__query5` | 1/0.3173/83/636.2 | 无 trajectory | NA | 无有效 trajectory（RuntimeError），不能把缺失成本当节省 |
| `dab__cve__query9` | 1/0.5065/104/466.8 | 0/0.1352/42/618.4/0/0 | -0.3713 | 成本降低，最大负贡献=cached-context；少62步；但 performance 回退，属于无效节省；同时存在运行异常=NonZeroAgentExitCodeError；成功→失败 |
| `dab__deps_dev_v1__query1` | 1/0.2354/74/318.4 | 0/0.0619/39/258.9/0/0 | -0.1734 | 成本降低，最大负贡献=model-output；少35步；平均 observation -19%；但 performance 回退，属于无效节省；同时存在运行异常=NonZeroAgentExitCodeError；成功→失败 |
| `dab__deps_dev_v1__query2` | 1/0.0714/30/414.9 | 无 trajectory | NA | 无有效 trajectory（NonZeroAgentExitCodeError），不能把缺失成本当节省 |
| `dab__github_repos__query1` | 0/0.0896/46/404.8 | 无 trajectory | NA | 无有效 trajectory（NonZeroAgentExitCodeError），不能把缺失成本当节省 |
| `dab__github_repos__query2` | 0/0.0448/26/558.7 | 0/0.0389/23/439.0/0/0 | -0.0059 | 成本降低，最大负贡献=new-input；少3步；平均 observation -21%；主要来自 rollout 随机性/路径变短，非工具采用；同时存在运行异常=NonZeroAgentExitCodeError |
| `dab__github_repos__query3` | 1/0.0437/33/295.1 | 1/0.0318/31/188.4/0/0 | -0.0118 | 成本降低，最大负贡献=new-input；少2步；平均 observation -36%；主要来自 rollout 随机性/路径变短，非工具采用 |
| `dab__googlelocal__query1` | 1/0.0121/11/329.8 | 无 trajectory | NA | 无有效 trajectory（NonZeroAgentExitCodeError），不能把缺失成本当节省 |
| `dab__googlelocal__query2` | 0/0.0145/16/199.9 | 0/0.0175/19/231.9/0/0 | 0.0029 | 成本增加，最大正贡献=model-output；多3步导致历史重复计费；平均 observation +16%；新工具零采用，差异来自固定 instruction、rollout 路径或运行噪声 |
| `dab__googlelocal__query3` | 1/0.0491/23/457.4 | 无 trajectory | NA | 无有效 trajectory（NonZeroAgentExitCodeError），不能把缺失成本当节省 |
| `dab__googlelocal__query4` | 1/0.0262/23/276.6 | 无 trajectory | NA | 无有效 trajectory（NonZeroAgentExitCodeError），不能把缺失成本当节省 |
| `dab__imdb__query1` | 1/0.2254/100/363.3 | 0/0.0058/5/53.5/0/0 | -0.2196 | 成本降低，最大负贡献=cached-context；少95步；平均 observation -85%；但 performance 回退，属于无效节省；同时存在运行异常=NonZeroAgentExitCodeError；成功→失败 |
| `dab__imdb__query2` | 1/0.1657/60/394.2 | 无 trajectory | NA | 无有效 trajectory（RuntimeError），不能把缺失成本当节省 |
| `dab__imdb__query6` | 1/0.3682/101/482.2 | 0/0.0000/0/0/0/0 | -0.3682 | 成本降低，最大负贡献=cached-context；少101步；平均 observation -100%；但 performance 回退，属于无效节省；同时存在运行异常=NonZeroAgentExitCodeError；成功→失败 |
| `dab__imdb__query7` | 1/0.2047/85/194.6 | 0/0.0049/3/40.2/0/0 | -0.1998 | 成本降低，最大负贡献=model-output；少82步；平均 observation -79%；但 performance 回退，属于无效节省；同时存在运行异常=NonZeroAgentExitCodeError；成功→失败 |
| `dab__krama__query10` | 1/0.2117/37/1523.0 | 0/0.1928/40/1055.3/0/0 | -0.0189 | 成本降低，最大负贡献=new-input；平均 observation -31%；但 performance 回退，属于无效节省；成功→失败 |
| `dab__krama__query1` | 1/0.2258/59/784.5 | 无 trajectory | NA | 无有效 trajectory（NonZeroAgentExitCodeError），不能把缺失成本当节省 |
| `dab__krama__query2` | 0/0.2351/62/814.5 | 无 trajectory | NA | 无有效 trajectory（RuntimeError），不能把缺失成本当节省 |
| `dab__krama__query3` | 0/0.3834/71/750.3 | 0/0.1032/24/1507.5/0/0 | -0.2802 | 成本降低，最大负贡献=model-output；少47步；主要来自 rollout 随机性/路径变短，非工具采用；同时存在运行异常=NonZeroAgentExitCodeError |
| `dab__krama__query4` | 1/0.2655/56/1211.1 | 无 trajectory | NA | 无有效 trajectory（NonZeroAgentExitCodeError），不能把缺失成本当节省 |
| `dab__krama__query5` | 1/0.3435/98/507.3 | 0/0.0512/13/1884.9/0/0 | -0.2923 | 成本降低，最大负贡献=cached-context；少85步；但 performance 回退，属于无效节省；同时存在运行异常=NonZeroAgentExitCodeError；成功→失败 |
| `dab__krama__query6` | 0/0.2575/56/840.3 | 无 trajectory | NA | 无有效 trajectory（NonZeroAgentExitCodeError），不能把缺失成本当节省 |
| `dab__krama__query7` | 1/0.3083/59/975.0 | 0/0.1028/35/774.8/0/0 | -0.2055 | 成本降低，最大负贡献=cached-context；少24步；平均 observation -21%；但 performance 回退，属于无效节省；同时存在运行异常=NonZeroAgentExitCodeError；成功→失败 |
| `dab__music_brainz_20k__query1` | 1/0.0092/10/140.1 | 1/0.0108/14/125.2/0/0 | 0.0016 | 成本增加，最大正贡献=new-input；多4步导致历史重复计费；新工具零采用，差异来自固定 instruction、rollout 路径或运行噪声 |
| `dab__music_brainz_20k__query2` | 1/0.0052/10/83.8 | 无 trajectory | NA | 无有效 trajectory（NonZeroAgentExitCodeError），不能把缺失成本当节省 |
| `dab__music_brainz_20k__query3` | 1/0.0245/23/294.8 | 0/0.0125/15/143.8/0/0 | -0.0120 | 成本降低，最大负贡献=new-input；少8步；平均 observation -51%；但 performance 回退，属于无效节省；成功→失败 |
| `dab__pancancer_atlas__query2` | 1/0.0805/44/437.0 | 1/0.0402/24/458.4/0/0 | -0.0403 | 成本降低，最大负贡献=model-output；少20步；主要来自 rollout 随机性/路径变短，非工具采用 |
| `dab__patents__query1` | 1/0.3918/99/292.0 | 无 trajectory | NA | 无有效 trajectory（NonZeroAgentExitCodeError），不能把缺失成本当节省 |
| `dab__patents__query2` | 1/0.2264/57/474.8 | 0/0.0000/0/0/0/0 | -0.2264 | 成本降低，最大负贡献=model-output；少57步；平均 observation -100%；但 performance 回退，属于无效节省；同时存在运行异常=NonZeroAgentExitCodeError；成功→失败 |
| `dab__stockindex__query2` | 1/0.0315/21/281.2 | 1/0.0220/18/258.3/0/0 | -0.0096 | 成本降低，最大负贡献=model-output；少3步；主要来自 rollout 随机性/路径变短，非工具采用 |
| `dab__stockmarket__query1` | 1/0.0139/10/397.5 | 1/0.0141/9/472.2/0/0 | 0.0002 | 成本增加，最大正贡献=model-output；平均 observation +19%；新工具零采用，差异来自固定 instruction、rollout 路径或运行噪声 |
| `dab__stockmarket__query3` | 1/0.0716/28/689.1 | 无 trajectory | NA | 无有效 trajectory（NonZeroAgentExitCodeError），不能把缺失成本当节省 |
| `dab__stockmarket__query4` | 1/0.0557/29/344.9 | 1/0.0596/33/350.1/0/0 | 0.0039 | 成本增加，最大正贡献=model-output；多4步导致历史重复计费；新工具零采用，差异来自固定 instruction、rollout 路径或运行噪声 |
| `dab__stockmarket__query5` | 1/0.0527/22/766.0 | 1/0.0376/24/365.6/0/0 | -0.0151 | 成本降低，最大负贡献=new-input；平均 observation -52%；主要来自 rollout 随机性/路径变短，非工具采用 |
| `dab__usaspending__query1` | 0/0.2796/50/868.5 | 0/0.2465/38/1030.5/0/0 | -0.0332 | 成本降低，最大负贡献=model-output；少12步；主要来自 rollout 随机性/路径变短，非工具采用 |
| `dab__usaspending__query2` | 0/0.1414/38/965.2 | 0/0.0572/21/918.8/0/0 | -0.0842 | 成本降低，最大负贡献=model-output；少17步；主要来自 rollout 随机性/路径变短，非工具采用；同时存在运行异常=NonZeroAgentExitCodeError |
| `dab__usaspending__query3` | 1/0.0339/23/224.9 | 无 trajectory | NA | 无有效 trajectory（NonZeroAgentExitCodeError），不能把缺失成本当节省 |
| `dab__usaspending__query8` | 1/0.1018/35/870.8 | 0/0.0553/18/883.5/0/0 | -0.0465 | 成本降低，最大负贡献=model-output；少17步；但 performance 回退，属于无效节省；同时存在运行异常=NonZeroAgentExitCodeError；成功→失败 |
| `dab__usaspending__query9` | 1/0.4396/85/795.2 | 无 trajectory | NA | 无有效 trajectory（NonZeroAgentExitCodeError），不能把缺失成本当节省 |
| `dab__yelp__query1` | 1/0.0231/23/271.1 | 1/0.0134/14/256.6/0/0 | -0.0097 | 成本降低，最大负贡献=new-input；少9步；主要来自 rollout 随机性/路径变短，非工具采用 |
| `dab__yelp__query2` | 1/0.0259/17/385.5 | 1/0.0327/24/269.0/0/0 | 0.0067 | 成本增加，最大正贡献=model-output；多7步导致历史重复计费；新工具零采用，差异来自固定 instruction、rollout 路径或运行噪声 |
| `dab__yelp__query3` | 1/0.0406/21/431.1 | 1/0.0467/18/905.0/0/0 | 0.0061 | 成本增加，最大正贡献=new-input；平均 observation +110%；新工具零采用，差异来自固定 instruction、rollout 路径或运行噪声 |
| `dab__yelp__query4` | 0/0.2144/45/761.8 | 0/0.1108/37/735.3/0/0 | -0.1036 | 成本降低，最大负贡献=model-output；少8步；主要来自 rollout 随机性/路径变短，非工具采用；同时存在运行异常=NonZeroAgentExitCodeError |
| `dab__yelp__query6` | 1/0.0316/22/287.3 | 无 trajectory | NA | 无有效 trajectory（NonZeroAgentExitCodeError），不能把缺失成本当节省 |

## 11. 最终判断

1. 当前 SWE-Bench v9 在可信配对上成本上升，不能达到目标。
2. 当前 DeepSWE 的原始微小 saving 不显著，清理异常后方向反转，而且 final harness 非法泄漏，结果无效。
3. 当前 DAB 没有发生 evolve，final eval 又受大规模 infrastructure failure 污染，结果无效。
4. 多样化 16 evolve cases、evolve/eval 分离、token×price 成本计算、batch_size=2 和 timeout/fallback observation 已基本落地；这些不是主要失败点。
5. 下一步应先完成 P0 修复，再仅针对 card 序列化、compiler prompt 与 tool-level gate 做局部改造。若这些修好，不需要立即推翻 v9 的“evidence → staging → gate”总体框架。
