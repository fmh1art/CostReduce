"""Plain-text LLM calls for fixed baseline adapters.

The normal mini-swe-agent call needs reasoning plus a bash tool call.  Baseline
internal calls instead need short visible text (an XML summary or JSON state).
Keep those two protocols separate and retain every response for cost auditing.
"""

from __future__ import annotations

import copy
import time
from typing import Any

import litellm


def _internal_kwargs(
    model: Any,
    *,
    max_tokens: int,
    stop: str | None,
) -> dict[str, Any]:
    kwargs = copy.deepcopy(dict(model.config.model_kwargs))
    kwargs.pop("max_completion_tokens", None)
    kwargs["max_tokens"] = max_tokens
    kwargs["temperature"] = 0.0
    if stop is not None:
        kwargs["stop"] = stop

    # OpenAI-compatible Doubao/DeepSeek endpoints accept this switch.  Without
    # it, a small internal-call budget can be consumed entirely by hidden
    # reasoning, leaving no JSON/summary in message.content.
    model_name = str(model.config.model_name)
    if model_name.startswith("openai/"):
        extra_body = copy.deepcopy(kwargs.get("extra_body") or {})
        extra_body["thinking"] = {"type": "disabled"}
        kwargs["extra_body"] = extra_body
    return kwargs


def plain_completion(
    model: Any,
    messages: list[dict[str, Any]],
    *,
    max_tokens: int,
    stop: str | None = None,
) -> tuple[str, dict[str, Any], float]:
    """Return visible text, an auditable usage record, and provider cost."""

    model_name = model.config.model_name
    kwargs = _internal_kwargs(
        model,
        max_tokens=max_tokens,
        stop=stop,
    )
    prepared = messages
    prepare = getattr(model, "_prepare_messages_for_api", None)
    if callable(prepare):
        prepared = prepare(messages)

    response = litellm.completion(
        model=model_name,
        messages=prepared,
        **kwargs,
    )
    content = response.choices[0].message.content or ""

    cost = 0.0
    try:
        cost = float(
            litellm.cost_calculator.completion_cost(
                response,
                model=model_name,
            )
            or 0.0
        )
    except Exception:
        # Local endpoints are not necessarily present in LiteLLM's price map.
        # Token counts in the response remain the authoritative cost source.
        cost = 0.0

    record = {
        "role": "assistant",
        "content": content,
        "extra": {
            "response": response.model_dump(),
            "cost": cost,
            "timestamp": time.time(),
            "baseline_internal_call": True,
            "internal_protocol": "visible_text_no_reasoning_v2",
        },
    }
    return content, record, cost


def response_usage(record: dict[str, Any]) -> tuple[int, int]:
    response = record.get("extra", {}).get("response", {})
    usage = response.get("usage") or {}
    input_tokens = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
    output_tokens = (
        usage.get("completion_tokens") or usage.get("output_tokens") or 0
    )
    return int(input_tokens), int(output_tokens)


def response_finish_reason(record: dict[str, Any]) -> str | None:
    response = record.get("extra", {}).get("response", {})
    choices = response.get("choices") or []
    if not choices:
        return None
    value = choices[0].get("finish_reason")
    return str(value) if value is not None else None
