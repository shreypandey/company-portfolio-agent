"""Deterministic ticker resolver — used by tools that take a `query` and need a ticker.

Order:
  1. SEC EDGAR's company_tickers.json — authoritative US public list (every ticker + CIK + name).
     Matches: exact ticker, exact name, substring name (case-insensitive).
  2. Finnhub /search — fallback for foreign tickers (Toyota → 7203.T, ASML, etc.).

No heuristics, no LLM. The result is reproducible: same input → same ticker.
"""
from __future__ import annotations

from ..context import RunContext
from ..sources import finnhub, sec_edgar
from ..sources.errors import SourceMiss


async def resolve_ticker(ctx: RunContext, query: str) -> dict | None:
    """Return {ticker, name, source, [cik]} or None if no source can resolve."""
    # 1. SEC EDGAR (US public — has the entire register)
    try:
        match = await sec_edgar.resolve(ctx, query)
        return {
            "ticker": match["ticker"],
            "name": match["name"],
            "cik": match["cik"],
            "source": "sec_edgar",
        }
    except SourceMiss:
        pass
    except Exception:
        pass  # SEC index load failure — fall through

    # 2. Finnhub search (foreign + niche)
    try:
        matches = await finnhub.symbol_lookup(ctx, query)
        for m in matches:
            sym = (m or {}).get("symbol")
            if sym:
                return {
                    "ticker": sym,
                    "name": m.get("description"),
                    "source": "finnhub",
                }
    except SourceMiss:
        pass
    except Exception:
        pass

    return None
