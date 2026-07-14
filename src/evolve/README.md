# Script Evolution Pipeline — Tutorial

从已有的 agent trajectory 演化出可复用的成本优化脚本。实验脚本默认将日志、报告和
最终脚本统一保存到 `results/evolve/<version>/<benchmark>/<timeflag>/`；prep、最终评测和
no-evolve 分别保存到 `results/prep/`、`results/eval/` 和 `results/no_evolve/`。

## 1. Pipeline 概览

```
┌──────────────┐     ┌────────────────┐     ┌──────────────┐
│  1. annotate │ ──▶ │ 2. contrastive │ ──▶ │  3. evolve   │
└──────────────┘     └────────────────┘     └──────────────┘
 TrajectoryAnnotator  ContrastiveSampleBuilder   ScriptEvolver
 LLM 标注 step 依赖    按依赖图裁剪出最小 traj    mini-swe-agent 演化脚本
```

### Stage 1 — annotate (`TrajectoryAnnotator`)

- **输入**: `<result_dir>/<task_id>/agent/trajectory.json`
  - 文件结构: `{schema_version, agent, steps:[...], final_metrics, ...}`
  - 每个 action step 含 `tool_calls` / `observation` / `message`。
- **输出**: 原地写回 `trajectory.json`，新增 `dependencies` 字段：
  ```json
  {"0": [], "1": [0], "2": [0,1], ...}
  ```
  - key 是 action step 的 1-based 序号
  - value 是它依赖的之前 step 序号列表
  - `0` 表示"初始状态"，几乎总是被依赖
- **副产物**: 日志（每步的 LLM 原始输出）。

### Stage 2 — contrastive (`ContrastiveSampleBuilder`)

- **输入**: Stage 1 产出的、含 `dependencies` 字段的 `trajectory.json`
- **输出**: 同目录下 `contrastive_sample.json`：
  ```json
  {
    "positive_sample": <裁剪后的最小 trajectory>,
    "negative_sample": <原始完整 trajectory>
  }
  ```
  - `positive_sample` 仅保留从 final action 反向可达的 step + 初始上下文 step
  - 新增 `minimal_step_indices` 字段记录保留下来的 action step 序号

### Stage 3 — evolve (`ScriptEvolver`)

- **输入**: Stage 2 产出的所有 `contrastive_sample.json` + `--scripts-dir` 指向的工作目录
- **输出**（写到 `--output-dir` 或 `<result_dir>/evolve_logs/`）：
  - `evolve_batch_<id>.traj.json` — mini-swe-agent 的 trajectory
  - `evolve_batch_<id>.prompt.md` — 发给 agent 的 prompt
  - `evolve_batch_<id>.done` — 完成标记（含 batch 内 sample 列表）
  - `scripts_dir` 下的脚本和 `instruction.md` 被 agent 就地修改
- **断点续跑**: 默认开启。已存在 `.done` sentinel 的 batch 会被跳过，`--no-resume` 关闭。

## 2. 运行方式

入口在 `run_evolve.py`，两种等价调用：

```bash
python -m src.evolve run RESULT_DIR ...        # 通过 __main__.py 转发
python -m src.evolve.run_evolve run RESULT_DIR ...  # 直接调用
```

四个子命令：

| 子命令 | 作用 |
|---|---|
| `annotate` | 只跑 Stage 1 |
| `contrastive` | 只跑 Stage 2 |
| `evolve` | 只跑 Stage 3 |
| `run` | 跑完整三段 pipeline |

## 3. 全部参数

### 3.1 通用参数（所有子命令）

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `result_dir` | 位置参数 | — | **必填**。result/run 目录，需含 `<task_id>/agent/trajectory.json` 结构 |
| `--task` | str | None | 按 task id 过滤。先按路径边界严格匹配（`<task>` 或 `<task>__*` 或 `__` 分隔的片段），无匹配时回退到子串匹配 |
| `--log-file` | path | None | 把日志额外写到文件。默认只输出到 stderr |

### 3.2 LLM 配置参数（`annotate` / `evolve` / `run`）

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `--config` | path | `_config/deepseekv4_flash.yaml` | LLM 配置 yaml，需含 `llm_name` / `key` / `openai_base_url` / `temperature` |

### 3.3 Annotate 参数（`annotate` / `run`）

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `--workers` | int | 1 | 跨文件+跨 step 的总并行 LLM 调用数。pipeline 会自动拆分为 `file_workers × step_workers`（向上取整），如 `--workers 4` + 3 个文件 → 3 个 file worker × 2 个 step worker |
| `--retry-failed` | int | 1 | 第一轮失败的 trajectory 文件重试次数。0 表示不重试 |

### 3.4 Evolve 参数（`evolve` / `run`）

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `--scripts-dir` | path | `.evolve_scripts` | agent 工作目录。脚本和 `instruction.md` 在此就地修改。不存在时会自动创建，并写入默认 `instruction.md` |
| `--mini-swe-agent-dir` | path | `agent/mini-swe-agent` | mini-swe-agent 仓库路径。实际通过 `uv run --directory <dir> mini ...` 调用 |
| `--batch-size` | int | 5 | 每个 batch 包含多少个 contrastive sample。一个 batch 跑一次 mini-swe-agent |
| `--max-observation-chars` | int | 500 | 序列化 trajectory 时每个 observation 的字符上限。多 result 时按 `budget / n_results` 分配 |
| `--output-dir` | path | `<result_dir>/evolve_logs` | evolve 产物（prompt、trajectory、sentinel）的输出目录 |
| `--dry-run` | flag | False | 只打印 mini-swe-agent 命令，不实际执行。prompt 也不会被写出 |
| `--no-resume` | flag | False | 忽略已存在的 `.done` sentinel，强制重跑每个 batch。默认会跳过已完成的 batch |

### 3.5 Pipeline 参数（仅 `run`）

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `--skip` | str (可重复) | `[]` | 跳过指定 stage。可重复：`--skip annotate --skip contrastive`。stage 名为 `annotate` / `contrastive` / `evolve` |

## 4. Tutorial

### 4.1 准备数据

需要先有 agent 跑出来的 trajectory 目录：

```
results/
└── deep-swe/
    └── deepseek-flash-without-evolve-tools/
        ├── task-foo__abc123/
        │   └── agent/
        │       └── trajectory.json   ← 必须存在
        └── task-bar__def456/
            └── agent/
                └── trajectory.json
```

trajectory.json 里每个 action step 应含 `tool_calls`（list）或 `action` 或 `observation`。

### 4.2 第一次跑：先 dry-run 看看命令对不对

```bash
python -m src.evolve run results/deep-swe/deepseek-flash-without-evolve-tools \
    --config _config/deepseekv4_flash.yaml \
    --scripts-dir .evolve_scripts \
    --workers 4 \
    --batch-size 5 \
    --dry-run
```

`--dry-run` 时 Stage 1/2 会真的跑（它们不调 mini-swe-agent），Stage 3 只打印命令不执行。可以借此验证 LLM 配置、数据路径、contrastive 样本生成是否正确。

### 4.3 正式全量跑

```bash
python -m src.evolve run results/deep-swe/deepseek-flash-without-evolve-tools \
    --config _config/deepseekv4_flash.yaml \
    --scripts-dir .evolve_scripts_v0_dag_mini_as_pos \
    --workers 8 \
    --batch-size 2 \
    --log-file log/evolve/evolve_scripts_v0_dag_mini_as_pos.log
```

跑完后：

- `results/.../evolve_logs/evolve_batch_*.done` — 每个 batch 的完成标记
- `results/.../evolve_logs/evolve_batch_*.prompt.md` — 发给 agent 的 prompt
- `results/.../evolve_logs/evolve_batch_*.traj.json` — mini-swe-agent 的 trajectory
- `.evolve_scripts/*.sh` / `.evolve_scripts/instruction.md` — 被演化的脚本

### 4.4 断点续跑

如果中途挂了，**直接重跑同一条命令**即可——已完成的 batch 会被 sentinel 跳过：

```bash
python -m src.evolve run results/... --config ... --scripts-dir .evolve_scripts
# 日志会显示: batch 1 already done (sentinel ... exists), skipping
```

如果想强制重跑某个 batch，删掉对应的 `.done` 文件，或加 `--no-resume`：

```bash
rm results/.../evolve_logs/evolve_batch_3.done   # 只重跑 batch 3
# 或者全部重跑
python -m src.evolve run results/... --no-resume
```

### 4.5 只跑单段（调试用）

```bash
# 只跑 annotate（标注依赖）
python -m src.evolve annotate results/... --config _config/deepseekv4_flash.yaml --workers 4

# 只跑 contrastive（裁剪最小 trajectory）
python -m src.evolve contrastive results/...

# 只跑 evolve（基于已有 contrastive sample 演化脚本）
python -m src.evolve evolve results/... --config _config/deepseekv4_flash.yaml --scripts-dir .evolve_scripts
```

### 4.6 跳过某段

如果你已经跑过 annotate，只想重跑 contrastive + evolve：

```bash
python -m src.evolve run results/... --skip annotate
```

### 4.7 只处理某个 task

```bash
python -m src.evolve run results/... --task task-foo
```

`--task` 会先按 task id 严格匹配（`task-foo` 或 `task-foo__*`），匹配不到时回退到路径子串匹配。

### 4.8 换一个 LLM 配置

```bash
python -m src.evolve run results/... --config _config/gpt5.5.yaml --scripts-dir .evolve_scripts
```

`_config/` 下可选：`deepseekv4_flash.yaml` / `deepseekv4_pro.yaml` / `gpt5.5.yaml` / `kimi26.yaml` / `mimo25.yaml`。

## 5. 替换中间模块

所有 stage 满足 `Stage` 协议（`name: str` + `run(result_dir, task=None)`）。三种替换粒度：

### 5.1 替换整个 stage

在 `run_evolve.py` 的 `build_pipeline()` 里把对应 stage 换成自定义实现：

```python
from my_module import MyAnnotator

def build_pipeline(args):
    stages = [
        MyAnnotator(...),              # ← 替换 Stage 1
        make_contrastive_builder(),
        make_evolver(...),
    ]
    return ScriptEvolvePipeline(stages=stages)
```

只要 `MyAnnotator` 有 `name` 属性和 `run(result_dir, task=None)` 方法即可。

### 5.2 替换 evolve 内部组件

`make_evolver` 支持注入 `runner`（agent 后端）和 `prompt_builder`（prompt 模板）：

```python
from src.evolve.evolver import AgentRunner, EvolvePromptBuilder, TrajectorySerializer

class MyRunner(AgentRunner):
    def run(self, prompt, prompt_path, output_path, cwd):
        # 你的 agent 后端
        ...

class MyPromptBuilder(EvolvePromptBuilder):
    HEADER = [...]  # 自定义 prompt 模板

# 在 build_pipeline 里
make_evolver(
    ...,
    runner=MyRunner(),
    prompt_builder=MyPromptBuilder(
        serializer=TrajectorySerializer(max_observation_chars=1000),
    ),
)
```

### 5.3 替换 trajectory 序列化

子类化 `TrajectorySerializer` 并注入 `EvolvePromptBuilder`：

```python
class CompactSerializer(TrajectorySerializer):
    def serialize(self, trajectory):
        # 你的紧凑序列化
        ...

make_evolver(..., prompt_builder=EvolvePromptBuilder(serializer=CompactSerializer()))
```

## 6. 常见问题

**Q: annotate 阶段很慢？**
A: 长 trajectory（100+ step）的 history 会 O(N²) 膨胀。先确认 `--workers` 用上了，再考虑调小 `TrajectoryAnnotator.MAX_OBSERVATION_CHARS`（默认 800）。

**Q: evolve 跑了一半挂了，重跑会覆盖已有结果吗？**
A: 不会。已写 `.done` sentinel 的 batch 会被跳过。但 `.evolve_scripts/` 下的脚本会被新 batch 继续修改——如果想从头演化，备份或删掉 `.evolve_scripts/` 再跑。

**Q: mini-swe-agent 报错怎么看？**
A: 日志里会打印 `mini-swe-agent failed (rc=...)` + 完整 stdout/stderr tail。也可以直接看 `evolve_batch_<id>.traj.json` 里 agent 自己的 trajectory。

**Q: `--task` 匹配不到？**
A: 先看 `result_dir` 下的目录结构。task id 通常是 `<task_id>__<random>` 形式，`--task task-foo` 会匹配 `task-foo__*`。如果是别的命名，回退到子串匹配，所以传任意路径片段都行。

## 7. Evolved scripts → native function tools（`native_tools.py` + `evolve_tools`）

历史做法是把 evolved scripts 当「伪工具」：在 system prompt 里教 agent 用
`bash /app/.preinstalled_scripts/<name>/main.sh <args>` 调用。现在改为**真正的 function
tool**——agent 直接按名字（如 `read-lines`）带结构化参数调用。

### 7.1 转换流水线（host 侧：`src/evolve/native_tools.py`）

evolve agent 编辑 `<scripts_dir>/<tool>/{main.sh, intro.json}` 后，converter 把这个目录变成
mini-swe-agent 注册 native tool 所需的三样东西：

| 产物 | 路径 | 作用 |
|---|---|---|
| manifest | `<scripts_dir>/.tools_manifest.json` | 每个 tool 的 JSON-schema `parameters` + `param_specs`（CLI 渲染规则）|
| runtime 包 | `<scripts_dir>/.runtime/evolve_tools/` | 通用 agent 端包（`EvolveToolsAgent` + 两个 Model 子类），容器内经 `PYTHONPATH` 导入 |
| config yaml | `<scripts_dir>/.evolve_tools_config.yaml` | 设 `model.model_class` / `agent.agent_class` 为 evolve_tools 类（+ `max_completion_tokens`）|

intro.json 参数 `name` → CLI 形式的映射规则（converter 据此把 function-call 参数渲染回 argv）：

| `name` 形式 | kind | 渲染 |
|---|---|---|
| `--numbered` | bool_flag | 值为真时 emit `--numbered` |
| `--head=N` | value_flag_eq | emit `--head=<value>` |
| `-c code` / `--x N` | value_flag_space | emit flag 再 emit value |
| `file`（无前导 `-`）| positional | 末尾按声明顺序 emit，多词值会被 shell-split |

CLI：

```bash
python -m src.evolve.native_tools deploy --scripts-dir .evolve_scripts --api-type chat
python -m src.evolve.native_tools build-manifest --scripts-dir .evolve_scripts
python -m src.evolve.native_tools deploy-runtime --scripts-dir .evolve_scripts
python -m src.evolve.native_tools write-config --scripts-dir .evolve_scripts --api-type responses
```

### 7.2 运行时（agent 侧：`minisweagent.extra.evolve_tools`）

参照 `agent/mini-swe-agent/add_new_tools.md` 的 calc_tool 模式，零改动核心源码：

- `registry.py` — 读 manifest（`EVOLVE_TOOLS_MANIFEST`，默认 `/app/.preinstalled_scripts/.tools_manifest.json`），
  暴露 chat/responses tool 列表、扩展解析器（认 `bash` + 所有注册 tool）、`run_tool`（`bash <main.sh> <rendered argv>`）。
- `agent.py` — `EvolveToolsAgent(DefaultAgent)`：按 `action["tool"]` 分流，注册 tool 走 `run_tool`，其余走 `env.execute`（bash）。
- `model.py` — `LitellmModelWithEvolveTools` / `LitellmResponseModelWithEvolveTools`：把 tool schemas 加进 `tools=`，换扩展解析器。

manifest 缺失时退化为「只有 bash」（不崩），所以空 scripts_dir 也能 rollout。

### 7.3 rollout 容器如何装上（`scripts/_bench_common.sh`）

设置 `EVOLVE_SCRIPTS_DIR` 后，每个 `run_<bench>.sh` 在调起容器前调用 `evolve_scripts_deploy`
（生成三样产物），并通过 pier/harbor 注入：

- `--ak config_file=<config yaml>` — 设 model/agent 类为 evolve_tools.*
- `--ae EVOLVE_TOOLS_MANIFEST` / `EVOLVE_TOOLS_SCRIPTS_DIR` / `PYTHONPATH=<scripts>/.runtime`

容器里 mini-swe-agent 来自 PyPI（没有 `extra/evolve_tools`），所以 runtime 包是从 host 拷进
`<scripts>/.runtime/` 并经 `PYTHONPATH` 导入的；脚本目录经 bind-mount 挂到 `/app/.preinstalled_scripts`。

测试：`cd agent/mini-swe-agent && uv run python tests/test_evolve_tools.py`（22 用例，免 LLM）。

## 8. evolve_v5_cycle — rollout ↔ evolve 闭环（4 cycles，native tools）

`src/evolve/evolve_v5_cycle.py` 把上面整条链路闭成 4 轮（`--n-cycles`，默认 4）：

```
for cycle in 1..N:
  1. rollout     mini-swe-agent 带【当前】native tools 跑 benchmark → 新 trajectories
  2. annotate    LLM 标注每条 trajectory 的 step 依赖
  3. contrastive 按依赖图裁出最小 trajectory
  4. evolve      contrastive samples 喂给 evolve agent → 改 scripts/intro.json
  5. bridge      converter 重建 manifest/runtime/config（下一轮 rollout 自动用上新 tools）
```

- **rollout agent** = `RolloutAgent`：调 `scripts/run_<bench>.sh`，临时软链 N 个 case 进 temp 目录。
- **evolve agent** = `EvolveAgent`：复用 `TrajectoryAnnotator` / `ContrastiveSampleBuilder` /
  `ScriptEvolver` / `MiniSweAgentRunner`，evolve 后调 `native_tools.deploy` 刷新注册文件。
- cycle 1 可用 `--baseline-dir` 复用已有 T0（无脚本轨迹），跳过首轮 rollout。

```bash
python -m src.evolve.evolve_v5_cycle run \
    --benchmark deep-swe \
    --config _config/deepseekv4_flash.yaml \
    --eval-cases-file results/prep/handles/deep-swe/deepseek-v4-flash/eval_cases.txt \
    --scripts-dir .evolve_scripts_v5_deep-swe \
    --work-dir results/v5_cycle/deep-swe \
    --n-cycles 4

# 或经实验脚本
EVOLVE_VERSION=v5 BENCHMARK=deep-swe bash scripts/run_evolve_experiment.sh
```

每轮报告写到 `<work_dir>/v5_report.json`。
