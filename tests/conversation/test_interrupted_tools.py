"""Tests for mid-tool-call recovery — Requirements 11.3, 11.4, 11.5, 11.6."""

from __future__ import annotations


from tvastar.conversation.records import RecordType, Record, record_to_dict
from tvastar.harness import Harness, _detect_interrupted_tools
from tvastar.memory.store import InMemoryStore
from tvastar.model import MockModel
from tvastar.types import Message, TextBlock, ToolUseBlock, ToolResultBlock
from tvastar import create_agent


def _make_harness(store: InMemoryStore) -> Harness:
    agent = create_agent("test-agent", model=MockModel(), instructions="hi")
    return Harness(agent, store=store)


def _msg_record(rtype: RecordType, message: Message, seq: int) -> dict:
    rec = Record(type=rtype, seq=seq, data={"message": message})
    return record_to_dict(rec)


def _tool_call_started(call_id: str, tool_name: str, seq: int) -> dict:
    rec = Record(
        type=RecordType.TOOL_CALL_STARTED,
        seq=seq,
        data={"call_id": call_id, "tool_name": tool_name, "arguments": {}},
    )
    return record_to_dict(rec)


def _tool_result(call_id: str, content: str, seq: int) -> dict:
    rec = Record(
        type=RecordType.TOOL_RESULT,
        seq=seq,
        data={"call_id": call_id, "tool_use_id": call_id, "content": content},
    )
    return record_to_dict(rec)


class TestDetectInterruptedTools:
    """Unit tests for _detect_interrupted_tools helper."""

    def test_no_tools_returns_empty(self):
        log = [{"type": "session_start", "seq": 0, "timestamp": 1.0, "data": {}}]
        assert _detect_interrupted_tools(log) == []

    def test_matched_pair_returns_empty(self):
        log = [
            _tool_call_started("call_1", "read_file", 0),
            _tool_result("call_1", "file content", 1),
        ]
        assert _detect_interrupted_tools(log) == []

    def test_unmatched_tool_call_detected(self):
        log = [
            _tool_call_started("call_1", "read_file", 0),
            # No matching tool_result for call_1
        ]
        result = _detect_interrupted_tools(log)
        assert result == ["call_1"]

    def test_multiple_unmatched_detected(self):
        log = [
            _tool_call_started("call_1", "read_file", 0),
            _tool_call_started("call_2", "write_file", 1),
            _tool_result("call_1", "done", 2),
            # call_2 has no result
        ]
        result = _detect_interrupted_tools(log)
        assert result == ["call_2"]

    def test_handles_none_records(self):
        log = [
            _tool_call_started("call_1", "read_file", 0),
            None,  # corrupted record
            _tool_result("call_1", "ok", 2),
        ]
        assert _detect_interrupted_tools(log) == []

    def test_handles_records_without_call_id(self):
        log = [
            {"type": "session_start", "seq": 0, "timestamp": 1.0, "data": {}},
            _tool_call_started("call_1", "tool_a", 1),
            _tool_result("call_1", "ok", 2),
        ]
        assert _detect_interrupted_tools(log) == []


class TestResumeMarksInterruptedTools:
    """Integration: resume() inserts Interrupted_Markers for unresolved tool calls."""

    def test_resume_inserts_marker_for_interrupted_tool(self):
        """REQ 11.3, 11.4: Detect unmatched tool_call_started, insert marker."""
        store = InMemoryStore()
        session_id = "interrupted-session"

        # Build a log where the assistant called a tool but the result was never written
        user_msg = Message(role="user", content=[TextBlock(text="Do something")])
        asst_msg = Message(
            role="assistant",
            content=[ToolUseBlock(id="call_abc", name="read_file", input={"path": "x.py"})],
        )

        log = [
            {"type": "session_start", "seq": 0, "timestamp": 1.0, "data": {}},
            _msg_record(RecordType.USER_MESSAGE, user_msg, seq=1),
            _msg_record(RecordType.ASSISTANT_MESSAGE, asst_msg, seq=2),
            _tool_call_started("call_abc", "read_file", 3),
            # Process crashed here — no tool_result for call_abc
        ]
        store.set(f"event_log:{session_id}", log)

        harness = _make_harness(store)
        session = harness.resume(session_id)

        assert session is not None
        # The messages should now contain the interrupted marker as a tool result
        # Original: user, assistant. After resume: user, assistant, user(tool_result interrupted)
        assert len(session.messages) == 3
        last_msg = session.messages[-1]
        assert last_msg.role == "user"
        # The content should be a list with a ToolResultBlock
        blocks = last_msg.blocks
        assert len(blocks) == 1
        assert isinstance(blocks[0], ToolResultBlock)
        assert blocks[0].tool_use_id == "call_abc"
        assert blocks[0].is_error is True
        assert "interrupted" in blocks[0].content.lower()

    def test_resume_does_not_reexecute_interrupted_tool(self):
        """REQ 11.5: Interrupted tools are NOT re-executed, only marked."""
        store = InMemoryStore()
        session_id = "no-reexecute"

        user_msg = Message(role="user", content=[TextBlock(text="run tool")])
        asst_msg = Message(
            role="assistant", content=[ToolUseBlock(id="call_xyz", name="dangerous_tool", input={})]
        )

        log = [
            _msg_record(RecordType.USER_MESSAGE, user_msg, seq=0),
            _msg_record(RecordType.ASSISTANT_MESSAGE, asst_msg, seq=1),
            _tool_call_started("call_xyz", "dangerous_tool", 2),
        ]
        store.set(f"event_log:{session_id}", log)

        harness = _make_harness(store)
        session = harness.resume(session_id)

        # Verify only a marker was inserted, not a real tool result
        assert session is not None
        updated_log = store.get(f"event_log:{session_id}")
        # Original 3 records + 2 new (TOOL_RESULT marker + USER_MESSAGE)
        assert len(updated_log) == 5
        # The TOOL_RESULT marker has interrupted=True
        marker = updated_log[3]
        assert marker["type"] == "tool_result"
        assert marker["data"]["interrupted"] is True
        assert marker["data"]["is_error"] is True
        assert marker["data"]["call_id"] == "call_xyz"

    def test_resume_with_all_tools_completed_no_markers(self):
        """When all tools completed normally, no markers are inserted."""
        store = InMemoryStore()
        session_id = "all-complete"

        user_msg = Message(role="user", content=[TextBlock(text="hi")])
        asst_msg = Message(
            role="assistant", content=[ToolUseBlock(id="call_1", name="read_file", input={})]
        )
        result_msg = Message(
            role="user", content=[ToolResultBlock(tool_use_id="call_1", content="file content")]
        )

        log = [
            _msg_record(RecordType.USER_MESSAGE, user_msg, seq=0),
            _msg_record(RecordType.ASSISTANT_MESSAGE, asst_msg, seq=1),
            _tool_call_started("call_1", "read_file", 2),
            _tool_result("call_1", "file content", 3),
            _msg_record(RecordType.USER_MESSAGE, result_msg, seq=4),
        ]
        store.set(f"event_log:{session_id}", log)

        harness = _make_harness(store)
        session = harness.resume(session_id)

        assert session is not None
        # Log unchanged — no markers added
        updated_log = store.get(f"event_log:{session_id}")
        assert len(updated_log) == 5

    def test_resume_writer_seq_accounts_for_markers(self):
        """Writer sequence number is correct after markers are inserted."""
        store = InMemoryStore()
        session_id = "seq-after-marker"

        log = [
            {"type": "session_start", "seq": 0, "timestamp": 1.0, "data": {}},
            _tool_call_started("call_1", "tool_a", 1),
        ]
        store.set(f"event_log:{session_id}", log)

        harness = _make_harness(store)
        session = harness.resume(session_id)

        assert session is not None
        # Original 2 + 2 marker records = 4
        assert session._writer._seq == 4

    def test_resume_multiple_interrupted_tools(self):
        """Multiple interrupted tools each get their own marker."""
        store = InMemoryStore()
        session_id = "multi-interrupt"

        asst_msg = Message(
            role="assistant",
            content=[
                ToolUseBlock(id="call_a", name="tool_1", input={}),
                ToolUseBlock(id="call_b", name="tool_2", input={}),
            ],
        )

        log = [
            _msg_record(RecordType.ASSISTANT_MESSAGE, asst_msg, seq=0),
            _tool_call_started("call_a", "tool_1", 1),
            _tool_call_started("call_b", "tool_2", 2),
            # Both interrupted
        ]
        store.set(f"event_log:{session_id}", log)

        harness = _make_harness(store)
        session = harness.resume(session_id)

        assert session is not None
        updated_log = store.get(f"event_log:{session_id}")
        # Original 3 + 4 (2 markers × 2 records each) = 7
        assert len(updated_log) == 7

        # Check that both markers are present
        markers = [
            r for r in updated_log if r["type"] == "tool_result" and r["data"].get("interrupted")
        ]
        assert len(markers) == 2
        marker_ids = {m["data"]["call_id"] for m in markers}
        assert marker_ids == {"call_a", "call_b"}
