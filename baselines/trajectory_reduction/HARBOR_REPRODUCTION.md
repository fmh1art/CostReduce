# AgentDiet / Trajectory Reduction

## 来源与适配

原始 artifact 位于 `original/artifact/`。适配器依据
`code/trae_agent/agents/traj_analyzer.py` 移植以下行为：

- `mode=ours`
- 500-token 分析阈值
- 前 1、后 2 步上下文
- 在目标步骤之后延迟 2 步执行压缩
- 压缩接受条件：
  `old-new >= 400` 或 `new < 0.8*old`
- 接受后用压缩内容替换活跃历史，并在结果轨迹中保留被删调用的 usage

压缩调用与 code-agent 调用使用同一豆包模型配置。基础代码 agent、
prompt、bash tool 和提交命令仍来自 mini-swe-agent 2.4.5。

关键文件：

- `harbor_agent/agentdiet_harbor/agent.py`
- `harbor_config.yaml`
- `run.sh`

## 运行

```bash
./trajectory_reduction/run.sh deepswe
./trajectory_reduction/run.sh swe-bench
./trajectory_reduction/run.sh dab
```

## smoke 统计

| Benchmark | Coding 调用 | 压缩分析 | 接受压缩 | 被压缩 tokens |
| --- | ---: | ---: | ---: | ---: |
| DeepSWE | 12 | 7 | 6 | 11,135 → 2,733 |
| SWE-bench | 12 | 3 | 2 | 1,573 → 155 |
| DAB | 12 | 3 | 3 | 6,460 → 1,390 |

三项均以 `LimitsExceeded` 正常结束，Harbor Exceptions=0。结果轨迹的
`info.agentdiet` 保存完整配置、压缩步骤和 usage 统计。

