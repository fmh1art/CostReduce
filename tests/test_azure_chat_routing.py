from __future__ import annotations

import importlib.util
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import openai
import yaml

from src.evolve._coat_runner import MiniSweAgentRunner
from src.evolve.native_tools_v6 import config_yaml_text
from src.tools.llm import LLM


ROOT = Path(__file__).resolve().parents[1]


def _write_azure_chat_config(
    tmp_path: Path, *, temperature: float | None = None, thinking: str | None = None
) -> Path:
    path = tmp_path / "azure-chat.yaml"
    path.write_text(
        "\n".join(
            [
                "api_type: azure_chat",
                "api_version: 2024-03-01-preview",
                "azure_endpoint: https://gateway.invalid/v2/crawl",
                "openai_base_url: https://gateway.invalid/v2/crawl",
                "key: test-key",
                "llm_name: test-gpt",
                *([] if temperature is None else [f"temperature: {temperature}"]),
                *([] if thinking is None else [f"thinking: {thinking}"]),
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def _write_internal_chat_config(tmp_path: Path) -> Path:
    path = tmp_path / "internal-chat.yaml"
    path.write_text(
        "\n".join(
            [
                "key: test-key",
                "llm_name: test-chat",
                "openai_base_url: https://ark-cn-beijing.bytedance.net/api/v3",
                "thinking: disabled",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def test_llm_azure_chat_uses_azure_client_and_omits_missing_temperature(
    tmp_path, monkeypatch
):
    config = _write_azure_chat_config(tmp_path)
    captured = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured["request"] = kwargs
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="OK"))]
            )

    class FakeAzureClient:
        def __init__(self, **kwargs):
            captured["client"] = kwargs
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(openai, "AzureOpenAI", FakeAzureClient)

    llm = LLM(config)
    assert llm.query("system", None, "user") == "OK"
    assert captured["client"]["azure_endpoint"] == "https://gateway.invalid/v2/crawl"
    assert captured["client"]["api_version"] == "2024-03-01-preview"
    assert captured["client"]["default_headers"]["X-TT-LOGID"].startswith(
        "optiharness-"
    )
    assert "temperature" not in captured["request"]


def test_llm_bypasses_ambient_proxy_only_for_internal_gateways():
    internal = LLM._internal_http_client(
        "https://aidp.bytedance.net/api/modelhub/online/v2/crawl"
    )
    try:
        assert internal is not None
        assert internal._trust_env is False
    finally:
        internal.close()
    assert LLM._internal_http_client("https://api.kimi.com/coding/v1") is None


def test_llm_forwards_temperature_and_thinking(tmp_path, monkeypatch):
    config = _write_azure_chat_config(
        tmp_path, temperature=1, thinking="disabled"
    )
    captured = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="OK"))]
            )

    class FakeAzureClient:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(openai, "AzureOpenAI", FakeAzureClient)

    assert LLM(config).query("system", None, "user") == "OK"
    assert captured["temperature"] == 1.0
    assert captured["extra_body"] == {"thinking": {"type": "disabled"}}


def test_evolve_runner_routes_azure_chat_to_litellm(tmp_path):
    config = _write_azure_chat_config(tmp_path)
    runner = MiniSweAgentRunner(tmp_path, config)

    env, model, temperature, model_class = runner._load_llm_env()

    assert model == "azure/test-gpt"
    assert model_class == "litellm"
    assert temperature is None
    assert env["AZURE_API_BASE"] == "https://gateway.invalid/v2/crawl"
    assert env["AZURE_API_VERSION"] == "2024-03-01-preview"
    assert env["MSWEA_COST_TRACKING"] == "ignore_errors"
    assert env["MSWEA_MODEL_RETRY_WAIT_SECONDS"] == "60"


def test_evolve_runner_bypasses_proxy_for_internal_chat_gateway(tmp_path):
    runner = MiniSweAgentRunner(tmp_path, _write_internal_chat_config(tmp_path))

    env, model, temperature, model_class = runner._load_llm_env()

    assert model == "openai/test-chat"
    assert model_class == "litellm"
    assert temperature is None
    assert ".bytedance.net" in env["NO_PROXY"]
    assert env["NO_PROXY"] == env["no_proxy"]
    assert runner._load_thinking() == "disabled"


def test_shell_loader_does_not_treat_azure_chat_as_responses(tmp_path):
    config = _write_azure_chat_config(
        tmp_path, temperature=1, thinking="disabled"
    )
    env = os.environ.copy()
    env.update(
        {
            "LLM_CONFIG": str(config),
            "ROOT_DIR": str(ROOT),
        }
    )
    result = subprocess.run(
        [
            "bash",
            "-c",
            "source scripts/_bench_common.sh; "
            "load_llm_config; "
            "resolved=$(mswea_llm_config_file \"\" litellm); "
            "printf '%s|%s|%s|%s|%s' \"$MODEL\" \"$LLM_API_TYPE\" \"$TEMPERATURE\" \"$LLM_THINKING\" \"$resolved\"",
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    model, api_type, temperature, thinking, resolved = result.stdout.split("|", 4)
    assert model == "azure/test-gpt"
    assert api_type == "azure_chat"
    assert temperature == "1"
    assert thinking == "disabled"
    merged = yaml.safe_load(Path(resolved).read_text(encoding="utf-8"))
    Path(resolved).unlink()
    assert merged["model"]["model_class"] == "litellm"
    assert merged["model"]["model_kwargs"] == {
        "temperature": 1.0,
        "extra_body": {"thinking": {"type": "disabled"}},
    }


def test_internal_llm_waits_sixty_seconds_after_retryable_api_error(
    tmp_path, monkeypatch
):
    config = _write_azure_chat_config(tmp_path)
    calls = 0
    sleeps = []

    class FakeCompletions:
        def create(self, **_kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("transient API failure")
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="OK"))]
            )

    class FakeAzureClient:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setenv("API_RETRY_PAUSE_SECONDS", "60")
    monkeypatch.setattr(openai, "AzureOpenAI", FakeAzureClient)
    monkeypatch.setattr(LLM, "_is_retryable", staticmethod(lambda _exc: True))
    monkeypatch.setattr(time, "sleep", sleeps.append)

    assert LLM(config).query("system", None, "user") == "OK"
    assert calls == 2
    assert sleeps == [60.0]


def test_container_retry_runtime_enforces_api_pause_only():
    runtime = ROOT / "src" / "tools" / "api_retry_runtime"
    code = r'''
from types import SimpleNamespace
from tenacity import wait_exponential

RateLimitError = type("RateLimitError", (Exception,), {"__module__": "openai"})

class Outcome:
    def __init__(self, exc): self._exc = exc
    def exception(self): return self._exc

def delay(exc):
    state = SimpleNamespace(attempt_number=1, outcome=Outcome(exc))
    return wait_exponential(multiplier=1, min=4, max=60)(state)

print(delay(RateLimitError("429")))
print(delay(ValueError("ordinary")))
'''
    env = os.environ.copy()
    env["PYTHONPATH"] = str(runtime)
    env["API_RETRY_PAUSE_SECONDS"] = "60"
    completed = subprocess.run(
        [sys.executable, "-c", code], env=env, text=True,
        capture_output=True, check=True,
    )
    assert completed.stdout.splitlines() == ["60.0", "4.0"]


def test_local_mini_swe_retry_honors_fixed_wait(monkeypatch):
    path = (
        ROOT / "agent" / "mini-swe-agent" / "src" / "minisweagent"
        / "models" / "utils" / "retry.py"
    )
    spec = importlib.util.spec_from_file_location("coat_mswea_retry", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    monkeypatch.setenv("MSWEA_MODEL_RETRY_WAIT_SECONDS", "60")
    spec.loader.exec_module(module)

    retrying = module.retry(logger=logging.getLogger("test"), abort_exceptions=[])
    assert retrying.wait(None) == 60.0


def test_benchmark_agent_mounts_container_retry_runtime():
    env = os.environ.copy()
    env.update({
        "ROOT_DIR": str(ROOT),
        "OPENAI_API_KEY": "test-key",
        "MSWEA_API_KEY": "test-key",
        "OPENAI_BASE_URL": "https://example.invalid/v1",
        "OPENAI_API_BASE": "https://example.invalid/v1",
        "EVOLVE_SCRIPTS_DIR": "",
        "EVOLVE_SCRIPTS_INCLUDE_DEFAULT_LOG_MOUNTS": "0",
    })
    completed = subprocess.run(
        [
            "bash", "-c",
            "source scripts/_bench_common.sh; "
            "evolve_scripts_mounts_json; printf '\\n--ENV--\\n'; agent_env_args",
        ],
        cwd=ROOT, env=env, text=True, capture_output=True, check=True,
    )
    mounts_text, agent_env = completed.stdout.split("\n--ENV--\n", 1)
    mounts = json.loads(mounts_text)
    assert any(
        mount["target"] == "/opt/optiharness_api_retry"
        and mount.get("read_only") is True
        for mount in mounts
    )
    assert "API_RETRY_PAUSE_SECONDS=60" in agent_env
    assert "MSWEA_MODEL_RETRY_WAIT_SECONDS=60" in agent_env
    assert "PYTHONPATH=/opt/optiharness_api_retry" in agent_env


def test_evolved_agent_merges_pythonpath_once():
    env = os.environ.copy()
    env.update({
        "ROOT_DIR": str(ROOT),
        "OPENAI_API_KEY": "test-key",
        "MSWEA_API_KEY": "test-key",
        "OPENAI_BASE_URL": "https://example.invalid/v1",
        "OPENAI_API_BASE": "https://example.invalid/v1",
        "EVOLVE_SCRIPTS_DIR": "/tmp/test-evolved-scripts",
        "EVOLVE_TOOLS_CONFIG_HOST": "/tmp/test-evolved-config.yaml",
    })
    completed = subprocess.run(
        [
            "bash", "-c",
            "source scripts/_bench_common.sh; "
            "evolve_scripts_native_tools_args; printf '\\n--ENV--\\n'; agent_env_args",
        ],
        cwd=ROOT, env=env, text=True, capture_output=True, check=True,
    )
    native_args, agent_env = completed.stdout.split("\n--ENV--\n", 1)
    combined = native_args + agent_env
    assert combined.count("PYTHONPATH=") == 1
    assert (
        "PYTHONPATH=/opt/optiharness_api_retry:"
        "/app/.preinstalled_scripts/.runtime"
    ) in agent_env


def test_deep_swe_disables_pier_auto_response_protocol():
    entry = (ROOT / "scripts" / "run_deep_swe.sh").read_text(encoding="utf-8")
    assert '--ak "model_class=${MSWEA_MODEL_CLASS}"' in entry
    assert '[[ "${LLM_API_TYPE:-chat}" == "responses" ]]' in entry


def test_native_tools_azure_chat_uses_chat_model_class():
    config = config_yaml_text("azure_chat", container=True)
    assert "LitellmModelWithEvolveToolsV6" in config
    assert "LitellmResponseModelWithEvolveToolsV6" not in config


def test_datamind_judge_azure_chat_omits_missing_temperature():
    judge_path = (
        ROOT
        / "tmp/harbor/adapters/longds/src/longds_adapter/task-template/tests/judge.py"
    )
    spec = importlib.util.spec_from_file_location("longds_judge", judge_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    captured = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="<score>1</score>")
                    )
                ]
            )

    client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    score = module.judge_one(
        "question", {"answer": 2}, "2", client, "test-gpt", "azure_chat", None,
        None,
    )

    assert score == 1
    assert "temperature" not in captured


def test_datamind_judge_forwards_thinking():
    judge_path = (
        ROOT
        / "tmp/harbor/adapters/longds/src/longds_adapter/task-template/tests/judge.py"
    )
    spec = importlib.util.spec_from_file_location("longds_judge_thinking", judge_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    captured = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="<score>1</score>"))]
            )

    client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    score = module.judge_one(
        "question", {"answer": 2}, "2", client, "test-model", "chat", 1.0,
        "disabled",
    )

    assert score == 1
    assert captured["temperature"] == 1.0
    assert captured["extra_body"] == {"thinking": {"type": "disabled"}}
