"""Integration tests for crash recovery — Requirements 4.1, 4.2, 4.3, 4.5."""

from __future__ import annotations

from tvastar.conversation.records import RecordType, record_to_dict, Record
from tvastar.harness import Harness
from tvastar.memory.store import InMemoryStore
from tvastar.model import MockModel
from tvastar.types import Message, TextBlock
from tvastar import create_agent


def _make_harness(store: InMemoryStore) -> Harness:
    """Create a minimal Harness backed by the given store."""
    agent = create_agent("test-agent", model=MockModel(), instructions="hi")
    return Harness(agent, store=store)


def _msg_record(rtype: RecordType, message: Message, seq: int) -> dict:
    """Build a serialized record dict suitable for store persistence."""
    rec = Record(type=rtype, seq=seq, data={"message": message})
    return record_to_dict(rec)


class TestFullRecovery:
    """Req 4.1, 4.2: Resume from event log after simulated restart."""

    def test_resume_recovers_all_messages(self):
        store = InMemoryStore()
        session_id = "crash-test-session"

        # Simulate a session that wrote several records before crash
        user_msg = Message(role="user", content=[TextBlock(text="Hello agent")])
        asst_msg = Message(role="assistant", content=[TextBlock(text="Hello human")])
        user_msg2 = Message(role="user", content=[TextBlock(text="How are you?")])

        log = [
            {"type": "session_start", "seq": 0, "timestamp": 1.0, "data": {}},
            _msg_record(RecordType.USER_MESSAGE, user_msg, seq=1),
            _msg_record(RecordType.ASSISTANT_MESSAGE, asst_msg, seq=2),
            _msg_record(RecordType.USER_MESSAGE, user_msg2, seq=3),
        ]
        store.set(f"event_log:{session_id}", log)

        # Simulate process restart: create a NEW harness with the same store
        harness = _make_harness(store)
        session = harness.resume(session_id)

        assert session is not None
        assert len(session.messages) == 3
        assert session.messages[0].role == "user"
        assert session.messages[0].text == "Hello agent"
        assert session.messages[1].role == "assistant"
        assert session.messages[1].text == "Hello human"
        assert session.messages[2].role == "user"
        assert session.messages[2].text == "How are you?"

    def test_resumed_session_retains_identity(self):
        """Req 4.4: Session identity survives resume."""
        store = InMemoryStore()
        session_id = "identity-test"

        msg = Message(role="user", content=[TextBlock(text="hi")])
        log = [_msg_record(RecordType.USER_MESSAGE, msg, seq=0)]
        store.set(f"event_log:{session_id}", log)

        harness = _make_harness(store)
        session = harness.resume(session_id)

        assert session is not None
        assert session.id == session_id

    def test_resumed_session_writer_seq_is_correct(self):
        """Writer continues from correct sequence number after resume."""
        store = InMemoryStore()
        session_id = "seq-test"

        msg = Message(role="user", content=[TextBlock(text="hi")])
        log = [
            {"type": "session_start", "seq": 0, "timestamp": 1.0, "data": {}},
            _msg_record(RecordType.USER_MESSAGE, msg, seq=1),
        ]
        store.set(f"event_log:{session_id}", log)

        harness = _make_harness(store)
        session = harness.resume(session_id)

        assert session is not None
        assert session._writer is not None
        assert session._writer._seq == 2


class TestPartialWriteRecovery:
    """Req 4.3: Detect and discard incomplete records on recovery."""

    def test_truncated_last_record_is_skipped(self):
        """Malformed trailing record (missing data field) is discarded."""
        store = InMemoryStore()
        session_id = "partial-write"

        good_msg = Message(role="user", content=[TextBlock(text="valid message")])
        log = [
            _msg_record(RecordType.USER_MESSAGE, good_msg, seq=0),
            # Truncated/malformed record — missing "data" field entirely
            {"type": "assistant_message", "seq": 1, "timestamp": 2.0},
        ]
        store.set(f"event_log:{session_id}", log)

        harness = _make_harness(store)
        session = harness.resume(session_id)

        assert session is not None
        # Only the valid first message should be recovered
        assert len(session.messages) == 1
        assert session.messages[0].text == "valid message"

    def test_record_with_incomplete_message_is_skipped(self):
        """Record with data dict but invalid message payload is skipped."""
        store = InMemoryStore()
        session_id = "incomplete-msg"

        good_msg = Message(role="user", content=[TextBlock(text="first")])
        asst_msg = Message(role="assistant", content=[TextBlock(text="second")])
        log = [
            _msg_record(RecordType.USER_MESSAGE, good_msg, seq=0),
            _msg_record(RecordType.ASSISTANT_MESSAGE, asst_msg, seq=1),
            # Partial write: has data but message is not a valid dict
            {"type": "user_message", "seq": 2, "timestamp": 3.0, "data": {"message": "not-a-dict"}},
        ]
        store.set(f"event_log:{session_id}", log)

        harness = _make_harness(store)
        session = harness.resume(session_id)

        assert session is not None
        # Valid prefix: first two messages recovered, malformed third skipped
        assert len(session.messages) == 2
        assert session.messages[0].text == "first"
        assert session.messages[1].text == "second"

    def test_none_record_in_log_is_skipped(self):
        """Completely corrupted entry (None) in the log is handled gracefully."""
        store = InMemoryStore()
        session_id = "null-record"

        good_msg = Message(role="user", content=[TextBlock(text="ok")])
        log = [
            _msg_record(RecordType.USER_MESSAGE, good_msg, seq=0),
            None,  # Simulates total corruption
        ]
        store.set(f"event_log:{session_id}", log)

        harness = _make_harness(store)
        session = harness.resume(session_id)

        assert session is not None
        assert len(session.messages) == 1
        assert session.messages[0].text == "ok"


class TestResumeNonExistent:
    """Req 4.5: resume() returns None for unknown sessions."""

    def test_resume_returns_none_for_missing_session(self):
        store = InMemoryStore()
        harness = _make_harness(store)

        result = harness.resume("nonexistent-session")

        assert result is None

    def test_resume_returns_none_when_durable_disabled(self):
        """When durable=False, resume always returns None."""
        store = InMemoryStore()
        agent = create_agent("test", model=MockModel(), instructions="hi")
        harness = Harness(agent, store=store, durable=False)

        result = harness.resume("any-session")

        assert result is None
