"""Property-based tests for durable-sessions correctness properties 8-13.

Uses Hypothesis to verify reducer round-trip, resume rebuilds state,
compaction correctness, list_sessions limit/filter, and delete semantics.

# Feature: durable-sessions, Properties 8-13
"""

from __future__ import annotations

import base64
import time

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tvastar import Harness, create_agent
from tvastar.conversation.records import RecordType
from tvastar.conversation.reducer import reduce
from tvastar.conversation.writer import ConversationWriter
from tvastar.durable import message_to_dict
from tvastar.memory.store import InMemoryStore
from tvastar.model import MockModel
from tvastar.types import (
    ImageBlock,
    Message,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

st_text_block = st.builds(
    TextBlock,
    text=st.text(
        alphabet=st.characters(categories=("L", "N", "P", "Z")),
        min_size=1,
        max_size=100,
    ),
)

st_tool_use_block = st.builds(
    ToolUseBlock,
    id=st.from_regex(r"call_[a-f0-9]{12}", fullmatch=True),
    name=st.from_regex(r"[a-z][a-z0-9_]{0,19}", fullmatch=True),
    input=st.dictionaries(
        keys=st.from_regex(r"[a-z_]{1,10}", fullmatch=True),
        values=st.one_of(
            st.text(min_size=0, max_size=50),
            st.integers(min_value=-1000, max_value=1000),
            st.booleans(),
        ),
        min_size=0,
        max_size=3,
    ),
)

st_tool_result_block = st.builds(
    ToolResultBlock,
    tool_use_id=st.from_regex(r"call_[a-f0-9]{12}", fullmatch=True),
    content=st.text(
        alphabet=st.characters(categories=("L", "N", "P", "Z")),
        min_size=0,
        max_size=100,
    ),
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

st_session_id = st.from_regex(r"[a-z][a-z0-9\-]{0,19}", fullmatch=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _blocks_equivalent(a_blocks, b_blocks) -> bool:
    """Compare two block lists by content (not object identity)."""
    if len(a_blocks) != len(b_blocks):
        return False
    for a, b in zip(a_blocks, b_blocks):
        if type(a) is not type(b):
            return False
        if isinstance(a, TextBlock):
            if a.text != b.text:
                return False
        elif isinstance(a, ToolUseBlock):
            if a.id != b.id or a.name != b.name or a.input != b.input:
                return False
        elif isinstance(a, ToolResultBlock):
            if a.tool_use_id != b.tool_use_id or a.content != b.content or a.is_error != b.is_error:
                return False
        elif isinstance(a, ImageBlock):
            if a.data != b.data or a.media_type != b.media_type or a.source_type != b.source_type:
                return False
    return True


def _messages_equivalent(original: list[Message], loaded: list[Message]) -> bool:
    """Check that two message lists are semantically equivalent."""
    if len(original) != len(loaded):
        return False
    for a, b in zip(original, loaded):
        if a.role != b.role:
            return False
        if not _blocks_equivalent(a.blocks, b.blocks):
            return False
    return True


def _make_harness(store: InMemoryStore) -> Harness:
    """Create a minimal Harness backed by the given store."""
    agent = create_agent("test", model=MockModel(script=["ok"]), instructions="test")
    return Harness(agent, store=store)


def _serialize_message_as_record(msg: Message, seq: int) -> dict:
    """Serialize a message as a record dict suitable for event log storage."""
    rtype = "user_message" if msg.role == "user" else "assistant_message"
    return {
        "type": rtype,
        "seq": seq,
        "timestamp": float(seq),
        "data": {"message": message_to_dict(msg)},
    }


# ---------------------------------------------------------------------------
# Property 8: Reducer round-trip
# Feature: durable-sessions, Property 8: Reducer round-trip
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=None)
@given(messages=st.lists(st_message, min_size=1, max_size=10))
def test_reducer_round_trip(messages: list[Message]):
    """Property 8: Reducer round-trip.

    For any list of Messages with known block types, serializing them as
    records (user_message/assistant_message) and then reducing the resulting
    event log SHALL produce an equivalent messages list.

    **Validates: Requirements 3.5**
    """
    # Serialize each message as an event log record
    log = [_serialize_message_as_record(msg, seq=i) for i, msg in enumerate(messages)]

    # Reduce and compare
    result = reduce(log)

    assert _messages_equivalent(messages, result), (
        f"Reducer round-trip failed.\n"
        f"Original: {len(messages)} messages, Result: {len(result)} messages"
    )


# ---------------------------------------------------------------------------
# Property 9: Resume rebuilds state from log
# Feature: durable-sessions, Property 9: Resume rebuilds state from log
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=None)
@given(messages=st.lists(st_message, min_size=1, max_size=10))
def test_resume_rebuilds_state_from_log(messages: list[Message]):
    """Property 9: Resume rebuilds state from log.

    For any valid event log stored under event_log:{id}, calling
    harness.resume(id) SHALL return a session whose messages list
    equals reduce(log).

    **Validates: Requirements 4.1, 4.2, 4.4**
    """
    store = InMemoryStore()
    session_id = "prop9-session"

    # Build event log from messages
    log = [_serialize_message_as_record(msg, seq=i) for i, msg in enumerate(messages)]
    store.set(f"event_log:{session_id}", log)

    # Resume via Harness
    harness = _make_harness(store)
    session = harness.resume(session_id)

    assert session is not None, "resume() returned None for a valid event log"

    # The session's messages should match what reduce(log) produces
    expected = reduce(log)
    assert _messages_equivalent(expected, session.messages), (
        f"Resume state mismatch.\n"
        f"Expected: {len(expected)} messages, Got: {len(session.messages)} messages"
    )


# ---------------------------------------------------------------------------
# Property 10: Compaction correctness
# Feature: durable-sessions, Property 10: Compaction correctness
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=None)
@given(messages=st.lists(st_message, min_size=5, max_size=20))
@pytest.mark.asyncio
async def test_compaction_correctness(messages: list[Message]):
    """Property 10: Compaction correctness.

    For any event log with more records than the compaction threshold,
    after compaction the compacted log reduced SHALL produce the same
    messages list as reducing the original full log.

    **Validates: Requirements 10.1, 10.3**
    """
    store = InMemoryStore()
    session_id = "prop10-session"

    # Use a low threshold so compaction triggers
    writer = ConversationWriter(store, session_id, compaction_threshold=3)

    # Append messages which will trigger compaction
    for msg in messages:
        rtype = RecordType.USER_MESSAGE if msg.role == "user" else RecordType.ASSISTANT_MESSAGE
        await writer.append(rtype, {"message": message_to_dict(msg)})

    # Read the (possibly compacted) log from store
    compacted_log = store.get(writer.key) or []

    # Reduce the compacted log
    result = reduce(compacted_log)

    # The result should be equivalent to the original messages
    assert _messages_equivalent(messages, result), (
        f"Compaction correctness failed.\n"
        f"Original: {len(messages)} messages, Compacted result: {len(result)} messages"
    )


# ---------------------------------------------------------------------------
# Property 11: List sessions respects limit
# Feature: durable-sessions, Property 11: List sessions respects limit
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=None)
@given(
    n_sessions=st.integers(min_value=1, max_value=20),
    limit=st.integers(min_value=1, max_value=20),
)
def test_list_sessions_respects_limit(n_sessions: int, limit: int):
    """Property 11: List sessions respects limit.

    For any set of persisted sessions and any positive integer limit,
    list_sessions(limit=limit) SHALL return at most limit results.

    **Validates: Requirements 8.1**
    """
    store = InMemoryStore()

    # Persist N sessions
    for i in range(n_sessions):
        store.set(
            f"session_meta:sess-{i}",
            {
                "id": f"sess-{i}",
                "last_activity": float(i),
            },
        )

    harness = _make_harness(store)
    result = harness.list_sessions(limit=limit)

    assert len(result) <= limit, (
        f"list_sessions(limit={limit}) returned {len(result)} results (expected at most {limit})"
    )


# ---------------------------------------------------------------------------
# Property 12: List sessions filter correctness
# Feature: durable-sessions, Property 12: List sessions filter correctness
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=None)
@given(
    session_ids=st.lists(
        st.from_regex(r"[a-z][a-z0-9\-]{2,15}", fullmatch=True),
        min_size=1,
        max_size=10,
        unique=True,
    ),
    filter_str=st.text(
        alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="-"),
        min_size=1,
        max_size=5,
    ),
)
def test_list_sessions_filter_correctness(session_ids: list[str], filter_str: str):
    """Property 12: List sessions filter correctness.

    For any filter string and set of sessions, all SessionInfo objects
    returned by list_sessions(filter=f) SHALL have an id containing f
    as a substring.

    **Validates: Requirements 8.2**
    """
    store = InMemoryStore()

    for sid in session_ids:
        store.set(
            f"session_meta:{sid}",
            {
                "id": sid,
                "last_activity": 1.0,
            },
        )

    harness = _make_harness(store)
    result = harness.list_sessions(filter=filter_str)

    for info in result:
        assert filter_str in info.id, (
            f"Session '{info.id}' returned by list_sessions(filter='{filter_str}') "
            f"does not contain the filter string"
        )


# ---------------------------------------------------------------------------
# Property 13: Delete removes session completely
# Feature: durable-sessions, Property 13: Delete removes session completely
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=None)
@given(session_id=st_session_id)
def test_delete_removes_session_completely(session_id: str):
    """Property 13: Delete removes session completely.

    For any session that has been stored, after delete_session(id) returns
    True, resume(id) SHALL return None and list_sessions() SHALL not include
    that session.

    **Validates: Requirements 8.5**
    """
    store = InMemoryStore()

    # Store a session with event log and metadata
    msg = Message(role="user", content=[TextBlock(text="hello")])
    log = [_serialize_message_as_record(msg, seq=0)]
    store.set(f"event_log:{session_id}", log)
    store.set(
        f"session_meta:{session_id}",
        {
            "id": session_id,
            "last_activity": time.time(),
        },
    )

    harness = _make_harness(store)

    # Delete should return True
    deleted = harness.delete_session(session_id)
    assert deleted is True, f"delete_session('{session_id}') returned False for existing session"

    # resume should return None
    resumed = harness.resume(session_id)
    assert resumed is None, f"resume('{session_id}') returned a session after deletion"

    # list_sessions should not include it
    listed_ids = {info.id for info in harness.list_sessions()}
    assert session_id not in listed_ids, (
        f"Session '{session_id}' still appears in list_sessions() after deletion"
    )
