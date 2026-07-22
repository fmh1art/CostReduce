统计范围限定为当前主实验 `evolve16_evalall`：evolve 16 cases、2 轮 COAT、final eval 全量 cases。旧的 `eval64`、`evolve1_eval1` 连通性实验以及仍在运行的结果没有混入。

截至 2026-07-21 16:58，共有：

- 11 组完整的 evolve + final eval。
- 9 组完整、有效的 no-evolve 配对。
- 5 组虽然进程终止，但 100% API/环境失败，单独列为无效结果。

## 统计口径

Token 字段：

- 输入：API 记录的全部 input/prompt tokens。
- 缓存：输入 token 的子集，不应再与输入相加。
- 非缓存输入 = 输入 − 缓存。
- 输出：completion/output tokens。
- 表中单位均为百万 token，记作 M。

“Evolve 开销”包含：

- 两轮、每轮 16 cases 的 rollout。
- COAT 修改 agent 的 token。
- 不包含 final eval。

“Evolve 总流程”则为：

> rollout + COAT 修改 agent + evolved final eval

有一个重要限制：annotation 和 promotion judge 使用的 `src/tools/llm.py` 当前只返回文本，没有持久化 API usage。因此下面 evolve 总 token 是可审计下界，不包括 annotation/judge token。调用次数仍可以精确统计。

## Performance：SWE-Bench Verified 与 DAB

这里 SWE-Bench 指 resolved rate，DAB 指 accuracy。Δ 为 evolve − no-evolve，单位是百分点。

| LLM | Benchmark | Cases | Evolve performance | No-evolve performance | Δ | Evolve / No-evolve errors |
|---|---:|---:|---:|---:|---:|---:|
| DeepSeek V4 Flash | SWE-Bench | 500 | 64.00% | 68.20% | -4.20 pp | 48 / 43 |
| DeepSeek V4 Pro | SWE-Bench | 500 | 64.60% | 67.60% | -3.00 pp | 41 / 35 |
| Doubao Seed 2 Lite | SWE-Bench | 500 | 48.80% | 46.00% | **+2.80 pp** | 43 / 53 |
| GPT‑5.5 | SWE-Bench | 500 | 68.20% | **70.40%** | -2.20 pp | 41 / 44 |
| DeepSeek V4 Flash | DAB | 104 | 45.19% | 52.88% | -7.69 pp | 1 / 1 |
| DeepSeek V4 Pro | DAB | 104 | 50.00% | 50.96% | -0.96 pp | 1 / 0 |
| GPT‑5.5 | DAB | 104 | **55.77%** | 补跑中 | — | 1 / — |

主要结论：

- 当前 SWE-Bench 最好的是 GPT‑5.5 no-evolve：70.40%。
- SWE-Bench 上只有 Doubao Lite 的 evolve 带来提升，+2.80 pp。
- DAB 上已完成配对的 DeepSeek Flash/Pro 都没有从 evolve 获益。
- GPT‑5.5 DAB evolve 达到 55.77%；no-evolve 的修复后补跑目前为 68/104，不能提前比较。

## Performance：Deep-SWE

Deep-SWE 同时报告：

- `reward`：完全通过率。
- `partial`：综合部分得分。
- `F2P`：fail-to-pass 测试通过比例。
- `P2P`：原有 pass-to-pass 测试保持比例。

| LLM | Phase | Cases | Reward | Partial | F2P | P2P | Errors |
|---|---|---:|---:|---:|---:|---:|---:|
| DeepSeek V4 Flash | evolve | 113 | 6.19% | 84.52% | 63.96% | 94.00% | 1 |
| DeepSeek V4 Flash | no-evolve | 113 | 6.19% | 85.07% | 64.53% | 94.70% | 1 |
|  | Δ |  | 0.00 pp | -0.54 pp | -0.57 pp | -0.70 pp |  |
| DeepSeek V4 Pro | evolve | 113 | 4.42% | 85.81% | 65.62% | 96.36% | 0 |
| DeepSeek V4 Pro | no-evolve | 113 | 7.08% | 87.60% | 68.27% | 97.06% | 1 |
|  | Δ |  | -2.65 pp | -1.80 pp | -2.65 pp | -0.70 pp |  |
| Doubao Seed 2 Lite | evolve | 113 | 0.88% | 51.03% | 14.52% | 68.47% | 4 |
| Doubao Seed 2 Lite | no-evolve | 尚未完成 | — | — | — | — | — |
| GPT‑5.5 | evolve | 113 | 39.82% | 93.01% | 86.55% | 95.49% | 0 |
| GPT‑5.5 | no-evolve | 113 | **48.67%** | **95.48%** | **91.07%** | **97.85%** | 0 |
|  | Δ |  | -8.85 pp | -2.47 pp | -4.51 pp | -2.36 pp |  |

GPT‑5.5 在 Deep-SWE 上显著领先其他模型，但本次 evolve 后各项指标都低于 no-evolve。

## Token 消耗明细

每个单元格格式为：

> 输入 / 缓存 / 输出，单位 M token

| LLM | Benchmark | Evolve 开销 | Evolved final eval | Evolve 总流程（已记录部分） | No-evolve |
|---|---|---:|---:|---:|---:|
| DeepSeek V4 Flash | DAB | 50.911 / 49.266 / 1.123 | 173.296 / 169.590 / 2.464 | 224.207 / 218.856 / 3.587 | 254.280 / 249.280 / 2.963 |
| DeepSeek V4 Flash | Deep-SWE | 239.488 / 236.515 / 2.389 | 1061.554 / 1053.865 / 6.408 | 1301.042 / 1290.380 / 8.797 | 1114.500 / 1106.002 / 6.387 |
| DeepSeek V4 Flash | SWE-Bench | 52.296 / 50.732 / 1.047 | 727.791 / 716.828 / 8.055 | 780.086 / 767.560 / 9.102 | 692.102 / 679.893 / 8.110 |
| DeepSeek V4 Pro | DAB | 30.129 / 28.771 / 0.880 | 147.728 / 144.578 / 2.672 | 177.857 / 173.349 / 3.552 | 155.349 / 151.868 / 2.773 |
| DeepSeek V4 Pro | Deep-SWE | 226.759 / 223.951 / 2.052 | 763.492 / 756.669 / 4.683 | 990.251 / 980.620 / 6.735 | 978.035 / 969.565 / 5.468 |
| DeepSeek V4 Pro | SWE-Bench | 50.242 / 48.729 / 0.924 | 482.939 / 473.533 / 5.765 | 533.181 / 522.263 / 6.689 | 678.449 / 665.888 / 7.341 |
| Doubao Seed 2 Lite | Deep-SWE | 187.086 / 11.723 / 1.416 | 678.268 / 24.629 / 4.123 | 865.354 / 36.352 / 5.540 | 尚未完成 |
| Doubao Seed 2 Lite | SWE-Bench | 27.350 / 0.937 / 0.474 | 284.459 / 29.668 / 3.962 | 311.809 / 30.605 / 4.436 | 339.813 / 18.696 / 4.427 |
| GPT‑5.5 | DAB | 7.686 / 0.997 / 0.398 | 25.050 / 1.249 / 0.919 | 32.736 / 2.246 / 1.317 | 补跑中 |
| GPT‑5.5 | Deep-SWE | 55.234 / 7.101 / 0.965 | 240.126 / 20.254 / 2.788 | 295.360 / 27.355 / 3.753 | 259.250 / 41.579 / 3.127 |
| GPT‑5.5 | SWE-Bench | 11.517 / 0.781 / 0.453 | 127.839 / 10.924 / 3.405 | 139.356 / 11.704 / 3.858 | 211.163 / 28.418 / 4.003 |

观察：

- DeepSeek 系列缓存率极高，特别是 Deep-SWE，超过 98% 的输入 token 命中缓存。
- Doubao Lite 缓存率明显较低，因此输入 token 虽不一定最高，实际非缓存输入成本很高。
- GPT‑5.5 的 token 数明显较少，但配置单价高。
- DeepSeek Pro 的 SWE-Bench evolved final eval 比 no-evolve 少约 195.5M 输入 token，同时 performance 低 3 pp。
- GPT‑5.5 SWE-Bench evolved final eval 比 no-evolve 少约 83.3M 输入 token，performance 低 2.2 pp。
- Doubao Lite SWE-Bench evolve 同时减少 final-eval 输入 token、提高 2.8 pp，是目前唯一明显的正向组合。

## Evolve 内部调用与未记录 token

| LLM | Benchmark | COAT agent API calls | Annotation calls，token 未记录 | Judge calls，token 未记录 |
|---|---|---:|---:|---:|
| DeepSeek V4 Flash | DAB | 624 | 969 | 33 |
| DeepSeek V4 Flash | Deep-SWE | 814 | 3,065 | 43 |
| DeepSeek V4 Flash | SWE-Bench | 722 | 1,589 | 46 |
| DeepSeek V4 Pro | DAB | 536 | 693 | 26 |
| DeepSeek V4 Pro | Deep-SWE | 576 | 3,112 | 33 |
| DeepSeek V4 Pro | SWE-Bench | 625 | 1,492 | 35 |
| Doubao Seed 2 Lite | Deep-SWE | 841 | 3,031 | 30 |
| Doubao Seed 2 Lite | SWE-Bench | 760 | 1,158 | 22 |
| GPT‑5.5 | DAB | 182 | 334 | 41 |
| GPT‑5.5 | Deep-SWE | 306 | 1,168 | 47 |
| GPT‑5.5 | SWE-Bench | 240 | 500 | 49 |

这意味着 evolve 实际 token 一定高于前表。尤其 Deep-SWE annotation 调用达到 3,000 次左右，缺失量可能不可忽略。

## 按 config 单价估算的 API cost 下界

公式：

> `(非缓存输入 × input price + 缓存输入 × cached price + 输出 × output price) / 1M`

由于 annotation/judge token 未记录，Evolve 总流程成本是下界；no-evolve 成本则基本完整。

| LLM | Benchmark | Evolve 总流程成本下界 | No-evolve 成本 |
|---|---|---:|---:|
| DeepSeek V4 Flash | DAB | ¥16.90 | ¥15.91 |
| DeepSeek V4 Flash | Deep-SWE | ¥54.06 | ¥43.39 |
| DeepSeek V4 Flash | SWE-Bench | ¥46.08 | ¥42.03 |
| DeepSeek V4 Pro | DAB | ¥39.17 | ¥30.88 |
| DeepSeek V4 Pro | Deep-SWE | ¥93.82 | ¥82.46 |
| DeepSeek V4 Pro | SWE-Bench | ¥85.95 | ¥98.38 |
| Doubao Seed 2 Lite | Deep-SWE | ¥521.71 | 尚未完成 |
| Doubao Seed 2 Lite | SWE-Bench | ¥188.36 | ¥210.85 |
| GPT‑5.5 | DAB | ¥746.04 | 补跑中 |
| GPT‑5.5 | Deep-SWE | ¥5,790.14 | ¥4,749.27 |
| GPT‑5.5 | SWE-Bench | ¥2,962.25 | ¥4,112.01 |

## 已终止但无效的“完成”结果

以下结果没有纳入 performance/token 对比：

| LLM | Benchmark | Phase | Cases | API/环境失败 |
|---|---|---|---:|---:|
| GPT‑5.4 Pro | SWE-Bench | no-evolve | 500 | 500/500 |
| GPT‑5.4 Pro | Deep-SWE | no-evolve | 113 | 113/113 |
| GPT‑5.4 Pro | DAB | no-evolve | 104 | 104/104 |
| GPT‑5.3 Codex | SWE-Bench | no-evolve | 500 | 500/500 |
| GPT‑5.5 旧 DAB | no-evolve | 104 | 104/104 |

前四组主要是 AIDP endpoint 返回 unsupported/404；GPT‑5.5 旧 DAB 是此前 `$HOME/.local/bin/env` 缺失。它们的 LLM token 均为 0 或未记录，因此不能把对应的接近 0 performance 解释为模型能力。

尚未完成、也未纳入表格的包括：

- GPT‑5.5 DAB no-evolve 补跑：68/104，0 errors。
- Doubao Lite Deep-SWE no-evolve：39/113，0 errors。
- O3 Pro SWE-Bench no-evolve：87/500。
- GPT‑5 Mini SWE-Bench no-evolve：347/500。
- GPT‑5.3 Codex Deep-SWE：28/113，28 个均为 API 失败。