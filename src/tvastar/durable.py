"""Durable execution — persist and restore a session so progress survives
crashes and process restarts.

**Superseded:** The `Checkpointer` class in this module is deprecated.
Use `tvastar.conversation.ConversationWriter` and `tvastar.conversation.reduce()`
for event-sourced session durability instead.

The utility functions `message_to_dict` and `message_from_dict` remain the
canonical serialization helpers and are used by the new conversation module.
"""

from __future__ import annotations

import warnings
from typing import Any, Optional

from .errors import DurableError
from .memory.store import Store
from .types import (
    ImageBlock,
    Message,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)

_CHECKPOINT_PREFIX = "session:"


def message_to_dict(m: Message) -> dict[str, Any]:
    blocks = []
    for b in m.blocks:
        if isinstance(b, TextBlock):
            blocks.append({"type": "text", "text": b.text})
        elif isinstance(b, ToolUseBlock):
            blocks.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
        elif isinstance(b, ToolResultBlock):
            blocks.append(
                {
                    "type": "tool_result",
                    "tool_use_id": b.tool_use_id,
                    "content": b.content,
                    "is_error": b.is_error,
                }
            )
        elif isinstance(b, ImageBlock):
            blocks.append(
                {
                    "type": "image",
                    "data": b.data,
                    "media_type": b.media_type,
                    "source_type": b.source_type,
                }
            )
    return {
        "id": m.id,
        "role": m.role,
        "blocks": blocks,
        "created_at": m.created_at,
        "metadata": m.metadata,
    }


def message_from_dict(d: dict[str, Any]) -> Message:
    blocks: list[Any] = []
    for b in d.get("blocks", []):
        t = b.get("type")
        if t == "text":
            blocks.append(TextBlock(text=b["text"]))
        elif t == "tool_use":
            blocks.append(ToolUseBlock(id=b["id"], name=b["name"], input=b["input"]))
        elif t == "tool_result":
            blocks.append(
                ToolResultBlock(
                    tool_use_id=b["tool_use_id"],
                    content=b["content"],
                    is_error=b.get("is_error", False),
                )
            )
        elif t == "image":
            blocks.append(
                ImageBlock(
                    data=b["data"],
                    media_type=b.get("media_type", "image/jpeg"),
                    source_type=b.get("source_type", "base64"),
                )
            )
    return Message(
        role=d["role"],
        content=blocks,
        id=d.get("id") or Message("user", "").id,
        created_at=d.get("created_at", 0.0),
        metadata=d.get("metadata", {}),
    )


class Checkpointer:
    """Reads/writes session checkpoints to a Store.

    .. deprecated::
        Use `tvastar.conversation.ConversationWriter` and
        `tvastar.conversation.reduce()` instead.
    """

    def __init__(self, store: Store):
        warnings.warn(
            "Checkpointer is deprecated and will be removed in a future release. "
            "Use tvastar.conversation.ConversationWriter and "
            "tvastar.conversation.reduce() instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        self.store = store

    def save(
        self,
        session_id: str,
        *,
        messages: list[Message],
        fs_snapshot: Optional[dict[str, str]] = None,
        meta: Optional[dict[str, Any]] = None,
    ) -> None:
        record = {
            "session_id": session_id,
            "messages": [message_to_dict(m) for m in messages],
            "fs_snapshot": fs_snapshot,
            "meta": meta or {},
        }
        try:
            self.store.set(_CHECKPOINT_PREFIX + session_id, record)
        except Exception as e:  # pragma: no cover
            raise DurableError(f"checkpoint save failed: {e}") from e

    def load(self, session_id: str) -> Optional[dict[str, Any]]:
        record = self.store.get(_CHECKPOINT_PREFIX + session_id)
        if not record:
            return None
        record["messages"] = [message_from_dict(m) for m in record.get("messages", [])]
        return record

    def exists(self, session_id: str) -> bool:
        return self.store.get(_CHECKPOINT_PREFIX + session_id) is not None

    def list_sessions(self) -> list[str]:
        n = len(_CHECKPOINT_PREFIX)
        return [k[n:] for k in self.store.keys(_CHECKPOINT_PREFIX)]
