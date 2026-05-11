"""Tool registry — composes the 13 semantic tools and dispatches by name.

Each tool module exports NAME, SCHEMA, and `async def run(ctx, **args) -> dict`.
The agent passes a RunContext into every dispatch; the tools handle source fallback,
caching, and event emission internally.
"""
from __future__ import annotations

from typing import Awaitable, Callable

from ..context import RunContext
from . import (
    analyst,
    competitors,
    earnings,
    excel,
    filings,
    macro,
    metrics,
    news_events,
    overview,
    ownership,
    page,
    search,
    statements,
)

ToolRun = Callable[..., Awaitable[dict]]

_MODULES = [
    overview,
    statements,
    metrics,
    earnings,
    analyst,
    ownership,
    competitors,
    news_events,
    filings,
    macro,
    search,
    page,
    excel,
]

_RUNS: dict[str, ToolRun] = {m.NAME: m.run for m in _MODULES}
SCHEMAS: list[dict] = [m.SCHEMA for m in _MODULES]

# Sanity check
_SCHEMA_NAMES = {s["name"] for s in SCHEMAS}
_RUN_NAMES = set(_RUNS.keys())
assert _SCHEMA_NAMES == _RUN_NAMES, f"schema/run mismatch: schemas-only={_SCHEMA_NAMES - _RUN_NAMES}, runs-only={_RUN_NAMES - _SCHEMA_NAMES}"


async def dispatch(name: str, args: dict, ctx: RunContext) -> dict:
    fn = _RUNS.get(name)
    if fn is None:
        return {
            "ok": False,
            "sources_attempted": [],
            "errors_by_source": {"registry": f"unknown tool '{name}'"},
            "hint": f"Valid tools: {sorted(_RUN_NAMES)}",
        }
    try:
        return await fn(ctx, **(args or {}))
    except TypeError as e:
        return {
            "ok": False,
            "sources_attempted": [],
            "errors_by_source": {"registry": f"bad args for '{name}': {e}"},
            "hint": "Check the tool's input_schema and pass the required fields.",
        }
    except Exception as e:
        return {
            "ok": False,
            "sources_attempted": [],
            "errors_by_source": {"registry": f"{type(e).__name__}: {e}"},
            "hint": "Tool crashed unexpectedly.",
        }
