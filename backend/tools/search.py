"""search_web — generic web search. Tavily primary (LLM-tuned, JS-rendered content),
Brave fallback when Tavily is missing/quota-exhausted, Wikipedia as last resort."""
from __future__ import annotations

from ..context import RunContext
from ..envelope import SourceChain
from ..sources import brave, tavily, wikipedia

NAME = "search_web"

SCHEMA = {
    "name": NAME,
    "description": (
        "Generic web search for gap-filling. Use when you need to find sources for a fact "
        "no semantic tool covers (e.g. private-co. press coverage, historical context). "
        "Returns titles + URLs + descriptions (often with pre-extracted content snippets). "
        "Follow up with fetch_page only when you need the FULL page content."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "kind": {"type": "string", "enum": ["general", "news"], "default": "general"},
            "count": {"type": "integer", "minimum": 1, "maximum": 15, "default": 8},
            "days": {
                "type": "integer", "minimum": 1, "maximum": 365, "default": 30,
                "description": "Only used when kind='news'. Window in days.",
            },
        },
        "required": ["query"],
    },
}


async def run(
    ctx: RunContext,
    query: str,
    kind: str = "general",
    count: int = 8,
    days: int = 30,
) -> dict:
    chain = SourceChain(ctx, NAME)
    topic = "news" if kind == "news" else "general"

    # Primary: Tavily (handles JS-rendered pages server-side, LLM-tuned content)
    data = await chain.try_(
        "tavily",
        lambda: tavily.search(ctx, query, topic=topic, max_results=count, days=days if topic == "news" else None),
    )
    if data:
        return chain.success({"query": query, "kind": kind, "results": data}).to_dict()

    # Fallback: Brave (if Tavily key missing / quota exhausted)
    if kind == "news":
        data = await chain.try_("brave_news", lambda: brave.news_search(ctx, query, count=count))
        if data:
            return chain.success({"query": query, "kind": kind, "results": data}).to_dict()
        data = await chain.try_("brave_web", lambda: brave.web_search(ctx, query, count=count))
        if data:
            return chain.success({"query": query, "kind": "general (news fallback)", "results": data}).to_dict()
    else:
        data = await chain.try_("brave_web", lambda: brave.web_search(ctx, query, count=count))
        if data:
            return chain.success({"query": query, "kind": kind, "results": data}).to_dict()

    # Last resort: Wikipedia opensearch
    data = await chain.try_("wikipedia", lambda: wikipedia.summary(ctx, query))
    if data:
        return chain.success(
            {
                "query": query,
                "kind": kind,
                "results": [
                    {
                        "title": data.get("title", ""),
                        "url": data.get("url", ""),
                        "description": data.get("extract") or "",
                    }
                ],
            }
        ).to_dict()

    return chain.fail(hint="All search sources failed or returned no results. Try a different query.").to_dict()
