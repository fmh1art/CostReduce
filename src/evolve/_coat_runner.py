"""Small, framework-private primitives used by :mod:`src.evolve.coat`.

Only the trajectory serializer and mini-swe-agent transport live here. COAT's
prompt, batching, gate, and evolution policy remain in the canonical
``coat.py`` module so this file cannot become a second evolution framework.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import List, Optional

from src.tools.llm import LLM

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


class TrajectorySerializer:
    """Render a trajectory dict as a human/agent-readable text block."""

    def __init__(self, max_observation_chars: int = 500, max_action_chars: int = 1000):
        self.max_observation_chars = int(max_observation_chars)
        self.max_action_chars = int(max_action_chars)

    def serialize(self, trajectory: dict) -> str:
        lines: List[str] = []
        step_index = 0
        for step in trajectory.get("steps", []):
            if not self._is_action_step(step):
                continue
            step_index += 1
            # 优先用 step 自带的 _display_index（原始 trajectory T 的 step index），
            # 否则回退到当前 block 内的位置计数。
            display_index = step.get("_display_index", step_index)
            lines += [
                f"\n#### Step {display_index}",
                "Action:",
                self._serialize_action(self._action_of(step)),
                "Observation:",
                self._serialize_observation(step.get("observation", "")),
            ]
        return "\n".join(lines)

    # ----- overridable building blocks -----

    @staticmethod
    def _is_action_step(step) -> bool:
        return bool(step.get("tool_calls") or "observation" in step or step.get("action"))

    @staticmethod
    def _action_of(step):
        return step.get("tool_calls") or step.get("action") or step.get("message") or ""

    def _serialize_action(self, action) -> str:
        if isinstance(action, list):
            lines = []
            for call in action:
                lines.append(f"- tool: {call.get('function_name', '')}")
                for key, value in (call.get("arguments") or {}).items():
                    lines.append(f"  {key}: {self._clip(value, self.max_action_chars)}")
            return "\n".join(lines)
        return self._clip(action, self.max_action_chars)

    def _serialize_observation(self, observation) -> str:
        if isinstance(observation, dict) and isinstance(observation.get("results"), list):
            results = observation["results"]
            # Give each result a fair share of the budget so a long first result
            # doesn't starve the rest.
            per_item = max(80, self.max_observation_chars // max(1, len(results)))
            text = "\n".join(self._format_result(item, per_item) for item in results)
        else:
            text = self._clip(observation, self.max_observation_chars)
        return self._clip(text, self.max_observation_chars)

    def _format_result(self, item, max_chars: Optional[int] = None) -> str:
        budget = max_chars if max_chars is not None else self.max_observation_chars
        content = item.get("content", item) if isinstance(item, dict) else item
        try:
            parsed = json.loads(content) if isinstance(content, str) else content
        except json.JSONDecodeError:
            parsed = content
        if not isinstance(parsed, dict):
            return self._clip(parsed, budget)
        parts = []
        if "returncode" in parsed:
            parts.append(f"returncode: {parsed.get('returncode')}")
        if parsed.get("output"):
            parts.append(f"output:\n{parsed.get('output')}")
        if parsed.get("exception_info"):
            parts.append(f"exception_info: {parsed.get('exception_info')}")
        return self._clip("\n".join(parts), budget)

    @staticmethod
    def _clip(value, max_chars: int) -> str:
        text = (
            value if isinstance(value, str)
            else json.dumps(value, ensure_ascii=False, default=str)
        )
        return text if len(text) <= max_chars else text[:max_chars] + "...<truncated>"


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


class AgentRunner:
    """Run an evolution agent on a prepared prompt. Override for new backends."""

    def run(self, prompt: str, prompt_path: Path, output_path: Path, cwd: Path) -> None:
        raise NotImplementedError


class MiniSweAgentRunner(AgentRunner):
    def __init__(self, mini_swe_agent_dir, llm_config, dry_run: bool = False, timeout: int = 3600):
        self.mini_swe_agent_dir = Path(mini_swe_agent_dir).resolve()
        self.llm_config = str(llm_config)
        self.dry_run = bool(dry_run)
        self.timeout = int(timeout)

    def run(self, prompt: str, prompt_path: Path, output_path: Path, cwd: Path) -> None:
        raise NotImplementedError(
            "the concrete COAT runner must pass the exact evolution prompt"
        )

    def _run_mini_swe(self, cmd: list, cwd: Path, env: dict) -> None:
        """跑 mini-swe-agent 子进程，stdout/stderr 实时流式回显到父进程 stderr，
        让 evolve 过程有可见进度（而非 capture_output 静默几分钟）。

        - Popen + 后台线程逐行 pump：边读边写 sys.stderr，同时累积用于失败 tail。
        - 主线程 ``proc.wait(timeout)`` 保住超时语义；超时则 kill 并抛 RuntimeError。
        - agent 的完整 trajectory 已由 mini-swe-agent 自己写到 ``--output`` 路径，
          这里流式输出的文本只用于实时进度 + 失败时的 tail 诊断。
        """
        logger.info("mini-swe-agent starting (cwd=%s, timeout=%ds)", cwd, self.timeout)
        # PYTHONUNBUFFERED=1：mini-swe-agent 是 Python，stdout 接 PIPE 时默认块缓冲，
        # 会让逐行流式退化成 4KB 一坨；强制 unbuffered 才能真正实时回显进度。
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            env={**env, "PYTHONUNBUFFERED": "1"},
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        captured: list = []

        def _pump() -> None:
            assert proc.stdout is not None
            for line in proc.stdout:
                captured.append(line)
                sys.stderr.write(line)
                sys.stderr.flush()

        reader = threading.Thread(target=_pump, daemon=True)
        reader.start()
        try:
            rc = proc.wait(timeout=self.timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=10)
            except Exception:
                pass
            reader.join(timeout=5)
            logger.error(
                "mini-swe-agent timed out after %ds (cwd=%s)", self.timeout, cwd
            )
            raise RuntimeError(f"mini-swe-agent timed out after {self.timeout}s")
        reader.join(timeout=5)
        out = "".join(captured)
        if rc != 0:
            logger.error(
                "mini-swe-agent failed (rc=%d, cwd=%s); streamed output above. tail:\n%s",
                rc,
                cwd,
                out[-2000:],
            )
            raise RuntimeError(f"mini-swe-agent exited with code {rc}")
        logger.info("mini-swe-agent finished (rc=0, cwd=%s)", cwd)

    def _load_llm_env(self):
        cfg = LLM._load_config(self.llm_config)
        api_type = (cfg.get("api_type") or "chat").strip().lower()
        if api_type not in {"chat", "azure_chat", "responses"}:
            raise ValueError(f"unsupported api_type={api_type!r}")
        raw_temperature = cfg.get("temperature")
        try:
            temperature = (
                float(raw_temperature) if raw_temperature not in (None, "") else None
            )
        except (TypeError, ValueError):
            temperature = None
        name = cfg.get("llm_name") or cfg.get("model")
        if api_type in {"azure_chat", "responses"}:
            azure_endpoint = cfg.get("azure_endpoint") or cfg.get("openai_base_url", "")
            env = {
                "AZURE_API_KEY": cfg.get("key", ""),
                "AZURE_API_BASE": azure_endpoint,
                "AZURE_API_VERSION": cfg.get("api_version") or "2024-03-01-preview",
                "MSWEA_COST_TRACKING": "ignore_errors",
                "MSWEA_MODEL_RETRY_WAIT_SECONDS": os.environ.get(
                    "MSWEA_MODEL_RETRY_WAIT_SECONDS", "60"
                ),
            }
            if "bytedance.net" in azure_endpoint:
                no_proxy = ".bytedance.net,bytedance.net,localhost,127.0.0.1,::1"
                env.update({"NO_PROXY": no_proxy, "no_proxy": no_proxy})
            model_class = "litellm_response" if api_type == "responses" else "litellm"
            return env, f"azure/{name}", temperature, model_class
        base_url = cfg.get("openai_base_url", "")
        env = {
            "OPENAI_API_KEY": cfg.get("key", ""),
            "OPENAI_BASE_URL": base_url,
            "OPENAI_API_BASE": base_url,
            "MSWEA_COST_TRACKING": "ignore_errors",
            "MSWEA_MODEL_RETRY_WAIT_SECONDS": os.environ.get(
                "MSWEA_MODEL_RETRY_WAIT_SECONDS", "60"
            ),
        }
        # COAT's evolve agent runs directly on the host.  ByteDance-internal
        # chat gateways must bypass the ambient public HTTP proxy just like
        # src.tools.llm and the benchmark containers do.
        if "bytedance.net" in base_url:
            no_proxy = ".bytedance.net,bytedance.net,localhost,127.0.0.1,::1"
            env.update({"NO_PROXY": no_proxy, "no_proxy": no_proxy})
        return env, f"openai/{name}", temperature, "litellm"

    def _load_thinking(self) -> Optional[str]:
        value = LLM._load_config(self.llm_config).get("thinking")
        thinking = str(value).strip().lower() if value not in (None, "") else None
        if thinking not in {None, "enabled", "disabled", "auto"}:
            raise ValueError(
                "thinking must be enabled, disabled, or auto "
                f"(got {thinking!r})"
            )
        return thinking
