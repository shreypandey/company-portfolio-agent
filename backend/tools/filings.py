"""get_filings_narrative — pull 10-K narrative sections from SEC EDGAR.

Resolves company → CIK → most recent 10-K → fetches the primary doc → extracts
clean text via trafilatura → uses heuristics to slice into Item 1 (Business),
Item 1A (Risk Factors), and Item 7 (MD&A).
"""
from __future__ import annotations

import re

from ..context import RunContext
from ..envelope import SourceChain
from ..sources import page_fetcher, sec_edgar

NAME = "get_filings_narrative"

SCHEMA = {
    "name": NAME,
    "description": (
        "Pull narrative sections from the most recent SEC 10-K: Item 1 (Business), Item 1A "
        "(Risk Factors), Item 7 (MD&A). US public companies only. Returns clean text per section "
        "plus the filing URL. Use for qualitative depth: business description, competitive landscape, "
        "key risks, management's view."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Company name or ticker"},
            "sections": {
                "type": "array",
                "items": {"type": "string", "enum": ["business", "risk", "mda"]},
                "default": ["business", "risk", "mda"],
            },
        },
        "required": ["query"],
    },
}

# Anchor patterns for 10-K section parsing. Conservative: match "Item 1." style headings
# (case-insensitive, with optional dot, allowing for stray whitespace from extraction).
_SECTION_ANCHORS = {
    "business":    re.compile(r"(?im)^\s*item\s*1\.?\s*\.?\s*business\b"),
    "risk":        re.compile(r"(?im)^\s*item\s*1a\.?\s*\.?\s*risk\s+factors?\b"),
    "ub_resolved": re.compile(r"(?im)^\s*item\s*1b\.?\s*"),     # boundary for risk
    "uc":          re.compile(r"(?im)^\s*item\s*1c\.?\s*"),     # cybersecurity (newer)
    "ud_unresolved": re.compile(r"(?im)^\s*item\s*2\.?\s*"),    # boundary
    "mda":         re.compile(r"(?im)^\s*item\s*7\.?\s*\.?\s*management'?s?\s+discussion"),
    "mda_end":     re.compile(r"(?im)^\s*item\s*7a\.?\s*|^\s*item\s*8\.?\s*"),  # boundaries
    "business_end":re.compile(r"(?im)^\s*item\s*1a\.?\s*|^\s*item\s*2\.?\s*"),
}


def _slice_section(text: str, start_re: re.Pattern, end_re: re.Pattern) -> str | None:
    m = start_re.search(text)
    if not m:
        return None
    start = m.start()
    e = end_re.search(text, m.end())
    end = e.start() if e else len(text)
    return text[start:end].strip()


async def run(ctx: RunContext, query: str, sections: list[str] | None = None) -> dict:
    sections = sections or ["business", "risk", "mda"]
    chain = SourceChain(ctx, NAME)

    # 1) Resolve to CIK
    resolved = await chain.try_("sec_edgar", lambda: sec_edgar.resolve(ctx, query))
    if not resolved:
        return chain.fail(
            hint="Company is not a US SEC filer. Foreign/private companies may file 20-F or no SEC docs at all."
        ).to_dict()

    cik = resolved["cik"]
    ticker = resolved["ticker"]

    # 2) Latest 10-K
    filings = await chain.try_("sec_edgar", lambda: sec_edgar.latest_filings_of_type(ctx, cik, ["10-K"], count=1))
    if not filings:
        return chain.fail(hint="No 10-K on file for this issuer.").to_dict()

    filing = filings[0]
    url = filing.get("url")
    if not url:
        return chain.fail(hint="10-K primary document URL missing.").to_dict()

    # 3) Fetch + clean
    page = await chain.try_("page_fetcher", lambda: page_fetcher.fetch_clean(ctx, url))
    if not page:
        return chain.fail(hint="Could not fetch the 10-K document.").to_dict()

    text = page["text"]

    # 4) Slice sections
    out_sections: dict[str, dict] = {}
    if "business" in sections:
        body = _slice_section(text, _SECTION_ANCHORS["business"], _SECTION_ANCHORS["business_end"])
        out_sections["business"] = {"found": bool(body), "text": body}
    if "risk" in sections:
        body = _slice_section(text, _SECTION_ANCHORS["risk"], _SECTION_ANCHORS["ud_unresolved"])
        out_sections["risk"] = {"found": bool(body), "text": body}
    if "mda" in sections:
        body = _slice_section(text, _SECTION_ANCHORS["mda"], _SECTION_ANCHORS["mda_end"])
        out_sections["mda"] = {"found": bool(body), "text": body}

    found_any = any(s["found"] for s in out_sections.values())
    return chain.success_multi(
        {
            "company": resolved["name"],
            "ticker": ticker,
            "cik": cik,
            "filing": filing,
            "sections": out_sections,
            "extraction_quality": "high" if found_any else "low (no section anchors matched — full text returned)",
            "full_text": text if not found_any else None,
        },
        sources_used=["sec_edgar", "page_fetcher"],
        partial=not found_any,
        notes="Section anchors not found — returning full cleaned text" if not found_any else "",
    ).to_dict()
