"""Tests for the adapt_trajectory function in the silent failure benchmark."""

from tvastar.bench.silent_failure import adapt_trajectory, RawTrajectory
from tvastar.detect.base import RunContext
from tvastar.types import TextBlock, ToolResultBlock, ToolUseBlock


def _make_raw(messages, **kwargs):
    """Helper to create a RawTrajectory with defaults."""
    defaults = {"id": "test-001", "model": "GPT-5.2", "domain": "airline", "reward": 0}
    defaults.update(kwargs)
    return RawTrajectory(messages=messages, **defaults)


class TestAdaptTrajectoryBasic:
    """Basic message mapping tests."""

    def test_returns_run_context(self):
        raw = _make_raw([{"role": "assistant", "content": "Hello"}])
        result = adapt_trajectory(raw)
        assert isinstance(result, RunContext)

    def test_user_message_preserved(self):
        raw = _make_raw(
            [
                {"role": "user", "content": "Help me"},
                {"role": "assistant", "content": "Sure"},
            ]
        )
        ctx = adapt_trajectory(raw)
        assert ctx.messages[0].role == "user"
        assert ctx.messages[0].text == "Help me"

    def test_system_message_preserved(self):
        raw = _make_raw(
            [
                {"role": "system", "content": "You are helpful"},
                {"role": "assistant", "content": "Hi"},
            ]
        )
        ctx = adapt_trajectory(raw)
        assert ctx.messages[0].role == "system"
        assert ctx.messages[0].text == "You are helpful"

    def test_assistant_text_content(self):
        raw = _make_raw([{"role": "assistant", "content": "I'll help you"}])
        ctx = adapt_trajectory(raw)
        msg = ctx.messages[0]
        assert msg.role == "assistant"
        blocks = msg.blocks
        assert len(blocks) == 1
        assert isinstance(blocks[0], TextBlock)
        assert blocks[0].text == "I'll help you"


class TestAdaptTrajectoryToolCalls:
    """Tool call conversion tests."""

    def test_tool_call_produces_tool_use_block(self):
        raw = _make_raw(
            [
                {
                    "role": "assistant",
                    "content": "Looking up.",
                    "tool_calls": [
                        {
                            "id": "call_abc",
                            "function": {"name": "lookup", "arguments": '{"key": "val"}'},
                        }
                    ],
                },
            ]
        )
        ctx = adapt_trajectory(raw)
        blocks = ctx.messages[0].blocks
        # TextBlock + ToolUseBlock
        assert len(blocks) == 2
        assert isinstance(blocks[0], TextBlock)
        assert isinstance(blocks[1], ToolUseBlock)

    def test_tool_use_block_fields(self):
        raw = _make_raw(
            [
                {
                    "role": "assistant",
                    "content": "Checking.",
                    "tool_calls": [
                        {
                            "id": "call_xyz",
                            "function": {"name": "get_order", "arguments": '{"order_id": 42}'},
                        }
                    ],
                },
            ]
        )
        ctx = adapt_trajectory(raw)
        tool_block = [b for b in ctx.messages[0].blocks if isinstance(b, ToolUseBlock)][0]
        assert tool_block.name == "get_order"
        assert tool_block.input == {"order_id": 42}
        assert tool_block.id == "call_xyz"

    def test_multiple_tool_calls_in_one_message(self):
        raw = _make_raw(
            [
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "function": {"name": "search_flights", "arguments": '{"dest": "NYC"}'},
                        },
                        {
                            "id": "call_2",
                            "function": {"name": "search_hotels", "arguments": '{"city": "NYC"}'},
                        },
                    ],
                },
            ]
        )
        ctx = adapt_trajectory(raw)
        tool_blocks = [b for b in ctx.messages[0].blocks if isinstance(b, ToolUseBlock)]
        assert len(tool_blocks) == 2
        assert tool_blocks[0].name == "search_flights"
        assert tool_blocks[1].name == "search_hotels"

    def test_invalid_json_arguments_wrapped(self):
        raw = _make_raw(
            [
                {
                    "role": "assistant",
                    "content": "Call.",
                    "tool_calls": [
                        {
                            "id": "call_bad",
                            "function": {"name": "foo", "arguments": "not valid json"},
                        }
                    ],
                },
            ]
        )
        ctx = adapt_trajectory(raw)
        tool_block = [b for b in ctx.messages[0].blocks if isinstance(b, ToolUseBlock)][0]
        assert tool_block.input == {"raw": "not valid json"}


class TestAdaptTrajectoryToolResults:
    """Tool result conversion tests."""

    def test_tool_message_produces_tool_result_block(self):
        raw = _make_raw(
            [
                {
                    "role": "assistant",
                    "content": "Checking.",
                    "tool_calls": [
                        {"id": "call_abc", "function": {"name": "lookup", "arguments": "{}"}}
                    ],
                },
                {"role": "tool", "tool_call_id": "call_abc", "content": "found it"},
                {"role": "assistant", "content": "Done."},
            ]
        )
        ctx = adapt_trajectory(raw)
        tool_msg = ctx.messages[1]
        assert tool_msg.role == "tool"
        blocks = tool_msg.blocks
        assert len(blocks) == 1
        assert isinstance(blocks[0], ToolResultBlock)

    def test_tool_result_linked_by_id(self):
        raw = _make_raw(
            [
                {
                    "role": "assistant",
                    "content": "Let me check.",
                    "tool_calls": [
                        {"id": "call_link", "function": {"name": "api", "arguments": "{}"}}
                    ],
                },
                {"role": "tool", "tool_call_id": "call_link", "content": "response data"},
                {"role": "assistant", "content": "Got it."},
            ]
        )
        ctx = adapt_trajectory(raw)
        result_block = ctx.messages[1].blocks[0]
        assert isinstance(result_block, ToolResultBlock)
        assert result_block.tool_use_id == "call_link"
        assert result_block.content == "response data"


class TestAdaptTrajectoryRegistry:
    """ToolRegistry construction tests."""

    def test_registry_contains_all_observed_tools(self):
        raw = _make_raw(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"id": "c1", "function": {"name": "tool_a", "arguments": "{}"}},
                        {"id": "c2", "function": {"name": "tool_b", "arguments": "{}"}},
                    ],
                },
                {"role": "tool", "tool_call_id": "c1", "content": "ok"},
                {"role": "tool", "tool_call_id": "c2", "content": "ok"},
                {"role": "assistant", "content": "Done"},
            ]
        )
        ctx = adapt_trajectory(raw)
        assert "tool_a" in ctx.tools
        assert "tool_b" in ctx.tools
        assert len(ctx.tools) == 2

    def test_registry_empty_when_no_tool_calls(self):
        raw = _make_raw(
            [
                {"role": "user", "content": "Hi"},
                {"role": "assistant", "content": "Hello!"},
            ]
        )
        ctx = adapt_trajectory(raw)
        assert len(ctx.tools) == 0


class TestAdaptTrajectoryStopReason:
    """Stop reason determination tests."""

    def test_end_turn_when_last_message_is_assistant(self):
        raw = _make_raw(
            [
                {"role": "user", "content": "Help"},
                {"role": "assistant", "content": "Done!"},
            ]
        )
        ctx = adapt_trajectory(raw)
        assert ctx.stopped == "end_turn"

    def test_max_steps_when_last_message_is_tool(self):
        raw = _make_raw(
            [
                {
                    "role": "assistant",
                    "content": "Calling.",
                    "tool_calls": [{"id": "c1", "function": {"name": "api", "arguments": "{}"}}],
                },
                {"role": "tool", "tool_call_id": "c1", "content": "result"},
            ]
        )
        ctx = adapt_trajectory(raw)
        assert ctx.stopped == "max_steps"

    def test_max_steps_when_last_message_is_user(self):
        raw = _make_raw(
            [
                {"role": "user", "content": "Hello"},
            ]
        )
        ctx = adapt_trajectory(raw)
        assert ctx.stopped == "max_steps"


class TestAdaptTrajectoryFinalText:
    """Final text extraction tests."""

    def test_final_text_from_last_assistant(self):
        raw = _make_raw(
            [
                {"role": "user", "content": "Help"},
                {"role": "assistant", "content": "First response"},
                {"role": "user", "content": "More"},
                {"role": "assistant", "content": "Final response"},
            ]
        )
        ctx = adapt_trajectory(raw)
        assert ctx.final_text == "Final response"

    def test_final_text_empty_when_no_assistant(self):
        raw = _make_raw(
            [
                {"role": "user", "content": "Hello"},
            ]
        )
        ctx = adapt_trajectory(raw)
        assert ctx.final_text == ""

    def test_import_cleanly(self):
        """Verify the function can be imported as specified in requirements."""
        from tvastar.bench.silent_failure import adapt_trajectory as fn

        assert callable(fn)
