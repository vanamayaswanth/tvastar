"""Tests for ConversationWriter degraded/recovered event emission (Req 5.1–5.8)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from tvastar.conversation.writer import ConversationWriter
from tvastar.conversation.records import RecordType
from tvastar.memory.store import InMemoryStore


@pytest.mark.asyncio
async def test_degraded_event_emitted_once_via_event_bus():
    """Req 5.2, 5.3, 5.5: emit session.degraded exactly once per None→Error transition."""
    bus = MagicMock()
    store = MagicMock()
    store.get.side_effect = Exception("disk full")

    writer = ConversationWriter(store, "sess-1", event_bus=bus)

    await writer.append(RecordType.USER_MESSAGE, {"text": "a"})
    await writer.append(RecordType.USER_MESSAGE, {"text": "b"})

    # Should have published exactly once
    assert bus.publish.call_count == 1
    topic, payload = bus.publish.call_args[0]
    assert topic == "session.degraded"
    assert payload["session_id"] == "sess-1"
    assert payload["operation"] == "append"
    assert "error_message" in payload
    assert "timestamp" in payload


@pytest.mark.asyncio
async def test_recovered_event_emitted_on_success_after_failure():
    """Req 5.8: emit session.recovered when last_error transitions Error→None."""
    bus = MagicMock()
    store = InMemoryStore()

    writer = ConversationWriter(store, "sess-r", event_bus=bus)

    # Simulate a previous error state
    from tvastar.errors import DurableError

    writer.last_error = DurableError("old failure", session_id="sess-r", operation="append")
    writer._degraded_emitted = True

    # Successful append should trigger recovery
    await writer.append(RecordType.USER_MESSAGE, {"text": "back!"})

    assert bus.publish.call_count == 1
    topic, payload = bus.publish.call_args[0]
    assert topic == "session.recovered"
    assert payload["session_id"] == "sess-r"
    assert payload["error_message"] is None
    assert payload["operation"] == "append"
    assert writer.last_error is None
    assert writer._degraded_emitted is False


@pytest.mark.asyncio
async def test_no_event_bus_falls_back_to_stderr(capsys):
    """Req 5.7: no EventBus → structured JSON on stderr."""
    store = MagicMock()
    store.get.side_effect = Exception("nope")

    writer = ConversationWriter(store, "sess-stderr")  # no event_bus

    await writer.append(RecordType.USER_MESSAGE, {"text": "x"})

    captured = capsys.readouterr()
    line = json.loads(captured.err.strip())
    assert line["event"] == "session.degraded"
    assert line["session_id"] == "sess-stderr"


@pytest.mark.asyncio
async def test_event_bus_publish_failure_falls_back_to_stderr(capsys):
    """Req 5.6: if EventBus.publish raises, fall back to stderr JSON."""
    bus = MagicMock()
    bus.publish.side_effect = RuntimeError("bus broken")

    store = MagicMock()
    store.get.side_effect = Exception("disk full")

    writer = ConversationWriter(store, "sess-fb", event_bus=bus)

    await writer.append(RecordType.USER_MESSAGE, {"text": "x"})

    captured = capsys.readouterr()
    line = json.loads(captured.err.strip())
    assert line["event"] == "session.degraded"
    assert line["session_id"] == "sess-fb"
