"""Provider-agnostic LLM client interface.

The agent stores messages in a canonical (Anthropic-shaped) format:

  user (plain):
    {"role": "user", "content": "text"}
  user (tool results):
    {"role": "user", "content": [{"type": "tool_result", "tool_use_id", "tool_name", "content", "is_error"?}]}
  assistant:
    {"role": "assistant", "content": [
        {"type": "text", "text": "..."},
        {"type": "tool_use", "id": "...", "name": "...", "input": {...}},
    ]}

Tools are stored canonically too (Anthropic shape: {name, description, input_schema}).
Each client converts to its provider's native format on the fly.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Awaitable, Callable

EventSink = Callable[[str, dict], Awaitable[None]]


@dataclass
class FinalTurn:
    """One assistant turn's output, in canonical content-block format."""
    content_blocks: list[dict]
    stop_reason: str       # "end_turn" | "tool_use" | "max_tokens" | "stop_sequence"
    usage: dict | None = None


class BaseLLMClient(ABC):
    """Interface every provider implements."""

    @property
    @abstractmethod
    def provider(self) -> str:
        """Short identifier, e.g. 'anthropic', 'gemini'."""

    @property
    @abstractmethod
    def model(self) -> str:
        """Model id being used."""

    @property
    @abstractmethod
    def context_window(self) -> int:
        """Approximate input-token capacity. Used by the agent to decide when to
        archive older tool results before we run out of room."""

    @abstractmethod
    async def stream_turn(
        self,
        *,
        system: str,
        messages: list[dict],
        tools: list[dict],
        max_tokens: int,
        sink: EventSink,
    ) -> FinalTurn:
        """Run one streaming turn. Emit canonical events to `sink` along the way:

            tool_start         {id, name}
            tool_input_delta   {id, partial_json}
            tool_input_done    {id}
            agent_text         {text}
            retry              {target, attempt}   (optional, on provider retries)

        Returns the final assembled turn.
        """

    @abstractmethod
    def encode_tool_results(self, results: list[dict]) -> list[dict]:
        """Convert tool-result blocks into the message(s) to append next.

        Each result: {"tool_use_id", "tool_name", "content"(str), "is_error"?(bool)}.
        Most providers return one user-role message; some (OpenAI) might return
        multiple separate tool-role messages.
        """
