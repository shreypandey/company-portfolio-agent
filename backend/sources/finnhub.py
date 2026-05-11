"""Finnhub — primary source for US public co. data on free tier.

Endpoints confirmed working on the free tier (US tickers):
  /search, /stock/profile2, /quote, /stock/peers, /stock/metric (all),
  /stock/financials-reported, /stock/earnings, /stock/recommendation,
  /stock/insider-transactions, /stock/insider-sentiment, /company-news,
  /news, /calendar/earnings, /calendar/ipo, plus alt-data:
  /stock/uspto-patent, /stock/visa-application, /stock/lobbying, /stock/usa-spending.

Foreign tickers (e.g. 7203.T) get 403 on most data endpoints — chain falls back to yfinance.

Free tier: 60 calls/min. We use a token bucket to stay under.
"""
from __future__ import annotations

import asyncio
import time
from datetime import date, timedelta
from typing import Any

from ..config import FINNHUB_API_KEY
from ..context import RunContext
from . import _http
from .errors import SourceError, SourceMiss

_BASE = "https://finnhub.io/api/v1"
_NAME = "finnhub"


class _RateLimiter:
    """60 tokens, refill 1/sec — keeps us under the 60/min free-tier cap."""

    def __init__(self, capacity: int = 60, refill_per_sec: float = 1.0):
        self.capacity = capacity
        self.tokens = float(capacity)
        self.refill = refill_per_sec
        self.last = time.monotonic()
        self.lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self.lock:
            now = time.monotonic()
            self.tokens = min(self.capacity, self.tokens + (now - self.last) * self.refill)
            self.last = now
            if self.tokens < 1:
                await asyncio.sleep((1 - self.tokens) / self.refill)
                self.tokens = 0
            else:
                self.tokens -= 1


_limiter = _RateLimiter()


async def _get(ctx: RunContext, endpoint: str, **params) -> Any:
    if not FINNHUB_API_KEY:
        raise SourceError(_NAME, endpoint, "no FINNHUB_API_KEY configured")
    await _limiter.acquire()
    params["token"] = FINNHUB_API_KEY
    return await _http.request(
        ctx,
        source=_NAME,
        endpoint=endpoint.replace("/", "_").strip("_"),
        url=f"{_BASE}{endpoint}",
        params=params,
    )


# ───── Identity / quote ─────

async def symbol_lookup(ctx: RunContext, query: str) -> list[dict]:
    data = await _get(ctx, "/search", q=query)
    result = (data or {}).get("result") or []
    if not result:
        raise SourceMiss(_NAME, "search", f"no matches for '{query}'")
    return result


async def company_profile(ctx: RunContext, symbol: str) -> dict:
    data = await _get(ctx, "/stock/profile2", symbol=symbol)
    if not data:
        raise SourceMiss(_NAME, "profile2", f"no profile for {symbol}")
    return data


async def quote(ctx: RunContext, symbol: str) -> dict:
    data = await _get(ctx, "/quote", symbol=symbol)
    if not data or not isinstance(data, dict) or data.get("c") in (None, 0):
        raise SourceMiss(_NAME, "quote", f"no quote for {symbol}")
    return data


# ───── Peers ─────

async def peers(ctx: RunContext, symbol: str) -> list[str]:
    data = await _get(ctx, "/stock/peers", symbol=symbol)
    if not data:
        raise SourceMiss(_NAME, "peers", f"no peers for {symbol}")
    return [p for p in (data if isinstance(data, list) else []) if p]


# ───── Metrics ─────

async def basic_financials(ctx: RunContext, symbol: str) -> dict:
    """Returns 200+ metrics: ratios, growth, margins, valuation, 52W stats."""
    data = await _get(ctx, "/stock/metric", symbol=symbol, metric="all")
    metric = (data or {}).get("metric") or {}
    series = (data or {}).get("series") or {}
    if not metric:
        raise SourceMiss(_NAME, "metric", f"no metrics for {symbol}")
    return {"metric": metric, "series": series}


# ───── Statements (as reported to SEC, structured XBRL) ─────

async def financials_reported(ctx: RunContext, symbol: str, freq: str = "annual") -> list[dict]:
    """Returns the as-reported financial statements (income/balance/cash flow) per filing,
    extracted from XBRL. Each item has filing metadata + a `report` dict with the three statements.
    `freq` = annual | quarterly.
    """
    data = await _get(ctx, "/stock/financials-reported", symbol=symbol, freq=freq)
    filings = (data or {}).get("data") or []
    if not filings:
        raise SourceMiss(_NAME, "financials-reported", f"no filings for {symbol}")
    return filings


# ───── Earnings ─────

async def earnings(ctx: RunContext, symbol: str) -> list[dict]:
    """Quarterly EPS history with actual + estimate + surprise."""
    data = await _get(ctx, "/stock/earnings", symbol=symbol)
    if not data:
        raise SourceMiss(_NAME, "earnings", f"no earnings for {symbol}")
    return data


# ───── Analyst ─────

async def recommendations(ctx: RunContext, symbol: str) -> list[dict]:
    """Monthly analyst recommendation distribution (strongBuy/buy/hold/sell/strongSell)."""
    data = await _get(ctx, "/stock/recommendation", symbol=symbol)
    if not data:
        raise SourceMiss(_NAME, "recommendation", f"no recommendations for {symbol}")
    return data


# ───── Insider activity ─────

async def insider_transactions(ctx: RunContext, symbol: str) -> list[dict]:
    data = await _get(ctx, "/stock/insider-transactions", symbol=symbol)
    items = (data or {}).get("data") or []
    if not items:
        raise SourceMiss(_NAME, "insider-transactions", f"no insider txns for {symbol}")
    return items


async def insider_sentiment(ctx: RunContext, symbol: str, days: int = 365) -> list[dict]:
    end = date.today()
    start = end - timedelta(days=days)
    data = await _get(
        ctx,
        "/stock/insider-sentiment",
        symbol=symbol,
        **{"from": start.isoformat(), "to": end.isoformat()},
    )
    items = (data or {}).get("data") or []
    if not items:
        raise SourceMiss(_NAME, "insider-sentiment", f"no sentiment for {symbol}")
    return items


# ───── News ─────

async def company_news(ctx: RunContext, symbol: str, days: int = 30) -> list[dict]:
    end = date.today()
    start = end - timedelta(days=min(max(days, 1), 90))
    data = await _get(
        ctx,
        "/company-news",
        symbol=symbol,
        **{"from": start.isoformat(), "to": end.isoformat()},
    )
    items = data if isinstance(data, list) else []
    if not items:
        raise SourceMiss(_NAME, "company-news", f"no news for {symbol}")
    items.sort(key=lambda x: x.get("datetime", 0), reverse=True)
    return items


# ───── Alternative data (US only) ─────

async def patents(ctx: RunContext, symbol: str, days: int = 730) -> list[dict]:
    end = date.today()
    start = end - timedelta(days=days)
    data = await _get(
        ctx,
        "/stock/uspto-patent",
        symbol=symbol,
        **{"from": start.isoformat(), "to": end.isoformat()},
    )
    items = (data or {}).get("data") or []
    if not items:
        raise SourceMiss(_NAME, "uspto-patent", f"no patents for {symbol}")
    return items


async def visa_applications(ctx: RunContext, symbol: str, days: int = 730) -> list[dict]:
    end = date.today()
    start = end - timedelta(days=days)
    data = await _get(
        ctx,
        "/stock/visa-application",
        symbol=symbol,
        **{"from": start.isoformat(), "to": end.isoformat()},
    )
    items = (data or {}).get("data") or []
    if not items:
        raise SourceMiss(_NAME, "visa-application", f"no visa apps for {symbol}")
    return items


async def lobbying(ctx: RunContext, symbol: str, days: int = 730) -> list[dict]:
    end = date.today()
    start = end - timedelta(days=days)
    data = await _get(
        ctx,
        "/stock/lobbying",
        symbol=symbol,
        **{"from": start.isoformat(), "to": end.isoformat()},
    )
    items = (data or {}).get("data") or []
    if not items:
        raise SourceMiss(_NAME, "lobbying", f"no lobbying for {symbol}")
    return items


async def usa_spending(ctx: RunContext, symbol: str, days: int = 730) -> list[dict]:
    end = date.today()
    start = end - timedelta(days=days)
    data = await _get(
        ctx,
        "/stock/usa-spending",
        symbol=symbol,
        **{"from": start.isoformat(), "to": end.isoformat()},
    )
    items = (data or {}).get("data") or []
    if not items:
        raise SourceMiss(_NAME, "usa-spending", f"no gov-spending for {symbol}")
    return items
