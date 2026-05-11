"""Per-run context: shared http client + event sink + dedupe cache.

Passed by reference into every source wrapper and semantic tool. Replaces the global
state pattern of v1 and gives us:
  - One httpx session per run (connection pooling)
  - Event emission with the current parent tool id (for nested UI rendering)
  - Cross-tool HTTP cache so e.g. `get_company_overview` and `get_competitors` don't
    both fetch the same FMP profile twice.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import httpx

EventSink = Callable[[str, dict], Awaitable[None]]


@dataclass
class RunContext:
    run_id: str
    sink: EventSink
    http: httpx.AsyncClient
    cache: dict[tuple, Any] = field(default_factory=dict)
    parent_tool_id: str | None = None  # set by agent.py before each tool dispatch

    async def emit(self, event_type: str, data: dict) -> None:
        """Forward an event to the SSE sink, tagging with parent tool id if set."""
        if self.parent_tool_id:
            data = {**data, "tool_id": self.parent_tool_id}
        await self.sink(event_type, data)

    @staticmethod
    def cache_key(source: str, endpoint: str, **args) -> tuple:
        normalized = tuple(sorted((k, str(v)) for k, v in args.items()))
        return (source, endpoint, normalized)
