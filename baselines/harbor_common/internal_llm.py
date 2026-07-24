"""LLM calls used internally by cost-reduction methods.

The public mini-swe-agent model interface always requests a bash tool call.  The
ZipAct state updater and AgentDiet trajectory compressor instead need a plain
text completion.  This module sends those calls through the same LiteLLM model
configuration as the code agent and records a mini-swe-compatible usage entry.
"""

from __future__ import annotations

import time
from typing import Any

import litellm


def plain_completion(
    model: Any,
    messages: list[dict[str, Any]],
    *,
    max_tokens: int,
    stop: str | None = None,
) -> tuple[str, dict[str, Any], float]:
    """Return text, a trajectory usage message, and calculated USD cost."""

    model_name = model.config.model_name
    kwargs = dict(model.config.model_kwargs)
    # The normal coding call gets a generous reasoning/tool-call budget.  The
    # method-internal JSON/compression calls need a smaller visible-text budget.
    kwargs.pop("max_completion_tokens", None)
    kwargs["max_tokens"] = max_tokens
    kwargs["temperature"] = 0.0
    if stop is not None:
        kwargs["stop"] = stop

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
            litellm.cost_calculator.completion_cost(response, model=model_name)
            or 0.0
        )
    except Exception:
        # The local Doubao endpoint is intentionally not registered in
        # LiteLLM's public pricing table. Token counts remain authoritative.
        cost = 0.0

    response_dict = response.model_dump()
    record = {
        "role": "assistant",
        "content": content,
        "extra": {
            "response": response_dict,
            "cost": cost,
            "timestamp": time.time(),
            "baseline_internal_call": True,
        },
    }
    return content, record, cost


def response_usage(record: dict[str, Any]) -> tuple[int, int]:
    """Extract input/output tokens from a mini-swe-compatible usage record."""

    response = record.get("extra", {}).get("response", {})
    usage = response.get("usage") or {}
    input_tokens = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
    output_tokens = (
        usage.get("completion_tokens") or usage.get("output_tokens") or 0
    )
    return int(input_tokens), int(output_tokens)
