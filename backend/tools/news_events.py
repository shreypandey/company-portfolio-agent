"""get_recent_news_and_events — Finnhub + Tavily + AV sentiment + SEC 8-Ks.

Parallel fetch from all available sources. Deduplicate by URL. Tavily is the
primary general-web news source (JS-rendered + LLM-tuned); Brave kept as a
fallback for if Tavily is missing/quota-exhausted. AV NEWS_SENTIMENT adds
sentiment scores (rate-limited — use sparingly).
"""
from __future__ import annotations

import asyncio
from datetime import datetime

from ..config import TAVILY_API_KEY
from ..context import RunContext
from ..envelope import SourceChain
from ..sources import alpha_vantage, brave, finnhub, sec_edgar, tavily
from ..sources.errors import SourceError, SourceMiss

NAME = "get_recent_news_and_events"

SCHEMA = {
    "name": NAME,
    "description": (
        "Get the company's recent news + material 8-K events + sentiment-scored items. Pulls in parallel "
        "from Finnhub company-news, Brave news, AV NEWS_SENTIMENT (US public), and SEC 8-K filings. "
        "Deduplicates by URL."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Company name or ticker"},
            "ticker": {"type": "string", "description": "Optional explicit ticker for ticker-tagged sources"},
            "days": {"type": "integer", "minimum": 1, "maximum": 90, "default": 30},
        },
        "required": [],
    },
}


def _ts_key(item: dict) -> float:
    for k in ("datetime", "time_published", "published_at", "publishedDate"):
        v = item.get(k)
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y%m%dT%H%M%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                try:
                    return datetime.strptime(v[:len(fmt) + 5].rstrip("Z"), fmt).timestamp()
                except Exception:
                    continue
    return 0.0


def _dedupe(items: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out = []
    for it in items:
        url = (it.get("url") or "").strip().lower()
        if not url or url in seen:
            continue
        seen.add(url)
        out.append(it)
    return out


async def run(ctx: RunContext, query: str | None = None, ticker: str | None = None, days: int = 30) -> dict:
    chain = SourceChain(ctx, NAME)

    query = (query or ticker or "").strip()

    tasks: dict[str, asyncio.Task] = {}
    if ticker:
        tasks["finnhub"] = asyncio.create_task(finnhub.company_news(ctx, ticker, days=days))
        tasks["alpha_vantage"] = asyncio.create_task(alpha_vantage.news_sentiment(ctx, ticker, limit=25))

    # Tavily primary for general-web news. Brave kept as fallback when Tavily missing/empty.
    news_query = f"{query} (earnings OR strategy OR launch OR announcement)"
    if TAVILY_API_KEY:
        tasks["tavily"] = asyncio.create_task(
            tavily.search(ctx, news_query, topic="news", max_results=15, days=days)
        )
    else:
        tasks["brave"] = asyncio.create_task(brave.news_search(ctx, news_query, count=15))

    collected: list[dict] = []
    sources_used: list[str] = []
    av_summary: dict | None = None
    sec_filings: list[dict] = []

    for name, task in tasks.items():
        chain.attempted.append(name)
        await ctx.emit("source_try", {"tool": NAME, "source": name})
        try:
            data = await task
            await ctx.emit("source_ok", {"tool": NAME, "source": name})
            sources_used.append(name)
            if name == "finnhub":
                for d in data:
                    collected.append(
                        {
                            "headline": d.get("headline"),
                            "url": d.get("url"),
                            "datetime": d.get("datetime"),
                            "source": f"finnhub:{d.get('source') or ''}",
                            "summary": d.get("summary") or "",
                            "image": d.get("image") or "",
                        }
                    )
            elif name == "alpha_vantage" and isinstance(data, dict):
                feed = data.get("feed") or []
                av_summary = {"ticker": ticker, "items": len(feed), "definition": data.get("sentiment_score_definition")}
                for d in feed:
                    collected.append(
                        {
                            "headline": d.get("title"),
                            "url": d.get("url"),
                            "time_published": d.get("time_published"),
                            "source": f"av:{d.get('source') or ''}",
                            "summary": d.get("summary") or "",
                            "av_sentiment_score": d.get("overall_sentiment_score"),
                            "av_sentiment_label": d.get("overall_sentiment_label"),
                            "av_topics": [t.get("topic") for t in (d.get("topics") or [])],
                            "av_ticker_sentiment": d.get("ticker_sentiment"),
                        }
                    )
            elif name == "tavily":
                for d in data:
                    collected.append(
                        {
                            "headline": d.get("title"),
                            "url": d.get("url"),
                            "published_date": d.get("published_date"),
                            "source": f"tavily",
                            "summary": d.get("description") or "",
                            "score": d.get("score"),
                        }
                    )
            elif name == "brave":
                for d in data:
                    collected.append(
                        {
                            "headline": d.get("title"),
                            "url": d.get("url"),
                            "age": d.get("age"),
                            "source": f"brave:{d.get('source') or ''}",
                            "summary": d.get("description") or "",
                        }
                    )
        except (SourceError, SourceMiss) as e:
            chain.errors[name] = str(e)
            await ctx.emit("source_error", {"tool": NAME, "source": name, "reason": str(e)})
        except Exception as e:
            chain.errors[name] = f"{type(e).__name__}: {e}"
            await ctx.emit("source_error", {"tool": NAME, "source": name, "reason": str(e)})

    # SEC 8-K filings if US ticker
    if ticker:
        chain.attempted.append("sec_edgar")
        await ctx.emit("source_try", {"tool": NAME, "source": "sec_edgar"})
        try:
            resolved = await sec_edgar.resolve(ctx, ticker)
            filings = await sec_edgar.latest_filings_of_type(ctx, resolved["cik"], ["8-K"], count=10)
            sec_filings = filings
            sources_used.append("sec_edgar")
            await ctx.emit("source_ok", {"tool": NAME, "source": "sec_edgar", "note": f"{len(filings)} 8-Ks"})
        except (SourceError, SourceMiss) as e:
            chain.errors["sec_edgar"] = str(e)
            await ctx.emit("source_error", {"tool": NAME, "source": "sec_edgar", "reason": str(e)})

    if not collected and not sec_filings:
        return chain.fail(hint="No news from any source.").to_dict()

    deduped = _dedupe(collected)
    deduped.sort(key=_ts_key, reverse=True)
    # Most recent 40 is enough signal for a portfolio narrative — keeps payload < 60KB.
    top_items = deduped[:40]

    return chain.success_multi(
        {
            "query": query,
            "ticker": ticker,
            "window_days": days,
            "news_items": top_items,
            "news_count_total": len(collected),
            "news_count_deduped": len(deduped),
            "news_count_returned": len(top_items),
            "av_sentiment_summary": av_summary,
            "sec_8k_filings": sec_filings,
        },
        sources_used=sources_used,
    ).to_dict()
