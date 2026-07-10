"""Tests for event log compaction in writer + reducer snapshot handling."""

import pytest

from tvastar.conversation.reducer import reduce
from tvastar.conversation.writer import ConversationWriter
from tvastar.conversation.records import RecordType
from tvastar.durable import message_to_dict
from tvastar.memory.store import InMemoryStore
from tvastar.types import Message, TextBlock


def _msg_record(rtype: str, message: Message, seq: int = 0) -> dict:
    return {
        "type": rtype,
        "seq": seq,
        "timestamp": 1700000000.0 + seq,
        "data": {"message": message_to_dict(message)},
    }


# ── Reducer: snapshot handling ──────────────────────────────────────────────


def test_reducer_session_start_with_snapshot_seeds_messages():
    """session_start with snapshot replaces messages list."""
    m1 = Message(role="user", content=[TextBlock(text="hi")])
    m2 = Message(role="assistant", content=[TextBlock(text="hello")])
    snapshot = [message_to_dict(m1), message_to_dict(m2)]
    log = [{"type": "session_start", "seq": 0, "timestamp": 1.0, "data": {"snapshot": snapshot}}]
    result = reduce(log)
    assert len(result) == 2
    assert result[0].text == "hi"
    assert result[1].text == "hello"


def test_reducer_snapshot_then_new_messages():
    """After snapshot, subsequent message records append normally."""
    m1 = Message(role="user", content=[TextBlock(text="old")])
    snapshot = [message_to_dict(m1)]
    m2 = Message(role="user", content=[TextBlock(text="new")])
    log = [
        {"type": "session_start", "seq": 0, "timestamp": 1.0, "data": {"snapshot": snapshot}},
        _msg_record("user_message", m2, seq=1),
    ]
    result = reduce(log)
    assert len(result) == 2
    assert result[0].text == "old"
    assert result[1].text == "new"


def test_reducer_session_start_without_snapshot_still_skipped():
    """Plain session_start (no snapshot) doesn't break anything."""
    m = Message(role="user", content=[TextBlock(text="hi")])
    log = [
        {"type": "session_start", "seq": 0, "timestamp": 1.0, "data": {}},
        _msg_record("user_message", m, seq=1),
    ]
    result = reduce(log)
    assert len(result) == 1
    assert result[0].text == "hi"


# ── Writer: compaction logic ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_compaction_triggers_when_threshold_exceeded():
    """Log compacts to single snapshot record when threshold is crossed."""
    store = InMemoryStore()
    writer = ConversationWriter(store, "compact-sess", compaction_threshold=3)

    # Append 4 records (threshold=3, so >3 triggers compaction after 4th)
    await writer.append(RecordType.SESSION_START, {})
    await writer.append(
        RecordType.USER_MESSAGE,
        {"message": message_to_dict(Message(role="user", content=[TextBlock(text="q1")]))},
    )
    await writer.append(
        RecordType.ASSISTANT_MESSAGE,
        {"message": message_to_dict(Message(role="assistant", content=[TextBlock(text="a1")]))},
    )
    # 4th append exceeds threshold=3
    await writer.append(
        RecordType.USER_MESSAGE,
        {"message": message_to_dict(Message(role="user", content=[TextBlock(text="q2")]))},
    )

    log = store.get("event_log:compact-sess")
    # After compaction: single session_start record with snapshot
    assert len(log) == 1
    assert log[0]["type"] == "session_start"
    assert "snapshot" in log[0]["data"]
    # seq should reset to 1
    assert writer._seq == 1


@pytest.mark.asyncio
async def test_compaction_preserves_messages():
    """reduce(compacted_log) == reduce(original_log) — correctness invariant."""
    store = InMemoryStore()
    writer = ConversationWriter(store, "correct-sess", compaction_threshold=3)

    msgs = [
        Message(role="user", content=[TextBlock(text="q1")]),
        Message(role="assistant", content=[TextBlock(text="a1")]),
        Message(role="user", content=[TextBlock(text="q2")]),
    ]

    await writer.append(RecordType.SESSION_START, {})
    for i, m in enumerate(msgs):
        rtype = RecordType.USER_MESSAGE if m.role == "user" else RecordType.ASSISTANT_MESSAGE
        await writer.append(rtype, {"message": message_to_dict(m)})

    # Log should be compacted now (4 records > threshold 3)
    log = store.get("event_log:correct-sess")
    result = reduce(log)
    assert len(result) == 3
    assert [r.text for r in result] == ["q1", "a1", "q2"]


@pytest.mark.asyncio
async def test_compaction_disabled_when_threshold_zero():
    """compaction_threshold=0 means never compact."""
    store = InMemoryStore()
    writer = ConversationWriter(store, "no-compact", compaction_threshold=0)

    for i in range(10):
        await writer.append(
            RecordType.USER_MESSAGE,
            {"message": message_to_dict(Message(role="user", content=[TextBlock(text=f"msg{i}")]))},
        )

    log = store.get("event_log:no-compact")
    assert len(log) == 10  # no compaction happened


@pytest.mark.asyncio
async def test_compaction_failure_preserves_original_log():
    """If compaction fails, original log is preserved and last_error is set."""
    store = InMemoryStore()
    writer = ConversationWriter(store, "fail-compact", compaction_threshold=2)

    await writer.append(RecordType.SESSION_START, {})
    await writer.append(
        RecordType.USER_MESSAGE,
        {"message": message_to_dict(Message(role="user", content=[TextBlock(text="hello")]))},
    )

    # After 2 appends, log has 2 records. Next append → 3 records → compaction triggers.
    # Patch store.set to fail only on the SECOND call within the next append
    # (first call = the append write, second call = compaction write).
    original_set = store.set
    call_count = [0]

    def failing_set(key, value):
        call_count[0] += 1
        if call_count[0] == 2:  # fail on compaction's store.set
            raise RuntimeError("store write failed during compaction")
        return original_set(key, value)

    store.set = failing_set
    await writer.append(
        RecordType.USER_MESSAGE,
        {"message": message_to_dict(Message(role="user", content=[TextBlock(text="world")]))},
    )

    # Compaction failed — last_error should be set
    assert writer.last_error is not None
    assert "compact" in writer.last_error.details.get("operation", "")
    # Original log should still have all 3 records (the append itself succeeded)
    log = store.get("event_log:fail-compact")
    assert len(log) == 3
