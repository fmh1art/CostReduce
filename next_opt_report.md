# 通过 src/evolve 脚本进化机制进一步节省 token 的方案

> 只读分析报告 | 分析对象：deep-swe / deepseek-flash + evolve-tools v2-chunk（97 case）vs 无 evolve 基线（97 case）
> 数据口径：`final_metrics`（total_prompt / total_completion / total_cached / total_steps / extra.peak_context_tokens）+ `mini-swe-agent.trajectory.json` 逐命令解析
> 关键认知：mini-swe-agent 每步重发全历史，prompt token 绝大部分被 cache（实验组 cached/prompt = 8,197,521/8,266,859 ≈ **99.16%**）。**真实增量成本 = 非缓存 prompt（每步新追加的 observation）+ completion token**。因此「步数」「每步 observation 体积」「peak_context 膨胀」是 token 的三大驱动；prompt 固定开销绝大部分被 cache，$ 成本低，但它撑大 peak_context、挤压长尾 case。

---

## 0. 成本基线（先把账算清）

| 指标（均值/case） | 实验组 v2-chunk | 基线无evolve | 差值 | 说明 |
|---|---|---|---|---|
| total_completion_tokens | **51,313** | 59,145 | **-13.2%** | 真实成本主项 |
| total_prompt_tokens | 8,266,859 | 9,765,987 | -15.4% | 99.16% 被 cache |
| total_cached_tokens | 8,197,521 | 9,691,207 | -15.4% | |
| **非缓存 prompt（增量）** | **69,338** | 74,779 | **-7.3%** | 真实成本另一主项 |
| **completion+非缓存（真实成本代理）** | **120,651** | 133,925 | **-9.9%** | 进化已省 ~10% |
| total_steps | 109 | 123 | -11.4% | |
| api_calls | 107 | 121 | -11.6% | |
| peak_context_tokens | 116,444 | 126,807 | -8.2% | 上下文膨胀指标 |
| 总 observation 字符 | 121,070 | 130,451 | -7.2% | 增量 context 来源 |
| reward=1（f2p 通过）| **3/97** | 6/97 | **-3** | 正确性回退 |

**结论**：v2-chunk 已经把真实成本代理压低 ~10%，步数 -11%、completion -13%，但**正确性净 -3**（3/97 vs 6/97）。本方案的目标是在**不伤正确性**的前提下进一步压缩真实增量成本。

---

## 一、发现的 token 浪费点（量化 + 代表 case）

### 1.1 冗余步骤：38.1% 的 action 落在「连续只读 run」里

- **510 个长度 ≥3 的连续只读 run，共 4,371 步 = 全部 11,474 action 的 38.1%**。
- `instruction.md` 明确写了「After 3 consecutive read-only steps … STOP exploring and make a targeted edit attempt」，但 agent 仍大规模违反 → 说明**纯文本行为契约约束力不足**，evolve 没有度量这条契约是否被遵守。
- **同文件重复读取 582 次**（cat/sed/nl/head/tail 反复读同一 `.go/.py/.ts/...`）。
- **多次提交（未批量 commit）34/97 case**：commit 调用总数 162，其中 34 个 case commit ≥2 次，top case 各 commit 4 次（`helm-unified-manifest-stream`、`mashumaro-flattened-dataclass`、`pest-character-class`、`scc-bounded-memory-spilling`、`scriggo-method-declarations`）。

> 代表：`scriggo-method-declarations__T5aGCNi` — 269 步、peak_ctx 201,625、obs 241,971 字符、reward=0。典型的「读→读→读→改一点→再读→再改」永不收敛。

### 1.2 失败 / 无效调用：8.6% 的 action returncode≠0

总 action 11,474，returncode≠0 共 **990（8.6%）**，分类：

| returncode | 次数 | 性质 | 是否浪费 |
|---|---|---|---|
| 1 | 672 | 测试失败 / grep 无命中 | 多为有效信号，非浪费 |
| **-1** | **219** | 见下分解 | 部分浪费 |
| 2 | 32 | 用法错误 | 浪费 |
| **129** | **23** | **git-commit -m 误用** | **纯契约 bug，浪费** |
| 128 / 101 / 124 | 12/12/3 | timeout / 中断 | 浪费 |
| 127 | 9 | command not found | 浪费 |

**rc=-1（219 次）分解**（采样）：
- ~90 次是 `echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT` 的「action was not executed」——提交动作，**良性，非浪费**。
- ~56 次是 `cd /app/... && go build ./...` / `npm test` 等**命令超时**（2 分钟上限）——agent 超时后重试，每次重跑又重发输出，**真实浪费**。
- ~55 次执行异常（部分亦超时）。

**git-commit -m 误用（22 次，22 个 case）**：根因已定位（见 §2.3）。代表命令：
```
/app/.preinstalled_scripts/git-commit/main.sh -C /app -m "feat: ..."
```
**cat helper 源码学用法 30 步**：agent 反复 `cat .preinstalled_scripts/git-commit/intro.json`、`main.sh --help || cat ...` 来学契约——**契约不清的直接证据**。如 `abs-stepped-slices`、`expr-try-catch-errors`、`dynamodb-toolbox` 等。

> 量化：git-commit 契约歧义共造成 **22 次误用 + 30 次学用法 ≈ 50+ 浪费步**；命令超时（rc=-1/124）≈ 60-70 浪费步。

### 1.3 每步 observation 膨胀：cat 是头号元凶

- 单步 observation 上限 ≈ 9,988 字符（mini-swe-agent 截断点）；**538 步（4.7%）obs > 5000 字符**。
- 按命令 verb：**cat 216**（整文件 dump，第一）、nl 75、find-and-read 63、sed 59、python3 22、timeout 16、grep 16、git 13、go 10、npx 10。
- 这些超长 observation 直接进非缓存增量，并抬高 peak_context：实验组 **peak_context 中位数 109,817、p75=136,471、max=264,548**；top10 case 的 obs 累计 **111K–282K 字符**。
- `cat` 整文件 dump 是「该读 50 行却 cat 了 2000 行」的典型浪费——而 `find-and-read`（带 file:start:end）本就是为解决这个问题进化的脚本，却仍被 216 次 cat 压过，说明**进化产物没真正改变读行为**。

### 1.4 prompt 固定开销：每步 18,531 字符，其中 3,697 是死重

实验组 user prompt 均值 **18,531 字符/case**（基线 5,132，evolve 多注入 **+13,399 字符/步**），由 11 个 helper 的 `intro.json`（合计 16,881B）+ `instruction.md`（2,021B）构成。各 helper 体积与实际调用：

| helper | intro 大小 | 调用数 | 使用 case | 评价 |
|---|---|---|---|---|
| run-test | 3,713B | 10 | 6/97 | 体积最大，用得少 |
| inline-exec | 2,069B | 5 | 3/97 | 体积大，用得少 |
| go-env-check | 1,909B | 8 | 3/97 | **仅 Go 仓有用**，却注入所有 case |
| multi-file-edit | 1,815B | **0** | 0/97 | **死重** |
| ts-typecheck | 1,360B | 8 | 1/97 | **仅 TS 仓有用**，却注入所有 case |
| find-and-read | 1,179B | 231 | 18/97 | 有用 |
| git-status | 1,087B | 31 | 21/97 | 有用 |
| gofmt-fix | 1,032B | **0** | 0/97 | **死重** |
| git-commit | 953B | 154 | 97/97 | 高频 |
| diff-filter | 914B | 17 | 11/97 | 有用 |
| stash-test | 850B | **0** | 0/97 | **死重** |

- **3 个零调用 helper（multi-file-edit / gofmt-fix / stash-test）= 3,697 字符/步，纯死重**，在每一步、每个 case 都重复携带。
- **语言专属 helper（go-env-check 1909B / ts-typecheck 1360B）无条件注入全部 case**——非 Go/TS 仓每步白带 ~3.3K 字符。
- 总量级：18,593 字符/步 × 107 calls × 97 cases ≈ **1.93 亿 char-calls ≈ 4,830 万 token-calls**。$ 成本因 cache 较低，但**每步多带的 13.4K 撑大 peak_context**，是长尾 case 触顶（264K）的诱因之一。

### 1.5 top10 token 消耗 case：全部 reward=0，长尾放大

按 `completion + 非缓存 prompt`（真实成本代理）排序：

| case（缩写） | completion | 非缓存 | peak_ctx | steps | 总obs | max_obs | f2p |
|---|---|---|---|---|---|---|---|
| ofetch-per-origin-circuit-breaker | 149,999 | 126,380 | 264,548 | 123 | 111K | 9.6K | 40/47 |
| oxvg-structural-selector | 98,231 | 134,360 | 222,831 | 208 | 216K | 7.6K | 0/6 |
| scriggo-method-declarations | 87,793 | 128,834 | 201,625 | 269 | 242K | 9.0K | 41/48 |
| adaptix-name-mapping | 93,109 | 118,494 | 200,859 | 230 | **282K** | 9.8K | 31/44 |
| tengo-destructuring | 108,549 | 86,609 | 186,550 | 191 | 170K | 7.9K | 48/91 |
| fastapi-implicit-head | 115,503 | 77,995 | 187,030 | 146 | 163K | 9.3K | 29/43 |
| expr-try-catch | 85,000 | 106,934 | 180,917 | 218 | 137K | 10K | 8/79 |
| obsidian-linter-scoped | 99,628 | 87,397 | 177,165 | 135 | 143K | 8.7K | 0/33 |
| obsidian-linter-auto-toc | 86,334 | 92,064 | 174,049 | 115 | 189K | 8.7K | 32/41 |
| koota-pair-relation | 80,035 | 95,862 | 171,062 | 139 | 144K | 9.0K | 0/38 |

**共同模式**：completion 接近 150K 上限（ofetch 触顶 149,999）、obs 累计 110K–282K、步数 115–269、peak_context 170K–264K、reward=0。根因都是**整文件 cat + 迭代 grep + 永不收敛 → 上下文滚雪球**。这 10 个 case 的成本约为均值的 2–3 倍，是进一步省 token 的最大杠杆。

---

## 二、进化方案设计（落到 src/evolve 各模块）

### 2.1 机制现状（数据流，标注改进挂载点）

```
trajectory.json
   │
   ▼  [Stage1] annotator.py：每步 1 次 LLM，只标 dependencies
   │            (v1/v2: +brief_observations, +step_meta[纯规则])
   │            ❌ 无 token / 无 rc / 无失败 / 无重复读取 信号
   ▼  [Stage2] contrastive.py / evolve_v2_chunk.py：
   │            positive=依赖图最小子图(anchor+BFS) / negative=全 traj
   │            cost_hotspot = 重复≥3次且 obs≥1000 字符的 bash verb（用字符，非 token）
   │            ❌ 无 reward、无真实配对、positive 仍是「反推/脑补」
   ▼  [Stage3] evolver.py：mini-swe-agent 在 scripts_dir 就地生成 main.sh+intro.json+instruction.md
   │            bind-mount 到 /app/.preinstalled_scripts/（benchmark 脚本负责，不在 src/evolve）
   │            stats_provider 钩子存在但默认 None → 开环，无下游反馈
   ▼  下游 trial → 新 trajectory.json
   │            ❌ v1/v2 不接回；仅 v3 设计文档（未实现）有 paired_trajectory + stats_provider 闭环
```

改进挂载点：**Stage1 加 token 信号（纯规则，零 LLM 增量）→ Stage2 产出「浪费步对比样本」→ Stage3 用真实下游 stats 驱动脚本生成/淘汰（闭环）→ 多 rollout 护栏**。

### 2.2 改造 A：把「token 增量」作为进化的显式优化信号

**目标**：让进化从「猜哪里能省」变成「数据告诉你哪里在浪费」。

**A1. 新增 `CostAnnotator`（纯规则，挂到 `_chunk_helpers.py`，不新增 LLM 调用）**
- 扩展 `classify_step_meta`（`_chunk_helpers.py:181-204`）输出新增字段：
  ```python
  {
    "obs_chars": int,              # 已有 observation_chars
    "completion_tokens": int,      # 从 ATIF step.metrics 取（trajectory.json 每步已有 metrics）
    "delta_context": int,          # = obs_chars + completion_tokens（该步对上下文的净增量）
    "is_wasted": bool,             # rc!=0 且非信号类（排除 grep无命中 rc=1 这类有效负反馈）
    "is_redundant_read": bool,     # 同文件已被读过 / 在≥3 连续只读 run 内
    "is_timeout": bool,            # rc∈{-1,124} 且输出含 timed out
  }
  ```
- 在 `annotator.py` 的 `is_annotated`（`:111-122`）判幂等时，要求 `step_meta` 含上述新字段（v2 已要求 step_meta，这里只扩字段，**零额外 LLM 成本**）。
- 聚合到 trajectory 顶层 `cost_profile`：
  ```python
  cost_profile = {
    "total_obs_chars": int, "total_completion": int,
    "wasted_steps": int, "redundant_read_steps": int,
    "timeout_steps": int, "multi_commit": bool,
    "long_obs_steps": [{"step":i,"verb":v,"chars":c}],  # obs>5000 的步，给 evolve 直接看
    "repeated_files": [(file, times)],
  }
  ```

**A2. Stage2 新增「浪费步对比样本」（`contrastive.py` / `evolve_v2_chunk.py:_build_positive_sample`）**
- 现有 positive = 依赖图最小子图（**脑补**）。新增第三类样本 `contrastive_waste_chunk_<id>.json`：
  ```json
  {
    "negative_sample": "原 chunk（含 6 次 cat 同文件 + 2 次 git-commit 误用）",
    "positive_sample": "把 6 次 cat 折叠为 1 次 find-and-read、删除误用步后的等价最小序列",
    "waste_pattern": "repeated_full_file_read | git_commit_contract_misuse | timeout_retry | non_batched_commit",
    "saved_steps": 7, "saved_obs_chars": 14200
  }
  ```
- positive 的构造是**纯规则折叠**：对每个 `is_redundant_read` 连续段，保留首次 + 用 `find-and-read`/`grep -e` 合并；对每个 `git-commit` 误用，替换为正确调用。折叠后**不改变最终 git 树状态**（可校验：折叠前后 patch 一致），保证不引入虚假正确性。
- 这把 v3 文档里「paired_trajectory 用真实配对」的思想**提前用规则折叠实现**，不依赖跑两次下游。

**A3. cost_hotspot 升级用真 token（落实 v3 文档 `evolve_v3_cycle.md:263`）**
- `evolve_v2_chunk.py:206-309` 的 `cost_hotspot` 现在用 `observation_chars`（`_chunk_helpers.py:342`）。改为 `tiktoken`/模型 tokenizer 估算 token，并按 `delta_context` 加权，让 hotspot 反映真实增量成本而非字符数。

### 2.3 改造 B：修高频脚本契约 + 自动淘汰零调用脚本

**B1. 修 git-commit 契约（消除 22 次 rc=129 + 30 次学用法）**

根因（已读 `.evolve_scripts_v2_chunk/git-commit/main.sh` 确认）：脚本约定 message 是**末位位置参数**，但 agent 本能写 `-m "msg"`（git 原生语法）。脚本 arg 解析器把 `-m` 当未知 token 收进 `args`，随后 `git add "${files[@]}"` 实际执行 `git add -m` → git 报「unknown switch」→ **rc=129**。

修复（改 `git-commit/main.sh`，并同步 intro.json examples）：
```bash
# 在 while 解析里显式接住 -m / --message：
case "$1" in
  -m|--message)
    [ $# -lt 2 ] && { echo "ERROR: -m requires a message" >&2; exit 2; }
    msg="$2"; shift 2 ;;
  -C) ... ;;
  --) shift; args+=("$@"); break ;;          # 兜底
  *) args+=("$1"); shift ;;
esac
# git add 时强制终止选项解析，避免文件名/误入 token 被当选项：
git add -- "${files[@]}"
```
- intro.json `examples` 增加最自然的写法：`git-commit -m "feat: ..."`，让 agent 一看就会，**不再触发 cat intro.json 学用法**。
- 更重要：**让 evolve 自动产出这个修复**。在 `_validate_intros`（`evolver.py:443-481`）加一条「契约自检」：对每个脚本，用 examples 里的 call 在沙箱里 dry-run，若 returncode≠0 则把失败 + 期望用法回灌给 evolve agent（见 §2.4 闭环）。

**B2. 自动淘汰零调用脚本（落实 v3 `evolve_v3_cycle.md:265-266` 的「自动 regression 修剪」）**
- `ChunkScriptEvolverV2` 已留 `stats_provider` 钩子（`evolve_v2_chunk.py:1181,1250`）但默认 None、CLI 未暴露。**把它接通**：
  ```python
  # run_evolve.py / evolve_v2_chunk.py CLI 新增 --downstream-stats <path>
  # stats_provider 读 downstream_stats.json（见 §2.4）返回 {script: {calls, failures, ...}}
  ```
- Stage3 生成前，对 `scripts_dir` 做**硬淘汰**（不靠 agent 自觉）：
  ```python
  def prune_dead_scripts(scripts_dir, stats, min_calls=1, min_cases=1):
      for name in os.listdir(scripts_dir):
          s = stats.get(name, {})
          if s.get("calls",0) < min_calls or s.get("cases",0) < min_cases:
              shutil.rmtree(os.path.join(scripts_dir, name))   # 直接删 gofmt-fix/multi-file-edit/stash-test
  ```
- 预期：删 3 个零调用 helper，user prompt **-3,697 字符/步**。

**B3. helper 注入按语言条件化（缩小语言专属死重）**
- 给 `intro.json` 增 `languages` 字段（`["*"]` 或 `["go"]`/`["ts"]`）。benchmark 挂载阶段（或新增 `select_scripts` Stage）只 bind-mount 匹配当前仓语言的 helper。
- 预期：非 Go 仓去掉 `go-env-check`（1909B）、`gofmt-fix`（1032B）；非 TS 仓去掉 `ts-typecheck`（1360B）→ 多数 case user prompt 再 **-2~4K 字符/步**。

### 2.4 改造 C：进化出「截断 / 精准读取 / 批量编辑+提交」类能真正降增量 context 的契约脚本

`instruction.md` 已写了这些行为，但**纯文本约束力不足**（38.1% 仍连续只读、216 次整文件 cat）。要把它们从「散文」变成「强制脚本」：

| 新/改脚本 | 解决的浪费 | 契约要点 |
|---|---|---|
| `precise-read`（升级 `find-and-read`） | 216 次整文件 cat | 入参 `file:start:end`；若 `end-start>200` 自动截断并提示「已截断到 200 行，用 grep 定位」；拒绝无范围的整文件 cat |
| `batch-grep` | 582 次重复读 + 多次 grep | 一次 `-e pat1 -e pat2 ...` 多模式多文件，输出合并去重 |
| `run-test`（升级，已有但用得少） | 56 次超时 + 测试输出膨胀 | 强制 `--timeout` + 输出只保留「前 N 行 fail + 末尾 summary」；失败时只回放首个 fail 用例 |
| `commit-once`（git-commit 收尾约定） | 34/97 多次 commit | 一次性 `git add -A` + commit；instruction 强调「收尾只调一次」 |
| 复活 `multi-file-edit`（当前 0 调用） | 跨文件同构编辑 | 把契约写对（之前 0 调用很可能是契约太复杂），example 给最常见场景 |

**关键**：这些脚本由 evolve Stage3 生成，但**生成依据是 §2.2 的浪费步对比样本**——evolve agent 看到原始 trajectory 里「6 次 cat 同文件」与「1 次 find-and-read」的对照，自然产出 `precise-read` 的契约。这把 evolve 的输入从「依赖图」升级为「真实浪费模式」。

### 2.5 改造 D：脚本有效性度量闭环（驱动留存/淘汰）

把 v3 设计文档里的 `stats_provider` 闭环**真正实现**：

**D1. 下游 stats 采集**（benchmark 脚本侧，或 evolve 跑完后扫 trajectory）
```python
# 每个 case 跑完，聚合到 downstream_stats.json：
{
  "git-commit": {"calls":154,"failures":22,"cases":97,"avg_obs_when_used":820},
  "find-and-read": {"calls":231,"failures":0,"cases":18,"avg_obs_when_used":1500},
  "gofmt-fix": {"calls":0,"failures":0,"cases":0,"avg_obs_when_used":0},   # → 自动淘汰
  ...
  "_per_case_cost": {"completion":51313,"non_cached":69338,"peak_ctx":116444}
}
```
采集逻辑已在 `_chunk_helpers.py` 的 bash 解析 + 本次分析脚本里验证可行（按 `.preinstalled_scripts/<name>/` 正则即可）。

**D2. 脚本评分**（Stage3 读 stats 后注入 prompt + 驱动淘汰）
```python
def script_score(s):
    calls, fail = s["calls"], s["failures"]
    usefulness = calls * (1 - fail/max(calls,1))          # 调用率×成功率
    cost_saved = s.get("avg_obs_saved",0)                 # 该脚本相对手写命令平均省的 obs
    correctness = s.get("correctness_delta",0)           # 用该脚本的 case 通过率 vs 不用
    return usefulness * (1 + cost_saved/1000) * (1 if correctness>=0 else 0.5)
# score < τ 的脚本：进入「待删除」列表，evolve agent 必须删或改
```
- 注入 prompt 的 `_downstream_stats_block`（`evolve_v2_chunk.py:1025-1045`）从「软提醒」升级为「硬指标 + 行动指令」：`"gofmt-fix: 0 calls/97 cases → DELETE. git-commit: 22/154 failures (rc=129) → FIX contract."`

**D3. 闭环数据流**
```
evolve 产出 scripts → 下游 trial → 采 downstream_stats.json
      ↑                                        │
      └──── Stage3 读 stats：prune + 注入指标 + 修正契约 ←┘
```
开环 → 闭环。这是本方案与 v1/v2 的本质区别。

### 2.6 改造 E：正确性护栏（避免重蹈 happy-dom 早停止覆盖不足）

上轮 happy-dom 是 evolve 致差的唯一确证 case（早停止覆盖不足）。护栏：

- **E1. 多 rollout 非劣性门**：每个候选 script-set 在评估集上跑 **K≥3 rollout/case**，pass_rate = 通过比例。promote 条件：
  ```
  Δcost < 0  AND  pass_rate_new ≥ pass_rate_base − ε   (非劣，ε=1/K 容忍 n=1 噪声)
  ```
  这直接解决上轮「n=1 随机性放大 -3」的问题。
- **E2. 浪费步折叠的正确性校验**（§2.2 A2）：折叠后的 positive 序列必须满足「最终 git 树状态 == 原 trajectory」——用 `git diff` 校验，折叠不改 patch。这样「省步」不等于「漏改」。
- **E3. 早停契约护栏**：`instruction.md` 的「STOP after 3 read-only」是 happy-dom 早停的嫌疑来源。改为「STOP exploring after 3 read-only **only if 至少 1 次 verify (test/build) 已尝试**」——即「先验证再停」，避免覆盖不足。
- **E4. 回归 case 固化**：把 happy-dom 等历史回归 case 加入 evolve 的**回归测试集**，每次 promote 前必跑，回归即拒。

---

## 三、预期收益（量化）

### 3.1 真实增量成本（completion + 非缓存 prompt，当前 120,651/case）

| 杠杆 | 机制 | 预期降幅 | 量化依据 |
|---|---|---|---|
| observation 截断（precise-read/run-test 截断） | 把 538 步超长 obs（均~7K）压到 ~2K | **非缓存 prompt -15~20%** | obs 是非缓存增量的主体；总 obs 121K→~95K |
| git-commit 契约修复 | 消 22 误用 + 30 学用法 ≈ 50 步 | **-1~2% 步数** + 消除失败链 | 集中在 22 个 case |
| 超时治理（run-test 强制 timeout+截断） | rc=-1/124 约 70 步减半 | **-0.5% 步数** | 集中在 top10 长尾 case |
| 行为契约脚本化（batch-grep/commit-once） | 582 重复读 + 34 多 commit | **-3~5% 步数** | 命中 38.1% 冗余只读 run |
| 合计真实成本代理 | | **-8~12%**（在已 -10% 基础上再降） | 120,651 → ~106K–110K |

### 3.2 peak_context_tokens（当前均值 116,444 / max 264,548）

| 杠杆 | 降幅 | 依据 |
|---|---|---|
| 删 3 零调用 helper | -3,697 字符/步 | 3,697B 死重 |
| helper 语言条件化 | -2~4K 字符/步（多数 case） | go-env-check/ts-typecheck/gofmt-fix |
| obs 截断 | -10~15K 字符/步（长尾） | 538 步超长 obs 压缩 |
| **peak_context 均值** | **-10~15K**（116K→~102K） | |
| **peak_context max（长尾）** | **-30~50K**（264K→~215K） | obs 膨胀是长尾触顶主因 |

### 3.3 prompt 固定开销

- user prompt：18,531 → **~12,500 字符/步（-33%）**（删 3 死重 3,697 + 语言条件化 ~2,400）。
- 全实验组：1.93 亿 char-calls → ~1.30 亿 char-calls（-33%）。$ 成本因 cache 影响小，但 peak_context 压力同步下降。

### 3.4 失败步

- returncode≠0：990 → 预计 **~850（-14%）**（消 22 rc=129 + 30 学用法 + 35 超时）。
- 浪费步占比：8.6% → **~7.4%**。

### 3.5 正确性约束（必须守）

- reward=1：当前 3/97。**目标：≥6/97（恢复基线水平）**，通过 E1 多 rollout 门 + E4 回归集保证不劣化。
- 代价：评估成本 ×K（K=3 时评估 token ×3），但这是离线 evolve 成本，不进线上 trial。

> 风险提示：obs 截断若过激可能丢关键上下文导致正确性下降（happy-dom 教训）。故 precise-read 必须保留「已截断，用 grep 精确定位」的回退提示，且受 E1 门约束。

---

## 四、实施建议

### 4.1 改动点清单与优先级

| 优先级 | 改动 | 文件 | 工作量 | 收益/风险 |
|---|---|---|---|---|
| **P0** | 修 git-commit `-m` 契约 + intro example | `git-commit/main.sh`、`intro.json` | 小 | 消 22 误用+30 学用法；零风险 |
| **P0** | 接通 `stats_provider` + 硬淘汰零调用脚本 | `run_evolve.py`、`evolve_v2_chunk.py:1181,1250` | 中 | -3.7K prompt/步；闭环基石 |
| **P0** | CostAnnotator（纯规则扩 step_meta） | `_chunk_helpers.py:181`、`annotator.py:111` | 中 | 零 LLM 增量；给进化喂真信号 |
| **P1** | 浪费步对比样本（contrastive_waste_chunk） | `contrastive.py`、`evolve_v2_chunk.py:_build_positive_sample` | 中 | 把进化输入从脑补→真实浪费 |
| **P1** | obs 截断契约脚本（precise-read / run-test 升级） | 新 `main.sh`+`intro.json` | 中 | 最大杠杆；受护栏约束 |
| **P1** | helper 语言条件化（intro 加 `languages`） | `intro.json`、benchmark 挂载 | 小 | -2~4K prompt/步（多数 case） |
| **P2** | 多 rollout 非劣性门 + 回归集 | 新 `eval_gate.py`、`evolve_v3_cycle.md` 落地 | 大 | 正确性护栏；评估成本×K |
| **P2** | cost_hotspot 用真 token | `evolve_v2_chunk.py:206`、`_chunk_helpers.py:342` | 小 | hotspot 精度↑ |
| **P2** | batch-grep / commit-once / 复活 multi-file-edit | 新脚本 | 中 | 命中冗余只读与多 commit |

### 4.2 风险与回滚

- **回滚粒度**：所有改动都在 `scripts_dir` 与 `src/evolve` 内，**不碰实验目录**。每轮 evolve 产出独立目录（`.evolve_scripts_v2_chunk_*`），可 `--scripts-dir` 切换回旧版即时回滚。
- **正确性风险**：obs 截断 / 早停契约是 happy-dom 教训重灾区 → 必须 P2 多 rollout 门先行（或与 P1 同步上），未过门不 promote。
- **闭环风险**：`downstream_stats.json` 依赖 benchmark 脚本采集，若采集缺失则 stats_provider 退化为空（与现状一致），不阻塞。
- **淘汰误删**：硬淘汰阈值 `min_calls≥1 & min_cases≥1`，仅删**零调用**，不误伤低频有用的脚本（如 run-test 6 case 但 10 调用，保留）。

### 4.3 评估方式

1. **离线 evolve 评估**（不跑线上）：用 §2.2 的 cost_profile 在历史 97 case trajectory 上回放，验证「浪费步对比样本」能正确定位浪费、折叠后 patch 不变。
2. **闭环 dry-run**：跑一轮 evolve → 下游 trial（子集 20 case × K=3 rollout）→ 采 stats → 第二轮 evolve，检查零调用脚本是否被删、git-commit 失败率是否下降。
3. **全量对照**：97 case 全跑 v3-chunk vs v2-chunk vs baseline，对比 `completion+非缓存` / `peak_context` / `reward=1 比例` / `returncode≠0 占比`。
4. **护栏验收**：回归集（含 happy-dom）pass_rate ≥ baseline−ε；否则拒 promote。

### 4.4 落地节奏

- **第 1 步（1 天）**：P0 三项（git-commit 契约 + stats 接通 + CostAnnotator）。立即可见 -3.7K prompt/步 + 消 50 浪费步。
- **第 2 步（3-5 天）**：P1（浪费步样本 + 截断脚本 + 语言条件化）。跑子集闭环，看真实成本代理降幅。
- **第 3 步（1 周）**：P2（多 rollout 门 + 回归集 + batch 脚本）。全量对照，验收正确性非劣。
