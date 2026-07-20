import json
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent" / "mini-swe-agent" / "src"))

from minisweagent.extra.evolve_tools_v6 import registry  # noqa: E402
from src.evolve.native_tools_v6 import config_yaml_text  # noqa: E402


def _install_executor(tmp_path: Path, source: str, monkeypatch) -> None:
    executor = tmp_path / "executor.py"
    executor.write_text(source, encoding="utf-8")
    tools = tmp_path / "tools.json"
    tools.write_text(json.dumps([{"name": "demo", "parameters": {}}]), encoding="utf-8")
    monkeypatch.setenv("EVOLVE_TOOLS_V6_EXECUTOR", str(executor))
    monkeypatch.setenv("EVOLVE_TOOLS_V6_REGISTRY", str(tools))
    registry.reload()


def test_tool_timeout_is_observation_and_recommends_bash(tmp_path: Path, monkeypatch):
    _install_executor(tmp_path, """
import time
def run_tool(action, cwd=None, timeout=120):
    time.sleep(5)
    return {'output': 'late', 'returncode': 0, 'exception_info': ''}
""", monkeypatch)
    monkeypatch.setenv("EVOLVE_TOOLS_V6_TIMEOUT_SECONDS", "1")
    started = time.monotonic()
    result = registry.run_tool({"tool": "demo"}, timeout=20)
    assert time.monotonic() - started < 3
    assert result["returncode"] == 124
    assert "timed out" in result["output"]
    assert "bash" in result["output"]


def test_tool_exception_does_not_escape_agent(tmp_path: Path, monkeypatch):
    _install_executor(tmp_path, """
def run_tool(action, cwd=None, timeout=120):
    raise RuntimeError('broken evolved helper')
""", monkeypatch)
    result = registry.run_tool({"tool": "demo"}, timeout=2)
    assert result["returncode"] != 0
    assert "broken evolved helper" in result["output"]
    assert "bash" in result["output"]


def test_worker_sigkill_does_not_kill_agent(tmp_path: Path, monkeypatch):
    _install_executor(tmp_path, """
import os
import signal
def run_tool(action, cwd=None, timeout=120):
    os.kill(os.getpid(), signal.SIGKILL)
""", monkeypatch)
    result = registry.run_tool({"tool": "demo"}, timeout=2)
    assert result["returncode"] == 125
    assert "worker failed" in result["output"]
    assert "bash" in result["output"]


def test_tool_pagination_metadata_survives_worker(tmp_path: Path, monkeypatch):
    _install_executor(tmp_path, """
def run_tool(action, cwd=None, timeout=120):
    return {'output': 'page', 'returncode': 0, 'exception_info': '', 'next_offset': 4}
""", monkeypatch)
    result = registry.run_tool({"tool": "demo"}, timeout=2)
    assert result == {"output": "page", "returncode": 0, "exception_info": "", "next_offset": 4}


def test_worker_enforces_output_cap_without_inventing_cursor(tmp_path: Path, monkeypatch):
    _install_executor(tmp_path, """
def run_tool(action, cwd=None, timeout=120):
    return {'output': 'x' * 10000, 'returncode': 0, 'exception_info': ''}
""", monkeypatch)
    monkeypatch.setenv("EVOLVE_TOOLS_V6_OUTPUT_TOKENS", "100")
    result = registry.run_tool({"tool": "demo"}, timeout=2)
    assert len(result["output"]) <= 400
    assert result["output_truncated"] is True
    assert "narrow" in result["output"]
    assert "next_offset" not in result


def test_native_config_aligns_environment_timeout(monkeypatch):
    monkeypatch.setenv("EVOLVE_TOOLS_V6_TIMEOUT_SECONDS", "600")
    config = config_yaml_text("chat", container=True)
    assert "environment:\n  timeout: 600\n" in config


def test_native_config_clamps_timeout_to_dab_limit(monkeypatch):
    monkeypatch.setenv("EVOLVE_TOOLS_V6_TIMEOUT_SECONDS", "9999")
    config = config_yaml_text("chat", container=True)
    assert "environment:\n  timeout: 600\n" in config


def test_native_config_forwards_llm_runtime_controls():
    config = yaml.safe_load(
        config_yaml_text(
            "chat", temperature=1, thinking="disabled", container=True
        )
    )
    assert config["model"]["model_kwargs"] == {
        "temperature": 1.0,
        "extra_body": {"thinking": {"type": "disabled"}},
    }
