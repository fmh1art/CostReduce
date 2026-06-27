import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)


class LLM:
    def __init__(self, config_path):
        cfg = self._load_config(config_path)
        from openai import OpenAI

        self.model = cfg.get("llm_name") or cfg.get("model")
        self.temperature = float(cfg.get("temperature", 0))
        self.client = OpenAI(api_key=cfg.get("key"), base_url=cfg.get("openai_base_url"))

    def query(self, system_prompt, history, user_prompt):
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
