"""Gemini implementation via Google's native genai SDK.

Why native instead of the OpenAI-compatible endpoint:
  - The OpenAI-compat layer corrupts parallel tool calls during streaming
    (collapses N calls into one with concatenated JSON args).
  - The native SDK emits proper per-call function_call parts in streaming events.

Notes:
  - Anthropic uses `input_schema`; Google's genai uses a Schema object. We convert
    JSON Schema dicts to genai.types.Schema recursively.
  - Tool results in Google's API are `function_response` parts inside a 'user' role
    Content. Each one needs the function NAME (we stored it canonically).
  - Stop reasons are FinishReason enum members; we map to our canonical strings.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from google import genai
from google.genai import types as gt

from .base import BaseLLMClient, EventSink, FinalTurn

log = logging.getLogger("agent.llm.gemini")


# ─────────── JSON Schema → Google Schema ───────────

_JSON_TYPE_MAP = {
    "string": "STRING",
    "integer": "INTEGER",
    "number": "NUMBER",
    "boolean": "BOOLEAN",
    "array": "ARRAY",
    "object": "OBJECT",
}


def _schema_from_json(s: dict | None) -> gt.Schema | None:
    """Convert a JSON Schema dict to genai.types.Schema (recursive)."""
    if not s:
        return None
    t = s.get("type")
    if isinstance(t, list):
        # Take the first non-null type — Google doesn't support union types
        t = next((x for x in t if x != "null"), "string")
    gt_type = _JSON_TYPE_MAP.get(t, "STRING") if t else None

    kwargs: dict[str, Any] = {}
    if gt_type:
        kwargs["type"] = gt_type
    if "description" in s:
        kwargs["description"] = s["description"]
    if "enum" in s:
        kwargs["enum"] = [str(e) for e in s["enum"]]
    if t == "object":
        props = {}
        for k, v in (s.get("properties") or {}).items():
            sub = _schema_from_json(v)
            if sub is not None:
                props[k] = sub
        if props:
            kwargs["properties"] = props
        if s.get("required"):
            kwargs["required"] = list(s["required"])
    if t == "array":
        items = _schema_from_json(s.get("items"))
        if items is not None:
            kwargs["items"] = items
    return gt.Schema(**kwargs) if kwargs else None


def _tools_to_genai(tools: list[dict]) -> list[gt.Tool]:
    """Canonical Anthropic-style tool defs → genai Tool list."""
    decls: list[gt.FunctionDeclaration] = []
    for t in tools:
        params = _schema_from_json(t.get("input_schema"))
        decls.append(
            gt.FunctionDeclaration(
                name=t["name"],
                description=t.get("description", ""),
                parameters=params,
            )
        )
    # genai accepts one Tool with multiple function_declarations
    return [gt.Tool(function_declarations=decls)] if decls else []


# ─────────── Canonical messages → genai Contents ───────────

def _messages_to_contents(messages: list[dict]) -> list[gt.Content]:
    """Convert canonical message list to genai Content objects."""
    out: list[gt.Content] = []
    for m in messages:
        role = m["role"]
        gt_role = "user" if role == "user" else "model"
        content = m.get("content")

        if isinstance(content, str):
            out.append(gt.Content(role=gt_role, parts=[gt.Part(text=content)]))
            continue

        parts: list[gt.Part] = []
        for block in (content or []):
            btype = block.get("type")
            if btype == "text":
                parts.append(gt.Part(text=block["text"]))
            elif btype == "tool_use":
                # The thought_signature lives on the PART, not on FunctionCall.
                # Must be replayed exactly where the model originally emitted it.
                fc = gt.FunctionCall(name=block["name"], args=block.get("input") or {})
                part_kwargs: dict[str, Any] = {"function_call": fc}
                sig = block.get("_gemini_thought_signature")
                if sig:
                    part_kwargs["thought_signature"] = sig
                parts.append(gt.Part(**part_kwargs))
            elif btype == "tool_result":
                # Function response. Google needs the function NAME — we stored it canonically.
                name = block.get("tool_name") or "unknown_tool"
                resp = block.get("content")
                # Google wants response as a dict; if we have a JSON string, parse it.
                if isinstance(resp, str):
                    try:
                        resp_obj = json.loads(resp)
                        if not isinstance(resp_obj, dict):
                            resp_obj = {"result": resp_obj}
                    except json.JSONDecodeError:
                        resp_obj = {"result": resp}
                else:
                    resp_obj = resp if isinstance(resp, dict) else {"result": resp}
                parts.append(gt.Part(function_response=gt.FunctionResponse(
                    name=name,
                    response=resp_obj,
                )))
            # ignore unknown block types
        if parts:
            out.append(gt.Content(role=gt_role, parts=parts))
    return out


# ─────────── FinishReason mapping ───────────

def _map_stop_reason(fr) -> str:
    if fr is None:
        return "end_turn"
    name = getattr(fr, "name", None) or str(fr)
    return {
        "STOP": "end_turn",
        "MAX_TOKENS": "max_tokens",
        "SAFETY": "stop_sequence",
        "RECITATION": "stop_sequence",
        "MALFORMED_FUNCTION_CALL": "end_turn",
        "OTHER": "end_turn",
    }.get(name, "end_turn")


class GeminiClient(BaseLLMClient):
    def __init__(self, api_key: str, model: str):
        if not api_key:
            raise RuntimeError("Gemini client requires LLM_API_KEY")
        self._client = genai.Client(api_key=api_key)
        self._model = model

    @property
    def provider(self) -> str:
        return "gemini"

    @property
    def model(self) -> str:
        return self._model

    @property
    def context_window(self) -> int:
        # Gemini 3.x and 2.5 Pro/Flash variants all expose 1,048,576 input tokens.
        # Older/preview models may differ; we'd return a lower default if added.
        m = (self._model or "").lower()
        if "gemini-3" in m or "gemini-2.5" in m:
            return 1_048_576
        return 32_768  # conservative default for unknown models

    async def stream_turn(
        self,
        *,
        system: str,
        messages: list[dict],
        tools: list[dict],
        max_tokens: int,
        sink: EventSink,
    ) -> FinalTurn:
        contents = _messages_to_contents(messages)
        config = gt.GenerateContentConfig(
            system_instruction=system,
            tools=_tools_to_genai(tools),
            max_output_tokens=max_tokens,
        )

        # Accumulators across streaming chunks
        text_buf: list[str] = []
        tool_calls: list[dict] = []   # [{id, name, input}]
        seen_call_signatures: set[str] = set()
        finish_reason = None
        usage: dict | None = None

        # Generate a stable id per (name, args) — Gemini doesn't emit unique ids itself
        def _make_id(name: str, args: dict, idx: int) -> str:
            return f"toolu_gm_{idx}_{name}"

        async for chunk in await self._client.aio.models.generate_content_stream(
            model=self._model, contents=contents, config=config,
        ):
            candidates = chunk.candidates or []
            if not candidates:
                continue
            cand = candidates[0]
            cand_finish = getattr(cand, "finish_reason", None)
            if cand_finish:
                finish_reason = cand_finish
            content = getattr(cand, "content", None)
            for part in (getattr(content, "parts", None) or []):
                if getattr(part, "text", None):
                    text = part.text
                    text_buf.append(text)
                    await sink("agent_text", {"text": text})
                fc = getattr(part, "function_call", None)
                if fc:
                    # Dedupe — Gemini streams may emit the same call across chunks
                    name = fc.name
                    args = dict(fc.args) if fc.args else {}
                    dedupe_key = f"{name}|{json.dumps(args, sort_keys=True)}"
                    if dedupe_key in seen_call_signatures:
                        continue
                    seen_call_signatures.add(dedupe_key)
                    tool_id = _make_id(name, args, len(tool_calls))
                    # thought_signature lives on the PART, not on FunctionCall.
                    # Required to be replayed in subsequent turns.
                    thought_sig = getattr(part, "thought_signature", None)
                    tool_calls.append({
                        "id": tool_id,
                        "name": name,
                        "input": args,
                        "_gemini_thought_signature": thought_sig,
                    })
                    # Synthesize the same events Anthropic emits so the UI is consistent.
                    await sink("tool_start", {"id": tool_id, "name": name})
                    await sink("tool_input_delta", {"id": tool_id, "partial_json": json.dumps(args)})
                    await sink("tool_input_done", {"id": tool_id})

            # Gemini sends usage_metadata on the final chunk
            um = getattr(chunk, "usage_metadata", None)
            if um:
                usage = {
                    "input_tokens": getattr(um, "prompt_token_count", None),
                    "output_tokens": getattr(um, "candidates_token_count", None),
                    "total_tokens": getattr(um, "total_token_count", None),
                }

        # Build canonical content_blocks
        content_blocks: list[dict] = []
        full_text = "".join(text_buf).strip()
        if full_text:
            content_blocks.append({"type": "text", "text": full_text})
        for tc in tool_calls:
            block = {"type": "tool_use", "id": tc["id"], "name": tc["name"], "input": tc["input"]}
            # Carry the thought_signature through so we can replay correctly.
            # Anthropic ignores extra keys; Gemini reuses them.
            if tc.get("_gemini_thought_signature") is not None:
                block["_gemini_thought_signature"] = tc["_gemini_thought_signature"]
            content_blocks.append(block)

        # Map stop reason: if we got tool calls, treat as tool_use; else use finish_reason mapping.
        if tool_calls:
            stop_reason = "tool_use"
        else:
            stop_reason = _map_stop_reason(finish_reason)

        return FinalTurn(content_blocks=content_blocks, stop_reason=stop_reason, usage=usage)

    def encode_tool_results(self, results: list[dict]) -> list[dict]:
        """One canonical user message containing tool_result blocks. Conversion to
        Google function_response parts happens in _messages_to_contents.
        """
        content = []
        for r in results:
            block = {
                "type": "tool_result",
                "tool_use_id": r["tool_use_id"],
                "tool_name": r.get("tool_name") or "unknown_tool",
                "content": r["content"],
            }
            if r.get("is_error"):
                block["is_error"] = True
            content.append(block)
        return [{"role": "user", "content": content}]
