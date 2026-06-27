# Evolved Tools 成本下降实验 — 跨 Benchmark 分析

## 1. 实验设置

同一套 agent（`mini-swe-agent` + `openai/deepseek-v4-flash`），在两个 benchmark 上分别跑「加 evolved tools」和「不加 evolved tools」两条路：

| 实验目录 | benchmark | 任务数 | 是否挂载 `/app/.preinstalled_scripts/` |
| --- | --- | --- | --- |
| `results/deep-swe/deepseek-flash-with-evolve-tools` | deep-swe | 101 | 是 |
| `results/deep-swe/deepseek-flash-without-evolve-tools` | deep-swe | 101 | 否 |
| `results/swe-atlas-qa/swe_atlas_with-0623-004601` | swe-atlas (qa) | 112 | 是 |
| `results/swe-atlas-qa/swe_atlas_without-0623-004601` | swe-atlas (qa) | 112 | 否 |

用户口径下的总成本变化：

- deep-swe：**−11.32 %**（加 tools 更便宜）
- swe-atlas-qa：**−2.74 %**（加 tools 更便宜，但幅度小很多）

下面所有数字都来自 `result.json` 与 `agent/trajectory.json`，取两侧都跑出结果的 case 配对（deep-swe 97 对、swe-atlas 102 对）。

## 2. Evolved Tools 一句话功能说明 + 使用样例

每个工具一行：左列是工具名，中列是一句话功能，右列是 1–4 条最典型的调用样例（agent 在容器里实际跑的就是 `/app/.preinstalled_scripts/<name>/main.sh …` 这条形式；为表格紧凑，下面样例统一省略前缀 `"/app/.preinstalled_scripts/<name>/main.sh"`，每条样例之间用 `;` 分隔多个示例）。

| 工具 | 一句话功能 | 使用样例（前缀 `"/app/.preinstalled_scripts/<name>/main.sh"` 省略） |
| --- | --- | --- |
| `batch_read` | 一次命令批量读取多个文件或它们的指定行区间，支持按目录+glob 过滤整批读，替代多次 `cat` / `sed -n 'L1,L2p'`。 | `file1.py file2.py` ; `file1.py:10-30` ; `--head=20 file1.py` ; `--dir=/project --include="*.py"` |
| `build_check` | 一键跑构建/静态检查/单测，覆盖 Go（build+vet+test）、TypeScript（tsc）、Python（语法检查）。 | `./pkg/` ; `./pkg/ --compile-only --tags="kqueue,dev"` ; `lib/ --ts --filter=circleci` ; `file.py --python` |
| `code_structure` | 解析源文件并列出其中的函数 / 类 / 结构体 / 接口 / trait / 枚举等定义，可输出紧凑摘要。 | `main.go` ; `--summary src/*.py` ; `utils.py handler.py` |
| `file_patch` | 用 replace / insert-before / insert-after / delete-matching / append / prepend 等结构化动作改文件，替代易出错的 `sed -i`。 | `file.go replace "old" "new"` ; `file.py insert-after "def foo():" "    print('bar')"` ; `file.go delete-matching "debugger;"` ; `file.py append "# end of file"` |
| `find_files` | 按文件名 glob、深度、路径、类型等条件查找文件，默认自动跳过 `.git` / `node_modules`。 | `. -n "*.go"` ; `. -n "*.rs" -n "*.c" -n "*.h"` ; `/project -n "*.go" -d 4 -l 50` ; `. -n "*test*" -i` |
| `find_repo_root` | 从任意目录向上定位当前 git 仓库的根目录。 | （无参数） ; `/workspace/subdir` |
| `git_commit` | 一步完成 `git add -A` + `git commit -m …`，把"暂存+提交"合二为一。 | `"fix: resolve type error"` ; `"feat: add new feature" /workspace/repo` |
| `git_diff` | 一次性输出 `git status` + `diff` + `log` 的综合视图，支持 stat-only / name-only / cached / oneline 等显示模式。 | （无参数） ; `--stat-only` ; `--log=5 --oneline` ; `--short` |
| `multi_replace` | 在同一个文件里一次性完成多组字符串替换，也支持传入 Python 脚本做自定义变换。 | `file.go "l.fill(" "l.frame.fill(l.selector, "` ; `file.go --pairs "old1" "new1" "old2" "new2"` ; `file.go -f transform.py` |
| `multi_search` | 一次文件系统扫描里同时查多个 pattern，支持按扩展名过滤、仅文件名匹配、只列文件名等模式。 | `. pattern1 pattern2` ; `. --include='*.py' class1 class2` ; `. --names-only test_* *_test.py` ; `. -i PATTERN` ; `. -l pattern` |
| `py_exec` | 执行行内 Python 代码或脚本文件，自动激活 venv 并允许注入环境变量，也支持只做语法检查。 | `"print('hello world')"` ; `-f test.py arg1 arg2` ; `--check file.py` ; `-e MY_VAR=123 "import os; print(os.environ['MY_VAR'])"` |
| `quick_map` | 生成项目目录的紧凑树状视图，附带文件大小与扩展名统计，可按 glob 过滤文件类型。 | `. 3` ; `/project` ; `. --filter="*.py,*.md"` ; `src/ 2 -f "*.go"` |
| `run_cmd` | 在指定目录、指定环境变量、可选超时下跑任意命令，替代 `cd … && export … && command` 这种易碎组合。 | `--dir=/app python -m scapy.tools.UTscapy -t test.uts` ; `--dir=/app/src -e DJANGO_SETTINGS_MODULE=paperless.settings pytest tests/` ; `--timeout=30 curl http://example.com` |
| `run_tests` | 自动识别语言与测试框架（pytest / go test / vitest / jest 等）执行测试，可强制框架、按名称过滤、设定超时与环境变量。 | `tests/` ; `--pytest tests/test_api.py` ; `--go ./pkg/...` ; `--go --tags="kqueue,dev" ./pkg/` ; `--vitest lib/module/` |
| `write_file` | 原子写文件，自动创建父目录，免去先 `mkdir -p` 再 `cat <<EOF` 的两步。 | `path/to/new_file.py "print('hello')"` ; `config/app.toml "$(cat <<'EOF'\n[server]\nport = 8080\nEOF\n)"` |

附属文件 `instruction.md` 是这套工具集塞进 agent system prompt 的"使用说明书"；`evolve_used_case_id.txt` 用来记录这些工具是基于哪些训练 case 演化出来的，运行期不参与。

## 3. 每个 case 的聚合数据

成本按 deepseek-v4-flash 类似的定价估算（cache_hit 0.07 / cache_miss 0.56 / output 1.68 USD per Mtok），美元绝对值与 harness 计费会有微小差异，但相对变化与用户口径一致。

### deep-swe — 97 对配对任务

|                       | 加 tools     | 不加 tools    | Δ        |
| --------------------- | -----------: | ------------: | -------: |
| 平均 input tokens     |    8,348,481 |     9,765,987 | −14.51 % |
| 平均 cache-read tokens|    8,273,669 |     9,691,207 | −14.63 % |
| 平均 output tokens    |       51,428 |        59,145 | −13.05 % |
| 平均 agent 步数       |        100.1 |         120.8 | −17.08 % |
| 平均 peak ctx tokens  |      121,818 |       126,807 |  −3.93 % |
| 平均单 task 成本      |      \$0.707 |       \$0.820 | −13.69 % |
| reward=1 case 数      |           4  |             6 | −2       |

下降主要来自 **步数**：每个 case 少跑约 21 步，单步 prompt 大小差不多，所以 input/cache/output 一起跟着掉。

### swe-atlas-qa — 102 对配对任务

|                       | 加 tools    | 不加 tools   | Δ       |
| --------------------- | ----------: | -----------: | ------: |
| 平均 input tokens     |   2,319,033 |    2,467,456 | −6.02 % |
| 平均 cache-read tokens|   2,255,945 |    2,405,091 | −6.20 % |
| 平均 output tokens    |      16,914 |       17,788 | −4.92 % |
| 平均单 task 成本      |     \$0.222 |      \$0.233 | −4.93 % |
| reward=1 case 数      |         24  |           20 | +4      |
| 异常退出数            |          4  |            7 | −3      |

加 tools 一侧的命令数也少（平均 65.5 vs 68.4，−4 %），方向与 deep-swe 一致，但幅度只剩零头。

（我重算出 −13.69 % / −4.93 %，比用户口径 **−11.32 % / −2.74 %** 高一点，差异来自：(a) 单侧异常的 case 我做了配对剔除；(b) 我用的不是 harness 的真实计费。结论性差距不变：deep-swe 比 swe-atlas 多省 4–5 倍。）

## 4. 从 case log 看节省到底来自哪

### 4.1 trajectory 里到底在调什么

把所有 trajectory 里的 tool call 抠出来，按首个二进制 / evolved-script 名分桶：

```
deep-swe   加 tools     : 10 611 条 — 3 544 条 evolved (33.4 %)
                          batch_read 2352, multi_search 350, file_patch 245,
                          quick_map 125, py_exec 76, find_files 73,
                          run_tests 59, code_structure 57, multi_replace 48,
                          git_commit 47
                          原生：cd 6082, cat 601, grep 59, sed 26, find 21

deep-swe   不加 tools   : 12 573 条 — 0 条 evolved
                          原生：cd 9173, cat 1395, sed 487, grep 446,
                          nl 183, find 155, python3 106

swe-atlas  加 tools     :  7 080 条 — 3 311 条 evolved (46.8 %)
                          batch_read 2255, multi_search 449, find_files 182,
                          quick_map 169, code_structure 106, py_exec 67
                          原生：cd 1235, grep 720, cat 426, find 270, ls 183

swe-atlas  不加 tools   :  6 980 条 — 0 条 evolved
                          原生：cd 1737, cat 1482, grep 1127, nl 661,
                          find 530, sed 312, ls 269
```

两点最关键的观察：

1. **`batch_read` 是两个 benchmark 的主要贡献者** —— 在两侧加 tools 实验中都占了 ~2 200 / ~3 400 条 evolved 调用；它替掉的是 baseline 里那一串 `cat … | head/tail/sed -n` 的长命令。
2. **edit 类工具（`file_patch`、`multi_replace`、`git_commit`、`build_check`、`run_tests`）只在 deep-swe 上活跃。** swe-atlas-qa 是只读 QA benchmark，prompt 明确写"Do NOT modify any files"，agent 只需把答案写到 `/logs/agent/answer.txt`。所以工具集里有一半在 swe-atlas 上是"暗的"。

### 4.2 deep-swe 上节省最大的 10 个 case

| task | 加 tools 成本 | 不加 tools 成本 | Δ %     | 步数 加→不加 | reward |
| --- | --: | --: | --: | --: | --: |
| ts-pattern-match-each                | \$0.120 | \$0.559 | −78.6 % | 26 → 78   | 0 → 0 |
| anko-default-function-arguments      | \$1.625 | \$5.673 | −71.4 % | 159 → 378 | 0 → 0 |
| cliffy-config-file-parsing           | \$0.445 | \$1.189 | −62.6 % | 101 → 221 | 0 → 0 |
| cattrs-partial-structuring-recov     | \$0.313 | \$0.806 | −61.2 % | 58 → 157  | 0 → 0 |
| geo-shapeindex-serialization         | \$0.216 | \$0.549 | −60.6 % | 39 → 96   | 0 → 0 |
| textual-richlog-follow-state         | \$0.346 | \$0.808 | −57.2 % | 70 → 117  | 0 → 0 |
| kysely-window-grouping-helpers       | \$0.732 | \$1.639 | −55.4 % | 111 → 230 | 0 → 0 |
| oxvg-structural-selector-preserv     | \$0.540 | \$1.159 | −53.4 % | 77 → 141  | 0 → 0 |
| httpx-multipart-response-parsing     | \$0.324 | \$0.660 | −51.0 % | 67 → 110  | 0 → 0 |
| wasmi-trap-coredumps                 | \$0.860 | \$1.749 | −50.8 % | 105 → 168 | 0 → 0 |

逐 trace 看，节省都来自同样的模式：

- **`batch_read file:start-end`** 把每文件 3–6 次原生 `cat | sed -n 'L1,L2p'` 合成 1 次。agent 排查 bug 一般会看 4–10 个文件，光这点就能砍掉 30–60 个"廉价但非空"的 round-trip 步。
- **`multi_search . p1 p2 p3`** 把 2–4 个独立 `grep -R` 合并成一次扫描，输出一次给模型理解。
- **`file_patch <file> replace "old" "new"`** 是 deep-swe 上"单步消除"贡献最大的工具：baseline 里 `sed -i` 经常因多行/转义失败、agent 反复 `nl` 看行号再换写法重试。换成 `file_patch` 后一发命中、立刻往下走（不加 tools 时 487 次 `sed` → 加 tools 后只剩 26 次）。
- **`git_commit`** 替掉 prompt 强制要求的 `git add -A && git commit -m … && git status` 三连，每个任务直接少 ~2 步、收尾阶段的 prompt churn 显著减少。

### 4.3 deep-swe 上回退最严重的 case

| task | 加 tools | 不加 tools | Δ %     | 步数      | reward |
| --- | --: | --: | --: | --: | --: |
| psd-tools-blend-range-api          | \$0.510 | \$0.200 | +155.4 % | 78 → 60   | 1 → 0 |
| opa-rego-rule-profiling            | \$1.371 | \$0.560 | +144.9 % | 167 → 137 | 0 → 0 |
| scriggo-method-declarations        | \$3.920 | \$1.689 | +132.2 % | 312 → 225 | 0 → 0 |
| bandit-structured-nosec-directiv   | \$0.812 | \$0.356 | +128.2 % | 128 → 83  | 0 → 0 |
| happy-dom-deterministic-intersec   | \$0.512 | \$0.225 | +127.4 % | 102 → 65  | 0 → 0 |
| mashumaro-flattened-dataclass-fi   | \$1.555 | \$0.790 |  +96.9 % | 181 → 147 | 0 → 0 |
| expr-try-catch-errors              | \$2.391 | \$1.382 |  +73.1 % | 267 → 183 | 0 → 0 |
| dynamodb-toolbox-conditional-att   | \$1.355 | \$0.801 |  +69.3 % | 170 → 153 | 0 → 0 |

并排看这些 trace，原因有几类：

- baseline 那侧早早放弃（线索探完就停），加 tools 这侧因为"探索更便宜"反而继续往下挖。回退 case 的步数普遍 **上升** 而不是下降：psd-tools 78 vs 60、opa-rego 167 vs 137。便宜的探索 → 多探索，但没换来 reward。
- `batch_read --dir=… --include=…` 偶尔会把巨大的生成文件（如 `scriggo`/`anko` 的 parser table）一起读进来，单步 prompt token 直接爆掉。
- `expr-try-catch-errors`、`dynamodb-toolbox` 的 trace 里能看到 `file_patch replace` 因为字面量空格不匹配反复失败，agent 又退回 `cat -n` 检查，相当于多花一遍力气。
- `psd-tools-blend-range-api` 这条 reward 从 1 掉到 0：加 tools 跑出来的补丁被 verifier 拒了。这部分回退不只是"多花钱"，还伴随质量下降。

这些 outlier 把 deep-swe 的净收益从 ~17–20% 压回 ~11%。

### 4.4 swe-atlas case 分析

top 赢家和输家形态一样，但量级小很多：

```
最大赢家： task-…9bb  −61.5 %, …9a7 −60.3 %, …9bd −57.7 %, …9cd −57.0 %,
           …9c6 −47.4 %, …a2a −44.9 %, …9e9 −43.8 %, …9f1 −43.0 %
最大输家： task-…a27 +213 %, …a14 +150 %, …9f0 +147 %, …9d5 +113 %,
           …9f2  +99 %, …9be  +84 %, …a11  +73 %, …9f5  +64 %
```

打开 swe-atlas 的赢家 trace，看到的是和 deep-swe 一模一样的 `batch_read` / `multi_search` 合并模式。打开输家 trace，看到的则是：

- **answer-only 任务的轻量循环。** 很多 swe-atlas case 30–50 条命令就能完事，agent 本身就已经很省，把 `cat foo.py` 换成 `batch_read foo.py` 几乎没空间。
- **读得过宽。** 因为有 `batch_read --dir`，agent 倾向于把整目录读下来，而不是只读真正需要的那一个文件，input 反而胀大。
- **简单 Q&A 上的多余探索。** 在只有 50 个文件的小仓库上还跑 `quick_map`，再来 3 次 `code_structure`，目标文件甚至不在最终答案里。
- 解决度上 with 赢 11 个、without 赢 7 个，基本对称 —— 工具集没有显著帮 agent 答得"更好"，只是"更便宜"。

### 4.5 两个 benchmark 的结构性差异

| 维度                            | deep-swe                                  | swe-atlas-qa                          |
| --- | --- | --- |
| 任务类型                        | bug-fix / 加 feature（必须产 patch）       | 只读调研类 Q&A                         |
| edit 类工具是否被用             | 是（`file_patch` 245、`multi_replace` 48、`git_commit` 47、`build_check`、`run_tests`） | 否（只读） |
| baseline 平均 cmds/task         | ~130                                      | ~68                                  |
| baseline 平均 tokens/task       | 9.77 M                                    | 2.47 M                               |
| baseline 原生 bash 主要构成     | `cd`、`cat`、`sed`、`grep`、`nl`           | `cd`、`cat`、`grep`、`nl`、`find`     |
| evolved-script 调用占比         | 33 %                                      | 47 %                                  |
| 最常用的 evolved 工具           | batch_read、multi_search、file_patch、quick_map | batch_read、multi_search、find_files、quick_map |
| 加 tools 后的步数减少幅度       | −17 %                                     | n_agent_steps 字段为空；命令数 −4 %    |
| 加 tools 后的单 task 成本下降   | −13.7 %                                   | −4.9 %                               |

## 5. 根因总结 —— 为什么 deep-swe 节省的多 4–5 倍

1. **一半工具在 swe-atlas 上是"哑铃片"。** 这是只读 QA：没 patch、没跑测、没 commit。`file_patch`、`multi_replace`、`git_commit`、`build_check`、`run_tests` 在 deep-swe 里合计被调用 ~400 次，在 swe-atlas 里只有 ~32 次。而这几个正是 baseline 里"合并步数空间最大"的工具（`sed` 重试 loop、`git add && git commit && git status` 三连、`pytest … 2>&1 | tail`）。
2. **deep-swe baseline 大量预算花在 edit + verify 循环上。** 不加 tools 的 deep-swe 一侧光 `sed` 就 487 次、`cat` 1395 次、`git` 95 次、`nl` 25 次 …… 这些每条都要付 cache+input。换成 `file_patch` / `git_commit` / `build_check` 后整步整步被吞掉。swe-atlas baseline 根本没有 `git` / `sed` 这类家务命令，没空间砍。
3. **轨迹越长，杠杆越大。** deep-swe 平均 121 步、swe-atlas 平均 ~68 命令。即使单步节省比例相同，deep-swe 的可压缩"步数容量"也接近 2 倍；再叠加 1，乘出来的差距就更明显。
4. **读侧的节省两边其实差不多。** `batch_read` / `multi_search` 在两个 benchmark 上的覆盖率相近。占总命令数比例上 swe-atlas 更高（47 % vs 33 %），但占 **成本** 上只能省 5–6 % —— 因为原生 `cat` / `grep` 本来就是单步、短命令，合并它们能省 output token，但省不了外层一步的 prompt 成本。swe-atlas 已经接近"只靠读侧能榨到的天花板"。
5. **swe-atlas 上"探索税"部分抵消了读侧的收益。** 工具让"读取看起来免费"，于是 agent 在原本只要几条精准 grep 的 case 上发起 `batch_read --dir`、`quick_map`、几次 `code_structure`。这就是 §4.4 里那些 +60 %…+200 % 长尾。deep-swe 上同样存在，但被巨大的 edit 侧收益吃掉了。
6. **deep-swe 的长尾任务主导均值。** `scriggo-method-declarations`（\$3.92 vs \$1.69）、`expr-try-catch-errors`（\$2.39 vs \$1.38）、`anko-default-function-arguments`（\$1.63 vs \$5.67）单个 case 都是中位数的 5–10 倍开销。在大花费 task 上的胜利（仅 `anko` 一项就省 \$4）足以盖住小花费 case 的回退，而这种"重尾"分布偏偏只在 deep-swe 上存在。

## 6. 后续建议

- **按 benchmark 类型裁剪工具集**：swe-atlas 这类只读 QA 只挂 `batch_read`、`multi_search`、`find_files`、`quick_map`、`code_structure`、`py_exec` 就够；把 edit 侧工具的说明从 system prompt 里去掉，能省下首次写入的 ~6k token（虽然有 cache，但首步还是真金白银）。
- **在 QA 类任务里弱化 `batch_read --dir`**：它是 swe-atlas 回退的主因。可以从 system prompt 里删掉这个 flag，或者补一句"调研类任务优先精准读取"的提示。
- **修一下 `file_patch` 的鲁棒性**：deep-swe 多个 +100 % 回退都源于字面量替换匹配不上。让 `file_patch replace` 容忍空白差异（或在 fall back 上给一个 regex 提示）能直接捞回这些 case。
- **复核 deep-swe 上的 reward 回退**：`psd-tools-blend-range-api` 1→0 加上另外几个净 reward 损失之前，不能宣称收益是"白拿"的。如果加 tools 系统性地"过度探索 + 错误编辑"，那省下的钱里有一部分是用质量换的。
