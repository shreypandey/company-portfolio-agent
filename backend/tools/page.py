"""fetch_page — read a specific URL.

Two-tier strategy:
  1. trafilatura (fast, no API call). Works for SSR pages (Wikipedia, SEC, most news).
  2. Tavily /extract — fallback when trafilatura returns empty or suspiciously thin
     text from a sizable HTML page (classic JS-rendered / paywall / Cloudflare signal).
"""
from __future__ import annotations

from ..context import RunContext
from ..envelope import SourceChain
from ..sources import page_fetcher, tavily

NAME = "fetch_page"

SCHEMA = {
    "name": NAME,
    "description": (
        "Fetch a URL and return its cleaned readable text. Uses trafilatura for static "
        "pages (fast); transparently falls back to Tavily /extract for JS-rendered or "
        "blocked pages. Use after search_web, get_filings_narrative, or any tool that "
        "returns a URL you want to read deeply."
    ),
    "input_schema": {
        "type": "object",
        "properties": {"url": {"type": "string", "format": "uri"}},
        "required": ["url"],
    },
}


async def run(ctx: RunContext, url: str) -> dict:
    chain = SourceChain(ctx, NAME)

    # Tier 1: fast static-HTML path
    data = await chain.try_("page_fetcher", lambda: page_fetcher.fetch_clean(ctx, url))
    if data:
        return chain.success(data).to_dict()

    # Tier 2: JS-rendered fallback via Tavily /extract
    data = await chain.try_("tavily_extract", lambda: tavily.extract(ctx, url))
    if data:
        return chain.success(data).to_dict()

    return chain.fail(
        hint="Page unreachable: trafilatura got nothing readable AND Tavily extract failed. URL may be invalid, deleted, or fully gated."
    ).to_dict()
