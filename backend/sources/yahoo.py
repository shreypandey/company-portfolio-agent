"""Yahoo Finance via yfinance — synchronous lib, offloaded to a thread.

Used as a fallback when FMP doesn't cover (foreign tickers) or fails. Returns raw
parsed dicts that the semantic tool layer normalizes.
"""
from __future__ import annotations

import asyncio
import math
from typing import Any

import yfinance as yf

from ..context import RunContext
from .errors import SourceError, SourceMiss

_NAME = "yahoo"


def _clean(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v


def _df_to_dict(df) -> dict:
    """yfinance DF: cols=periods, index=line items. → {period: {item: value}}"""
    if df is None or getattr(df, "empty", True):
        return {}
    out: dict[str, dict] = {}
    try:
        for col in df.columns:
            key = str(col.date()) if hasattr(col, "date") else str(col)
            row: dict[str, float | None] = {}
            for idx in df.index:
                val = df.at[idx, col]
                if hasattr(val, "item"):
                    try:
                        val = val.item()
                    except Exception:
                        pass
                row[str(idx)] = _clean(val)
            out[key] = row
    except Exception:
        return {}
    return out


def _fetch_sync(ticker: str) -> dict:
    t = yf.Ticker(ticker)
    try:
        info = dict(t.info or {})
    except Exception:
        info = {}
    if not info or info.get("symbol") is None and info.get("longName") is None:
        raise SourceMiss(_NAME, "info", f"no info for {ticker}")
    return {
        "info": {k: _clean(v) for k, v in info.items()},
        "income_annual": _df_to_dict(getattr(t, "financials", None)),
        "income_quarterly": _df_to_dict(getattr(t, "quarterly_financials", None)),
        "balance_annual": _df_to_dict(getattr(t, "balance_sheet", None)),
        "balance_quarterly": _df_to_dict(getattr(t, "quarterly_balance_sheet", None)),
        "cashflow_annual": _df_to_dict(getattr(t, "cashflow", None)),
        "cashflow_quarterly": _df_to_dict(getattr(t, "quarterly_cashflow", None)),
    }


async def full_dump(ctx: RunContext, ticker: str) -> dict:
    """Return everything yfinance gives us in one shot (info + statements)."""
    cache_key = ctx.cache_key(_NAME, "full_dump", ticker=ticker)
    if cache_key in ctx.cache:
        await ctx.emit("source_cache_hit", {"source": _NAME, "endpoint": "full_dump"})
        return ctx.cache[cache_key]
    try:
        data = await asyncio.wait_for(asyncio.to_thread(_fetch_sync, ticker), timeout=45.0)
    except asyncio.TimeoutError:
        raise SourceError(_NAME, "full_dump", "timeout", retriable=True)
    except SourceMiss:
        raise
    except Exception as e:
        raise SourceError(_NAME, "full_dump", f"{type(e).__name__}: {e}")
    ctx.cache[cache_key] = data
    return data
