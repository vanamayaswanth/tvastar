"""Unit tests for the conversation reducer."""

from tvastar.conversation.reducer import reduce
from tvastar.durable import message_to_dict
from tvastar.types import Message, TextBlock, ToolUseBlock, ToolResultBlock


def _msg_record(rtype: str, message: Message, seq: int = 0) -> dict:
    """Helper: build a serialized record dict with a message payload."""
    return {
        "type": rtype,
        "seq": seq,
        "timestamp": 1700000000.0 + seq,
        "data": {"message": message_to_dict(message)},
    }


def test_empty_log():
    assert reduce([]) == []


def test_user_message():
    msg = Message(role="user", content=[TextBlock(text="hello")])
    log = [_msg_record("user_message", msg)]
    result = reduce(log)
    assert len(result) == 1
    assert result[0].role == "user"
    assert result[0].text == "hello"


def test_assistant_message():
    msg = Message(role="assistant", content=[TextBlock(text="hi there")])
    log = [_msg_record("assistant_message", msg)]
    result = reduce(log)
    assert len(result) == 1
    assert result[0].role == "assistant"
    assert result[0].text == "hi there"


def test_tool_use_and_result():
    tool_use_msg = Message(
        role="assistant",
        content=[ToolUseBlock(name="search", input={"q": "test"}, id="call_abc")],
    )
    tool_result_msg = Message(
        role="tool",
        content=[ToolResultBlock(tool_use_id="call_abc", content="found it")],
    )
    log = [
        _msg_record("tool_use", tool_use_msg, seq=0),
        _msg_record("tool_result", tool_result_msg, seq=1),
    ]
    result = reduce(log)
    assert len(result) == 2
    assert result[0].tool_uses[0].name == "search"
    assert result[1].blocks[0].content == "found it"


def test_skips_error_records():
    msg = Message(role="user", content=[TextBlock(text="before error")])
    log = [
        _msg_record("user_message", msg, seq=0),
        {
            "type": "error",
            "seq": 1,
            "timestamp": 1700000001.0,
            "data": {"error_class": "ModelError", "message": "rate limit"},
        },
    ]
    result = reduce(log)
    assert len(result) == 1
    assert result[0].text == "before error"


def test_skips_unknown_record_types():
    msg = Message(role="user", content=[TextBlock(text="hello")])
    log = [
        {"type": "future_type_v2", "seq": 0, "timestamp": 1.0, "data": {}},
        _msg_record("user_message", msg, seq=1),
        {"type": "another_unknown", "seq": 2, "timestamp": 2.0, "data": {}},
    ]
    result = reduce(log)
    assert len(result) == 1
    assert result[0].text == "hello"


def test_skips_session_start_and_end():
    msg = Message(role="user", content=[TextBlock(text="hi")])
    log = [
        {"type": "session_start", "seq": 0, "timestamp": 1.0, "data": {}},
        _msg_record("user_message", msg, seq=1),
        {"type": "session_end", "seq": 2, "timestamp": 2.0, "data": {}},
    ]
    result = reduce(log)
    assert len(result) == 1


def test_skips_malformed_trailing_records():
    """Partial write recovery: malformed records at end are discarded."""
    msg = Message(role="user", content=[TextBlock(text="good")])
    log = [
        _msg_record("user_message", msg, seq=0),
        {"type": "user_message", "seq": 1},  # missing data key
        None,  # completely malformed
        {"type": "user_message"},  # missing data.message
    ]
    result = reduce(log)
    assert len(result) == 1
    assert result[0].text == "good"


def test_skips_record_with_missing_message_in_data():
    """Record has data dict but no message key — skip it."""
    log = [
        {"type": "user_message", "seq": 0, "timestamp": 1.0, "data": {"other": "stuff"}},
    ]
    result = reduce(log)
    assert len(result) == 0


def test_does_not_mutate_input():
    """Reducer is pure — input log unchanged after call."""
    msg = Message(role="user", content=[TextBlock(text="hi")])
    log = [_msg_record("user_message", msg)]
    import copy

    log_copy = copy.deepcopy(log)
    reduce(log)
    assert log == log_copy


def test_multi_message_sequence():
    """Full conversation sequence produces correct ordered messages."""
    msgs = [
        ("user_message", Message(role="user", content=[TextBlock(text="q1")])),
        ("assistant_message", Message(role="assistant", content=[TextBlock(text="a1")])),
        ("user_message", Message(role="user", content=[TextBlock(text="q2")])),
        ("assistant_message", Message(role="assistant", content=[TextBlock(text="a2")])),
    ]
    log = [_msg_record(rtype, m, seq=i) for i, (rtype, m) in enumerate(msgs)]
    result = reduce(log)
    assert len(result) == 4
    assert [r.text for r in result] == ["q1", "a1", "q2", "a2"]
