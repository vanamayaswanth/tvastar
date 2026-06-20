"""Core data types shared across Tvastar.

These are intentionally lightweight dataclasses so the framework has zero
runtime dependencies in its core. Provider adapters translate to/from these.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, Optional, Union


Role = Literal["system", "user", "assistant", "tool"]


@dataclass
class TextBlock:
    """A chunk of plain text in a message."""

    text: str
    type: Literal["text"] = "text"


@dataclass
class ToolUseBlock:
    """A request from the model to invoke a tool."""

    name: str
    input: dict[str, Any]
    id: str = field(default_factory=lambda: f"call_{uuid.uuid4().hex[:12]}")
    type: Literal["tool_use"] = "tool_use"


@dataclass
class ToolResultBlock:
    """The result of a tool invocation, fed back to the model."""

    tool_use_id: str
    content: str
    is_error: bool = False
    type: Literal["tool_result"] = "tool_result"


@dataclass
class ImageBlock:
    """An image in a user message, forwarded to vision-capable models.

    Args:
        data: Base64-encoded image bytes, or a URL when ``source_type="url"``.
        media_type: MIME type — ``image/jpeg``, ``image/png``, ``image/gif``,
                    or ``image/webp``.
        source_type: ``"base64"`` (default) or ``"url"``.
    """

    data: str
    media_type: str = "image/jpeg"
    source_type: str = "base64"
    type: Literal["image"] = "image"


ContentBlock = Union[TextBlock, ImageBlock, ToolUseBlock, ToolResultBlock]


@dataclass
class Message:
    """A single turn in a conversation.

    `content` is either a plain string (sugar for a single TextBlock) or a
    list of content blocks. Normalize with `.blocks`.
    """

    role: Role
    content: Union[str, list[ContentBlock]]
    id: str = field(default_factory=lambda: f"msg_{uuid.uuid4().hex[:12]}")
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def blocks(self) -> list[ContentBlock]:
        if isinstance(self.content, str):
            return [TextBlock(text=self.content)]
        return self.content

    @property
    def text(self) -> str:
        """Concatenated text of all text blocks."""
        return "".join(b.text for b in self.blocks if isinstance(b, TextBlock))

    @property
    def tool_uses(self) -> list[ToolUseBlock]:
        return [b for b in self.blocks if isinstance(b, ToolUseBlock)]


class StopReason(str, Enum):
    END_TURN = "end_turn"
    TOOL_USE = "tool_use"
    MAX_TOKENS = "max_tokens"
    STOP_SEQUENCE = "stop_sequence"
    ERROR = "error"


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
        )


@dataclass
class ModelResponse:
    """A model's reply to a list of messages."""

    message: Message
    stop_reason: StopReason
    usage: Usage = field(default_factory=Usage)
    raw: Optional[Any] = None

    @property
    def tool_uses(self) -> list[ToolUseBlock]:
        return self.message.tool_uses


@dataclass
class ToolSpec:
    """Provider-agnostic description of a callable tool."""

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass
class StreamEvent:
    """Emitted during a streaming agent run for observability/UX."""

    type: Literal[
        "text_delta",
        "tool_call",
        "tool_result",
        "turn_start",
        "turn_end",
        "skill_loaded",
        "task_spawned",
        "error",
    ]
    data: dict[str, Any] = field(default_factory=dict)
    at: float = field(default_factory=time.time)
