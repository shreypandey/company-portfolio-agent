"""get_competitors — Finnhub /peers + parallel profile+quote enrichment."""
from __future__ import annotations

import asyncio

from ..context import RunContext
from ..envelope import SourceChain
from ..sources import brave, finnhub
from ..sources.errors import SourceError, SourceMiss

NAME = "get_competitors"

SCHEMA = {
    "name": NAME,
    "description": (
        "Get the company's competitors / peer list with side-by-side comparison metrics (market cap, "
        "P/E, margins, etc.). Pulls peer list from Finnhub, then enriches each top peer in parallel."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "ticker": {"type": "string"},
            "depth": {"type": "integer", "minimum": 1, "maximum": 8, "default": 4},
        },
        "required": ["ticker"],
    },
}


async def _enrich(ctx: RunContext, peer_ticker: str) -> dict | None:
    try:
        prof_t = asyncio.create_task(finnhub.company_profile(ctx, peer_ticker))
        q_t = asyncio.create_task(finnhub.quote(ctx, peer_ticker))
        metric_t = asyncio.create_task(finnhub.basic_financials(ctx, peer_ticker))

        profile: dict = {}
        quote: dict = {}
        metric: dict = {}
        try:
            profile = await prof_t
        except (SourceMiss, SourceError):
            pass
        try:
            quote = await q_t
        except (SourceMiss, SourceError):
            pass
        try:
            metric = (await metric_t).get("metric", {})
        except (SourceMiss, SourceError):
            pass

        if not (profile or quote):
            return None

        mc_m = profile.get("marketCapitalization")
        return {
            "ticker": peer_ticker,
            "name": profile.get("name"),
            "exchange": profile.get("exchange"),
            "industry": profile.get("finnhubIndustry"),
            "country": profile.get("country"),
            "currency": profile.get("currency"),
            "market_cap": int(mc_m * 1_000_000) if mc_m else None,
            "price": quote.get("c"),
            "change_pct": quote.get("dp"),
            "52w_high": metric.get("52WeekHigh"),
            "52w_low": metric.get("52WeekLow"),
            "pe_ttm": metric.get("peTTM"),
            "ps_ttm": metric.get("psTTM"),
            "pb_annual": metric.get("pbAnnual"),
            "eps_ttm": metric.get("epsTTM"),
            "operating_margin_ttm": metric.get("operatingMarginTTM"),
            "net_margin_ttm": metric.get("netProfitMarginTTM"),
            "roe_ttm": metric.get("roeTTM"),
            "revenue_growth_ttm_yoy": metric.get("revenueGrowthTTMYoy"),
            "beta": metric.get("beta"),
        }
    except Exception:
        return None


async def run(ctx: RunContext, ticker: str, depth: int = 4) -> dict:
    chain = SourceChain(ctx, NAME)

    peers = await chain.try_("finnhub", lambda: finnhub.peers(ctx, ticker))
    peers = [p for p in (peers or []) if p and p.upper() != ticker.upper()]

    if not peers:
        # Brave fallback — return raw search results, no structured comparison
        data = await chain.try_("brave", lambda: brave.web_search(ctx, f"{ticker} top competitors comparison", count=8))
        if data:
            return chain.success_multi(
                {"ticker": ticker, "peer_tickers": [], "peers_enriched": [], "search_results": data},
                sources_used=["brave"],
                partial=True,
                notes="No structured peer list — search results returned for interpretation.",
            ).to_dict()
        return chain.fail(hint="No peer list available.").to_dict()

    depth = max(1, min(depth, len(peers)))
    chosen = peers[:depth]

    enriched_results = await asyncio.gather(*[_enrich(ctx, t) for t in chosen], return_exceptions=True)
    enriched = [r for r in enriched_results if isinstance(r, dict) and r is not None]

    return chain.success_multi(
        {"ticker": ticker, "peer_tickers": peers, "peers_enriched": enriched},
        sources_used=["finnhub"],
    ).to_dict()
