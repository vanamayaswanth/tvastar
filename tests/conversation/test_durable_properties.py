"""Property-based tests for durable-sessions (Properties 1-7).

Uses Hypothesis to verify correctness properties across random inputs.
"""

from __future__ import annotations

import asyncio
import base64

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tvastar import Harness, create_agent
from tvastar.conversation.records import Record, RecordType, record_from_dict, record_to_dict
from tvastar.conversation.reducer import reduce
from tvastar.conversation.writer import ConversationWriter
from tvastar.errors import DurableError
from tvastar.memory.store import InMemoryStore, Store
from tvastar.model import MockModel
from tvastar.types import ImageBlock, Message, TextBlock, ToolResultBlock, ToolUseBlock

# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

# Valid session name characters: letters, numbers, punctuation, symbols (no null bytes)
st_session_name = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "S"), blacklist_characters="\x00"),
    min_size=1,
    max_size=50,
)

# Block strategies
st_text_block = st.builds(TextBlock, text=st.text(min_size=1, max_size=100))

st_tool_use_block = st.builds(
    ToolUseBlock,
    id=st.text(min_size=1, max_size=20).map(lambda s: f"tu_{s}"),
    name=st.text(min_size=1, max_size=30),
    input=st.dictionaries(st.text(min_size=1, max_size=10), st.text(max_size=50), max_size=3),
)

st_tool_result_block = st.builds(
    ToolResultBlock,
    tool_use_id=st.text(min_size=1, max_size=20).map(lambda s: f"tu_{s}"),
    content=st.text(max_size=200),
    is_error=st.booleans(),
)

st_image_block = st.builds(
    ImageBlock,
    data=st.binary(min_size=1, max_size=100).map(lambda b: base64.b64encode(b).decode()),
    media_type=st.sampled_from(["image/jpeg", "image/png", "image/gif", "image/webp"]),
    source_type=st.sampled_from(["base64", "url"]),
)

st_block = st.one_of(st_text_block, st_tool_use_block, st_tool_result_block, st_image_block)

st_message = st.builds(
    Message,
    role=st.sampled_from(["user", "assistant"]),
    content=st.lists(st_block, min_size=1, max_size=4),
)

# Record strategy — message-bearing record types carry a message in data
st_record_type = st.sampled_from(list(RecordType))

st_message_bearing_type = st.sampled_from(
    [
        RecordType.USER_MESSAGE,
        RecordType.ASSISTANT_MESSAGE,
        RecordType.TOOL_USE,
        RecordType.TOOL_RESULT,
    ]
)


@st.composite
def st_record(draw: st.DrawFn) -> Record:
    """Generate a random Record with appropriate data for its type."""
    rtype = draw(st_record_type)
    seq = draw(st.integers(min_value=0, max_value=10000))
    timestamp = draw(st.floats(min_value=0.0, max_value=2000000000.0, allow_nan=False))
    if rtype in (
        RecordType.USER_MESSAGE,
        RecordType.ASSISTANT_MESSAGE,
        RecordType.TOOL_USE,
        RecordType.TOOL_RESULT,
    ):
        msg = draw(st_message)
        data = {"message": msg}
    elif rtype == RecordType.ERROR:
        data = {"error": draw(st.text(min_size=1, max_size=50))}
    elif rtype == RecordType.SESSION_START:
        data = {}
    else:  # SESSION_END
        data = {}
    return Record(type=rtype, seq=seq, timestamp=timestamp, data=data)


@st.composite
def st_event_log(draw: st.DrawFn) -> list[dict]:
    """Generate a valid event log (list of record dicts with message-bearing types)."""
    n = draw(st.integers(min_value=1, max_value=20))
    log = []
    for i in range(n):
        rtype = draw(st_message_bearing_type)
        msg = draw(st_message)
        from tvastar.durable import message_to_dict

        record_dict = {
            "type": rtype.value,
            "seq": i,
            "timestamp": 1700000000.0 + i,
            "data": {"message": message_to_dict(msg)},
        }
        log.append(record_dict)
    return log


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_harness():
    agent = create_agent("test", model=MockModel(script=["ok"]), instructions="test")
    return Harness(agent)


class FailingStore(Store):
    """A Store that always raises on set()."""

    def __init__(self):
        self._data: dict = {}

    def get(self, key: str):
        return self._data.get(key)

    def set(self, key: str, value) -> None:
        raise IOError("store write failed")

    def delete(self, key: str) -> None:
        self._data.pop(key, None)

    def keys(self, prefix: str = "") -> list[str]:
        return [k for k in self._data if k.startswith(prefix)]


# ---------------------------------------------------------------------------
# Property Tests
# ---------------------------------------------------------------------------


# Feature: durable-sessions, Property 1: Session identity preservation
class TestProperty1SessionIdentity:
    """For any valid name string provided to harness.session(name=X),
    the resulting session's id field SHALL equal X.

    **Validates: Requirements 1.1**
    """

    @given(name=st_session_name)
    @settings(max_examples=100)
    def test_session_id_equals_name(self, name: str):
        harness = make_harness()
        session = harness.session(name=name)
        assert session.id == name


# Feature: durable-sessions, Property 2: Session singleton
class TestProperty2SessionSingleton:
    """For any name string, calling harness.session(name=X) twice on the same
    Harness instance SHALL return the same object (identity equality).

    **Validates: Requirements 1.6**
    """

    @given(name=st_session_name)
    @settings(max_examples=100)
    def test_session_singleton(self, name: str):
        harness = make_harness()
        s1 = harness.session(name=name)
        s2 = harness.session(name=name)
        assert s1 is s2


# Feature: durable-sessions, Property 3: Record serialization round-trip
class TestProperty3RecordRoundTrip:
    """For any valid Record containing messages with any combination of known
    block types (TextBlock, ToolUseBlock, ToolResultBlock, ImageBlock),
    record_from_dict(record_to_dict(record)) SHALL produce an equivalent Record.

    **Validates: Requirements 2.6, 7.2, 7.3**
    """

    @given(record=st_record())
    @settings(max_examples=100)
    def test_record_round_trip(self, record: Record):
        serialized = record_to_dict(record)
        deserialized = record_from_dict(serialized)

        assert deserialized.type == record.type
        assert deserialized.seq == record.seq
        assert deserialized.timestamp == record.timestamp

        # For message-bearing records, verify message content is preserved
        if "message" in record.data and isinstance(record.data["message"], Message):
            orig_msg = record.data["message"]
            deser_msg = deserialized.data["message"]
            assert isinstance(deser_msg, Message)
            assert deser_msg.role == orig_msg.role
            assert len(deser_msg.blocks) == len(orig_msg.blocks)
            for orig_block, deser_block in zip(orig_msg.blocks, deser_msg.blocks):
                assert type(orig_block) is type(deser_block)
                if isinstance(orig_block, TextBlock):
                    assert deser_block.text == orig_block.text
                elif isinstance(orig_block, ToolUseBlock):
                    assert deser_block.id == orig_block.id
                    assert deser_block.name == orig_block.name
                    assert deser_block.input == orig_block.input
                elif isinstance(orig_block, ToolResultBlock):
                    assert deser_block.tool_use_id == orig_block.tool_use_id
                    assert deser_block.content == orig_block.content
                    assert deser_block.is_error == orig_block.is_error
                elif isinstance(orig_block, ImageBlock):
                    assert deser_block.data == orig_block.data
                    assert deser_block.media_type == orig_block.media_type
                    assert deser_block.source_type == orig_block.source_type
        else:
            # Non-message data should round-trip as plain dict
            assert deserialized.data == record.data


# Feature: durable-sessions, Property 4: Event log integrity
class TestProperty4EventLogIntegrity:
    """For any sequence of N append calls to a ConversationWriter, the resulting
    event log SHALL contain exactly N records with sequence numbers 0..N-1
    and non-decreasing timestamps.

    **Validates: Requirements 2.2, 2.3**
    """

    @given(n=st.integers(min_value=1, max_value=50))
    @settings(max_examples=100)
    @pytest.mark.asyncio
    async def test_event_log_integrity(self, n: int):
        store = InMemoryStore()
        writer = ConversationWriter(store, "test-session", compaction_threshold=0)

        for _ in range(n):
            await writer.append(
                RecordType.USER_MESSAGE,
                {"message": {"role": "user", "blocks": [{"type": "text", "text": "hi"}]}},
            )

        log = store.get(writer.key) or []
        assert len(log) == n

        # Sequence numbers are 0..N-1
        seqs = [r["seq"] for r in log]
        assert seqs == list(range(n))

        # Timestamps are non-decreasing
        timestamps = [r["timestamp"] for r in log]
        for i in range(1, len(timestamps)):
            assert timestamps[i] >= timestamps[i - 1]


# Feature: durable-sessions, Property 5: Best-effort durability degradation
class TestProperty5BestEffortDurability:
    """For any ConversationWriter with a Store that raises on set(), after a
    failed append the writer's last_error SHALL be a DurableError, and
    subsequent appends SHALL continue to produce valid Records.

    **Validates: Requirements 2.5**
    """

    @given(n=st.integers(min_value=1, max_value=20))
    @settings(max_examples=100)
    @pytest.mark.asyncio
    async def test_best_effort_degradation(self, n: int):
        store = FailingStore()
        writer = ConversationWriter(store, "fail-session", compaction_threshold=0)

        records = []
        for _ in range(n):
            record = await writer.append(
                RecordType.USER_MESSAGE,
                {"message": {"role": "user", "blocks": [{"type": "text", "text": "hi"}]}},
            )
            records.append(record)

        # last_error should be a DurableError
        assert isinstance(writer.last_error, DurableError)

        # All appends still produced valid Record objects
        assert len(records) == n
        for i, r in enumerate(records):
            assert isinstance(r, Record)
            assert r.seq == i
            assert r.type == RecordType.USER_MESSAGE


# Feature: durable-sessions, Property 6: Concurrent append safety
class TestProperty6ConcurrentAppendSafety:
    """For any set of N records appended concurrently from M coroutines to the
    same ConversationWriter, the final event log SHALL contain exactly N records
    with sequence numbers 0..N-1 and no lost updates.

    **Validates: Requirements 2.7, 9.1, 9.2**
    """

    @given(n=st.integers(min_value=5, max_value=20))
    @settings(max_examples=100)
    @pytest.mark.asyncio
    async def test_concurrent_append_safety(self, n: int):
        store = InMemoryStore()
        writer = ConversationWriter(store, "conc-session", compaction_threshold=0)

        async def do_append():
            return await writer.append(
                RecordType.USER_MESSAGE,
                {"message": {"role": "user", "blocks": [{"type": "text", "text": "hi"}]}},
            )

        # Launch N concurrent appends
        results = await asyncio.gather(*[do_append() for _ in range(n)])

        # All N records were returned
        assert len(results) == n

        # Final log contains exactly N records
        log = store.get(writer.key) or []
        assert len(log) == n

        # Sequence numbers are 0..N-1 (no gaps, no duplicates)
        seqs = sorted(r["seq"] for r in log)
        assert seqs == list(range(n))


# Feature: durable-sessions, Property 7: Reducer determinism
class TestProperty7ReducerDeterminism:
    """For any valid event log, calling reduce(log) multiple times SHALL always
    produce the same messages list (referential transparency).

    **Validates: Requirements 3.1, 3.2**
    """

    @given(log=st_event_log())
    @settings(max_examples=100)
    def test_reducer_determinism(self, log: list[dict]):
        result1 = reduce(log)
        result2 = reduce(log)
        result3 = reduce(log)

        # All three calls produce the same number of messages
        assert len(result1) == len(result2) == len(result3)

        # Each message is equivalent across calls
        for m1, m2, m3 in zip(result1, result2, result3):
            assert m1.role == m2.role == m3.role
            assert len(m1.blocks) == len(m2.blocks) == len(m3.blocks)
            for b1, b2, b3 in zip(m1.blocks, m2.blocks, m3.blocks):
                assert type(b1) is type(b2) is type(b3)
                if isinstance(b1, TextBlock):
                    assert b1.text == b2.text == b3.text
                elif isinstance(b1, ToolUseBlock):
                    assert b1.id == b2.id == b3.id
                    assert b1.name == b2.name == b3.name
                    assert b1.input == b2.input == b3.input
                elif isinstance(b1, ToolResultBlock):
                    assert b1.tool_use_id == b2.tool_use_id == b3.tool_use_id
                    assert b1.content == b2.content == b3.content
                    assert b1.is_error == b2.is_error == b3.is_error
                elif isinstance(b1, ImageBlock):
                    assert b1.data == b2.data == b3.data
                    assert b1.media_type == b2.media_type == b3.media_type
                    assert b1.source_type == b2.source_type == b3.source_type
