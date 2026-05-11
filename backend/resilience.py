"""Shared resilience primitives for tools and external API calls.

Every tool is wrapped with `@tool_resilient` which guarantees:
  - hard timeout
  - retry with exponential backoff + jitter on transient errors (network, 5xx, 429)
  - structured `{"error": ..., "hint": ...}` returned to the LLM on final failure
    instead of an exception, so the agent loop keeps running and the model can pivot.
"""
from __future__ import annotations

import asyncio
import functools
import logging
from typing import Any, Awaitable, Callable

import httpx
from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from .config import DEFAULT_RETRY_ATTEMPTS, DEFAULT_TOOL_TIMEOUT

log = logging.getLogger("agent.resilience")

_TRANSIENT_HTTP = {408, 425, 429, 500, 502, 503, 504, 522, 524, 529}


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _TRANSIENT_HTTP
    return False


def tool_resilient(
    name: str,
    timeout: float = DEFAULT_TOOL_TIMEOUT,
    attempts: int = DEFAULT_RETRY_ATTEMPTS,
    fallback_hint: str = "",
) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[dict]]]:
    """Wrap an async tool function. Always returns a dict; never raises."""

    def decorator(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[dict]]:
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs) -> dict:
            attempt_num = 0
            try:
                async for attempt in AsyncRetrying(
                    stop=stop_after_attempt(attempts),
                    wait=wait_exponential_jitter(initial=1.0, max=8.0),
                    retry=retry_if_exception(_is_transient),
                    reraise=True,
                ):
                    with attempt:
                        attempt_num = attempt.retry_state.attempt_number
                        if attempt_num > 1:
                            log.warning("tool=%s retry attempt=%d", name, attempt_num)
                        result = await asyncio.wait_for(fn(*args, **kwargs), timeout=timeout)
                        return result if isinstance(result, dict) else {"data": result}
            except asyncio.TimeoutError:
                log.error("tool=%s timeout after %.1fs (attempts=%d)", name, timeout, attempt_num)
                return {
                    "error": f"tool '{name}' timed out after {timeout:.0f}s",
                    "hint": fallback_hint or "Try a different tool or a more specific query.",
                }
            except httpx.HTTPStatusError as e:
                log.error("tool=%s http_error status=%d", name, e.response.status_code)
                return {
                    "error": f"http {e.response.status_code} from {name}",
                    "status": e.response.status_code,
                    "hint": fallback_hint or "Source may be down — try an alternative.",
                }
            except RetryError as e:  # pragma: no cover — defensive
                inner = e.last_attempt.exception() if e.last_attempt else e
                return {"error": f"{type(inner).__name__}: {inner}", "hint": fallback_hint}
            except Exception as e:
                log.exception("tool=%s unexpected error", name)
                return {"error": f"{type(e).__name__}: {e}", "hint": fallback_hint}

        wrapper.__tool_name__ = name  # type: ignore[attr-defined]
        return wrapper

    return decorator


