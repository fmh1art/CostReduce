# Baseline 复现说明

已在 Harbor 下完成以下三个 baseline 的适配。原有 3×3 单样例 smoke
矩阵之外，Terminal-Bench 2.1 与 DevEval 也已完成 3×2 单样例连通性
验证；SWE-Pruner 按任务说明未复现。

固定 16 个 SWE-Bench Verified case、并行度 8 的正式实验入口和参数说明
见 [`BASELINE_EXPERIMENTS.md`](BASELINE_EXPERIMENTS.md)。正式结果写入
项目根目录的 `results/baselines/{agentdiet,zipact,eet}/`。

- AgentDiet / Trajectory Reduction
- ZipAct
- EET

## 复现口径

三个方法统一使用：

- `_config/doubao_seed2_lite.yaml`
- `mini-swe-agent==2.4.5`
- mini-swe-agent 自带 `mini` 配置中的 system/instance prompt、bash tool
  协议、observation 格式和提交命令
- `step_limit: 12`，用于受控 smoke 验证
- 每个 benchmark 固定一个相同样例

各方法只替换 `agent_class` 并加入方法自身机制，没有在
`harbor_config.yaml` 中覆盖 prompt 模板：

| 方法 | Agent 实现 | 方法机制 |
| --- | --- | --- |
| AgentDiet | 自定义 mini-swe-agent agent | 延迟逐步压缩、上下文窗口、原论文接受规则 |
| ZipAct | ZipAct G-W-C 状态式 agent | initializer、memoryless actor、state updater |
| EET | EET 经验增强 agent | 官方经验库 TF-IDF 检索、经验注入、置信度控制 |

固定任务为：

- DeepSWE：`datacurve/anko-default-function-arguments`
- SWE-bench Verified：`swe-bench/swebench-verified__django__django-15382`
- DAB：`dab__github_repos__query3`
- Terminal-Bench 2.1：`fix-code-vulnerability`
- DevEval：`python-readtime-unit-testing`

DeepSWE 原任务把主环境设置为无网络，这会同时阻断 code agent
访问 LLM。runner 会在 `baselines/runtime_tasks/` 生成该任务的副本，
只把主/agent 环境改为可联网；独立 verifier 仍保持原始无网络设置。
源数据集不会被修改。

## 环境与运行

三个 conda prefix 都位于 `baselines/envs/`，不会污染项目环境：

```bash
cd baselines
./setup_envs.sh

./trajectory_reduction/run.sh deepswe
./trajectory_reduction/run.sh swe-bench
./trajectory_reduction/run.sh dab
./trajectory_reduction/run.sh terminal-bench-2.1 \
  --llm-config ../_config/deepseekv4_flash.yaml
./trajectory_reduction/run.sh deveval \
  --llm-config ../_config/deepseekv4_flash.yaml

./zipact/run.sh deepswe
./zipact/run.sh swe-bench
./zipact/run.sh dab
./zipact/run.sh terminal-bench-2.1 \
  --llm-config ../_config/deepseekv4_flash.yaml
./zipact/run.sh deveval \
  --llm-config ../_config/deepseekv4_flash.yaml

./eet/run.sh deepswe
./eet/run.sh swe-bench
./eet/run.sh dab
./eet/run.sh terminal-bench-2.1 \
  --llm-config ../_config/deepseekv4_flash.yaml
./eet/run.sh deveval \
  --llm-config ../_config/deepseekv4_flash.yaml
```

网络受限时可显式启用任务指定的代理：

```bash
BASELINE_PROXY=http://sys-proxy-rd-relay.byted.org:8118 ./setup_envs.sh
```

Harbor 任务镜像本身可能禁网，因此 runner 将对应的已固定 conda
环境以只读方式挂载到容器，避免容器运行时从 PyPI 安装依赖。
DevEval Hub 导出的 verifier 还会通过幂等兼容步骤设置 `TEST_DIR=/tests`
并优先使用已挂载的固定 `uv`；测试内容和 reward 逻辑不变。

## 新 benchmark 连通性结果

日期：2026-07-23。使用 `_config/deepseekv4_flash.yaml` 和 12-step smoke
配置；六项均 `n_completed_trials=1`、`n_errored_trials=0`，并生成有效
Harbor reward。

| 方法 | Terminal-Bench 2.1 | DevEval |
| --- | --- | --- |
| AgentDiet | 0.0 | 0.0 |
| ZipAct | 0.0 | 0.0 |
| EET | 0.0 | 0.0 |

这里验证的是 agent、环境、verifier 的调用链，0.0 不代表完整预算下的
方法效果。

## 正式 smoke 结果

日期：2026-07-23。九项任务均 `n_completed_trials=1`、
`n_errored_trials=0`、Harbor `Exceptions=0`。

| 方法 | DeepSWE | SWE-bench | DAB |
| --- | --- | --- | --- |
| AgentDiet | 0，8m04s | 0，2m49s | 0，3m44s |
| ZipAct | 0，4m54s | 0，4m41s | 0，4m59s |
| EET | 0，2m41s | 0，2m12s | 0，2m27s |

这里的 0 是 12-step smoke 的 task reward，不代表完整预算下的论文效果。
三项 DeepSWE 运行均保持原有测试 119/119 通过，但未在 12 步内通过新增
测试。正式做性能对比前，应把三个 `harbor_config.yaml` 的
`step_limit` 同步提高或移除，并使用全量、多 seed 评测。

正式结果目录：

- `trajectory_reduction/results/{deepswe,swe-bench,dab}/`
- `zipact/results/{deepswe,swe-bench,dab}/`
- `eet/results/{deepswe,swe-bench,dab}/`

新 benchmark 使用相同目录规则，benchmark 子目录分别是
`terminal-bench-2.1` 与 `deveval`。

此前用于定位安装、网络和长任务行为的 `r2`–`r5`/无上限结果仅是诊断
记录，不计入上表。正式 job 名均以 `smoke12` 结尾。

## 版本与来源

- AgentDiet artifact：
  `tmp/artifact.zip`，SHA-256
  `099d8e87df7be5b2b878d5ac9e06cc86fb827254cd6e7f394845650f9ee8aa5e`
- ZipAct：`f0258044c3be203d1e2edcb8d2559cbdf3c5de00`
- EET：`91d9936afaaead424c0f5ce90880905cfa81adf4`
- Harbor：本地 checkout `4c2c2d1413401952fc79ee959713f2e11403d9ad`
  （CLI 报告 0.14.0）
- Python：3.12.11

更细的实现与统计见各方法的 `HARBOR_REPRODUCTION.md`。
