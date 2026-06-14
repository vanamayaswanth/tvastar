"""Anthropic (Claude) adapter — the default model provider.

Requires the ``anthropic`` package: ``uv add anthropic``. Translates Tvastar's
provider-agnostic types to/from the Anthropic Messages API, including native
tool use, streaming, and extended thinking.

thinking_level mapping
----------------------
``None``     → no extended thinking (default)
``'low'``    → ``budget_tokens=1024``
``'medium'`` → ``budget_tokens=8000``
``'high'``   → ``budget_tokens=16000``

When extended thinking is enabled:
  - ``temperature`` is forced to ``1.0`` (Anthropic requirement).
  - The ``interleaved-thinking-2025-05-14`` beta header is added.
  - ``thinking`` blocks in the response are silently stripped from public
    blocks (they are not useful to the tool layer and inflate context).
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

# budget_tokens per reasoning level
_THINKING_BUDGET: dict[str, int] = {
    "low": 1_024,
    "medium": 8_000,
    "high": 16_000,
}


class AnthropicModel(Model):
    system = "anthropic"

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
            # thinking blocks (type="thinking") are intentionally dropped
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

    def _thinking_kwargs(self, thinking_level: Optional[str], temperature: float) -> dict[str, Any]:
        """Return extra kwargs for extended thinking, or empty dict."""
        if not thinking_level:
            return {}
        budget = _THINKING_BUDGET.get(thinking_level, _THINKING_BUDGET["medium"])
        return {
            "thinking": {"type": "enabled", "budget_tokens": budget},
            # Anthropic requires temperature=1.0 when thinking is enabled
            "temperature": 1.0,
            "betas": ["interleaved-thinking-2025-05-14"],
        }

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
        thinking_level: Optional[str] = None,
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

        thinking_kw = self._thinking_kwargs(thinking_level, temperature)
        # betas must be passed separately via the client, not as a kwarg
        betas = thinking_kw.pop("betas", None)
        kwargs.update(thinking_kw)

        try:
            if betas:
                resp = await self._client.beta.messages.create(betas=betas, **kwargs)
            else:
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
        thinking_level: Optional[str] = None,
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

        thinking_kw = self._thinking_kwargs(thinking_level, temperature)
        betas = thinking_kw.pop("betas", None)
        kwargs.update(thinking_kw)

        try:
            if betas:
                async with self._client.beta.messages.stream(betas=betas, **kwargs) as stream:
                    async for text in stream.text_stream:
                        yield StreamEvent("text_delta", {"text": text})
                    final = await stream.get_final_message()
            else:
                async with self._client.messages.stream(**kwargs) as stream:
                    async for text in stream.text_stream:
                        yield StreamEvent("text_delta", {"text": text})
                    final = await stream.get_final_message()
        except Exception as e:  # pragma: no cover - network
            yield StreamEvent("error", {"message": str(e)})
            raise ModelError(f"Anthropic stream failed: {e}") from e
        yield StreamEvent("turn_end", {"response": self._from_anthropic(final)})
