"""get_analyst_coverage — Finnhub /recommendation only on free tier.

Price targets and upgrade/downgrade actions require Finnhub paid tier. We surface only
the monthly recommendation distribution. Better-than-nothing; the gap is acknowledged.
"""
from __future__ import annotations

from ..context import RunContext
from ..envelope import SourceChain
from ..sources import finnhub

NAME = "get_analyst_coverage"

SCHEMA = {
    "name": NAME,
    "description": (
        "Get analyst coverage — monthly consensus rating distribution (strong buy/buy/hold/sell/strong "
        "sell) over time. US public only. Note: price targets and individual upgrade/downgrade actions "
        "are not available on the free tier."
    ),
    "input_schema": {
        "type": "object",
        "properties": {"ticker": {"type": "string"}},
        "required": ["ticker"],
    },
}


async def _from_finnhub(ctx: RunContext, ticker: str) -> dict:
    raw = await finnhub.recommendations(ctx, ticker)
    # Most recent first
    raw.sort(key=lambda r: r.get("period", ""), reverse=True)
    # Compute latest aggregate
    if raw:
        latest = raw[0]
        total = sum(latest.get(k, 0) or 0 for k in ("strongBuy", "buy", "hold", "sell", "strongSell"))
        if total:
            pct = {k: round(100 * (latest.get(k, 0) or 0) / total, 1) for k in ("strongBuy", "buy", "hold", "sell", "strongSell")}
        else:
            pct = {}
    else:
        latest = {}
        pct = {}
    return {
        "ticker": ticker,
        "monthly_recommendations": raw[:24],
        "latest_period": latest.get("period"),
        "latest_total_analysts": sum(latest.get(k, 0) or 0 for k in ("strongBuy", "buy", "hold", "sell", "strongSell")),
        "latest_distribution_pct": pct,
        "note": "Free tier: monthly consensus only. Price targets and individual upgrade/downgrade actions require paid Finnhub.",
    }


async def run(ctx: RunContext, ticker: str) -> dict:
    chain = SourceChain(ctx, NAME)
    data = await chain.try_("finnhub", lambda: _from_finnhub(ctx, ticker))
    if data:
        return chain.success(data, partial=True, notes="price targets unavailable on free tier").to_dict()
    return chain.fail(hint="No analyst recommendations available. Try search_web for coverage articles.").to_dict()
