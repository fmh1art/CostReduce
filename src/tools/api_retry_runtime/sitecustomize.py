"""Enforce a minimum pause for transient API retries in agent containers.

Harbor installs mini-swe-agent inside each task container, so editing the host
copy of mini-swe-agent alone cannot change its Tenacity retry policy.  Python
loads this module automatically through ``PYTHONPATH``.  The patch is narrow:
only waits whose current exception looks like a transient model/API transport
failure are raised to ``API_RETRY_PAUSE_SECONDS`` (60 seconds by default).
"""

from __future__ import annotations

import os
from typing import Any


def _pause_seconds() -> float:
    raw = os.getenv("API_RETRY_PAUSE_SECONDS", "60")
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return 60.0


def _exception_from_state(retry_state: Any) -> BaseException | None:
    outcome = getattr(retry_state, "outcome", None)
    if outcome is None:
        return None
    try:
        return outcome.exception()
    except Exception:
        return None


def _is_transient_api_exception(exc: BaseException | None) -> bool:
    if exc is None:
        return False
    name = type(exc).__name__.lower()
    module = type(exc).__module__.lower()
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if isinstance(status, int) and (status == 429 or 500 <= status < 600):
        return True
    transient_names = (
        "ratelimit",
        "apiconnection",
        "apitimeout",
        "internalserver",
        "serviceunavailable",
        "connecterror",
        "connecttimeout",
        "readtimeout",
        "writetimeout",
        "pooltimeout",
    )
    return any(token in name for token in transient_names) and any(
        token in module for token in ("openai", "litellm", "httpx", "httpcore")
    )


try:
    from tenacity.wait import wait_exponential
except ImportError:
    wait_exponential = None


if wait_exponential is not None and not getattr(
    wait_exponential, "_optiharness_api_pause_patched", False
):
    _original_call = wait_exponential.__call__

    def _call_with_api_pause(self, retry_state):
        delay = _original_call(self, retry_state)
        if _is_transient_api_exception(_exception_from_state(retry_state)):
            return max(float(delay), _pause_seconds())
        return delay

    wait_exponential.__call__ = _call_with_api_pause
    wait_exponential._optiharness_api_pause_patched = True
