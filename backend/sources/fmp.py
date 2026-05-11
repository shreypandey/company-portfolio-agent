"""Financial Modeling Prep — primary source for US public co. data.

Free tier limits: 250 calls/day, US-focused, some endpoints paywalled. We catch
403s and treat them as SourceError so chains can fall back. Each function returns
raw FMP JSON (lists of dicts) — normalization happens in semantic tools.
"""
from __future__ import annotations

from typing import Any

from ..config import FMP_API_KEY
from ..context import RunContext
from . import _http
from .errors import SourceError, SourceMiss

_BASE = "https://financialmodelingprep.com/api"
_NAME = "fmp"


async def _get(ctx: RunContext, endpoint: str, **params) -> Any:
    if not FMP_API_KEY:
        raise SourceError(_NAME, endpoint, "no FMP_API_KEY configured")
    params["apikey"] = FMP_API_KEY
    data = await _http.request(
        ctx,
        source=_NAME,
        endpoint=endpoint,
        url=f"{_BASE}/{endpoint}",
        params=params,
    )
    # FMP returns {"Error Message": "..."} for some failures with status 200
    if isinstance(data, dict) and data.get("Error Message"):
        raise SourceError(_NAME, endpoint, data["Error Message"])
    # FMP free tier rejects some endpoints with a special premium-required message
    if isinstance(data, dict) and "premium" in str(data).lower():
        raise SourceError(_NAME, endpoint, "premium endpoint")
    if isinstance(data, list) and not data:
        raise SourceMiss(_NAME, endpoint, "empty list")
    return data


# ───── Identity / quote ─────

async def search(ctx: RunContext, query: str, limit: int = 10) -> list[dict]:
    return await _get(ctx, "v3/search", query=query, limit=limit)


async def profile(ctx: RunContext, ticker: str) -> list[dict]:
    return await _get(ctx, f"v3/profile/{ticker}")


async def quote(ctx: RunContext, ticker: str) -> list[dict]:
    return await _get(ctx, f"v3/quote/{ticker}")


# ───── Historical statements ─────

async def income_statement(ctx: RunContext, ticker: str, period: str = "annual", limit: int = 10) -> list[dict]:
    return await _get(ctx, f"v3/income-statement/{ticker}", period=period, limit=limit)


async def balance_sheet(ctx: RunContext, ticker: str, period: str = "annual", limit: int = 10) -> list[dict]:
    return await _get(ctx, f"v3/balance-sheet-statement/{ticker}", period=period, limit=limit)


async def cash_flow(ctx: RunContext, ticker: str, period: str = "annual", limit: int = 10) -> list[dict]:
    return await _get(ctx, f"v3/cash-flow-statement/{ticker}", period=period, limit=limit)


# ───── Ratios / metrics over time ─────

async def ratios(ctx: RunContext, ticker: str, period: str = "annual", limit: int = 10) -> list[dict]:
    return await _get(ctx, f"v3/ratios/{ticker}", period=period, limit=limit)


async def key_metrics(ctx: RunContext, ticker: str, period: str = "annual", limit: int = 10) -> list[dict]:
    return await _get(ctx, f"v3/key-metrics/{ticker}", period=period, limit=limit)


async def financial_growth(ctx: RunContext, ticker: str, period: str = "annual", limit: int = 10) -> list[dict]:
    return await _get(ctx, f"v3/financial-growth/{ticker}", period=period, limit=limit)


# ───── Earnings ─────

async def earnings_surprises(ctx: RunContext, ticker: str) -> list[dict]:
    return await _get(ctx, f"v3/earnings-surprises/{ticker}")


async def earnings_calendar(ctx: RunContext, ticker: str, limit: int = 20) -> list[dict]:
    return await _get(ctx, f"v3/historical/earning_calendar/{ticker}", limit=limit)


# ───── Analyst ─────

async def analyst_recommendations(ctx: RunContext, ticker: str) -> list[dict]:
    return await _get(ctx, f"v3/analyst-stock-recommendations/{ticker}")


async def price_target(ctx: RunContext, ticker: str) -> Any:
    return await _get(ctx, "v4/price-target", symbol=ticker)


async def upgrades_downgrades(ctx: RunContext, ticker: str) -> Any:
    return await _get(ctx, "v4/upgrades-downgrades", symbol=ticker)


# ───── Ownership ─────

async def insider_trading(ctx: RunContext, ticker: str, page: int = 0) -> Any:
    return await _get(ctx, "v4/insider-trading", symbol=ticker, page=page)


async def institutional_holders(ctx: RunContext, ticker: str) -> list[dict]:
    return await _get(ctx, f"v3/institutional-holder/{ticker}")


# ───── Peers ─────

async def stock_peers(ctx: RunContext, ticker: str) -> Any:
    return await _get(ctx, "v4/stock_peers", symbol=ticker)


# ───── News & filings ─────

async def stock_news(ctx: RunContext, ticker: str, limit: int = 20) -> list[dict]:
    return await _get(ctx, "v3/stock_news", tickers=ticker, limit=limit)


async def press_releases(ctx: RunContext, ticker: str, limit: int = 20) -> list[dict]:
    return await _get(ctx, f"v3/press-releases/{ticker}", limit=limit)


async def sec_filings(ctx: RunContext, ticker: str, type: str = "", limit: int = 20) -> list[dict]:
    params: dict[str, Any] = {"limit": limit}
    if type:
        params["type"] = type
    return await _get(ctx, f"v3/sec_filings/{ticker}", **params)


# ───── Price history ─────

async def historical_prices(ctx: RunContext, ticker: str, from_date: str | None = None, to_date: str | None = None) -> dict:
    params: dict[str, Any] = {"serietype": "line"}
    if from_date:
        params["from"] = from_date
    if to_date:
        params["to"] = to_date
    data = await _get(ctx, f"v3/historical-price-full/{ticker}", **params)
    return data if isinstance(data, dict) else {"historical": []}
