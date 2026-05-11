"""Brave Search — web + news. Global, JSON, generous free tier."""
from __future__ import annotations

from typing import Any

from ..config import BRAVE_API_KEY
from ..context import RunContext
from . import _http
from .errors import SourceError, SourceMiss

_BASE = "https://api.search.brave.com/res/v1"
_NAME = "brave"


def _headers() -> dict:
    if not BRAVE_API_KEY:
        return {}
    return {"Accept": "application/json", "X-Subscription-Token": BRAVE_API_KEY}


async def web_search(ctx: RunContext, query: str, count: int = 8) -> list[dict]:
    if not BRAVE_API_KEY:
        raise SourceError(_NAME, "web", "no BRAVE_SEARCH_API_KEY configured")
    count = min(max(int(count), 1), 20)
    data = await _http.request(
        ctx,
        source=_NAME,
        endpoint="web",
        url=f"{_BASE}/web/search",
        headers=_headers(),
        params={"q": query, "count": count, "safesearch": "moderate", "text_decorations": "false"},
        timeout=20.0,
    )
    results = ((data or {}).get("web") or {}).get("results") or []
    if not results:
        raise SourceMiss(_NAME, "web", f"no web results for '{query}'")
    return [
        {
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "description": r.get("description") or "",
            "age": r.get("age", ""),
        }
        for r in results[:count]
    ]


async def news_search(ctx: RunContext, query: str, count: int = 8, freshness: str = "pm") -> list[dict]:
    if not BRAVE_API_KEY:
        raise SourceError(_NAME, "news", "no BRAVE_SEARCH_API_KEY configured")
    if freshness not in {"pd", "pw", "pm", "py"}:
        freshness = "pm"
    count = min(max(int(count), 1), 20)
    data = await _http.request(
        ctx,
        source=_NAME,
        endpoint="news",
        url=f"{_BASE}/news/search",
        headers=_headers(),
        params={"q": query, "count": count, "freshness": freshness},
        timeout=20.0,
    )
    results = (data or {}).get("results") or []
    if not results:
        raise SourceMiss(_NAME, "news", f"no news for '{query}'")
    return [
        {
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "description": r.get("description") or "",
            "age": r.get("age", ""),
            "source": (r.get("meta_url") or {}).get("hostname", ""),
        }
        for r in results[:count]
    ]
