"""get_earnings_history — Finnhub /stock/earnings (already includes beat/miss), AV fallback."""
from __future__ import annotations

from ..context import RunContext
from ..envelope import SourceChain
from ..sources import alpha_vantage, finnhub
from ..sources.errors import SourceMiss

NAME = "get_earnings_history"

SCHEMA = {
    "name": NAME,
    "description": (
        "Get the company's quarterly EPS history with actual vs estimated consensus and surprise %. "
        "US public companies. Returns up to ~16 most recent quarters."
    ),
    "input_schema": {
        "type": "object",
        "properties": {"ticker": {"type": "string"}},
        "required": ["ticker"],
    },
}


async def _from_finnhub(ctx: RunContext, ticker: str) -> dict:
    raw = await finnhub.earnings(ctx, ticker)
    items = []
    for row in raw[:32]:
        actual = row.get("actual")
        est = row.get("estimate")
        surprise = row.get("surprise")
        surprise_pct = row.get("surprisePercent")
        beat = (actual is not None and est is not None and actual > est)
        items.append(
            {
                "period": row.get("period"),  # YYYY-MM-DD fiscal period end
                "eps_actual": actual,
                "eps_estimated": est,
                "surprise": surprise,
                "surprise_pct": surprise_pct,
                "beat": beat,
            }
        )
    if not items:
        raise SourceMiss("finnhub", "earnings", "empty")
    return {"ticker": ticker, "quarters": items, "count": len(items)}


async def _from_av(ctx: RunContext, ticker: str) -> dict:
    data = await alpha_vantage.earnings(ctx, ticker)
    quarterly = data.get("quarterlyEarnings") or []
    if not quarterly:
        raise SourceMiss("alpha_vantage", "earnings", "no quarterlyEarnings")
    items = []
    for q in quarterly[:32]:
        def f(k):
            v = q.get(k)
            if v in (None, "None"):
                return None
            try:
                return float(v)
            except (TypeError, ValueError):
                return None

        actual, est, surprise, surprise_pct = f("reportedEPS"), f("estimatedEPS"), f("surprise"), f("surprisePercentage")
        items.append(
            {
                "period": q.get("fiscalDateEnding"),
                "report_date": q.get("reportedDate"),
                "eps_actual": actual,
                "eps_estimated": est,
                "surprise": surprise,
                "surprise_pct": surprise_pct,
                "beat": (actual is not None and est is not None and actual > est),
            }
        )
    return {"ticker": ticker, "quarters": items, "count": len(items)}


async def run(ctx: RunContext, ticker: str) -> dict:
    chain = SourceChain(ctx, NAME)

    data = await chain.try_("finnhub", lambda: _from_finnhub(ctx, ticker))
    if data:
        return chain.success(data).to_dict()

    data = await chain.try_("alpha_vantage", lambda: _from_av(ctx, ticker))
    if data:
        return chain.success(data).to_dict()

    return chain.fail(hint="No earnings history. Likely foreign/private/recent-IPO.").to_dict()
