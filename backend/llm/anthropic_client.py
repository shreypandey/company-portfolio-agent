"""Anthropic implementation of BaseLLMClient. Wraps the existing streaming logic."""
from __future__ import annotations

import logging
from typing import Any

from anthropic import APIConnectionError, APIStatusError, APITimeoutError, AsyncAnthropic
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential_jitter

from .base import BaseLLMClient, EventSink, FinalTurn

log = logging.getLogger("agent.llm.anthropic")


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, (APITimeoutError, APIConnectionError)):
        return True
    if isinstance(exc, APIStatusError):
        return exc.status_code in (408, 425, 429, 500, 502, 503, 504, 529)
    return False


def _block_to_dict(b) -> dict:
    """Serialize an SDK ContentBlock to a JSON-safe dict that the API accepts on resend.

    Note: model_dump() leaks derived fields (e.g. parsed_output on text blocks) that
    Anthropic's API rejects when sent back. We construct the minimal valid shape.
    """
    t = getattr(b, "type", None)
    if t == "text":
        d = {"type": "text", "text": b.text}
        cites = getattr(b, "citations", None)
        if cites:
            d["citations"] = [c.model_dump(exclude_none=True) for c in cites]
        return d
    if t == "tool_use":
        return {"type": "tool_use", "id": b.id, "name": b.name, "input": b.input}
    if t == "thinking":
        return {"type": "thinking", "thinking": b.thinking, "signature": b.signature}
    if t == "redacted_thinking":
        return {"type": "redacted_thinking", "data": b.data}
    return b.model_dump(exclude_none=True)


def _canonical_to_anthropic_messages(messages: list[dict]) -> list[dict]:
    """Messages are already in Anthropic shape canonically — just strip our extra
    `tool_name` field from tool_result blocks since Anthropic doesn't accept it."""
    out = []
    for m in messages:
        if isinstance(m.get("content"), list):
            content = []
            for block in m["content"]:
                if block.get("type") == "tool_result":
                    clean = {"type": "tool_result", "tool_use_id": block["tool_use_id"], "content": block["content"]}
                    if block.get("is_error"):
                        clean["is_error"] = True
                    content.append(clean)
                else:
                    content.append(block)
            out.append({"role": m["role"], "content": content})
        else:
            out.append(m)
    return out


class AnthropicClient(BaseLLMClient):
    def __init__(self, api_key: str, model: str):
        if not api_key:
            raise RuntimeError("Anthropic client requires LLM_API_KEY")
        self._client = AsyncAnthropic(api_key=api_key, timeout=120.0)
        self._model = model

    @property
    def provider(self) -> str:
        return "anthropic"

    @property
    def model(self) -> str:
        return self._model

    @property
    def context_window(self) -> int:
        # Claude 4.x line: 200K standard. (Extended 1M context requires a beta header
        # we don't set today.) Conservative figure used to trigger archival earlier.
        return 200_000

    async def stream_turn(
        self,
        *,
        system: str,
        messages: list[dict],
        tools: list[dict],
        max_tokens: int,
        sink: EventSink,
    ) -> FinalTurn:
        retry = AsyncRetrying(
            stop=stop_after_attempt(4),
            wait=wait_exponential_jitter(initial=2.0, max=30.0),
            retry=retry_if_exception(_is_transient),
            reraise=True,
        )
        async for attempt in retry:
            with attempt:
                n = attempt.retry_state.attempt_number
                if n > 1:
                    await sink("retry", {"target": "anthropic", "attempt": n})

                async with self._client.messages.stream(
                    model=self._model,
                    system=system,
                    max_tokens=max_tokens,
                    tools=tools,
                    messages=_canonical_to_anthropic_messages(messages),
                ) as stream:
                    tool_blocks: dict[int, dict] = {}
                    async for event in stream:
                        et = getattr(event, "type", None)
                        if et == "content_block_start":
                            cb = event.content_block
                            if cb.type == "tool_use":
                                tool_blocks[event.index] = {"id": cb.id, "name": cb.name}
                                await sink("tool_start", {"id": cb.id, "name": cb.name})
                        elif et == "content_block_delta":
                            d = event.delta
                            if d.type == "text_delta":
                                await sink("agent_text", {"text": d.text})
                            elif d.type == "input_json_delta":
                                t = tool_blocks.get(event.index)
                                if t:
                                    await sink("tool_input_delta", {"id": t["id"], "partial_json": d.partial_json})
                        elif et == "content_block_stop":
                            t = tool_blocks.pop(event.index, None)
                            if t:
                                await sink("tool_input_done", {"id": t["id"]})
                    final = await stream.get_final_message()

                content_blocks = [_block_to_dict(b) for b in final.content]
                usage: dict | None = None
                try:
                    usage = final.usage.model_dump() if final.usage else None
                except Exception:
                    usage = None
                return FinalTurn(
                    content_blocks=content_blocks,
                    stop_reason=final.stop_reason or "end_turn",
                    usage=usage,
                )
        raise RuntimeError("unreachable")

    def encode_tool_results(self, results: list[dict]) -> list[dict]:
        content = []
        for r in results:
            block = {"type": "tool_result", "tool_use_id": r["tool_use_id"], "content": r["content"]}
            if r.get("is_error"):
                block["is_error"] = True
            content.append(block)
        return [{"role": "user", "content": content}]
