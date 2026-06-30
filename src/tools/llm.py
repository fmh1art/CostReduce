import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)


class LLM:
    def __init__(self, config_path):
        cfg = self._load_config(config_path)
        # api_type=responses: bytedance aidp 网关对这批 GPT 模型只暴露 Responses API
        # (chat-completions 路径全部 404)，走 AzureOpenAI + responses.create。
        self.api_type = (cfg.get("api_type") or "chat").strip().lower()
        self.model = cfg.get("llm_name") or cfg.get("model")
        self.temperature = float(cfg.get("temperature", 0))
        if self.api_type == "responses":
            from openai import AzureOpenAI

            self.client = AzureOpenAI(
                api_key=cfg.get("key"),
                azure_endpoint=cfg.get("azure_endpoint"),
                api_version=cfg.get("api_version") or "2024-03-01-preview",
            )
            self.max_output_tokens = int(cfg.get("max_output_tokens", 4096))
        else:
            from openai import OpenAI

            self.client = OpenAI(api_key=cfg.get("key"), base_url=cfg.get("openai_base_url"))

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
                rsp = self.client.chat.completions.create(
                    model=self.model,
                    temperature=self.temperature,
                    messages=messages,
                    timeout=self._timeout,
                )
                return rsp.choices[0].message.content
            except Exception as exc:
                last_exc = exc
                if not self._is_retryable(exc):
                    raise
                wait = self._backoff_seconds * (2 ** attempt)
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
                rsp = self.client.responses.create(
                    model=self.model,
                    input=inp,
                    instructions=system_prompt or None,
                    temperature=self.temperature,
                    max_output_tokens=self.max_output_tokens,
                    timeout=self._timeout,
                )
                return self._extract_responses_text(rsp)
            except Exception as exc:
                last_exc = exc
                if not self._is_retryable(exc):
                    raise
                wait = self._backoff_seconds * (2 ** attempt)
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
        return 1.0

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
