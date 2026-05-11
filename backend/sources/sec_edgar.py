"""SEC EDGAR raw wrappers — ticker index + submissions JSON + 10-K narrative URLs."""
from __future__ import annotations

import asyncio
import re
from typing import Any

from ..config import SEC_USER_AGENT
from ..context import RunContext
from . import _http, ticker_index
from .errors import SourceError, SourceMiss

_NAME = "sec_edgar"
_HEADERS = {"User-Agent": SEC_USER_AGENT, "Accept": "application/json"}

# Process-global ticker→CIK index (lazy, one fetch per process lifetime).
_index: dict[str, dict] | None = None
_index_lock = asyncio.Lock()


async def _load_index(ctx: RunContext) -> dict[str, dict]:
    global _index
    async with _index_lock:
        if _index is not None:
            return _index
        data = await _http.request(
            ctx,
            source=_NAME,
            endpoint="company_tickers",
            url="https://www.sec.gov/files/company_tickers.json",
            headers=_HEADERS,
            timeout=30.0,
        )
        idx: dict[str, dict] = {}
        for _, entry in (data or {}).items():
            ticker = (entry.get("ticker") or "").upper()
            name = entry.get("title") or ""
            cik = str(entry.get("cik_str") or "").zfill(10)
            if ticker:
                idx[ticker] = {"cik": cik, "ticker": ticker, "name": name}
            if name:
                idx[name.upper()] = {"cik": cik, "ticker": ticker, "name": name}
        _index = idx
        return idx


def _normalize(s: str) -> str:
    return re.sub(r"[^A-Z0-9 ]", "", s.upper()).strip()


async def resolve(ctx: RunContext, query: str) -> dict:
    """Resolve a name/ticker to {ticker, name, cik} using the shared in-memory index."""
    match = await ticker_index.resolve_one(query)
    if match:
        return match
    raise SourceMiss(_NAME, "ticker_index", f"no SEC filer matches '{query}'")


async def submissions(ctx: RunContext, cik: str) -> dict:
    cik_padded = cik.zfill(10)
    return await _http.request(
        ctx,
        source=_NAME,
        endpoint="submissions",
        url=f"https://data.sec.gov/submissions/CIK{cik_padded}.json",
        headers=_HEADERS,
        timeout=20.0,
    )


def filing_url(cik: str, accession: str, primary_doc: str) -> str:
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/{primary_doc}"


async def latest_filings_of_type(ctx: RunContext, cik: str, forms: list[str], count: int = 10) -> list[dict]:
    sub = await submissions(ctx, cik)
    recent = (sub.get("filings", {}) or {}).get("recent", {}) or {}
    out: list[dict] = []
    f_forms = recent.get("form", [])
    accs = recent.get("accessionNumber", [])
    dates = recent.get("filingDate", [])
    primary = recent.get("primaryDocument", [])
    items = recent.get("items", [])  # 8-K item codes (e.g. "2.02,9.01")
    for i, form in enumerate(f_forms):
        if form in forms:
            out.append(
                {
                    "form": form,
                    "accession": accs[i] if i < len(accs) else None,
                    "filed": dates[i] if i < len(dates) else None,
                    "primary_doc": primary[i] if i < len(primary) else None,
                    "items": items[i] if i < len(items) else None,
                    "url": (
                        filing_url(cik, accs[i], primary[i])
                        if i < len(accs) and i < len(primary) and primary[i]
                        else None
                    ),
                }
            )
            if len(out) >= count:
                break
    if not out:
        raise SourceMiss(_NAME, "filings", f"no {forms} filings found")
    return out


async def fetch_filing_text(ctx: RunContext, url: str) -> str:
    """Fetch a filing's HTML and return raw HTML text (cleaning happens upstream)."""
    text, status = await _http.request_text(
        ctx, source=_NAME, endpoint="filing_doc", url=url, headers={"User-Agent": SEC_USER_AGENT}, timeout=30.0
    )
    return text
