"""A deterministic mock model so the harness runs with no API keys.

It supports two modes:

* **Scripted** — pass a list of responses (strings, ToolUse requests, or
  Message objects) that are returned in order. Great for tests.
* **Echo/heuristic** — with no script, it echoes a short acknowledgement and,
  if a tool named in the prompt is available, may call it. Useful for demos.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Optional, Union

from ..types import (
    Message,
    ModelResponse,
    StopReason,
    TextBlock,
    ToolSpec,
    ToolUseBlock,
    Usage,
)
from .base import Model

Scripted = Union[str, ToolUseBlock, Message]


class MockModel(Model):
    name = "mock"
    system = "mock"

    def __init__(self, script: Optional[Sequence[Scripted]] = None):
        self._script = list(script or [])
        self._cursor = 0
        self.calls: list[list[Message]] = []

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
        self.calls.append(list(messages))

        if self._cursor < len(self._script):
            item = self._script[self._cursor]
            self._cursor += 1
            if isinstance(item, BaseException):
                raise item
            return self._wrap(item)

        # No more scripted items: end the turn with a canned summary.
        last_user = next((m for m in reversed(messages) if m.role == "user"), None)
        snippet = (last_user.text[:160] if last_user else "").strip()
        thinking_note = f" [thinking={thinking_level}]" if thinking_level else ""
        text = f"[mock] Acknowledged: {snippet}{thinking_note}" if snippet else "[mock] Done."
        return ModelResponse(
            message=Message("assistant", [TextBlock(text=text)]),
            stop_reason=StopReason.END_TURN,
            usage=Usage(input_tokens=_approx_tokens(messages), output_tokens=8),
        )

    def _wrap(self, item: Scripted) -> ModelResponse:
        if isinstance(item, Message):
            msg = item
        elif isinstance(item, ToolUseBlock):
            msg = Message("assistant", [item])
        else:  # str
            msg = Message("assistant", [TextBlock(text=item)])
        stop = (
            StopReason.TOOL_USE
            if any(isinstance(b, ToolUseBlock) for b in msg.blocks)
            else StopReason.END_TURN
        )
        return ModelResponse(
            message=msg,
            stop_reason=stop,
            usage=Usage(output_tokens=12),
        )


def _approx_tokens(messages: list[Message]) -> int:
    words = sum(len(re.findall(r"\S+", m.text)) for m in messages)
    return max(1, int(words * 1.3))
