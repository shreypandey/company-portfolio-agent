"""Result envelope + SourceChain helper.

Every semantic tool returns a `Result` (serialized to dict for the LLM). The shape is
uniform so the LLM can reliably read `ok`, `data`, `sources_used`, `hint`. Errors are
aggregated by source, not surfaced as exceptions.

SourceChain is the building block used inside each tool: register attempts, capture
errors by source, emit fine-grained UI events, terminate with either success or a
structured failure.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from .context import RunContext
from .sources.errors import SourceError, SourceMiss


@dataclass
class Result:
    ok: bool
    data: dict | None = None
    sources_used: list[str] = field(default_factory=list)
    sources_attempted: list[str] = field(default_factory=list)
    errors_by_source: dict[str, str] = field(default_factory=dict)
    partial: bool = False
    notes: str = ""
    hint: str = ""

    @classmethod
    def success(
        cls,
        data: dict,
        sources_used: list[str],
        *,
        sources_attempted: list[str] | None = None,
        partial: bool = False,
        notes: str = "",
    ) -> "Result":
        return cls(
            ok=True,
            data=data,
            sources_used=sources_used,
            sources_attempted=sources_attempted or sources_used,
            partial=partial,
            notes=notes,
        )

    @classmethod
    def failure(
        cls,
        sources_attempted: list[str],
        errors_by_source: dict[str, str],
        hint: str = "",
    ) -> "Result":
        return cls(
            ok=False,
            sources_attempted=sources_attempted,
            errors_by_source=errors_by_source,
            hint=hint,
        )

    def to_dict(self) -> dict:
        if self.ok:
            out = {
                "ok": True,
                "data": self.data,
                "sources_used": self.sources_used,
                "sources_attempted": self.sources_attempted,
            }
            if self.partial:
                out["partial"] = True
            if self.notes:
                out["notes"] = self.notes
            return out
        return {
            "ok": False,
            "sources_attempted": self.sources_attempted,
            "errors_by_source": self.errors_by_source,
            "hint": self.hint or "Data not available from any source.",
        }


class SourceChain:
    """Sequentially try source functions; aggregate errors; emit nested events.

    Usage:
        chain = SourceChain(ctx, tool="get_company_overview")
        data = await chain.try_("fmp", lambda: _build_from_fmp(ctx, q))
        if data:
            return chain.success(data)
        data = await chain.try_("yahoo", lambda: _build_from_yahoo(ctx, q))
        if data:
            return chain.success(data)
        return chain.fail(hint="…").to_dict()
    """

    def __init__(self, ctx: RunContext, tool: str):
        self.ctx = ctx
        self.tool = tool
        self.attempted: list[str] = []
        self.errors: dict[str, str] = {}

    async def try_(
        self,
        source: str,
        fn: Callable[[], Awaitable[Any]],
    ) -> Any | None:
        """Run `fn`. Return its result on success, None on SourceError/SourceMiss."""
        self.attempted.append(source)
        await self.ctx.emit("source_try", {"tool": self.tool, "source": source})
        try:
            data = await fn()
            if data is None:
                self.errors[source] = "returned None"
                await self.ctx.emit(
                    "source_miss",
                    {"tool": self.tool, "source": source, "reason": "returned None"},
                )
                return None
            await self.ctx.emit("source_ok", {"tool": self.tool, "source": source})
            return data
        except SourceMiss as e:
            self.errors[source] = f"miss: {e.message}"
            await self.ctx.emit(
                "source_miss",
                {"tool": self.tool, "source": source, "reason": e.message},
            )
            return None
        except SourceError as e:
            self.errors[source] = str(e)
            await self.ctx.emit(
                "source_error",
                {
                    "tool": self.tool,
                    "source": source,
                    "reason": e.message,
                    "status": e.status,
                },
            )
            return None
        except Exception as e:  # defensive — unexpected source crash
            self.errors[source] = f"unexpected {type(e).__name__}: {e}"
            await self.ctx.emit(
                "source_error",
                {"tool": self.tool, "source": source, "reason": f"crash: {e}"},
            )
            return None

    def success(self, data: dict, *, partial: bool = False, notes: str = "") -> Result:
        # The successful source is the last one in `attempted` (the one that worked).
        used = [self.attempted[-1]] if self.attempted else []
        return Result.success(
            data,
            sources_used=used,
            sources_attempted=list(self.attempted),
            partial=partial,
            notes=notes,
        )

    def success_multi(self, data: dict, sources_used: list[str], *, partial: bool = False, notes: str = "") -> Result:
        """For tools that legitimately combine multiple sources."""
        return Result.success(
            data,
            sources_used=sources_used,
            sources_attempted=list(self.attempted),
            partial=partial,
            notes=notes,
        )

    def fail(self, hint: str = "") -> Result:
        return Result.failure(list(self.attempted), dict(self.errors), hint=hint)
