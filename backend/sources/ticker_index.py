"""SEC EDGAR ticker index — lazy-loaded once per process, queried in-memory after.

The full US public company list lives at https://www.sec.gov/files/company_tickers.json
(~10K entries, ~1MB). We fetch once at first use and answer all subsequent searches
from memory. Used by:
  - `/symbols/search` FastAPI endpoint (typeahead in the UI)
  - `sec_edgar.resolve` (ticker resolution during agent runs)
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

import httpx

from ..config import SEC_USER_AGENT

log = logging.getLogger("agent.ticker_index")

_HEADERS = {"User-Agent": SEC_USER_AGENT, "Accept": "application/json"}

_records: Optional[list[dict]] = None  # list of {ticker, name, cik}
_by_ticker: Optional[dict[str, dict]] = None
_lock = asyncio.Lock()


async def _load() -> None:
    global _records, _by_ticker
    async with _lock:
        if _records is not None:
            return
        async with httpx.AsyncClient(timeout=30.0, headers=_HEADERS) as c:
            r = await c.get("https://www.sec.gov/files/company_tickers.json")
            r.raise_for_status()
            raw = r.json()
        records: list[dict] = []
        by_ticker: dict[str, dict] = {}
        for _, entry in (raw or {}).items():
            ticker = (entry.get("ticker") or "").upper()
            name = entry.get("title") or ""
            cik = str(entry.get("cik_str") or "").zfill(10)
            rec = {"ticker": ticker, "name": name, "cik": cik}
            records.append(rec)
            if ticker:
                by_ticker[ticker] = rec
        _records = records
        _by_ticker = by_ticker
        log.info("loaded SEC EDGAR ticker index: %d records", len(_records))


async def search(query: str, limit: int = 10) -> list[dict]:
    """Search the index. Returns ranked matches (best first).

    Ranking:
      1. Exact ticker match
      2. Ticker startswith query
      3. Name startswith query (word boundary aware)
      4. Substring match on name
    """
    await _load()
    assert _records is not None and _by_ticker is not None

    q = query.strip().upper()
    if not q:
        return []

    seen_ciks: set[str] = set()
    results: list[dict] = []

    def add(rec: dict) -> None:
        if rec["cik"] in seen_ciks:
            return
        seen_ciks.add(rec["cik"])
        results.append(rec)

    # 1. Exact ticker
    exact = _by_ticker.get(q)
    if exact:
        add(exact)

    # 2. Ticker startswith
    for rec in _records:
        if len(results) >= limit:
            break
        if rec["ticker"].startswith(q):
            add(rec)

    # 3. Name startswith (after normalizing — strip leading "The ")
    for rec in _records:
        if len(results) >= limit:
            break
        name_upper = rec["name"].upper()
        normalized = name_upper[4:] if name_upper.startswith("THE ") else name_upper
        if name_upper.startswith(q) or normalized.startswith(q):
            add(rec)

    # 4. Substring on name
    for rec in _records:
        if len(results) >= limit:
            break
        if q in rec["name"].upper():
            add(rec)

    return results[:limit]


async def resolve_one(query: str) -> dict | None:
    """Return the single best match, or None."""
    matches = await search(query, limit=1)
    return matches[0] if matches else None
