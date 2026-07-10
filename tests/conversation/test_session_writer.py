"""Tests for ConversationWriter integration in Session lifecycle (Task 6.1).

Validates:
- Writer is created on session start when durable=True
- SESSION_START appended on start, SESSION_END on close
- USER_MESSAGE, ASSISTANT_MESSAGE, TOOL_USE, TOOL_RESULT records appended
- Writer is None when durable=False
- Write failure sets last_checkpoint_error (degraded mode)
"""

import pytest

from tvastar.agent import create_agent
from tvastar.conversation.records import RecordType
from tvastar.conversation.writer import ConversationWriter, _EVENT_LOG_PREFIX
from tvastar.harness import Harness
from tvastar.memory.store import InMemoryStore
from tvastar.types import Message, ModelResponse, StopReason, ToolUseBlock, Usage


class ScriptedModel:
    """Model that returns a canned response sequence."""

    name = "scripted"
    system = ""

    def __init__(self, responses):
        self._responses = list(responses)
        self._idx = 0

    async def generate(self, messages, **kw):
        resp = self._responses[self._idx % len(self._responses)]
        self._idx += 1
        return resp


def _simple_response(text="Hello"):
    return ModelResponse(
        message=Message("assistant", text),
        usage=Usage(input_tokens=10, output_tokens=5),
        stop_reason=StopReason.END_TURN,
    )


def _tool_use_response():
    """Model response requesting a tool call."""
    use = ToolUseBlock(id="tu_1", name="test_tool", input={"x": 1})
    return ModelResponse(
        message=Message("assistant", [use]),
        usage=Usage(input_tokens=10, output_tokens=5),
        stop_reason=StopReason.TOOL_USE,
    )


class TestSessionWriterLifecycle:
    """Session._writer is created on start() and records lifecycle events."""

    @pytest.mark.asyncio
    async def test_writer_created_on_start_durable(self):
        model = ScriptedModel([_simple_response()])
        spec = create_agent("test", model=model, instructions="hi")
        h = Harness(spec, durable=True)
        s = h.session(name="dur-sess")
        assert s._writer is None
        await s.start()
        assert s._writer is not None
        assert isinstance(s._writer, ConversationWriter)
        await s.close()

    @pytest.mark.asyncio
    async def test_writer_none_when_not_durable(self):
        model = ScriptedModel([_simple_response()])
        spec = create_agent("test", model=model, instructions="hi")
        h = Harness(spec, durable=False)
        s = h.session(name="no-dur")
        await s.start()
        assert s._writer is None
        await s.close()

    @pytest.mark.asyncio
    async def test_session_start_appends_session_start_record(self):
        model = ScriptedModel([_simple_response()])
        spec = create_agent("test", model=model, instructions="hi")
        store = InMemoryStore()
        h = Harness(spec, store=store, durable=True)
        s = h.session(name="start-test")
        await s.start()
        key = f"{_EVENT_LOG_PREFIX}start-test"
        log = store.get(key)
        assert log is not None
        assert len(log) == 1
        assert log[0]["type"] == RecordType.SESSION_START.value
        await s.close()

    @pytest.mark.asyncio
    async def test_session_close_appends_session_end_record(self):
        model = ScriptedModel([_simple_response()])
        spec = create_agent("test", model=model, instructions="hi")
        store = InMemoryStore()
        h = Harness(spec, store=store, durable=True)
        s = h.session(name="end-test")
        await s.start()
        await s.close()
        key = f"{_EVENT_LOG_PREFIX}end-test"
        log = store.get(key)
        assert log[-1]["type"] == RecordType.SESSION_END.value

    @pytest.mark.asyncio
    async def test_prompt_records_user_and_assistant_messages(self):
        model = ScriptedModel([_simple_response("world")])
        spec = create_agent("test", model=model, instructions="hi")
        store = InMemoryStore()
        h = Harness(spec, store=store, durable=True)
        s = h.session(name="prompt-test")
        async with s:
            await s.prompt("hello")
        key = f"{_EVENT_LOG_PREFIX}prompt-test"
        log = store.get(key)
        types = [r["type"] for r in log]
        assert RecordType.SESSION_START.value in types
        assert RecordType.USER_MESSAGE.value in types
        assert RecordType.ASSISTANT_MESSAGE.value in types
        assert RecordType.SESSION_END.value in types

    @pytest.mark.asyncio
    async def test_tool_use_and_result_recorded(self):
        """When the model uses a tool, TOOL_USE and TOOL_RESULT records are appended."""
        from tvastar.tools.base import Tool

        async def fake_fn(x: int) -> str:
            """A test tool."""
            return f"result_{x}"

        tool = Tool(
            name="test_tool",
            fn=fake_fn,
            description="test",
            input_schema={"type": "object", "properties": {"x": {"type": "integer"}}},
        )
        responses = [_tool_use_response(), _simple_response("done")]
        model = ScriptedModel(responses)
        spec = create_agent("test", model=model, instructions="hi", tools=[tool])
        store = InMemoryStore()
        h = Harness(spec, store=store, durable=True)
        s = h.session(name="tool-test")
        async with s:
            await s.prompt("do something")
        key = f"{_EVENT_LOG_PREFIX}tool-test"
        log = store.get(key)
        types = [r["type"] for r in log]
        assert RecordType.TOOL_USE.value in types
        assert RecordType.TOOL_RESULT.value in types

    @pytest.mark.asyncio
    async def test_write_failure_sets_last_checkpoint_error(self):
        """On writer failure, session continues in degraded mode."""
        model = ScriptedModel([_simple_response("hi")])
        spec = create_agent("test", model=model, instructions="hi")

        class FailingStore:
            """Store that fails on set (simulating disk/network error)."""

            def get(self, key):
                return None

            def set(self, key, value):
                raise IOError("disk full")

            def delete(self, key):
                pass

            def keys(self, prefix=""):
                return []

        store = FailingStore()
        h = Harness(spec, store=store, durable=True)
        s = h.session(name="fail-test")
        await s.start()
        # The start() should NOT raise — degraded mode
        assert s.last_checkpoint_error is not None
        await s.close()
