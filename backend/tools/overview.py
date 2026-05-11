"""get_company_overview — Finnhub-primary (US), yfinance fallback (global), Wikipedia last."""
from __future__ import annotations

import asyncio

from ..context import RunContext
from ..envelope import SourceChain
from ..sources import finnhub, wikipedia, yahoo
from ..sources.errors import SourceMiss
from ._resolver import resolve_ticker

NAME = "get_company_overview"

SCHEMA = {
    "name": NAME,
    "description": (
        "Get the company's identity + current market snapshot. Returns name, ticker, exchange, "
        "country, sector, industry, IPO date, website, current price, market cap, 52W H/L, etc. "
        "Always start here for any company."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Company name or ticker"},
        },
        "required": ["query"],
    },
}


async def _from_finnhub(ctx: RunContext, query: str) -> dict:
    """Resolve query → ticker via deterministic resolver (SEC EDGAR list, then Finnhub),
    then parallel profile + quote.
    """
    resolved = await resolve_ticker(ctx, query)
    if not resolved:
        raise SourceMiss("finnhub", "resolver", f"could not resolve '{query}' to any known ticker")
    symbol = resolved["ticker"]

    profile_t = asyncio.create_task(finnhub.company_profile(ctx, symbol))
    quote_t = asyncio.create_task(finnhub.quote(ctx, symbol))

    profile = quote = None
    try:
        profile = await profile_t
    except SourceMiss:
        pass
    try:
        quote = await quote_t
    except SourceMiss:
        pass

    if not profile:
        raise SourceMiss("finnhub", "profile2", f"no profile for {symbol}")

    market_cap_m = profile.get("marketCapitalization")
    shares_m = profile.get("shareOutstanding")
    return {
        "name": profile.get("name"),
        "ticker": symbol,
        "exchange": profile.get("exchange"),
        "country": profile.get("country"),
        "currency": profile.get("currency"),
        "industry": profile.get("finnhubIndustry"),
        "ipo_date": profile.get("ipo"),
        "website": profile.get("weburl"),
        "phone": profile.get("phone"),
        "logo": profile.get("logo"),
        "market_cap": int(market_cap_m * 1_000_000) if market_cap_m else None,
        "shares_outstanding": int(shares_m * 1_000_000) if shares_m else None,
        "price": (quote or {}).get("c") if quote else None,
        "change": (quote or {}).get("d") if quote else None,
        "change_pct": (quote or {}).get("dp") if quote else None,
        "day_high": (quote or {}).get("h") if quote else None,
        "day_low": (quote or {}).get("l") if quote else None,
        "previous_close": (quote or {}).get("pc") if quote else None,
    }


async def _from_yahoo(ctx: RunContext, query: str) -> dict:
    dump = await yahoo.full_dump(ctx, query.upper())
    info = dump.get("info") or {}
    if not info:
        raise SourceMiss("yahoo", "info", "empty")
    return {
        "name": info.get("longName") or info.get("shortName"),
        "ticker": info.get("symbol") or query.upper(),
        "exchange": info.get("exchange"),
        "country": info.get("country"),
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "employees": info.get("fullTimeEmployees"),
        "website": info.get("website"),
        "description": info.get("longBusinessSummary"),
        "currency": info.get("currency"),
        "market_cap": info.get("marketCap"),
        "shares_outstanding": info.get("sharesOutstanding"),
        "price": info.get("currentPrice") or info.get("regularMarketPrice"),
        "52w_high": info.get("fiftyTwoWeekHigh"),
        "52w_low": info.get("fiftyTwoWeekLow"),
        "pe": info.get("trailingPE"),
        "forward_pe": info.get("forwardPE"),
        "eps": info.get("trailingEps"),
        "beta": info.get("beta"),
        "dividend_yield_annual": info.get("dividendYield"),
        "profit_margins": info.get("profitMargins"),
        "operating_margins": info.get("operatingMargins"),
    }


async def _from_wikipedia(ctx: RunContext, query: str) -> dict:
    s = await wikipedia.summary(ctx, query)
    return {
        "name": s.get("title"),
        "description": s.get("extract"),
        "source_url": s.get("url"),
        "summary_short": s.get("description"),
    }


async def run(ctx: RunContext, query: str) -> dict:
    chain = SourceChain(ctx, NAME)

    # Finnhub primary (US, rich)
    data = await chain.try_("finnhub", lambda: _from_finnhub(ctx, query))
    if data:
        # Enrich with yfinance for fields Finnhub lacks (sector, employees, description, ratios)
        try:
            yf_data = await yahoo.full_dump(ctx, data["ticker"])
            info = yf_data.get("info") or {}
            data.setdefault("sector", info.get("sector"))
            data.setdefault("employees", info.get("fullTimeEmployees"))
            data.setdefault("description", info.get("longBusinessSummary"))
            data.setdefault("52w_high", info.get("fiftyTwoWeekHigh"))
            data.setdefault("52w_low", info.get("fiftyTwoWeekLow"))
            data.setdefault("pe", info.get("trailingPE"))
            data.setdefault("eps", info.get("trailingEps"))
            data.setdefault("beta", info.get("beta"))
            data.setdefault("dividend_yield_annual", info.get("dividendYield"))
            chain.attempted.append("yahoo")
            await ctx.emit("source_ok", {"tool": NAME, "source": "yahoo", "note": "enrich"})
            return chain.success_multi(data, sources_used=["finnhub", "yahoo"]).to_dict()
        except Exception:
            return chain.success(data).to_dict()

    # Yahoo fallback (global tickers)
    data = await chain.try_("yahoo", lambda: _from_yahoo(ctx, query))
    if data:
        return chain.success(data).to_dict()

    # Wikipedia narrative-only
    data = await chain.try_("wikipedia", lambda: _from_wikipedia(ctx, query))
    if data:
        return chain.success(data, partial=True, notes="narrative only — no structured profile").to_dict()

    return chain.fail(hint="No source could identify this company. Verify name spelling or use search_web.").to_dict()
