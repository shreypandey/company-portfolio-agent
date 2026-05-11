"""Typed source-layer errors.

Sources (raw API wrappers) raise these; semantic tools catch them and aggregate into
the chain's `errors_by_source` map. The LLM never sees these directly.
"""
from __future__ import annotations


class SourceError(Exception):
    def __init__(
        self,
        source: str,
        endpoint: str,
        message: str,
        *,
        status: int | None = None,
        retriable: bool = False,
    ):
        self.source = source
        self.endpoint = endpoint
        self.message = message
        self.status = status
        self.retriable = retriable
        suffix = f" (HTTP {status})" if status else ""
        super().__init__(f"{source}.{endpoint}: {message}{suffix}")


class SourceMiss(SourceError):
    """Source responded successfully but has no data for this entity.

    Distinct from SourceError because it's not really a 'failure' — it's a clean
    'I don't cover this'. Treated by chains as a normal 'try next' signal but
    logged with a different event so the UI can distinguish.
    """
    pass


class RateLimitError(SourceError):
    """Source rejected us for rate-limit reasons. Retriable after backoff."""
    def __init__(self, source: str, endpoint: str, message: str = "rate limited"):
        super().__init__(source, endpoint, message, status=429, retriable=True)
