"""Reducer — derive session state from an event log.

Pure function: no side effects, no external state, does not modify input.
Handles malformed trailing records gracefully (partial write recovery).
"""

from __future__ import annotations

from typing import Any

from ..durable import message_from_dict
from ..types import Message

# Message-bearing record types (compared as string values from serialized dicts)
_MESSAGE_TYPES = frozenset(
    {
        "user_message",
        "assistant_message",
        "tool_use",
        "tool_result",
    }
)


def reduce(records: list[dict[str, Any]]) -> list[Message]:
    """Derive session messages from an event log. Pure function.

    Folds the event log into a list of Messages by extracting message payloads
    from message-bearing record types. Skips error records, unknown types, and
    malformed records silently for forward compatibility and partial-write recovery.

    A ``session_start`` record with a ``"snapshot"`` key in its data seeds
    the messages list from the snapshot (compacted state), replacing any
    previously accumulated messages.
    """
    messages: list[Message] = []
    for raw in records:
        try:
            rtype = raw.get("type") if isinstance(raw, dict) else None
            if rtype == "session_start":
                snapshot = raw.get("data", {}).get("snapshot")
                if snapshot and isinstance(snapshot, list):
                    messages = [message_from_dict(m) for m in snapshot]
            elif rtype in _MESSAGE_TYPES:
                msg_data = raw.get("data", {}).get("message")
                if msg_data and isinstance(msg_data, dict):
                    messages.append(message_from_dict(msg_data))
            # error records, session_end, unknown types: skip
        except Exception:
            # Malformed record (partial write, missing fields, bad data) — skip
            continue
    return messages
