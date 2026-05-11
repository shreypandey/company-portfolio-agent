"""LLM provider factory.

Selection via .env:
    LLM_PROVIDER = anthropic | gemini

Both providers read the API key from LLM_API_KEY.
For anthropic: uses ANTHROPIC_MODEL (defaults to claude-sonnet-4-6).
For gemini:    uses LLM_MODEL (Google native SDK).
"""
from __future__ import annotations

from ..config import (
    ANTHROPIC_MODEL,
    LLM_API_KEY,
    LLM_MODEL,
    LLM_PROVIDER,
)
from .base import BaseLLMClient, EventSink, FinalTurn


def get_llm_client() -> BaseLLMClient:
    provider = (LLM_PROVIDER or "anthropic").lower()

    if not LLM_API_KEY:
        raise RuntimeError(f"LLM_PROVIDER={provider} requires LLM_API_KEY in .env")

    if provider == "anthropic":
        from .anthropic_client import AnthropicClient
        return AnthropicClient(api_key=LLM_API_KEY, model=ANTHROPIC_MODEL)

    if provider == "gemini":
        if not LLM_MODEL:
            raise RuntimeError("LLM_PROVIDER=gemini requires LLM_MODEL in .env")
        from .gemini_client import GeminiClient
        return GeminiClient(api_key=LLM_API_KEY, model=LLM_MODEL)

    raise RuntimeError(f"unknown LLM_PROVIDER: {provider!r}")


__all__ = ["get_llm_client", "BaseLLMClient", "EventSink", "FinalTurn"]
