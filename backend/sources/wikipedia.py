"""Wikipedia REST + opensearch. Global, free, no key."""
from __future__ import annotations

import urllib.parse

from ..context import RunContext
from . import _http
from .errors import SourceError, SourceMiss

_NAME = "wikipedia"
_HEADERS = {"User-Agent": "PortfolioAgent/2.0 (research)"}


async def summary(ctx: RunContext, query: str) -> dict:
    # 1. Resolve via opensearch
    opens = await _http.request(
        ctx,
        source=_NAME,
        endpoint="opensearch",
        url="https://en.wikipedia.org/w/api.php",
        headers=_HEADERS,
        params={"action": "opensearch", "search": query, "limit": 1, "format": "json"},
        timeout=15.0,
    )
    titles = opens[1] if isinstance(opens, list) and len(opens) > 1 else []
    if not titles:
        raise SourceMiss(_NAME, "opensearch", f"no Wikipedia page for '{query}'")
    title = titles[0]

    # 2. Pull summary
    s = await _http.request(
        ctx,
        source=_NAME,
        endpoint="summary",
        url=f"https://en.wikipedia.org/api/rest_v1/page/summary/{urllib.parse.quote(title)}",
        headers=_HEADERS,
        timeout=15.0,
    )
    return {
        "title": s.get("title"),
        "description": s.get("description"),
        "extract": s.get("extract") or "",
        "url": ((s.get("content_urls") or {}).get("desktop") or {}).get("page"),
        "wikibase_item": s.get("wikibase_item"),
    }
