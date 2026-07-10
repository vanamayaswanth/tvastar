"""Unit tests for tvastar.conversation.records."""

from tvastar.conversation import Record, RecordType, record_from_dict, record_to_dict
from tvastar.types import Message, TextBlock, ToolResultBlock, ToolUseBlock


def test_record_type_values():
    """All expected RecordType variants exist with correct string values."""
    assert RecordType.SESSION_START == "session_start"
    assert RecordType.USER_MESSAGE == "user_message"
    assert RecordType.ASSISTANT_MESSAGE == "assistant_message"
    assert RecordType.TOOL_USE == "tool_use"
    assert RecordType.TOOL_RESULT == "tool_result"
    assert RecordType.ERROR == "error"
    assert RecordType.SESSION_END == "session_end"


def test_round_trip_with_text_message():
    msg = Message(role="user", content=[TextBlock(text="Hello")])
    r = Record(type=RecordType.USER_MESSAGE, seq=0, timestamp=1700000000.0, data={"message": msg})
    d = record_to_dict(r)
    r2 = record_from_dict(d)
    assert r2.type == r.type
    assert r2.seq == r.seq
    assert r2.timestamp == r.timestamp
    assert r2.data["message"].role == "user"
    assert r2.data["message"].blocks[0].text == "Hello"


def test_round_trip_with_tool_use_message():
    msg = Message(
        role="assistant",
        content=[ToolUseBlock(name="grep", input={"q": "x"})],
    )
    r = Record(type=RecordType.TOOL_USE, seq=3, timestamp=1700000001.0, data={"message": msg})
    d = record_to_dict(r)
    r2 = record_from_dict(d)
    assert r2.data["message"].blocks[0].name == "grep"
    assert r2.data["message"].blocks[0].input == {"q": "x"}


def test_round_trip_with_tool_result_message():
    msg = Message(
        role="tool",
        content=[ToolResultBlock(tool_use_id="call_abc", content="done", is_error=False)],
    )
    r = Record(type=RecordType.TOOL_RESULT, seq=4, timestamp=1700000002.0, data={"message": msg})
    d = record_to_dict(r)
    r2 = record_from_dict(d)
    blk = r2.data["message"].blocks[0]
    assert blk.tool_use_id == "call_abc"
    assert blk.content == "done"
    assert blk.is_error is False


def test_round_trip_without_message():
    """Records like session_start/session_end carry plain data, no Message."""
    r = Record(
        type=RecordType.SESSION_START, seq=0, timestamp=1700000000.0, data={"session_id": "s1"}
    )
    d = record_to_dict(r)
    r2 = record_from_dict(d)
    assert r2.type == RecordType.SESSION_START
    assert r2.data == {"session_id": "s1"}


def test_round_trip_error_record_with_details():
    r = Record(
        type=RecordType.ERROR,
        seq=5,
        timestamp=1700000005.0,
        data={
            "error_class": "ModelError",
            "message": "Rate limit",
            "details": {
                "error_code": "rate_limit",
                "category": "transient",
                "context": {"retry_after": 30},
            },
        },
    )
    d = record_to_dict(r)
    r2 = record_from_dict(d)
    assert r2.data["details"]["context"]["retry_after"] == 30


def test_record_to_dict_does_not_mutate_original():
    msg = Message(role="user", content=[TextBlock(text="x")])
    r = Record(type=RecordType.USER_MESSAGE, seq=0, timestamp=0.0, data={"message": msg})
    record_to_dict(r)
    # Original data dict still holds the Message object, not a dict
    assert not isinstance(r.data["message"], dict)
