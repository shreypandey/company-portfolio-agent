"""get_key_metrics_history — Finnhub /metric (rich snapshot + time series), yfinance fallback."""
from __future__ import annotations

from ..context import RunContext
from ..envelope import SourceChain
from ..sources import finnhub, yahoo
from ..sources.errors import SourceMiss

NAME = "get_key_metrics_history"

SCHEMA = {
    "name": NAME,
    "description": (
        "Get current key metrics + historical series: P/E, P/B, ROE, ROA, margins, growth, debt ratios, "
        "52W H/L, beta. Finnhub returns both snapshot metrics and quarterly/annual series for several "
        "of them (US public)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {"ticker": {"type": "string"}},
        "required": ["ticker"],
    },
}


# Curated subset of useful Finnhub metric keys — the full payload has ~200 keys.
_KEEP_METRICS = [
    "marketCapitalization", "enterpriseValue", "peTTM", "pbAnnual", "psTTM",
    "pcfShareTTM", "evEbitdaTTM", "currentRatioAnnual", "quickRatioAnnual",
    "totalDebt/totalEquityAnnual", "longTermDebt/equityAnnual",
    "grossMarginTTM", "grossMargin5Y", "operatingMarginTTM", "operatingMargin5Y",
    "netProfitMarginTTM", "netProfitMargin5Y", "roeTTM", "roe5Y",
    "roaTTM", "roa5Y", "roiTTM", "roi5Y",
    "revenueGrowthTTMYoy", "revenueGrowth5Y", "revenueGrowth3Y",
    "epsGrowthTTMYoy", "epsGrowth5Y", "epsGrowth3Y",
    "ebitdaCagr5Y", "epsBasicExclExtraItemsAnnual",
    "dividendYieldIndicatedAnnual", "dividendPerShareAnnual", "payoutRatioAnnual",
    "52WeekHigh", "52WeekLow", "52WeekHighDate", "52WeekLowDate",
    "beta", "10DayAverageTradingVolume", "3MonthAverageTradingVolume",
    "epsTTM", "bookValuePerShareAnnual", "tangibleBookValuePerShareAnnual",
    "cashPerSharePerShareAnnual", "freeCashFlowPerShareTTM",
    "totalDebt/totalCapital", "interestCoverage5Y",
    "assetTurnoverTTM", "inventoryTurnoverTTM",
]


_KEEP_SERIES = [
    "currentRatio", "quickRatio", "longtermDebtTotalCapital", "totalDebtToEquity",
    "grossMargin", "operatingMargin", "netMargin", "pretaxMargin",
    "roe", "roa", "roic",
    "ebitPerShare", "ebitdaPerShare", "epsBasicExclExtraItems",
    "salesPerShare", "bookValue", "cashRatio",
    "pe", "ps", "pb", "pfcfShare",
]


def _slim_series(series: dict, keep: list[str], max_periods: int = 12) -> dict:
    """Keep only useful series keys, with the most recent N periods each."""
    if not series:
        return {}
    out: dict[str, list[dict]] = {}
    for k in keep:
        if k in series:
            arr = series[k] or []
            # Newest first; keep most recent N
            out[k] = sorted(arr, key=lambda r: r.get("period", ""), reverse=True)[:max_periods]
    return out


async def _from_finnhub(ctx: RunContext, ticker: str) -> dict:
    data = await finnhub.basic_financials(ctx, ticker)
    metric = data.get("metric") or {}
    series = data.get("series") or {}
    snapshot = {k: metric[k] for k in _KEEP_METRICS if k in metric}
    return {
        "ticker": ticker,
        "snapshot": snapshot,
        "annual_series": _slim_series(series.get("annual") or {}, _KEEP_SERIES, max_periods=12),
        "quarterly_series": _slim_series(series.get("quarterly") or {}, _KEEP_SERIES, max_periods=12),
        "metric_count_total": len(metric),
    }


async def _from_yahoo(ctx: RunContext, ticker: str) -> dict:
    dump = await yahoo.full_dump(ctx, ticker)
    info = dump.get("info") or {}
    keep = [
        "marketCap", "enterpriseValue", "trailingPE", "forwardPE", "priceToBook",
        "priceToSalesTrailing12Months", "enterpriseToEbitda",
        "profitMargins", "operatingMargins", "grossMargins",
        "returnOnEquity", "returnOnAssets", "earningsGrowth", "revenueGrowth",
        "debtToEquity", "totalCash", "totalDebt", "freeCashflow",
        "fiftyTwoWeekHigh", "fiftyTwoWeekLow", "beta",
        "dividendYield", "payoutRatio",
    ]
    snapshot = {k: info[k] for k in keep if k in info}
    if not snapshot:
        raise SourceMiss("yahoo", "info", "no metrics")
    return {"ticker": ticker, "snapshot": snapshot, "annual_series": {}, "quarterly_series": {}}


async def run(ctx: RunContext, ticker: str) -> dict:
    chain = SourceChain(ctx, NAME)

    data = await chain.try_("finnhub", lambda: _from_finnhub(ctx, ticker))
    if data:
        return chain.success(data).to_dict()

    data = await chain.try_("yahoo", lambda: _from_yahoo(ctx, ticker))
    if data:
        return chain.success(data, partial=True, notes="snapshot only — no historical series").to_dict()

    return chain.fail(hint="No metrics available.").to_dict()
