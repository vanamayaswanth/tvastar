"""Record types for event-sourced conversation logs.

Each state transition in a session is captured as a typed Record in an
append-only event log.  Serialization delegates message payloads to the
existing ``message_to_dict`` / ``message_from_dict`` helpers in ``durable.py``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..durable import message_from_dict, message_to_dict
from ..types import Message


class RecordType(str, Enum):
    SESSION_START = "session_start"
    USER_MESSAGE = "user_message"
    ASSISTANT_MESSAGE = "assistant_message"
    TOOL_USE = "tool_use"
    TOOL_RESULT = "tool_result"
    ERROR = "error"
    SESSION_END = "session_end"
    RUN_START = "run_start"
    STEP_COMPLETE = "step_complete"
    RUN_END = "run_end"
    TOOL_CALL_STARTED = "tool_call_started"


@dataclass
class Record:
    """A single typed event in the session event log."""

    type: RecordType
    seq: int
    timestamp: float = field(default_factory=time.time)
    data: dict[str, Any] = field(default_factory=dict)


def record_to_dict(record: Record) -> dict[str, Any]:
    """Serialize a Record to a JSON-compatible dict.

    If ``record.data`` contains a ``"message"`` key holding a Message object,
    it is serialized via ``message_to_dict``.
    """
    data = dict(record.data)
    if "message" in data and isinstance(data["message"], Message):
        data["message"] = message_to_dict(data["message"])
    return {
        "type": record.type.value,
        "seq": record.seq,
        "timestamp": record.timestamp,
        "data": data,
    }


def record_from_dict(d: dict[str, Any]) -> Record:
    """Deserialize a dict back into a Record.

    If ``d["data"]`` contains a ``"message"`` key, it is deserialized via
    ``message_from_dict``.
    """
    data = dict(d.get("data", {}))
    if "message" in data and isinstance(data["message"], dict):
        data["message"] = message_from_dict(data["message"])
    return Record(
        type=RecordType(d["type"]),
        seq=d["seq"],
        timestamp=d["timestamp"],
        data=data,
    )
