"""Generic URL fetcher + trafilatura extraction.

Returns SourceMiss when the page yields no extractable text OR when the extraction
is suspiciously thin from a sizable HTML payload — both are strong signals of a
JS-rendered SPA, paywall, or anti-bot challenge that Tavily /extract can handle.
The SourceMiss carries a `js_suspected` flag in its message so the caller (tools/page.py)
can decide to escalate to Tavily.
"""
from __future__ import annotations

import asyncio

import trafilatura

from ..context import RunContext
from . import _http
from .errors import SourceError, SourceMiss

_NAME = "page"
_DEFAULT_UA = "Mozilla/5.0 (compatible; PortfolioAgent/2.0)"

# Heuristic: extracted text shorter than this from raw HTML larger than this →
# almost certainly a JS-rendered / paywall / challenge page. Numbers chosen to
# avoid false positives on legitimate short news blurbs.
_JS_EXTRACT_THRESHOLD = 200
_JS_RAW_THRESHOLD = 8000


def _extract(html: str) -> str:
    return trafilatura.extract(html, include_comments=False, include_tables=True, favor_recall=True) or ""


async def fetch_clean(ctx: RunContext, url: str) -> dict:
    cache_key = ctx.cache_key(_NAME, "fetch_clean", url=url)
    if cache_key in ctx.cache:
        await ctx.emit("source_cache_hit", {"source": _NAME, "endpoint": "fetch_clean"})
        return ctx.cache[cache_key]

    html, status = await _http.request_text(
        ctx,
        source=_NAME,
        endpoint="fetch",
        url=url,
        headers={"User-Agent": _DEFAULT_UA},
        timeout=25.0,
    )
    text = await asyncio.to_thread(_extract, html)
    raw_size = len(html)
    extracted_size = len(text) if text else 0

    # Signal up to the caller whether this looks like a JS-rendered page so it can
    # decide whether to escalate to Tavily /extract.
    js_suspected = (
        extracted_size == 0
        or (extracted_size < _JS_EXTRACT_THRESHOLD and raw_size > _JS_RAW_THRESHOLD)
    )

    if not text or js_suspected:
        # The message carries the signal — tools/page.py inspects it.
        flag = "js_suspected" if js_suspected else "no_text"
        raise SourceMiss(
            _NAME,
            "extract",
            f"{flag} (raw={raw_size}, extracted={extracted_size})",
        )

    result = {
        "url": url,
        "status": status,
        "raw_length": raw_size,
        "extracted_length": extracted_size,
        "text": text,
    }
    ctx.cache[cache_key] = result
    return result
