#!/usr/bin/env python3
"""Run code-agent benchmarks with a shared Harbor interface."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HARBOR_ROOT = ROOT / "tmp" / "harbor"
SWE_ATLAS = ROOT / "benchmark" / "SWE-Atlas"
DEFAULT_LLM_CONFIG = Path("/home/fanmeihao/projects/CostReduce/_config/deepseekv4_flash.yaml")
DEFAULT_PROXY = "http://sys-proxy-rd-relay.byted.org:8118"
DOCKER_PROXY_CONFIG_DIR = ROOT / "tmp" / "docker-config-proxy"

CODE_BENCHMARKS = {
    "deep-swe": ROOT / "benchmark" / "deep-swe" / "tasks",
    "swe-atlas-qa": SWE_ATLAS / "data" / "qa",
    "swe-atlas-tw": SWE_ATLAS / "data" / "tw",
    "swe-atlas-rf": SWE_ATLAS / "data" / "rf",
}


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    # 选择要运行的代码类 benchmark：deep-swe、swe-atlas 或全部。
    p.add_argument("--benchmark", choices=["deep-swe", "swe-atlas", "all"], default="all")
    # SWE-Atlas 子集列表，逗号分隔；可选 qa（代码问答）、tw（测试编写）、rf（重构）。
    p.add_argument("--swe-atlas-splits", default="qa,tw,rf")
    # Harbor 中注册的 agent 名称；默认测评 mini-swe-agent。
    p.add_argument("--agent", default="mini-swe-agent")
    # LLM 配置文件路径；默认读取 deepseekv4_flash.yaml 中的模型、key、base_url、temperature。
    p.add_argument("--llm-config", default=str(DEFAULT_LLM_CONFIG))
    # 容器/子进程访问外网时使用的代理；默认使用内网 HTTP proxy，传空字符串可关闭。
    p.add_argument("--proxy", default=DEFAULT_PROXY)
    # 是否把 --proxy 写入临时 Docker config，使 docker build 阶段也能通过代理下载 uv、pip 包等依赖。
    p.add_argument("--docker-build-proxy", dest="docker_build_proxy", action="store_true", default=True)
    # 禁用 Docker build 阶段代理写入；仅在 Docker daemon 已有全局代理配置时使用。
    p.add_argument("--no-docker-build-proxy", dest="docker_build_proxy", action="store_false")
    # 本地 Docker 运行前探测 daemon 是否还能分配 bridge 网络；可提前给出清晰修复建议。
    p.add_argument("--docker-network-check", dest="docker_network_check", action="store_true", default=True)
    # 跳过 Docker bridge 地址池探测；仅在确定 Docker 网络已正常或不使用本地 docker 环境时使用。
    p.add_argument("--no-docker-network-check", dest="docker_network_check", action="store_false")
    # 覆盖配置文件中的模型名；Harbor/mini-swe-agent 需要 provider/model 格式，如 openai/deepseek-v4-flash。
    p.add_argument("--model")
    # DeepSWE/Pier 传给 mini-swe-agent 的 model_class；默认用 LiteLLM Chat Completions，避免 OpenAI-compatible DeepSeek 被 Pier 自动切到 /responses 接口。
    p.add_argument("--pier-model-class", default="litellm")
    # Harbor 执行环境；默认 docker（本地，不需要 Modal 登录），如需云端并发可显式传 --env modal。
    p.add_argument("--env", default=os.getenv("HARBOR_ENV", "docker"))
    # 结果输出根目录；实际目录为 {jobs_dir}/{benchmark}/{run_id}/{case_id}，其中 run_id 默认是 MM-dd HH-MM-SS。
    p.add_argument("--jobs-dir", default=str(ROOT / "results"))
    # 兼容旧命令的 job 前缀参数；当前默认布局下不再拼到目录名中。
    p.add_argument("--job-prefix", default="miniswe")
    # 本次运行的时间/后缀；不传则自动生成 MM-dd HH-MM-SS，如 06-19 20-30-05。
    p.add_argument("--run-id")
    # 并发 trial 数量；本地资源不足时保持 1。
    p.add_argument("-n", "--n-concurrent", type=int, default=1)
    # 每个任务重复尝试次数，用于 pass@k 或多次采样。
    p.add_argument("-k", "--n-attempts", type=int, default=1)
    # 最多运行多少个任务；不传则运行选中 benchmark 的全部任务。
    p.add_argument("--n-tasks", type=int)
    # 只运行匹配的任务名；可重复传入，支持 Harbor 的 glob 过滤。
    p.add_argument("--include-task", action="append", default=[])
    # 传给 mini-swe-agent 的 reasoning effort；对支持 reasoning 参数的模型生效。
    p.add_argument("--reasoning-effort", default="high")
    # 覆盖 mini-swe-agent 配置文件；默认 SWE-Atlas 使用各 split 自带 mswea_*.yaml。
    p.add_argument("--mini-config", help="Override mini-swe-agent config for every run")
    # 额外传给 Harbor agent 的 key=value 参数；会转换为 harbor run --ak。
    p.add_argument("--agent-kwarg", action="append", default=[], help="Extra Harbor --ak key=value")
    # 额外传给 agent 容器/进程的环境变量 KEY=VALUE；会转换为 harbor run --ae。
    p.add_argument("--agent-env", action="append", default=[], help="Extra Harbor --ae KEY=VALUE")
    # Harbor 读取的 .env 文件路径；用于非 LLM 的额外运行变量。
    p.add_argument("--env-file")
    # 优先使用仓库内 tmp/harbor，通过 uv run --directory 调用。
    p.add_argument("--use-local-harbor", action="store_true", default=True)
    # 不使用本地 tmp/harbor，改为调用 PATH 中的 harbor 命令。
    p.add_argument("--no-local-harbor", dest="use_local_harbor", action="store_false")
    # 准备运行环境：同步 Harbor 依赖，并用 uv tool 安装本地 mini-swe-agent。
    p.add_argument("--prepare", action="store_true", help="Install Harbor and the local mini-swe-agent with uv")
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


def apply_llm_config(args: argparse.Namespace) -> dict[str, str]:
    config = load_llm_config(args.llm_config)
    args.model = args.model or f"openai/{config['llm_name']}"
    env = {
        "OPENAI_API_KEY": config["key"],
        "OPENAI_BASE_URL": config["openai_base_url"],
        "OPENAI_API_BASE": config["openai_base_url"],
        "EVAL_MODEL": args.model.split("/", 1)[-1],
    }
    env.update(proxy_env(args.proxy))
    return env


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
    }


def docker_proxy_config_env(proxy: str | None, enabled: bool) -> dict[str, str]:
    """Create a Docker client config that applies proxy settings to builds.

    Pier/Harbor pass ``--ae`` variables to the agent process, but the failing
    DeepSWE step happens earlier during ``docker compose build``. Docker only
    forwards proxy variables to Dockerfile ``RUN`` steps reliably through the
    client ``config.json`` proxy section or explicit build args, while Pier does
    not expose build args. Keeping this config under ``tmp/`` avoids changing
    the user's global ``~/.docker/config.json`` and makes every task build use
    the same proxy behavior.
    """
    if not proxy or not enabled:
        return {}

    source_config = Path(os.environ.get("DOCKER_CONFIG", Path.home() / ".docker")) / "config.json"
    config: dict = {}
    if source_config.exists():
        try:
            # 只继承非敏感 Docker 客户端配置；不要把 auths 等凭据复制到项目 tmp 目录。
            source_data = json.loads(source_config.read_text())
            config = {key: value for key, value in source_data.items() if key != "auths"}
        except json.JSONDecodeError:
            config = {}

    config.setdefault("proxies", {})["default"] = {
        "httpProxy": proxy,
        "httpsProxy": proxy,
        "noProxy": "localhost,127.0.0.1,::1",
    }
    DOCKER_PROXY_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    (DOCKER_PROXY_CONFIG_DIR / "config.json").write_text(json.dumps(config, indent=2))
    return {"DOCKER_CONFIG": str(DOCKER_PROXY_CONFIG_DIR)}


def run(cmd: list[str], cwd: Path = ROOT, env: dict[str, str] | None = None, dry_run: bool = False) -> None:
    print("+", " ".join(shlex.quote(x) for x in cmd))
    if not dry_run:
        subprocess.run(cmd, cwd=cwd, env={**os.environ, **(env or {})}, check=True)


def check_docker_network_pool(args: argparse.Namespace) -> None:
    """Fail early when Docker cannot allocate a new compose bridge network."""
    if args.dry_run or not args.docker_network_check or args.env != "docker":
        return

    network_name = f"costreduce-network-check-{os.getpid()}"
    result = subprocess.run(
        ["docker", "network", "create", network_name],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if result.returncode == 0:
        subprocess.run(
            ["docker", "network", "rm", network_name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return

    output = (result.stdout or "").strip()
    if "could not find an available, non-overlapping IPv4 address pool" not in output:
        raise RuntimeError(f"Docker network preflight failed: {output}")

    raise RuntimeError(
        "Docker 无法创建新的 bridge 网络，Harbor 的 docker compose 任务会在启动环境时失败。\n"
        f"Docker 输出: {output}\n\n"
        "常见原因是旧 benchmark 容器/compose 网络未清理，占满了 daemon 默认地址池。\n"
        "如果确认没有正在运行的 benchmark，可先执行：\n"
        "  docker ps -a --format '{{.Names}}' | grep -E '^(task-|ipython-session-).*-(main|pier-egress-proxy)-1$' | xargs -r docker rm -f\n"
        "  docker network prune -f\n"
        "长期建议在 /etc/docker/daemon.json 配置更大的 default-address-pools 后重启 Docker。\n"
        "若已确认 Docker 网络正常，可加 --no-docker-network-check 跳过此探测。"
    )


def harbor_cmd(args: argparse.Namespace) -> list[str]:
    if args.use_local_harbor and HARBOR_ROOT.exists():
        return ["uv", "run", "--directory", str(HARBOR_ROOT), "harbor"]
    return ["harbor"]


def pier_cmd() -> list[str]:
    return ["uv", "tool", "run", "--from", "datacurve-pier", "pier"]


def selected_benchmarks(args: argparse.Namespace) -> list[tuple[str, Path]]:
    if args.benchmark == "deep-swe":
        return [("deep-swe", CODE_BENCHMARKS["deep-swe"])]
    splits = [s.strip() for s in args.swe_atlas_splits.split(",") if s.strip()]
    atlas = [(f"swe-atlas-{s}", CODE_BENCHMARKS[f"swe-atlas-{s}"]) for s in splits]
    return atlas if args.benchmark == "swe-atlas" else [("deep-swe", CODE_BENCHMARKS["deep-swe"]), *atlas]


def mini_config_for(name: str, args: argparse.Namespace) -> str | None:
    if args.mini_config:
        return str(Path(args.mini_config).resolve())
    if name.startswith("swe-atlas-"):
        split = name.rsplit("-", 1)[1]
        return str((SWE_ATLAS / "run_config" / split / f"mswea_{split}_config.yaml").resolve())
    return None


def add_common_options(cmd: list[str], args: argparse.Namespace, *, add_proxy_agent_env: bool = True) -> list[str]:
    if args.env_file:
        cmd += ["--env-file", args.env_file]
    if args.n_tasks:
        cmd += ["--n-tasks", str(args.n_tasks)]
    for task in args.include_task:
        cmd += ["--include-task-name", task]
    # Harbor's mini-swe-agent adapter maps reasoning_effort on openai/* models to
    # LiteLLM's Responses API. DeepSeek's OpenAI-compatible endpoint only exposes
    # Chat Completions, so passing reasoning_effort makes smoke runs fail with
    # `404 Not Found: /responses`. Pier/DeepSWE is unaffected because it uses a
    # separate model_class override.
    if args.reasoning_effort and not args.model.startswith("openai/deepseek"):
        cmd += ["--ak", f"reasoning_effort={args.reasoning_effort}"]
    for item in args.agent_kwarg:
        cmd += ["--ak", item]
    for item in args.agent_env:
        cmd += ["--ae", item]
    if add_proxy_agent_env:
        for key, value in proxy_env(args.proxy).items():
            cmd += ["--ae", f"{key}={value}"]
    return cmd


def has_agent_kwarg(args: argparse.Namespace, key: str) -> bool:
    prefix = f"{key}="
    return any(item.startswith(prefix) for item in args.agent_kwarg)


def default_run_id() -> str:
    """Return the default run timestamp in the requested MM-dd HH-MM-SS format."""
    return datetime.now().strftime("%m%d-%H%M%S")


def benchmark_jobs_dir(name: str, args: argparse.Namespace) -> str:
    """Return the parent dir so Harbor/Pier writes {benchmark}/{run_id}/{case_id}."""
    # Harbor is invoked with ``uv run --directory tmp/harbor`` for local source
    # usage, so a relative ``-o results/...`` would otherwise be interpreted
    # under ``tmp/harbor``.  Resolve against the project root to keep all
    # benchmark outputs under the requested CostReduce results directory.
    jobs_dir = Path(args.jobs_dir).expanduser()
    if not jobs_dir.is_absolute():
        jobs_dir = ROOT / jobs_dir
    return str((jobs_dir / name).resolve())


def job_name(args: argparse.Namespace) -> str:
    """Use the run timestamp as the job directory name."""
    return args.run_id


def build_harbor_run(name: str, path: Path, args: argparse.Namespace) -> list[str]:
    if not path.exists():
        raise FileNotFoundError(path)
    cmd = [
        *harbor_cmd(args),
        "run",
        "-p",
        str(path),
        "-a",
        args.agent,
        "-m",
        args.model,
        "-e",
        args.env,
        "-k",
        str(args.n_attempts),
        "-n",
        str(args.n_concurrent),
        "-o",
        benchmark_jobs_dir(name, args),
        "--job-name",
        job_name(args),
        "--yes",
    ]
    config = mini_config_for(name, args)
    if config:
        cmd += ["--ak", f"config_file={config}"]
    return add_common_options(cmd, args)


def build_pier_run(name: str, path: Path, args: argparse.Namespace) -> list[str]:
    if not path.exists():
        raise FileNotFoundError(path)
    cmd = [
        *pier_cmd(),
        "run",
        "-p",
        str(path),
        "-a",
        args.agent,
        "-m",
        args.model,
        "-e",
        args.env,
        "-k",
        str(args.n_attempts),
        "-n",
        str(args.n_concurrent),
        "-o",
        benchmark_jobs_dir(name, args),
        "--job-name",
        job_name(args),
    ]
    if args.pier_model_class and not has_agent_kwarg(args, "model_class"):
        cmd += ["--ak", f"model_class={args.pier_model_class}"]
    # DeepSWE 的 allow_internet=false 任务由 Pier 生成内部 egress-proxy。
    # 这里不能再把外部 sys-proxy 注入为 agent 的 HTTP_PROXY，否则运行期会覆盖
    # Pier 的 egress-proxy，导致 main 容器在 internal network 内访问不到外部代理。
    # Docker build 阶段的联网需求由 docker_proxy_config_env() 通过 DOCKER_CONFIG 解决。
    return add_common_options(cmd, args, add_proxy_agent_env=False)


def build_run(name: str, path: Path, args: argparse.Namespace) -> list[str]:
    if name == "deep-swe":
        return build_pier_run(name, path, args)
    return build_harbor_run(name, path, args)


def prepare(args: argparse.Namespace) -> None:
    if HARBOR_ROOT.exists():
        run(["uv", "sync"], cwd=HARBOR_ROOT, env=proxy_env(args.proxy), dry_run=args.dry_run)
    else:
        run(["uv", "tool", "install", "harbor"], env=proxy_env(args.proxy), dry_run=args.dry_run)
    run(["uv", "tool", "install", "--force", str(ROOT / "agent" / "mini-swe-agent")], env=proxy_env(args.proxy), dry_run=args.dry_run)
    run(["uv", "tool", "install", "datacurve-pier"], env=proxy_env(args.proxy), dry_run=args.dry_run)


def main() -> None:
    args = parser().parse_args()
    args.run_id = args.run_id or default_run_id()
    llm_env = apply_llm_config(args)
    llm_env.update(docker_proxy_config_env(args.proxy, args.docker_build_proxy))
    check_docker_network_pool(args)
    if args.prepare:
        prepare(args)
    for name, path in selected_benchmarks(args):
        run(build_run(name, path, args), env=llm_env, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
