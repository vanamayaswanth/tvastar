"""The Model interface — the bottom layer of the harness.

A Model turns (messages + tools + system prompt) into a ModelResponse. It is
deliberately small: everything else in Tvastar is built on top of this contract.
Providers (Anthropic, OpenAI, mock) implement `generate`. Streaming is optional
and defaults to a non-streamed shim.
"""

from __future__ import annotations

import abc
from collections.abc import AsyncIterator
from typing import Optional

from ..types import Message, ModelResponse, StreamEvent, TextBlock, ToolSpec


class Model(abc.ABC):
    """Abstract async model provider."""

    #: human-readable identifier, e.g. "claude-opus-4-8"
    name: str = "model"

    @abc.abstractmethod
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
        """Produce a single assistant response.

        Args:
            thinking_level: Reasoning effort hint — ``'low'``, ``'medium'``,
                ``'high'``, or ``None`` (no extended thinking). Providers map
                this to their native reasoning/thinking API.
        """
        raise NotImplementedError

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
        """Stream a response as events, ending with a ``turn_end`` carrying the
        full ModelResponse in ``data["response"]``.

        Default implementation falls back to ``generate`` and emits the text as
        a single delta. Providers may override for true token streaming.
        """
        resp = await self.generate(
            messages,
            system=system,
            tools=tools,
            max_tokens=max_tokens,
            temperature=temperature,
            stop_sequences=stop_sequences,
            thinking_level=thinking_level,
        )
        for block in resp.message.blocks:
            if isinstance(block, TextBlock) and block.text:
                yield StreamEvent("text_delta", {"text": block.text})
        yield StreamEvent("turn_end", {"response": resp})
