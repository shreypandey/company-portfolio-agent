"""Shared HTTP helper for source wrappers.

Every source uses `request()` so we get uniform:
  - cache lookup (cross-tool dedupe per run)
  - retry on transient HTTP (429/5xx, network, timeout) with exponential backoff
  - status-aware error mapping (404 → SourceMiss; 429 → RateLimitError; rest → SourceError)
  - cache write on success
"""
from __future__ import annotations

import asyncio
import json as _json
import logging
from typing import Any

import httpx

from ..context import RunContext
from .errors import RateLimitError, SourceError, SourceMiss

log = logging.getLogger("agent.sources")

_TRANSIENT_HTTP = {408, 425, 500, 502, 503, 504, 522, 524, 529}


async def request(
    ctx: RunContext,
    *,
    source: str,
    endpoint: str,
    url: str,
    method: str = "GET",
    params: dict | None = None,
    headers: dict | None = None,
    json: Any = None,
    timeout: float = 20.0,
    attempts: int = 2,
    treat_404_as_miss: bool = True,
) -> Any:
    """Fetch + parse JSON with cache, retry, and typed error mapping."""

    cache_key = ctx.cache_key(source, endpoint, url=url, params=tuple(sorted((params or {}).items())))
    if cache_key in ctx.cache:
        await ctx.emit("source_cache_hit", {"source": source, "endpoint": endpoint})
        return ctx.cache[cache_key]

    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            r = await ctx.http.request(
                method,
                url,
                params=params,
                headers=headers,
                json=json,
                timeout=timeout,
            )
        except httpx.TimeoutException as e:
            last_exc = SourceError(source, endpoint, "timeout", retriable=True)
            log.warning("source=%s endpoint=%s timeout attempt=%d", source, endpoint, attempt)
        except httpx.NetworkError as e:
            last_exc = SourceError(source, endpoint, f"network: {e}", retriable=True)
            log.warning("source=%s endpoint=%s network attempt=%d err=%s", source, endpoint, attempt, e)
        else:
            # Status handling
            if r.status_code == 404 and treat_404_as_miss:
                raise SourceMiss(source, endpoint, "not found", status=404)
            if r.status_code == 429:
                last_exc = RateLimitError(source, endpoint, r.reason_phrase or "rate limited")
            elif r.status_code in _TRANSIENT_HTTP:
                last_exc = SourceError(source, endpoint, r.reason_phrase, status=r.status_code, retriable=True)
            elif r.status_code >= 400:
                # Try to extract a useful error message from the body
                msg = r.reason_phrase
                try:
                    body = r.json()
                    if isinstance(body, dict):
                        msg = body.get("error") or body.get("message") or body.get("Error Message") or msg
                except Exception:
                    pass
                raise SourceError(source, endpoint, msg, status=r.status_code)
            else:
                # Success
                try:
                    data = r.json()
                except Exception:
                    raise SourceError(source, endpoint, "invalid json response")
                ctx.cache[cache_key] = data
                return data

        # backoff before retry
        if attempt < attempts:
            await asyncio.sleep(1.5 * attempt)

    assert last_exc is not None
    raise last_exc


async def request_text(
    ctx: RunContext,
    *,
    source: str,
    endpoint: str,
    url: str,
    headers: dict | None = None,
    timeout: float = 25.0,
) -> tuple[str, int]:
    """For HTML/text fetches (page scraping). Returns (text, status_code)."""
    try:
        r = await ctx.http.get(url, headers=headers, timeout=timeout, follow_redirects=True)
    except httpx.TimeoutException:
        raise SourceError(source, endpoint, "timeout", retriable=True)
    except httpx.NetworkError as e:
        raise SourceError(source, endpoint, f"network: {e}", retriable=True)
    if r.status_code == 404:
        raise SourceMiss(source, endpoint, "not found", status=404)
    if r.status_code >= 400:
        raise SourceError(source, endpoint, r.reason_phrase, status=r.status_code)
    return r.text, r.status_code
