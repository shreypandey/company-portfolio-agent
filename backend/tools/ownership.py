"""get_ownership_activity — Finnhub insider transactions + sentiment.

Institutional 13F holders require paid tier on both Finnhub and FMP. We surface the gap;
the LLM should note it in the portfolio.
"""
from __future__ import annotations

import asyncio

from ..context import RunContext
from ..envelope import SourceChain
from ..sources import finnhub
from ..sources.errors import SourceMiss

NAME = "get_ownership_activity"

SCHEMA = {
    "name": NAME,
    "description": (
        "Get recent insider transactions (Form 4) and insider sentiment. US public only. "
        "Note: institutional 13F holder data is not available on free tier."
    ),
    "input_schema": {
        "type": "object",
        "properties": {"ticker": {"type": "string"}},
        "required": ["ticker"],
    },
}


def _summarize_insiders(items: list[dict]) -> dict:
    buys = sum(1 for r in items if (r.get("transactionCode") or "").upper() == "P")
    sells = sum(1 for r in items if (r.get("transactionCode") or "").upper() == "S")
    net_value = 0.0
    for r in items:
        try:
            change = float(r.get("change") or 0)
            price = float(r.get("transactionPrice") or 0)
            net_value += change * price  # buys positive (change > 0), sells negative
        except Exception:
            continue
    return {
        "buys_count": buys,
        "sells_count": sells,
        "net_insider_value_usd": round(net_value, 2),
        "total_transactions": len(items),
    }


async def run(ctx: RunContext, ticker: str) -> dict:
    chain = SourceChain(ctx, NAME)

    async def gather() -> dict:
        txn_t = asyncio.create_task(finnhub.insider_transactions(ctx, ticker))
        sent_t = asyncio.create_task(finnhub.insider_sentiment(ctx, ticker))

        txns: list[dict] = []
        sentiment: list[dict] = []
        try:
            txns = await txn_t
        except SourceMiss:
            pass
        try:
            sentiment = await sent_t
        except SourceMiss:
            pass

        if not txns and not sentiment:
            raise SourceMiss("finnhub", "insider", "no insider data")
        return {
            "ticker": ticker,
            "insider_recent": txns[:30],
            "insider_summary": _summarize_insiders(txns),
            "insider_sentiment_monthly": sentiment[-12:],  # last 12 months
            "note": "Institutional 13F holders not on free tier.",
        }

    data = await chain.try_("finnhub", gather)
    if data:
        return chain.success(data, partial=True, notes="13F holders unavailable on free tier").to_dict()
    return chain.fail(hint="Insider data unavailable. Try search_web for recent activity.").to_dict()
