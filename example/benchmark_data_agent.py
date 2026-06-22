#!/usr/bin/env python3
"""Run DataMind/LongDS data-agent evaluations."""

from __future__ import annotations

import argparse
import os
import shutil
import shlex
import subprocess
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATAMIND = ROOT / "benchmark" / "DataMind"
DSGYM = DATAMIND / "longds" / "DSGym"
DM_EVAL = DATAMIND / "eval" / "Datamind"
DM_ANALYSIS = DATAMIND / "eval" / "DataMind-Analysis"
DEFAULT_LLM_CONFIG = Path("/home/fanmeihao/projects/CostReduce/_config/deepseekv4_flash.yaml")
DEFAULT_PROXY = "http://sys-proxy-rd-relay.byted.org:8118"


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    # 选择要运行的数据类评测：LongDS、DataMind Python、DataMind SQL、Analysis 或全部。
    p.add_argument("--suite", choices=["longds", "datamind-python", "datamind-sql", "analysis", "all"], default="longds")
    # LLM 配置文件路径；默认读取 deepseekv4_flash.yaml 中的模型、key、base_url、temperature。
    p.add_argument("--llm-config", default=str(DEFAULT_LLM_CONFIG))
    # 容器/子进程访问外网时使用的代理；默认使用内网 HTTP proxy，传空字符串可关闭。
    p.add_argument("--proxy", default=DEFAULT_PROXY)
    # 覆盖配置文件中的模型名；LongDS/LiteLLM 推荐 provider/model 格式，如 openai/deepseek-v4-flash。
    p.add_argument("--model")
    # DataMind/DSGym 推理后端；常用 litellm，也可用 vllm/sglang。
    p.add_argument("--backend", default="litellm")
    # 不使用 uv 时的 conda 环境名；默认使用用户指定的 0324。
    p.add_argument("--conda-env", default="0324")
    # 对包含 pyproject.toml 的目录优先使用 uv run python。
    p.add_argument("--use-uv", action="store_true", default=True)
    # 禁用 uv，强制使用 conda run -n {conda-env} python。
    p.add_argument("--no-uv", dest="use_uv", action="store_false")
    # 结果输出根目录；实际目录为 {output_dir}/{suite}/{run_id}/...，其中 run_id 默认是 MM-dd HH-MM-SS。
    p.add_argument("--output-dir", default=str(ROOT / "results"))
    # 本次运行的时间/后缀；不传则自动生成 MM-dd HH-MM-SS，如 06-19 20-30-05。
    p.add_argument("--run-id")
    # LongDS 最多运行多少个任务目录；不传则运行全部。
    p.add_argument("--task-limit", type=int)
    # LongDS 每个任务最多运行多少轮对话；用于快速 smoke test。
    p.add_argument("--turn-limit", type=int)
    # LongDS 从 task_list.json 的第几个任务开始运行。
    p.add_argument("--start-index", type=int, default=0)
    # LongDS 每轮 agent 最大行动步数。
    p.add_argument("--max-steps", type=int, default=40)
    # 采样温度；默认从 llm-config 的 temperature 读取。
    p.add_argument("--temperature", type=float)
    # nucleus sampling 的 top_p 参数。
    p.add_argument("--top-p", type=float, default=1.0)
    # 覆盖 llm-config 中的 API key；不会要求用户 export。
    p.add_argument("--api-key")
    # 覆盖 llm-config 中的 OpenAI-compatible base_url。
    p.add_argument("--base-url")
    # LLM-as-judge 使用的模型名；默认 deepseek-v4-pro。
    p.add_argument("--judge-model", default=os.getenv("JUDGE_MODEL", "deepseek-v4-pro"))
    # 覆盖 judge API key；默认复用 --api-key / llm-config key。
    p.add_argument("--judge-api-key", default=os.getenv("JUDGE_API_KEY"))
    # 覆盖 judge base_url；默认复用 --base-url / llm-config openai_base_url。
    p.add_argument("--judge-base-url", default=os.getenv("JUDGE_BASE_URL"))
    # LongDS 代码执行 sandbox manager 地址。
    p.add_argument("--manager-url", default="http://localhost:5000")
    # DataMind Python/SQL eval 的 batch size。
    p.add_argument("--bs", type=int, default=5)
    # DataMind-Analysis 起始样本下标。
    p.add_argument("--bidx", default="0")
    # DataMind-Analysis 结束样本下标；None 表示不限制。
    p.add_argument("--eidx", default="None")
    # 准备运行环境：uv sync DSGym，并在 conda 环境中安装 DataMind requirements。
    p.add_argument("--prepare", action="store_true")
    # 只打印将要执行的命令，不真正运行 benchmark。
    p.add_argument("--dry-run", action="store_true")
    return p


def load_llm_config(path: str) -> dict[str, str]:
    config_path = Path(path).expanduser()
    if not config_path.exists():
        config_path = ROOT / "_config" / config_path.name
    data = {}
    for line in config_path.read_text().splitlines():
        if ":" in line and not line.startswith(" "):
            key, value = line.split(":", 1)
            data[key.strip()] = value.strip().strip('"\'')
    return data


def apply_llm_config(args: argparse.Namespace) -> None:
    config = load_llm_config(args.llm_config)
    args.model = args.model or f"openai/{config['llm_name']}"
    args.api_key = args.api_key or config["key"]
    args.base_url = args.base_url or config["openai_base_url"]
    args.judge_api_key = args.judge_api_key or args.api_key
    args.judge_base_url = args.judge_base_url or args.base_url
    args.temperature = args.temperature if args.temperature is not None else float(config.get("temperature", 0.0))


def run(cmd: list[str], cwd: Path, env: dict[str, str] | None = None, dry_run: bool = False) -> None:
    print("+", " ".join(shlex.quote(x) for x in cmd))
    if not dry_run:
        subprocess.run(cmd, cwd=cwd, env={**os.environ, **(env or {})}, check=True)


def py(args: argparse.Namespace, cwd: Path) -> list[str]:
    if args.use_uv and (cwd / "pyproject.toml").exists():
        return ["uv", "run", "python"]
    env_python = Path.home() / "anaconda3" / "envs" / args.conda_env / "bin" / "python"
    if env_python.exists():
        return [str(env_python)]
    conda = shutil.which("conda")
    if conda is None:
        candidate = Path(sys.executable).resolve().parents[1] / "bin" / "conda"
        if candidate.exists():
            conda = str(candidate)
    if conda is None:
        for candidate in (Path.home() / "anaconda3" / "bin" / "conda", Path.home() / "miniconda3" / "bin" / "conda"):
            if candidate.exists():
                conda = str(candidate)
                break
    if conda is None:
        raise FileNotFoundError("conda executable not found; pass --use-uv for uv projects or add conda to PATH")
    return [conda, "run", "-n", args.conda_env, "python"]


def env(args: argparse.Namespace) -> dict[str, str]:
    out = proxy_env(args.proxy)
    if args.api_key:
        out["OPENAI_API_KEY"] = args.api_key
    if args.base_url:
        out["OPENAI_BASE_URL"] = args.base_url
        out["OPENAI_API_BASE"] = args.base_url
    if args.judge_api_key:
        out["JUDGE_API_KEY"] = args.judge_api_key
    if args.judge_base_url:
        out["JUDGE_BASE_URL"] = args.judge_base_url
    if args.judge_model:
        out["JUDGE_MODEL"] = args.judge_model
    return out


def proxy_env(proxy: str | None) -> dict[str, str]:
    if not proxy:
        return {}
    return {
        "HTTP_PROXY": proxy,
        "HTTPS_PROXY": proxy,
        "http_proxy": proxy,
        "https_proxy": proxy,
        "NO_PROXY": "localhost,127.0.0.1,::1",
        "no_proxy": "localhost,127.0.0.1,::1",
        # uv 的大依赖下载在内网环境偶发较慢，放宽单请求超时避免 smoke test 卡在依赖同步阶段。
        "UV_HTTP_TIMEOUT": "300",
    }


def default_run_id() -> str:
    """Return the default run timestamp in the requested MM-dd HH-MM-SS format."""
    return datetime.now().strftime("%m%d-%H%M%S")


def suite_output_dir(args: argparse.Namespace, suite: str) -> Path:
    """Return {output_dir}/{suite}/{run_id} for this benchmark suite."""
    # Some suites run from nested benchmark directories.  Resolve relative
    # output dirs against the project root so all results land in one place.
    output_dir = Path(args.output_dir).expanduser()
    if not output_dir.is_absolute():
        output_dir = ROOT / output_dir
    return (output_dir / suite / args.run_id).resolve()


def prepare(args: argparse.Namespace) -> None:
    run(["uv", "sync"], cwd=DSGYM, dry_run=args.dry_run)
    run(["conda", "run", "-n", args.conda_env, "pip", "install", "-r", "requirements.txt"], cwd=DM_EVAL, dry_run=args.dry_run)
    run(["conda", "run", "-n", args.conda_env, "pip", "install", "-r", "requirements.txt"], cwd=DM_ANALYSIS, dry_run=args.dry_run)


def longds(args: argparse.Namespace) -> tuple[list[str], Path]:
    cmd = [
        *py(args, DSGYM),
        "examples/longds.py",
        "--dataset",
        "longds",
        "--model",
        args.model,
        "--backend",
        args.backend,
        "--output-dir",
        str(suite_output_dir(args, "longds")),
        "--start-index",
        str(args.start_index),
        "--max-steps",
        str(args.max_steps),
        "--temperature",
        str(args.temperature),
        "--manager-url",
        args.manager_url,
        "--judge-model",
        args.judge_model,
    ]
    if args.task_limit is not None:
        cmd += ["--task-limit", str(args.task_limit)]
    if args.turn_limit is not None:
        cmd += ["--turn-limit", str(args.turn_limit)]
    return cmd, DSGYM


def datamind_python(args: argparse.Namespace) -> tuple[list[str], Path]:
    py_root = DM_EVAL / "python"
    return [
        *py(args, py_root),
        "eval_python.py",
        "--model",
        args.model.split("/", 1)[-1],
        "--temperature",
        str(args.temperature),
        "--top_p",
        str(args.top_p),
        "--bs",
        str(args.bs),
        "--test_bench",
        "dabench",
        "--test_file",
        str(py_root / "test_file" / "daeval_test.parquet"),
        "--csv_or_db_folder",
        str(py_root / "da-dev-tables"),
    ], suite_output_dir(args, "datamind-python")


def datamind_sql(args: argparse.Namespace) -> tuple[list[str], Path]:
    sql_root = DM_EVAL / "sql"
    return [
        *py(args, sql_root),
        "eval_bird.py",
        "--model",
        args.model.split("/", 1)[-1],
        "--temperature",
        str(args.temperature),
        "--top_p",
        str(args.top_p),
        "--bs",
        str(args.bs),
        "--test_bench",
        "bird",
        "--test_file",
        str(sql_root / "bird" / "test_file" / "bird_dev.parquet"),
        "--csv_or_db_folder",
        str(sql_root / "bird" / "dev_sqlite_files"),
        "--gold_csv_results_dir",
        str(sql_root / "bird" / "bird_dev_csv_results"),
        "--db_schema_data_path",
        str(sql_root / "bird" / "bird_dev_omni_ddl.json"),
    ], suite_output_dir(args, "datamind-sql")


def analysis(args: argparse.Namespace) -> tuple[list[str], Path]:
    cmd = [
        *py(args, DM_ANALYSIS),
        "do_generate.py",
        "--model_name",
        args.model.split("/", 1)[-1],
        "--check_model",
        args.judge_model,
        "--output",
        str(suite_output_dir(args, "analysis")),
        "--api_port",
        os.getenv("DATAMIND_API_PORT", "8000"),
        "--temperature",
        str(args.temperature),
        "--top_p",
        str(args.top_p),
        "--dataset_name",
        "QRData",
        "--bidx",
        args.bidx,
    ]
    if args.eidx not in (None, "", "None", "none", "null"):
        cmd += ["--eidx", args.eidx]
    return cmd, DM_ANALYSIS


SUITES = {
    "longds": longds,
    "datamind-python": datamind_python,
    "datamind-sql": datamind_sql,
    "analysis": analysis,
}


def main() -> None:
    args = parser().parse_args()
    apply_llm_config(args)
    args.run_id = args.run_id or default_run_id()
    if args.prepare:
        prepare(args)
    suites = SUITES if args.suite == "all" else {args.suite: SUITES[args.suite]}
    for suite, build in suites.items():
        cmd, cwd = build(args)
        if suite in {"datamind-python", "datamind-sql"}:
            cwd.mkdir(parents=True, exist_ok=True)
            cwd = DM_EVAL / ("python" if suite == "datamind-python" else "sql")
        run(cmd, cwd=cwd, env=env(args), dry_run=args.dry_run)


if __name__ == "__main__":
    main()
