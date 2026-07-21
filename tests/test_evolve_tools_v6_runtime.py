import json
import sys
import time
from pathlib import Path

import yaml
from jinja2 import StrictUndefined, Template

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent" / "mini-swe-agent" / "src"))

from minisweagent.extra.evolve_tools_v6 import registry  # noqa: E402
from src.evolve.native_tools_v6 import config_yaml_text, write_config  # noqa: E402


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


def test_native_config_separates_policy_from_task():
    instruction = "Use {{ literal_braces }} and {% raw-looking syntax %} literally."
    config = yaml.safe_load(
        config_yaml_text("chat", evolve_instruction=instruction, container=True)
    )
    system_prompt = config["agent"]["system_template"]
    user_prompt = config["agent"]["instance_template"]
    format_error = config["model"]["format_error_template"]

    assert "## Recommended workflow" in system_prompt
    assert "Bash is available as a general fallback" in system_prompt
    assert user_prompt.strip() == "## Task\n\n{{ task }}"
    assert "must include AT LEAST ONE bash" not in system_prompt
    assert "any available tool" in format_error
    rendered = Template(system_prompt, undefined=StrictUndefined).render(
        system="Linux", release="test", version="1", machine="x86_64"
    )
    assert instruction in rendered


def test_write_config_embeds_instruction_in_system_prompt(tmp_path: Path):
    instruction = "First line.\nSecond line with {{ braces }}."
    (tmp_path / "instruction.md").write_text(instruction, encoding="utf-8")

    config = yaml.safe_load(write_config(tmp_path).read_text(encoding="utf-8"))
    system_prompt = Template(
        config["agent"]["system_template"], undefined=StrictUndefined
    ).render(system="Linux", release="test", version="1", machine="x86_64")

    assert instruction in system_prompt
    assert config["agent"]["instance_template"].strip() == "## Task\n\n{{ task }}"
