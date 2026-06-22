# Benchmark 测试命令

工作目录：

```bash
cd /home/fanmeihao/projects/CostReduce
```

默认配置：

- LLM 配置：`/home/fanmeihao/projects/CostReduce/_config/deepseekv4_flash.yaml`
- 默认代理：`http://sys-proxy-rd-relay.byted.org:8118`
- Code benchmark 输出：`results/{benchmark}/{time}/{case_id}`，`time` 默认格式为 `MMdd-HHMMSS`
- Data benchmark 输出：`results/{suite}/{time}`，`time` 默认格式为 `MMdd-HHMMSS`
- 以下命令默认用于 smoke test：每个 benchmark 尽量只测 1 个 case/最小批量

## 1. Code benchmark 测试命令

### 1.1 DeepSWE（Pier）

```bash
python example/benchmark_code_agent.py \
  --benchmark deep-swe \
  --n-tasks 1 \
  -n 1 \
  -k 1
```

### 1.2 SWE-Atlas QA（Harbor）

```bash
python example/benchmark_code_agent.py \
  --benchmark swe-atlas \
  --swe-atlas-splits qa \
  --n-tasks 1 \
  -n 1 \
  -k 1
```

### 1.3 SWE-Atlas TW（Harbor）

```bash
python example/benchmark_code_agent.py \
  --benchmark swe-atlas \
  --swe-atlas-splits tw \
  --n-tasks 1 \
  -n 1 \
  -k 1
```

### 1.4 SWE-Atlas RF（Harbor）

```bash
python example/benchmark_code_agent.py \
  --benchmark swe-atlas \
  --swe-atlas-splits rf \
  --n-tasks 1 \
  -n 1 \
  -k 1
```

### 1.5 一次运行全部 code benchmark（每类 1 个 case）

```bash
python example/benchmark_code_agent.py \
  --benchmark all \
  --swe-atlas-splits qa,tw,rf \
  --n-tasks 1 \
  -n 1 \
  -k 1
```

## 2. Data benchmark 测试命令

### 2.1 LongDS

```bash
python example/benchmark_data_agent.py \
  --suite longds \
  --task-limit 1 \
  --turn-limit 1
```

### 2.2 DataMind Python

```bash
python example/benchmark_data_agent.py \
  --suite datamind-python \
  --bs 1
```

说明：当前底层 `eval_python.py` 未暴露样本数限制参数，`--bs 1` 仅表示最小 batch/并发设置。

### 2.3 DataMind SQL

```bash
python example/benchmark_data_agent.py \
  --suite datamind-sql \
  --bs 1
```

说明：当前底层 `eval_bird.py` 未暴露样本数限制参数，`--bs 1` 仅表示最小 batch/并发设置。

### 2.4 DataMind Analysis

```bash
python example/benchmark_data_agent.py \
  --suite analysis \
  --bidx 0 \
  --eidx 1
```

### 2.5 一次运行全部 data benchmark

```bash
python example/benchmark_data_agent.py \
  --suite all \
  --task-limit 1 \
  --turn-limit 1 \
  --bs 1 \
  --bidx 0 \
  --eidx 1
```

## 3. 常用输出目录示例

### Code benchmark

```text
results/deep-swe/0619-203005/<case_id>
results/swe-atlas-qa/0619-203005/<case_id>
results/swe-atlas-tw/0619-203005/<case_id>
results/swe-atlas-rf/0619-203005/<case_id>
```

### Data benchmark

```text
results/longds/0619-203005
results/analysis/0619-203005
```

## 4. `benchmark_code_agent.py` 参数说明

| 参数 | 默认值 | 说明 |
|---|---:|---|
| `--benchmark` | `all` | 选择代码类 benchmark：`deep-swe`、`swe-atlas`、`all`。 |
| `--swe-atlas-splits` | `qa,tw,rf` | SWE-Atlas 子集，逗号分隔，可选 `qa`、`tw`、`rf`。 |
| `--agent` | `mini-swe-agent` | Harbor/Pier 中注册的 agent 名称。 |
| `--llm-config` | `_config/deepseekv4_flash.yaml` | LLM 配置文件路径。 |
| `--proxy` | `http://sys-proxy-rd-relay.byted.org:8118` | 容器和子进程访问外网时使用的代理；传空字符串可关闭。 |
| `--docker-build-proxy` | 开启 | 将 `--proxy` 写入临时 Docker config，使 docker build 阶段也走代理。 |
| `--no-docker-build-proxy` | 关闭项 | 禁用 Docker build 阶段代理写入。 |
| `--model` | config 中的 `llm_name` | 覆盖模型名，格式如 `openai/deepseek-v4-flash`。 |
| `--pier-model-class` | `litellm` | DeepSWE/Pier 传给 mini-swe-agent 的 `model_class`。 |
| `--env` | `docker` | Harbor/Pier 执行环境；本地建议 `docker`。 |
| `--jobs-dir` | `results` | Code benchmark 输出根目录。 |
| `--job-prefix` | `miniswe` | 兼容旧参数；当前默认目录布局不再依赖该前缀。 |
| `--run-id` | 自动时间戳 | 本次运行目录名；不传则自动生成 `MMdd-HHMMSS`。 |
| `-n`, `--n-concurrent` | `1` | 并发 trial 数量。 |
| `-k`, `--n-attempts` | `1` | 每个任务重复尝试次数。 |
| `--n-tasks` | 不限制 | 最多运行多少个任务。 |
| `--include-task` | 空 | 只运行匹配的任务名；可重复传入，支持 glob。 |
| `--reasoning-effort` | `high` | 传给 mini-swe-agent 的 reasoning effort。 |
| `--mini-config` | 空 | 覆盖 mini-swe-agent 配置文件；SWE-Atlas 默认使用 split 自带配置。 |
| `--agent-kwarg` | 空 | 额外传给 agent 的 `key=value` 参数；可重复传入。 |
| `--agent-env` | 空 | 额外传给 agent 的环境变量 `KEY=VALUE`；可重复传入。 |
| `--env-file` | 空 | Harbor/Pier 读取的 `.env` 文件路径。 |
| `--use-local-harbor` | 开启 | 优先使用仓库内 `tmp/harbor`。 |
| `--no-local-harbor` | 关闭项 | 不使用本地 `tmp/harbor`，改用 PATH 中的 `harbor`。 |
| `--prepare` | `False` | 准备运行环境：安装/同步 Harbor、Pier、mini-swe-agent。 |
| `--dry-run` | `False` | 只打印命令，不实际运行。 |

## 5. `benchmark_data_agent.py` 参数说明

| 参数 | 默认值 | 说明 |
|---|---:|---|
| `--suite` | `longds` | 选择数据类 benchmark：`longds`、`datamind-python`、`datamind-sql`、`analysis`、`all`。 |
| `--llm-config` | `_config/deepseekv4_flash.yaml` | LLM 配置文件路径。 |
| `--model` | config 中的 `llm_name` | 覆盖模型名，格式如 `openai/deepseek-v4-flash`。 |
| `--backend` | `litellm` | DataMind/DSGym 推理后端，如 `litellm`、`vllm`、`sglang`。 |
| `--conda-env` | `0324` | 不使用 uv 时的 conda 环境名。 |
| `--use-uv` | 开启 | 对包含 `pyproject.toml` 的目录优先使用 `uv run python`。 |
| `--no-uv` | 关闭项 | 禁用 uv，强制使用 `conda run -n {conda-env} python`。 |
| `--output-dir` | `results` | Data benchmark 输出根目录。 |
| `--run-id` | 自动时间戳 | 本次运行目录名；不传则自动生成 `MMdd-HHMMSS`。 |
| `--task-limit` | 不限制 | LongDS 最多运行多少个任务目录。 |
| `--turn-limit` | 不限制 | LongDS 每个任务最多运行多少轮对话。 |
| `--start-index` | `0` | LongDS 从 task list 的第几个任务开始。 |
| `--max-steps` | `40` | LongDS 每轮 agent 最大行动步数。 |
| `--temperature` | config 中的值 | 采样温度。 |
| `--top-p` | `1.0` | nucleus sampling 参数。 |
| `--api-key` | config 中的 key | 覆盖主模型 API key。 |
| `--base-url` | config 中的 base_url | 覆盖主模型 OpenAI-compatible base URL。 |
| `--judge-model` | `deepseek-v4-pro` | LLM-as-judge 使用的模型名。 |
| `--judge-api-key` | 复用主 key | 覆盖 judge API key。 |
| `--judge-base-url` | 复用主 base_url | 覆盖 judge base URL。 |
| `--manager-url` | `http://localhost:5000` | LongDS 代码执行 sandbox manager 地址。 |
| `--bs` | `5` | DataMind Python/SQL eval 的 batch size。 |
| `--bidx` | `0` | DataMind Analysis 起始样本下标。 |
| `--eidx` | `None` | DataMind Analysis 结束样本下标；`None` 表示不限制。 |
| `--prepare` | `False` | 准备运行环境：同步 DSGym，并安装 DataMind requirements。 |
| `--dry-run` | `False` | 只打印命令，不实际运行。 |

## 6. 调试命令

只打印 code benchmark 命令：

```bash
python example/benchmark_code_agent.py \
  --benchmark deep-swe \
  --n-tasks 1 \
  --dry-run
```

只打印 data benchmark 命令：

```bash
python example/benchmark_data_agent.py \
  --suite longds \
  --task-limit 1 \
  --turn-limit 1 \
  --dry-run
```
