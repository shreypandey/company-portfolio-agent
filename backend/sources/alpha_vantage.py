"""Alpha Vantage — kept narrow: NEWS_SENTIMENT (unique value) + EARNINGS (backup).

Free tier: 25 calls/day, 5/min. Use sparingly.

Note: AV returns 200 with a "Note" or "Information" field for rate-limit responses,
which we map to RateLimitError so chains can fall back rather than retry.
"""
from __future__ import annotations

from typing import Any

from ..config import ALPHAVANTAGE_API_KEY
from ..context import RunContext
from . import _http
from .errors import RateLimitError, SourceError, SourceMiss

_BASE = "https://www.alphavantage.co/query"
_NAME = "alpha_vantage"


async def _get(ctx: RunContext, function: str, **params) -> dict:
    if not ALPHAVANTAGE_API_KEY:
        raise SourceError(_NAME, function, "no ALPHAVANTAGE_API_KEY configured")
    params["function"] = function
    params["apikey"] = ALPHAVANTAGE_API_KEY
    data = await _http.request(
        ctx,
        source=_NAME,
        endpoint=function.lower(),
        url=_BASE,
        params=params,
        timeout=30.0,
    )
    if not isinstance(data, dict):
        raise SourceError(_NAME, function.lower(), "unexpected non-dict response")
    # AV signals rate limit via Note/Information at HTTP 200
    note = data.get("Note") or data.get("Information")
    if note and ("call frequency" in note.lower() or "premium" in note.lower() or "rate" in note.lower()):
        raise RateLimitError(_NAME, function.lower(), note[:200])
    if "Error Message" in data:
        raise SourceError(_NAME, function.lower(), data["Error Message"])
    # AV returns an empty {} or near-empty when symbol is unrecognized
    if not data or list(data.keys()) == ["Information"]:
        raise SourceMiss(_NAME, function.lower(), "no data")
    return data


async def news_sentiment(ctx: RunContext, ticker: str, limit: int = 25) -> dict:
    return await _get(ctx, "NEWS_SENTIMENT", tickers=ticker, limit=limit)


async def earnings(ctx: RunContext, ticker: str) -> dict:
    return await _get(ctx, "EARNINGS", symbol=ticker)
