"""FRED (Federal Reserve Economic Data) — macro context for US-co. narrative.

Unlimited, free. We expose two primitives:
  - series_info: title/description of a series
  - series_observations: time-series values for a series

Higher up, the `get_macro_context` semantic tool uses a sector→series mapping to
pull a small bundle (e.g. for retail: CPI, retail sales, unemployment).
"""
from __future__ import annotations

from typing import Any

from ..config import FRED_API_KEY
from ..context import RunContext
from . import _http
from .errors import SourceError, SourceMiss

_BASE = "https://api.stlouisfed.org/fred"
_NAME = "fred"


async def _get(ctx: RunContext, endpoint: str, **params) -> dict:
    if not FRED_API_KEY:
        raise SourceError(_NAME, endpoint, "no FRED_API_KEY configured")
    params["api_key"] = FRED_API_KEY
    params["file_type"] = "json"
    data = await _http.request(
        ctx,
        source=_NAME,
        endpoint=endpoint.replace("/", "_"),
        url=f"{_BASE}/{endpoint}",
        params=params,
        timeout=15.0,
    )
    if not isinstance(data, dict):
        raise SourceError(_NAME, endpoint, "non-dict response")
    if "error_message" in data:
        raise SourceError(_NAME, endpoint, data["error_message"])
    return data


async def series_info(ctx: RunContext, series_id: str) -> dict:
    data = await _get(ctx, "series", series_id=series_id)
    series = data.get("seriess") or []
    if not series:
        raise SourceMiss(_NAME, "series", f"no info for {series_id}")
    return series[0]


async def series_observations(
    ctx: RunContext,
    series_id: str,
    limit: int = 24,
    sort_order: str = "desc",
) -> list[dict]:
    data = await _get(
        ctx,
        "series/observations",
        series_id=series_id,
        limit=limit,
        sort_order=sort_order,
    )
    obs = data.get("observations") or []
    if not obs:
        raise SourceMiss(_NAME, "series/observations", f"no observations for {series_id}")
    return obs
