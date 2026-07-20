import logging
import os
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class LLM:
    def __init__(self, config_path):
        cfg = self._load_config(config_path)
        self.api_type = (cfg.get("api_type") or "chat").strip().lower()
        if self.api_type not in {"chat", "azure_chat", "responses"}:
            raise ValueError(
                f"unsupported api_type={self.api_type!r}; expected chat, azure_chat, or responses"
            )
        self.model = cfg.get("llm_name") or cfg.get("model")
        raw_retry_pause = cfg.get(
            "api_retry_pause_seconds",
            os.getenv("API_RETRY_PAUSE_SECONDS", "60"),
        )
        try:
            self.retry_pause_seconds = max(0.0, float(raw_retry_pause))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "api_retry_pause_seconds must be a non-negative number"
            ) from exc
        raw_temperature = cfg.get("temperature")
        self.temperature = (
            float(raw_temperature) if raw_temperature not in (None, "") else None
        )
        raw_thinking = cfg.get("thinking")
        self.thinking = (
            str(raw_thinking).strip().lower()
            if raw_thinking not in (None, "")
            else None
        )
        if self.thinking not in {None, "enabled", "disabled", "auto"}:
            raise ValueError(
                "thinking must be enabled, disabled, or auto "
                f"(got {self.thinking!r})"
            )
        if self.api_type in {"azure_chat", "responses"}:
            from openai import AzureOpenAI

            azure_endpoint = cfg.get("azure_endpoint") or cfg.get("openai_base_url")
            if not azure_endpoint:
                raise ValueError(f"api_type={self.api_type} requires azure_endpoint")
            default_headers = None
            if self.api_type == "azure_chat":
                # AIDP accepts requests without this header, but attaching a unique
                # log id makes failed calls traceable on the gateway side.
                default_headers = {
                    "X-TT-LOGID": f"optiharness-{uuid.uuid4().hex}"
                }
            client_kwargs = dict(
                api_key=cfg.get("key"),
                azure_endpoint=azure_endpoint,
                api_version=cfg.get("api_version") or "2024-03-01-preview",
                default_headers=default_headers,
            )
            http_client = self._internal_http_client(azure_endpoint)
            if http_client is not None:
                client_kwargs["http_client"] = http_client
            self.client = AzureOpenAI(**client_kwargs)
            if self.api_type == "responses":
                self.max_output_tokens = int(cfg.get("max_output_tokens", 4096))
        else:
            from openai import OpenAI

            base_url = cfg.get("openai_base_url")
            client_kwargs = {"api_key": cfg.get("key"), "base_url": base_url}
            http_client = self._internal_http_client(base_url)
            if http_client is not None:
                client_kwargs["http_client"] = http_client
            self.client = OpenAI(**client_kwargs)

    @staticmethod
    def _internal_http_client(endpoint):
        """Bypass ambient proxies for ByteDance-internal model gateways.

        Experiment shells intentionally use a proxy for public endpoints such
        as Kimi.  ByteDance gateways are reachable only on the internal route;
        relying on process-global ``NO_PROXY`` made direct COAT annotation fail
        even when the same config worked inside the benchmark container.
        """
        host = (urlparse(str(endpoint or "")).hostname or "").lower()
        if host == "bytedance.net" or host.endswith(".bytedance.net"):
            import httpx

            return httpx.Client(trust_env=False)
        return None

    def query(self, system_prompt, history, user_prompt):
        if self.api_type == "responses":
            return self._query_responses(system_prompt, history, user_prompt)
        messages = [
            {"role": "system", "content": system_prompt},
        ]
        if history:
            messages.append(
                {"role": "user", "content": f"{history}\n\n{user_prompt}"}
            )
        else:
            messages.append({"role": "user", "content": user_prompt})

        last_exc = None
        for attempt in range(self._max_retries):
            try:
                kwargs = dict(
                    model=self.model,
                    messages=messages,
                    timeout=self._timeout,
                )
                if self.temperature is not None:
                    kwargs["temperature"] = self.temperature
                if self.thinking is not None:
                    kwargs["extra_body"] = {
                        "thinking": {"type": self.thinking}
                    }
                rsp = self.client.chat.completions.create(**kwargs)
                return rsp.choices[0].message.content
            except Exception as exc:
                last_exc = exc
                if not self._is_retryable(exc):
                    raise
                if attempt + 1 >= self._max_retries:
                    break
                wait = self._backoff_seconds
                logger.warning(
                    "LLM call failed (attempt %d/%d): %s; retrying in %.1fs",
                    attempt + 1,
                    self._max_retries,
                    exc,
                    wait,
                )
                time.sleep(wait)
        raise RuntimeError(f"LLM call failed after {self._max_retries} retries") from last_exc

    def _query_responses(self, system_prompt, history, user_prompt):
        text = f"{history}\n\n{user_prompt}" if history else user_prompt
        inp = [{"role": "user", "content": [{"type": "input_text", "text": text}]}]
        last_exc = None
        for attempt in range(self._max_retries):
            try:
                kwargs = dict(
                    model=self.model,
                    input=inp,
                    instructions=system_prompt or None,
                    max_output_tokens=self.max_output_tokens,
                    timeout=self._timeout,
                )
                if self.temperature is not None:
                    kwargs["temperature"] = self.temperature
                if self.thinking is not None:
                    kwargs["extra_body"] = {
                        "thinking": {"type": self.thinking}
                    }
                rsp = self.client.responses.create(**kwargs)
                return self._extract_responses_text(rsp)
            except Exception as exc:
                last_exc = exc
                if not self._is_retryable(exc):
                    raise
                if attempt + 1 >= self._max_retries:
                    break
                wait = self._backoff_seconds
                logger.warning(
                    "LLM responses call failed (attempt %d/%d): %s; retrying in %.1fs",
                    attempt + 1,
                    self._max_retries,
                    exc,
                    wait,
                )
                time.sleep(wait)
        raise RuntimeError(f"LLM responses call failed after {self._max_retries} retries") from last_exc

    @staticmethod
    def _extract_responses_text(rsp):
        # 优先用 SDK 的 output_text 便捷属性；没有就扫 output 里 message 项的文本。
        txt = getattr(rsp, "output_text", None)
        if txt:
            return txt
        for item in getattr(rsp, "output", []) or []:
            if getattr(item, "type", None) != "message":
                continue
            for part in getattr(item, "content", []) or []:
                t = getattr(part, "text", None)
                if t:
                    return t
        raise RuntimeError(f"responses API returned no text: {rsp!r}")

    @property
    def _max_retries(self) -> int:
        return 4

    @property
    def _backoff_seconds(self) -> float:
        return self.retry_pause_seconds

    @property
    def _timeout(self) -> float:
        return 120.0

    @staticmethod
    def _is_retryable(exc) -> bool:
        from openai import APIError, RateLimitError, APITimeoutError, APIConnectionError

        if isinstance(exc, (RateLimitError, APITimeoutError, APIConnectionError)):
            return True
        if isinstance(exc, APIError):
            status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
            if isinstance(status, int) and 500 <= status < 600:
                return True
        return False

    @staticmethod
    def _load_config(path):
        text = Path(path).read_text(encoding="utf-8")
        try:
            import yaml

            return yaml.safe_load(text) or {}
        except Exception:
            cfg = {}
            for line in text.splitlines():
                line = line.split("#", 1)[0].strip()
                if ":" in line and not line.startswith("-"):
                    k, v = line.split(":", 1)
                    cfg[k.strip()] = v.strip().strip('"\'')
            return cfg
