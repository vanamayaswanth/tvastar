"""Unit tests for Checkpointer and resume (durable execution).

Tests cover:
- Checkpoint saves message history after every tool turn
- harness.resume() restores Session with full message history
- Corrupted checkpoint store returns None from resume()
- Checkpoint format includes session ID, messages, sandbox state
- Resumed Session continues loop from restored state

Validates: Requirements 11.1, 11.2, 11.3, 11.4, 11.5
"""

from __future__ import annotations

from typing import Any, Optional

from tvastar import Harness, create_agent
from tvastar.durable import Checkpointer, message_from_dict, message_to_dict
from tvastar.memory.store import InMemoryStore, Store
from tvastar.model.mock import MockModel
from tvastar.tools.base import tool
from tvastar.types import (
    Message,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@tool
def echo(text: str) -> str:
    """Echo back the text."""
    return f"echo:{text}"


@tool
def concat(a: str, b: str) -> str:
    """Concatenate two strings."""
    return f"{a}{b}"


def _make_agent(script, tools=None, max_steps=20):
    """Create a test agent with a scripted MockModel."""
    tool_list = tools or [echo, concat]
    return create_agent(
        "test-durable",
        model=MockModel(script),
        instructions="You are a durable test agent.",
        tools=tool_list,
        max_steps=max_steps,
        detect=False,
    )


class NoneReturningStore(Store):
    """A store that always returns None for get() — simulating missing/corrupted data."""

    def get(self, key: str) -> Optional[Any]:
        return None

    def set(self, key: str, value: Any) -> None:
        pass

    def delete(self, key: str) -> None:
        pass

    def keys(self, prefix: str = "") -> list[str]:
        return []


# ---------------------------------------------------------------------------
# Test: Checkpoint saves message history after every tool turn
# ---------------------------------------------------------------------------


async def test_checkpoint_saves_after_tool_turn():
    """Verify that checkpoint is called after every tool turn during a session.

    Validates: Requirement 11.1
    """
    # Script: model calls echo tool, then ends turn
    script = [
        ToolUseBlock(id="tu_1", name="echo", input={"text": "hello"}),
        "Done echoing.",
    ]
    spec = _make_agent(script)
    store = InMemoryStore()
    harness = Harness(spec, store=store, durable=True)

    session = harness.session(session_id="test-cp-001")
    async with session:
        result = await session.prompt("Echo hello please")

    assert result.stopped == "end_turn"
    # Checkpoint should have been saved — verify it exists
    record = store.get("session:test-cp-001")
    assert record is not None
    assert "messages" in record
    assert "session_id" in record
    assert record["session_id"] == "test-cp-001"
    # Messages should include the conversation
    assert len(record["messages"]) > 0


async def test_checkpoint_saves_multi_step_tool_turns():
    """Verify checkpoint is saved after each tool turn in a multi-step interaction.

    Validates: Requirement 11.1
    """
    # Script: model calls tool twice, then ends
    script = [
        ToolUseBlock(id="tu_1", name="echo", input={"text": "first"}),
        ToolUseBlock(id="tu_2", name="echo", input={"text": "second"}),
        "All done.",
    ]
    spec = _make_agent(script)
    store = InMemoryStore()
    harness = Harness(spec, store=store, durable=True)

    session = harness.session(session_id="test-cp-002")
    async with session:
        result = await session.prompt("Echo twice")

    assert result.stopped == "end_turn"
    record = store.get("session:test-cp-002")
    assert record is not None
    # Should have all messages: user + (assistant+tool_result)*2 + final assistant
    messages = record["messages"]
    assert len(messages) >= 5  # user, asst+tool_res, asst+tool_res, final asst


# ---------------------------------------------------------------------------
# Test: harness.resume() restores Session with full message history
# ---------------------------------------------------------------------------


async def test_resume_restores_session_with_history():
    """Verify that harness.resume() restores a session with full message history.

    Validates: Requirement 11.2
    """
    # First, create a session and run a prompt to save checkpoint
    script = [
        ToolUseBlock(id="tu_1", name="echo", input={"text": "saved"}),
        "Checkpoint saved.",
    ]
    spec = _make_agent(script)
    store = InMemoryStore()
    harness = Harness(spec, store=store, durable=True)

    session = harness.session(session_id="resume-001")
    async with session:
        await session.prompt("Save this")

    # Verify checkpoint was saved
    assert store.get("session:resume-001") is not None

    # Now resume from a fresh harness using the same store
    resume_script = ["Resumed successfully."]
    resume_spec = _make_agent(resume_script)
    harness2 = Harness(resume_spec, store=store, durable=True)

    restored = harness2.resume("resume-001")
    assert restored is not None
    # Restored session should have the messages from original run
    assert len(restored.messages) > 0
    # Check that the original user message is in the history
    user_msgs = [m for m in restored.messages if m.role == "user"]
    assert any("Save this" in m.text for m in user_msgs)


async def test_resume_returns_none_for_unknown_session():
    """Verify resume() returns None when the session ID doesn't exist.

    Validates: Requirement 11.2
    """
    spec = _make_agent(["Done."])
    store = InMemoryStore()
    harness = Harness(spec, store=store, durable=True)

    result = harness.resume("nonexistent-session-999")
    assert result is None


async def test_resume_returns_none_without_store():
    """Verify resume() returns None when durable=False (no checkpointer).

    Validates: Requirement 11.2
    """
    spec = _make_agent(["Done."])
    harness = Harness(spec, durable=False)

    result = harness.resume("any-session-id")
    assert result is None


# ---------------------------------------------------------------------------
# Test: Corrupted checkpoint store returns None from resume()
# ---------------------------------------------------------------------------


async def test_corrupted_store_returns_none_on_resume():
    """Verify that a store simulating corruption (returning None) causes
    resume() to return None gracefully.

    FileStore.get() returns None on corrupted/unreadable data. This test
    verifies that the chain from Store.get()→None → Checkpointer.load()→None
    → Harness.resume()→None works as expected.

    Validates: Requirement 11.3
    """
    spec = _make_agent(["Done."])
    store = NoneReturningStore()
    harness = Harness(spec, store=store, durable=True)

    # The store returns None for any get — resume() should return None
    result = harness.resume("broken-session")
    assert result is None


async def test_corrupted_store_with_saved_then_corrupted():
    """Verify that a store that had data saved but then returns garbled data
    (simulating disk corruption) causes resume() to return None.

    Validates: Requirement 11.3
    """
    # Use a real InMemoryStore, save a checkpoint, then corrupt it
    spec = _make_agent([
        ToolUseBlock(id="tu_1", name="echo", input={"text": "data"}),
        "Saved.",
    ])
    store = InMemoryStore()
    harness = Harness(spec, store=store, durable=True)

    session = harness.session(session_id="corrupt-001")
    async with session:
        await session.prompt("Save something")

    # Verify checkpoint exists
    assert store.get("session:corrupt-001") is not None

    # Now corrupt the data by replacing with something invalid
    store.set("session:corrupt-001", None)

    # Resume should return None because the record is falsy
    spec2 = _make_agent(["Done."])
    harness2 = Harness(spec2, store=store, durable=True)
    result = harness2.resume("corrupt-001")
    assert result is None


async def test_none_returning_store_returns_none_on_resume():
    """Verify that a store returning None for a session causes resume() to return None.

    Validates: Requirement 11.3
    """
    spec = _make_agent(["Done."])
    store = NoneReturningStore()
    harness = Harness(spec, store=store, durable=True)

    result = harness.resume("missing-session")
    assert result is None


# ---------------------------------------------------------------------------
# Test: Checkpoint format includes session ID, messages, sandbox state
# ---------------------------------------------------------------------------


async def test_checkpoint_format_includes_session_id():
    """Verify checkpoint format includes the session_id field.

    Validates: Requirement 11.4
    """
    script = [
        ToolUseBlock(id="tu_1", name="echo", input={"text": "format-test"}),
        "Format verified.",
    ]
    spec = _make_agent(script)
    store = InMemoryStore()
    harness = Harness(spec, store=store, durable=True)

    session = harness.session(session_id="format-001")
    async with session:
        await session.prompt("Test format")

    record = store.get("session:format-001")
    assert record is not None
    assert record["session_id"] == "format-001"


async def test_checkpoint_format_includes_messages():
    """Verify checkpoint format includes serialized messages.

    Validates: Requirement 11.4
    """
    script = [
        ToolUseBlock(id="tu_1", name="echo", input={"text": "msg-test"}),
        "Messages stored.",
    ]
    spec = _make_agent(script)
    store = InMemoryStore()
    harness = Harness(spec, store=store, durable=True)

    session = harness.session(session_id="format-002")
    async with session:
        await session.prompt("Test messages in format")

    record = store.get("session:format-002")
    assert record is not None
    assert "messages" in record
    # Messages should be serialized as dicts (before load reconstruction)
    messages = record["messages"]
    assert isinstance(messages, list)
    assert len(messages) > 0
    # Each message should have the expected structure
    for msg in messages:
        assert "role" in msg
        assert "blocks" in msg


async def test_checkpoint_format_includes_sandbox_state():
    """Verify checkpoint format includes fs_snapshot for VirtualSandbox.

    Validates: Requirement 11.4
    """
    script = [
        ToolUseBlock(id="tu_1", name="echo", input={"text": "sandbox-test"}),
        "Sandbox captured.",
    ]
    spec = _make_agent(script)
    store = InMemoryStore()
    harness = Harness(spec, store=store, durable=True)

    session = harness.session(session_id="format-003")
    async with session:
        await session.prompt("Test sandbox state")

    record = store.get("session:format-003")
    assert record is not None
    # fs_snapshot key should be present (may be None or dict depending on sandbox)
    assert "fs_snapshot" in record


async def test_checkpoint_format_includes_meta():
    """Verify checkpoint format includes metadata (agent name, timestamp).

    Validates: Requirement 11.4
    """
    script = [
        ToolUseBlock(id="tu_1", name="echo", input={"text": "meta-test"}),
        "Meta stored.",
    ]
    spec = _make_agent(script)
    store = InMemoryStore()
    harness = Harness(spec, store=store, durable=True)

    session = harness.session(session_id="format-004")
    async with session:
        await session.prompt("Test meta in format")

    record = store.get("session:format-004")
    assert record is not None
    assert "meta" in record
    meta = record["meta"]
    assert "agent" in meta
    assert meta["agent"] == "test-durable"
    assert "at" in meta  # timestamp


# ---------------------------------------------------------------------------
# Test: Resumed Session continues loop from restored state
# ---------------------------------------------------------------------------


async def test_resumed_session_continues_from_restored_state():
    """Verify that a resumed session can continue the loop from restored state.

    Validates: Requirement 11.5
    """
    # Phase 1: Create session and run initial prompt to establish history
    script_phase1 = [
        ToolUseBlock(id="tu_1", name="echo", input={"text": "phase1"}),
        "Phase 1 complete.",
    ]
    spec1 = _make_agent(script_phase1)
    store = InMemoryStore()
    harness1 = Harness(spec1, store=store, durable=True)

    session1 = harness1.session(session_id="continue-001")
    async with session1:
        result1 = await session1.prompt("Start phase 1")

    assert result1.stopped == "end_turn"
    original_msg_count = len(session1.messages)
    assert original_msg_count > 0

    # Phase 2: Resume and continue with new prompt
    script_phase2 = [
        ToolUseBlock(id="tu_2", name="concat", input={"a": "hello", "b": "world"}),
        "Phase 2 complete.",
    ]
    spec2 = _make_agent(script_phase2)
    harness2 = Harness(spec2, store=store, durable=True)

    restored = harness2.resume("continue-001")
    assert restored is not None
    assert len(restored.messages) == original_msg_count

    # Continue from restored state — session should work with existing history
    async with restored:
        result2 = await restored.prompt("Continue to phase 2")

    assert result2.stopped == "end_turn"
    # Messages should have grown from original state
    assert len(restored.messages) > original_msg_count


async def test_resumed_session_preserves_message_order():
    """Verify that resumed session messages are in the same order as original.

    Validates: Requirement 11.5
    """
    script = [
        ToolUseBlock(id="tu_1", name="echo", input={"text": "first"}),
        ToolUseBlock(id="tu_2", name="echo", input={"text": "second"}),
        "All done.",
    ]
    spec = _make_agent(script)
    store = InMemoryStore()
    harness = Harness(spec, store=store, durable=True)

    session = harness.session(session_id="order-001")
    async with session:
        await session.prompt("Do two things")

    # Capture original message roles/content before resume
    original_roles = [m.role for m in session.messages]

    # Resume and verify order is preserved
    spec2 = _make_agent(["Continued."])
    harness2 = Harness(spec2, store=store, durable=True)
    restored = harness2.resume("order-001")
    assert restored is not None

    restored_roles = [m.role for m in restored.messages]
    assert restored_roles == original_roles


# ---------------------------------------------------------------------------
# Test: Checkpointer unit tests (low-level)
# ---------------------------------------------------------------------------


def test_checkpointer_save_and_load():
    """Verify Checkpointer.save() followed by load() round-trips messages."""
    store = InMemoryStore()
    cp = Checkpointer(store)

    messages = [
        Message("user", [TextBlock(text="Hello")]),
        Message("assistant", [TextBlock(text="Hi there!")]),
    ]

    cp.save("unit-001", messages=messages)
    record = cp.load("unit-001")

    assert record is not None
    assert record["session_id"] == "unit-001"
    loaded_msgs = record["messages"]
    assert len(loaded_msgs) == 2
    assert loaded_msgs[0].role == "user"
    assert loaded_msgs[0].text == "Hello"
    assert loaded_msgs[1].role == "assistant"
    assert loaded_msgs[1].text == "Hi there!"


def test_checkpointer_load_nonexistent_returns_none():
    """Verify Checkpointer.load() returns None for missing session."""
    store = InMemoryStore()
    cp = Checkpointer(store)

    result = cp.load("does-not-exist")
    assert result is None


def test_checkpointer_exists():
    """Verify Checkpointer.exists() reflects saved state."""
    store = InMemoryStore()
    cp = Checkpointer(store)

    assert cp.exists("test-exist") is False
    cp.save("test-exist", messages=[])
    assert cp.exists("test-exist") is True


def test_checkpointer_list_sessions():
    """Verify Checkpointer.list_sessions() returns all saved session IDs."""
    store = InMemoryStore()
    cp = Checkpointer(store)

    cp.save("sess-a", messages=[])
    cp.save("sess-b", messages=[])
    cp.save("sess-c", messages=[])

    sessions = cp.list_sessions()
    assert "sess-a" in sessions
    assert "sess-b" in sessions
    assert "sess-c" in sessions


def test_checkpointer_saves_fs_snapshot():
    """Verify Checkpointer saves fs_snapshot when provided."""
    store = InMemoryStore()
    cp = Checkpointer(store)

    snapshot = {"main.py": "print('hello')", "data.txt": "some data"}
    cp.save("snap-001", messages=[], fs_snapshot=snapshot)

    record = cp.load("snap-001")
    assert record is not None
    assert record["fs_snapshot"] == snapshot


def test_checkpointer_saves_meta():
    """Verify Checkpointer saves meta when provided."""
    store = InMemoryStore()
    cp = Checkpointer(store)

    meta = {"agent": "test-agent", "at": 1234567890.0}
    cp.save("meta-001", messages=[], meta=meta)

    record = cp.load("meta-001")
    assert record is not None
    assert record["meta"] == meta


# ---------------------------------------------------------------------------
# Test: message_to_dict / message_from_dict serialization
# ---------------------------------------------------------------------------


def test_message_to_dict_text_block():
    """Verify TextBlock serialization."""
    msg = Message("user", [TextBlock(text="Hello world")])
    d = message_to_dict(msg)
    assert d["role"] == "user"
    assert d["blocks"] == [{"type": "text", "text": "Hello world"}]


def test_message_to_dict_tool_use_block():
    """Verify ToolUseBlock serialization."""
    msg = Message("assistant", [ToolUseBlock(id="tu_1", name="search", input={"q": "test"})])
    d = message_to_dict(msg)
    assert d["blocks"] == [{"type": "tool_use", "id": "tu_1", "name": "search", "input": {"q": "test"}}]


def test_message_to_dict_tool_result_block():
    """Verify ToolResultBlock serialization."""
    msg = Message("user", [ToolResultBlock(tool_use_id="tu_1", content="result", is_error=False)])
    d = message_to_dict(msg)
    assert d["blocks"] == [
        {"type": "tool_result", "tool_use_id": "tu_1", "content": "result", "is_error": False}
    ]


def test_message_from_dict_round_trip():
    """Verify message_to_dict → message_from_dict round-trips correctly."""
    original = Message(
        "assistant",
        [
            TextBlock(text="Let me search for that."),
            ToolUseBlock(id="tu_1", name="search", input={"query": "python"}),
        ],
    )
    d = message_to_dict(original)
    restored = message_from_dict(d)

    assert restored.role == original.role
    assert len(restored.blocks) == 2
    assert isinstance(restored.blocks[0], TextBlock)
    assert restored.blocks[0].text == "Let me search for that."
    assert isinstance(restored.blocks[1], ToolUseBlock)
    assert restored.blocks[1].name == "search"
    assert restored.blocks[1].input == {"query": "python"}
