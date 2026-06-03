"""Anthropic (Claude) adapter — the default model provider.

Requires the `anthropic` package: `uv add anthropic`. Translates Tvastar's
provider-agnostic types to/from the Anthropic Messages API, including native
tool use and streaming.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any, Optional

from ..errors import ModelError
from ..types import (
    Message,
    ModelResponse,
    StopReason,
    StreamEvent,
    TextBlock,
    ToolResultBlock,
    ToolSpec,
    ToolUseBlock,
    Usage,
)
from .base import Model

_STOP_MAP = {
    "end_turn": StopReason.END_TURN,
    "tool_use": StopReason.TOOL_USE,
    "max_tokens": StopReason.MAX_TOKENS,
    "stop_sequence": StopReason.STOP_SEQUENCE,
}


class AnthropicModel(Model):
    def __init__(
        self,
        model: str = "claude-opus-4-8",
        *,
        api_key: Optional[str] = None,
        client: Any = None,
    ):
        self.name = model
        self._model = model
        if client is not None:
            self._client = client
        else:
            try:
                from anthropic import AsyncAnthropic
            except ImportError as e:  # pragma: no cover
                raise ModelError("anthropic package not installed. Run: uv add anthropic") from e
            self._client = AsyncAnthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))

    # ---- translation helpers -------------------------------------------

    def _to_anthropic_messages(self, messages: list[Message]) -> list[dict]:
        out: list[dict] = []
        for m in messages:
            if m.role == "system":
                continue  # system handled separately
            content: list[dict] = []
            for b in m.blocks:
                if isinstance(b, TextBlock):
                    content.append({"type": "text", "text": b.text})
                elif isinstance(b, ToolUseBlock):
                    content.append(
                        {
                            "type": "tool_use",
                            "id": b.id,
                            "name": b.name,
                            "input": b.input,
                        }
                    )
                elif isinstance(b, ToolResultBlock):
                    content.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": b.tool_use_id,
                            "content": b.content,
                            "is_error": b.is_error,
                        }
                    )
            role = "assistant" if m.role == "assistant" else "user"
            out.append({"role": role, "content": content})
        return out

    def _from_anthropic(self, resp: Any) -> ModelResponse:
        blocks: list[Any] = []
        for b in resp.content:
            if b.type == "text":
                blocks.append(TextBlock(text=b.text))
            elif b.type == "tool_use":
                blocks.append(ToolUseBlock(id=b.id, name=b.name, input=dict(b.input)))
        msg = Message("assistant", blocks)
        return ModelResponse(
            message=msg,
            stop_reason=_STOP_MAP.get(resp.stop_reason, StopReason.END_TURN),
            usage=Usage(
                input_tokens=resp.usage.input_tokens,
                output_tokens=resp.usage.output_tokens,
            ),
            raw=resp,
        )

    @staticmethod
    def _tools(tools: Optional[list[ToolSpec]]) -> list[dict]:
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
            }
            for t in (tools or [])
        ]

    # ---- API ------------------------------------------------------------

    async def generate(
        self,
        messages: list[Message],
        *,
        system: Optional[str] = None,
        tools: Optional[list[ToolSpec]] = None,
        max_tokens: int = 4096,
        temperature: float = 1.0,
        stop_sequences: Optional[list[str]] = None,
    ) -> ModelResponse:
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": self._to_anthropic_messages(messages),
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = self._tools(tools)
        if stop_sequences:
            kwargs["stop_sequences"] = stop_sequences
        try:
            resp = await self._client.messages.create(**kwargs)
        except Exception as e:  # pragma: no cover - network
            raise ModelError(f"Anthropic request failed: {e}") from e
        return self._from_anthropic(resp)

    async def stream(
        self,
        messages: list[Message],
        *,
        system: Optional[str] = None,
        tools: Optional[list[ToolSpec]] = None,
        max_tokens: int = 4096,
        temperature: float = 1.0,
        stop_sequences: Optional[list[str]] = None,
    ) -> AsyncIterator[StreamEvent]:
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": self._to_anthropic_messages(messages),
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = self._tools(tools)
        if stop_sequences:
            kwargs["stop_sequences"] = stop_sequences
        try:
            async with self._client.messages.stream(**kwargs) as stream:
                async for text in stream.text_stream:
                    yield StreamEvent("text_delta", {"text": text})
                final = await stream.get_final_message()
        except Exception as e:  # pragma: no cover - network
            yield StreamEvent("error", {"message": str(e)})
            raise ModelError(f"Anthropic stream failed: {e}") from e
        yield StreamEvent("turn_end", {"response": self._from_anthropic(final)})
