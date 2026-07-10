"""Unit tests for ConversationWriter — append, load_seq, and degraded mode."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tvastar.conversation.writer import ConversationWriter
from tvastar.conversation.records import RecordType
from tvastar.errors import DurableError
from tvastar.memory.store import InMemoryStore


@pytest.mark.asyncio
async def test_append_creates_record_with_incrementing_seq():
    store = InMemoryStore()
    writer = ConversationWriter(store, "sess-1")

    r0 = await writer.append(RecordType.USER_MESSAGE, {"text": "hello"})
    r1 = await writer.append(RecordType.ASSISTANT_MESSAGE, {"text": "hi"})

    assert r0.seq == 0
    assert r1.seq == 1
    assert r0.type == RecordType.USER_MESSAGE
    assert r1.type == RecordType.ASSISTANT_MESSAGE


@pytest.mark.asyncio
async def test_append_persists_to_store():
    store = InMemoryStore()
    writer = ConversationWriter(store, "sess-1")

    await writer.append(RecordType.SESSION_START, {})
    await writer.append(RecordType.USER_MESSAGE, {"text": "hi"})

    log = store.get("event_log:sess-1")
    assert log is not None
    assert len(log) == 2
    assert log[0]["type"] == "session_start"
    assert log[1]["type"] == "user_message"


@pytest.mark.asyncio
async def test_load_seq_recovers_from_existing_log():
    store = InMemoryStore()
    # Simulate pre-existing log with 3 records
    store.set(
        "event_log:sess-2",
        [
            {"type": "session_start", "seq": 0, "timestamp": 1.0, "data": {}},
            {"type": "user_message", "seq": 1, "timestamp": 2.0, "data": {}},
            {"type": "assistant_message", "seq": 2, "timestamp": 3.0, "data": {}},
        ],
    )

    writer = ConversationWriter(store, "sess-2")
    seq = writer.load_seq()

    assert seq == 3
    # Next append should use seq=3
    r = await writer.append(RecordType.USER_MESSAGE, {"text": "next"})
    assert r.seq == 3


@pytest.mark.asyncio
async def test_load_seq_returns_zero_for_empty_log():
    store = InMemoryStore()
    writer = ConversationWriter(store, "new-session")
    assert writer.load_seq() == 0


@pytest.mark.asyncio
async def test_degraded_mode_on_store_failure():
    store = MagicMock()
    store.get.side_effect = Exception("disk full")

    writer = ConversationWriter(store, "sess-fail")
    record = await writer.append(RecordType.USER_MESSAGE, {"text": "oops"})

    # Record is still returned (in-memory)
    assert record.seq == 0
    assert record.type == RecordType.USER_MESSAGE
    # Error is captured
    assert writer.last_error is not None
    assert isinstance(writer.last_error, DurableError)
    assert "sess-fail" in writer.last_error.details.get("session_id", "")
    assert writer.last_error.details.get("operation") == "append"


def test_key_format():
    store = InMemoryStore()
    writer = ConversationWriter(store, "my-session")
    assert writer.key == "event_log:my-session"
