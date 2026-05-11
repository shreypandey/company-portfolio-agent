"""get_macro_context — sector-aware FRED bundle for narrative context.

Curated mapping of sector keywords → relevant FRED series. The semantic tool fetches
all series in the bundle and returns a normalized time-series + meta for each.
"""
from __future__ import annotations

import asyncio

from ..context import RunContext
from ..envelope import SourceChain
from ..sources import fred

NAME = "get_macro_context"

# Curated FRED series per broad sector. Each value is a list of (series_id, label, why).
# A "default" bundle is used when no sector matches.
_SECTOR_SERIES: dict[str, list[tuple[str, str, str]]] = {
    "technology": [
        ("FEDFUNDS", "Fed Funds Rate", "discount-rate sensitivity"),
        ("DGS10", "10-Year Treasury", "long-duration tech valuation pressure"),
        ("CPIAUCSL", "CPI (All Items)", "inflation backdrop"),
        ("UNRATE", "Unemployment", "consumer + enterprise demand proxy"),
    ],
    "consumer_discretionary": [
        ("UMCSENT", "Consumer Sentiment", "discretionary spending signal"),
        ("RSAFS", "Retail Sales", "category-level demand"),
        ("CPIAUCSL", "CPI", "pricing power vs input costs"),
        ("UNRATE", "Unemployment", "wallet pressure"),
    ],
    "consumer_staples": [
        ("CPIAUCSL", "CPI", "pricing pass-through"),
        ("PPIACO", "Producer Price Index", "input cost trend"),
        ("UMCSENT", "Consumer Sentiment", "demand stickiness check"),
    ],
    "retail": [
        ("RSAFS", "Retail Sales", "category demand"),
        ("UMCSENT", "Consumer Sentiment", "forward demand"),
        ("CPIAUCSL", "CPI", "inflation backdrop"),
        ("UNRATE", "Unemployment", "wallet pressure"),
    ],
    "financial": [
        ("FEDFUNDS", "Fed Funds Rate", "NIM driver"),
        ("DGS10", "10-Year Treasury", "yield-curve / NIM"),
        ("DGS2", "2-Year Treasury", "short end of curve"),
        ("MORTGAGE30US", "30Y Mortgage Rate", "mortgage origination"),
    ],
    "banks": [
        ("FEDFUNDS", "Fed Funds Rate", "NIM driver"),
        ("DGS10", "10-Year Treasury", "yield-curve / NIM"),
        ("DGS2", "2-Year Treasury", "short end of curve"),
    ],
    "energy": [
        ("DCOILWTICO", "WTI Crude Oil", "primary revenue driver"),
        ("DHHNGSP", "Henry Hub Natural Gas", "gas revenue driver"),
        ("CPIAUCSL", "CPI", "inflation reflexivity"),
    ],
    "healthcare": [
        ("CPIMEDSL", "Medical CPI", "pricing environment"),
        ("UNRATE", "Unemployment", "employer-insurance enrollment"),
    ],
    "industrials": [
        ("INDPRO", "Industrial Production", "demand for industrials"),
        ("ISRATIO", "Inventory-to-Sales Ratio", "cycle position"),
        ("DGORDER", "Durable Goods Orders", "leading indicator"),
    ],
    "real_estate": [
        ("MORTGAGE30US", "30Y Mortgage Rate", "transaction volume"),
        ("HOUST", "Housing Starts", "new build pipeline"),
        ("CSUSHPINSA", "Case-Shiller Home Price Index", "asset values"),
    ],
    "auto": [
        ("DCOILWTICO", "WTI Crude Oil", "fuel cost → demand"),
        ("UMCSENT", "Consumer Sentiment", "big-ticket discretionary"),
        ("RSAFS", "Retail Sales", "broader demand"),
        ("MORTGAGE30US", "30Y Mortgage Rate", "auto-loan rate proxy"),
    ],
    "utilities": [
        ("DGS10", "10-Year Treasury", "valuation proxy"),
        ("CPIAUCSL", "CPI", "rate-base / pass-through"),
    ],
    "telecom": [
        ("DGS10", "10-Year Treasury", "valuation"),
        ("CPIAUCSL", "CPI", "service price pass-through"),
    ],
    "default": [
        ("GDPC1", "Real GDP", "macro backdrop"),
        ("CPIAUCSL", "CPI", "inflation"),
        ("FEDFUNDS", "Fed Funds Rate", "rate environment"),
        ("UNRATE", "Unemployment", "labor market"),
    ],
}

SCHEMA = {
    "name": NAME,
    "description": (
        "Fetch a curated bundle of FRED macro/sector series relevant to a company's sector. "
        "Returns 3-5 indicators with recent observations. Pass the company's sector or industry "
        "(e.g. 'technology', 'banks', 'energy', 'auto', 'retail'). Use the data in the 'Macro Context' "
        "section of the portfolio."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "sector": {
                "type": "string",
                "description": "Sector keyword. Examples: technology, financial, banks, energy, auto, retail, "
                "consumer_discretionary, consumer_staples, healthcare, industrials, real_estate, utilities, telecom, default",
            },
            "observation_count": {"type": "integer", "minimum": 6, "maximum": 60, "default": 18},
        },
        "required": ["sector"],
    },
}


def _resolve_bundle(sector: str) -> tuple[str, list[tuple[str, str, str]]]:
    key = sector.lower().strip().replace(" ", "_").replace("&", "and")
    # exact match
    if key in _SECTOR_SERIES:
        return key, _SECTOR_SERIES[key]
    # substring fallback
    for k, v in _SECTOR_SERIES.items():
        if k != "default" and (k in key or key in k):
            return k, v
    return "default", _SECTOR_SERIES["default"]


async def run(ctx: RunContext, sector: str, observation_count: int = 18) -> dict:
    chain = SourceChain(ctx, NAME)
    bundle_key, bundle = _resolve_bundle(sector)

    async def fetch_one(series_id: str) -> dict | None:
        try:
            obs = await fred.series_observations(ctx, series_id, limit=observation_count)
            return {
                "series_id": series_id,
                "observations": [
                    {"date": o.get("date"), "value": o.get("value")} for o in obs
                ],
            }
        except Exception:
            return None

    # Track via chain (for UI events) but parallel-fetch
    chain.attempted.append("fred")
    await ctx.emit("source_try", {"tool": NAME, "source": "fred"})
    try:
        results = await asyncio.gather(*[fetch_one(sid) for sid, *_ in bundle])
    except Exception as e:
        chain.errors["fred"] = f"{type(e).__name__}: {e}"
        await ctx.emit("source_error", {"tool": NAME, "source": "fred", "reason": str(e)})
        return chain.fail(hint="FRED unavailable.").to_dict()

    series = []
    for (sid, label, why), data in zip(bundle, results):
        if data:
            data["label"] = label
            data["why_relevant"] = why
            series.append(data)

    if not series:
        chain.errors["fred"] = "no series returned"
        await ctx.emit("source_miss", {"tool": NAME, "source": "fred", "reason": "no data"})
        return chain.fail(hint="No FRED series returned for this sector.").to_dict()

    await ctx.emit("source_ok", {"tool": NAME, "source": "fred", "note": f"{len(series)} series"})
    return chain.success(
        {
            "sector_resolved": bundle_key,
            "sector_requested": sector,
            "series": series,
        }
    ).to_dict()
