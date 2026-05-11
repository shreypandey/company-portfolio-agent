"""Tavily Search + Extract — LLM-tuned search with JS rendering.

Search:  POST /search   — returns ranked results with title/url/content snippets.
                          With include_raw_content=true, each result includes the
                          full clean page text inline (JS-rendered server-side).
Extract: POST /extract  — fetches a specific URL with JS rendering and returns
                          clean markdown content. Used as fallback in page_fetcher
                          when trafilatura returns empty/thin text.

Free tier: 1000 search + 1000 extract requests per month.
"""
from __future__ import annotations

from typing import Any

from ..config import TAVILY_API_KEY
from ..context import RunContext
from . import _http
from .errors import RateLimitError, SourceError, SourceMiss

_BASE = "https://api.tavily.com"
_NAME = "tavily"


async def _post(ctx: RunContext, path: str, body: dict) -> dict:
    if not TAVILY_API_KEY:
        raise SourceError(_NAME, path.lstrip("/"), "no TAVILY_API_KEY configured")
    # Tavily accepts api_key in body OR in Authorization header — body keeps URL clean for caching.
    body = {**body, "api_key": TAVILY_API_KEY}
    data = await _http.request(
        ctx,
        source=_NAME,
        endpoint=path.lstrip("/"),
        url=f"{_BASE}{path}",
        method="POST",
        json=body,
        timeout=30.0,
    )
    if not isinstance(data, dict):
        raise SourceError(_NAME, path.lstrip("/"), "non-dict response")
    if data.get("error"):
        msg = str(data["error"])
        if "rate" in msg.lower() or "quota" in msg.lower():
            raise RateLimitError(_NAME, path.lstrip("/"), msg[:200])
        raise SourceError(_NAME, path.lstrip("/"), msg[:200])
    return data


async def search(
    ctx: RunContext,
    query: str,
    *,
    topic: str = "general",
    max_results: int = 10,
    days: int | None = None,
    include_raw_content: bool = False,
    search_depth: str = "basic",
) -> list[dict]:
    """topic: "general" | "news". days: only used when topic="news"."""
    body: dict[str, Any] = {
        "query": query,
        "topic": topic,
        "max_results": min(max(int(max_results), 1), 20),
        "search_depth": search_depth,
        "include_raw_content": include_raw_content,
        "include_answer": False,
        "include_images": False,
    }
    if topic == "news" and days:
        body["days"] = min(max(int(days), 1), 365)

    data = await _post(ctx, "/search", body)
    results = data.get("results") or []
    if not results:
        raise SourceMiss(_NAME, "search", f"no results for '{query}'")

    # Normalize shape to match what other tools expect
    normalized = []
    for r in results:
        normalized.append(
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "description": r.get("content", "") or "",
                "published_date": r.get("published_date"),
                "score": r.get("score"),
                "raw_content": r.get("raw_content"),
            }
        )
    return normalized


async def extract(ctx: RunContext, url: str) -> dict:
    """Fetch a single URL with JS rendering. Returns {url, title, content}."""
    data = await _post(ctx, "/extract", {"urls": [url]})
    results = data.get("results") or []
    if not results:
        failed = data.get("failed_results") or []
        reason = failed[0].get("error") if failed else "no result"
        raise SourceMiss(_NAME, "extract", f"failed to extract {url}: {reason}")
    r = results[0]
    text = r.get("raw_content") or ""
    if not text.strip():
        raise SourceMiss(_NAME, "extract", "extracted content empty")
    return {
        "url": r.get("url") or url,
        "title": r.get("title", ""),
        "text": text,
        "raw_length": len(text),
        "extracted_length": len(text),
    }
