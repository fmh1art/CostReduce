# Baseline 正式实验脚本

## 五 benchmark × 四 backbone 矩阵

`run_benchmark_matrix.sh` 是当前完整矩阵入口：

- benchmark：DeepSWE、SWE-Bench Verified、DAB、Terminal-Bench 2.1、
  DevEval；
- baseline：AgentDiet、EET 每 benchmark 最多 64 case（DevEval 全部 63
  case），ZipAct 各 16 case；
- backbone 最大并行度：DeepSeek V4 Flash=8、DeepSeek V4 Pro=8、
  Doubao Seed2 Lite=6、GPT-5.5=4；
- 同一 backbone 内的 Harbor job 串行运行，因此总并发不会超过该模型上限；
  四个 backbone 由 `all` 模式并行启动；
- case 使用稳定的 codebase-round-robin 多样性策略选取，清单和 provenance
  写入 `experiments/matrix64_v1/`。

启动完整矩阵：

```bash
cd baselines
./run_benchmark_matrix.sh all baseline-matrix64x16-YYYYMMDD-HHMMSS
```

检查状态：

```bash
./run_benchmark_matrix.sh status baseline-matrix64x16-YYYYMMDD-HHMMSS
```

只运行一个 backbone：

```bash
./run_benchmark_matrix.sh one \
  ../_config/deepseekv4_flash.yaml 8 \
  baseline-matrix64x16-YYYYMMDD-HHMMSS
```

共享计划、日志和状态写入：

```text
results/baselines/_matrix/<matrix-id>/
```

每个 Harbor job 及其中间结果仍按方法写入：

```text
results/baselines/{agentdiet,eet,zipact}/<matrix-id>-<model>-<method>-<benchmark>-n<N>/
```

DeepSWE 原任务保持不变。runner 在
`baselines/runtime_tasks/matrix64_v1/deep-swe/` 创建所选任务副本，只将
主 agent 环境改为可联网；独立 verifier 继续保持无网络。

## 固定 16-case SWE-Bench 实验

当前目录原先已有单 case 的 smoke runner，但没有满足“固定 16 个
SWE-Bench case、并行度 8、统一结果目录”的正式实验入口。现提供：

- `run_swebench16.sh`：推荐的 shell 入口，可运行单一方法或全部方法。
- `run_swebench16.py`：参数校验、task 筛选和 Harbor 命令构建。
- `experiments/swebench16_cases.txt`：固定的 16 case 清单。
- 各方法的 `harbor_experiment_config.yaml`：正式配置，不含 smoke 使用的
  12-step 上限。

## 默认运行

在项目根目录执行：

```bash
cd baselines
./run_swebench16.sh all
```

`all` 会按 AgentDiet、ZipAct、EET 顺序运行。每个方法内部同时运行
8 个 case，因此任一时刻的总并行度都是 8，不会变成 24。

2026-07-23 已按上述口径完成一次正式运行，job ID 为
`swebench16-doubao-seed2-lite-20260723-1652`。完整聚合结果和输出审计见
[`results/baselines/swebench16-doubao-seed2-lite-20260723-1652-summary.md`](../results/baselines/swebench16-doubao-seed2-lite-20260723-1652-summary.md)。

也可只运行一个方法：

```bash
cd baselines
./run_swebench16.sh agentdiet
./run_swebench16.sh zipact
./run_swebench16.sh eet
```

默认参数为：

```text
n_tasks       = 16
n_concurrent  = 8
llm_config    = ../_config/doubao_seed2_lite.yaml
output_root   = ../results/baselines
```

可先进行不调用模型的完整配置检查：

```bash
./run_swebench16.sh all --dry-run
```

也可覆盖参数：

```bash
./run_swebench16.sh zipact \
  --run-id zipact-swebench16-rerun \
  --n-concurrent 8 \
  --llm-config ../_config/doubao_seed2_lite.yaml \
  --output-root ../results/baselines
```

runner 不会覆盖已有同名 job；重跑时应使用新的 `--run-id`。参数说明可用：

```bash
baselines/envs/agentdiet/bin/python \
  baselines/run_swebench16.py --help
```

## 结果与中间文件

输出目录为：

```text
results/baselines/
├── agentdiet/<run-id>/
├── zipact/<run-id>/
└── eet/<run-id>/
```

每个 `<run-id>` 是完整的 Harbor job，包含：

- `config.json`：本次运行配置。
- `job.log`：job 级日志。
- `result.json`：聚合结果。
- 每个 trial 的 agent、verifier、异常、token/cost 和 patch 等中间文件。

固定清单沿用项目已有
`results/doubao_seed2_lite/evolve16_evalall/prep` 运行中的 16 个样本，
确保 baseline 与既有实验可以逐 case 对比。runner 会检查清单唯一性、
task 目录及 `task.toml`，并用 `--include-task-name` 显式传给 Harbor。

## 公平性口径

三个正式配置继续继承 mini-swe-agent 2.4.5 的 system prompt、instance
prompt、bash tool 协议、observation 格式及提交命令，没有覆盖 prompt。
差别只来自各 baseline 的 agent 实现和方法参数。AgentDiet、ZipAct 和
EET 分别使用自定义的对应 agent；不要求方法内部必须走原始
mini-swe-agent agent class。

smoke 配置中的 `step_limit: 12` 只用于快速连通性验证。正式配置不沿用
该 smoke 限制；AgentDiet 按发布的 SWE-bench runner 使用 50-turn
上限，ZipAct 使用其原始 50-step 上限，EET 则继承对应正式 agent 配置。
