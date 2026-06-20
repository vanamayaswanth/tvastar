"""Tests for tvastar.adapters — OpenAI, LangGraph, AgentCore wrappers."""

from __future__ import annotations

import pytest

from tvastar.detect import Finding, Severity
from tvastar.wrap import WrappedResult


# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------


def _make_openai_messages(text: str = "Done.", with_tool: bool = False) -> list[dict]:
    msgs = [{"role": "user", "content": "Fix the tests."}]
    if with_tool:
        msgs.append(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "bash", "arguments": '{"command": "pytest"}'},
                    }
                ],
            }
        )
        msgs.append({"role": "tool", "tool_call_id": "call_1", "content": "5 passed"})
    msgs.append({"role": "assistant", "content": text})
    return msgs


# ---------------------------------------------------------------------------
# adapters.openai
# ---------------------------------------------------------------------------


class TestScoreOpenAIMessages:
    def test_clean_run_passes(self):
        from tvastar.adapters.openai import score_openai_messages

        msgs = _make_openai_messages("All done. Tests pass.")
        r = score_openai_messages(msgs)
        assert isinstance(r, WrappedResult)
        assert r.text == "All done. Tests pass."
        assert r.quality.grade in ("PASS", "WARN")

    def test_extracts_final_assistant_text(self):
        from tvastar.adapters.openai import score_openai_messages

        msgs = _make_openai_messages("Final answer here.")
        r = score_openai_messages(msgs)
        assert r.text == "Final answer here."

    def test_tool_call_preserved_in_messages(self):
        from tvastar.adapters.openai import _convert_messages

        msgs = _make_openai_messages(with_tool=True)
        tvastar_msgs = _convert_messages(msgs)
        # Should have: user, assistant (tool_use block), user (tool_result), assistant (text)
        roles = [m.role for m in tvastar_msgs]
        assert "assistant" in roles
        assert "user" in roles

    def test_empty_text_triggers_finding(self):
        from tvastar.adapters.openai import score_openai_messages

        msgs = [{"role": "user", "content": "q"}, {"role": "assistant", "content": ""}]
        r = score_openai_messages(msgs)
        assert any(f.detector == "empty_answer" for f in r.findings)

    def test_error_stop_lowers_score(self):
        from tvastar.adapters.openai import score_openai_messages

        msgs = _make_openai_messages("partial answer")
        r = score_openai_messages(msgs, stopped="error")
        assert r.quality.score <= 50

    def test_raw_is_original_messages_list(self):
        from tvastar.adapters.openai import score_openai_messages

        msgs = _make_openai_messages("ok")
        r = score_openai_messages(msgs)
        assert r.raw is msgs

    def test_custom_detector_applied(self):
        from tvastar.adapters.openai import score_openai_messages

        def _always_warn(ctx):
            return [Finding("forced", Severity.WARNING, "test", {})]

        r = score_openai_messages(_make_openai_messages(), detectors=[_always_warn])
        assert any(f.detector == "forced" for f in r.findings)

    def test_system_messages_skipped(self):
        from tvastar.adapters.openai import _convert_messages

        msgs = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        converted = _convert_messages(msgs)
        # system message should not appear
        assert all(m.role != "system" for m in converted)

    def test_malformed_tool_call_arguments_handled(self):
        from tvastar.adapters.openai import score_openai_messages

        msgs = [
            {"role": "user", "content": "q"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "bash", "arguments": "NOT JSON"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "result"},
            {"role": "assistant", "content": "done"},
        ]
        r = score_openai_messages(msgs)
        assert r.text == "done"  # didn't crash

    def test_empty_messages_list_handled(self):
        from tvastar.adapters.openai import score_openai_messages

        r = score_openai_messages([])
        assert isinstance(r, WrappedResult)
        assert r.text == ""


class TestOpenAILoopWrapper:
    def test_sync_context_manager_scores_on_exit(self):
        from tvastar.adapters.openai import OpenAILoopWrapper

        with OpenAILoopWrapper() as loop:
            loop.messages.append({"role": "user", "content": "Fix it."})
            loop.messages.append({"role": "assistant", "content": "Fixed."})

        assert loop.result is not None
        assert loop.result.text == "Fixed."
        assert isinstance(loop.result.quality.score, int)

    @pytest.mark.asyncio
    async def test_async_context_manager_scores_on_exit(self):
        from tvastar.adapters.openai import OpenAILoopWrapper

        async with OpenAILoopWrapper() as loop:
            loop.messages.append({"role": "user", "content": "Go."})
            loop.messages.append({"role": "assistant", "content": "Done."})

        assert loop.result is not None
        assert loop.result.text == "Done."

    def test_exception_in_block_sets_error_stop(self):
        from tvastar.adapters.openai import OpenAILoopWrapper

        with pytest.raises(ValueError):
            with OpenAILoopWrapper() as loop:
                loop.messages.append({"role": "user", "content": "q"})
                raise ValueError("boom")
        # result is still populated (scored with stopped="error")
        assert loop.result is not None
        assert loop.result.quality.grade == "FAIL"

    def test_duration_recorded(self):
        from tvastar.adapters.openai import OpenAILoopWrapper

        with OpenAILoopWrapper() as loop:
            loop.messages.append({"role": "assistant", "content": "ok"})
        assert loop.result.duration >= 0


# ---------------------------------------------------------------------------
# adapters.langgraph
# ---------------------------------------------------------------------------


class _FakeLangChainAIMessage:
    """Minimal stand-in for langchain_core.messages.AIMessage."""

    def __init__(self, content: str, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


class _FakeLangChainHumanMessage:
    def __init__(self, content: str):
        self.content = content


class _FakeLangChainToolMessage:
    def __init__(self, content: str, tool_call_id: str = "c1"):
        self.content = content
        self.tool_call_id = tool_call_id
        self.is_error = False


class _FakeGraph:
    """Minimal LangGraph compiled graph stand-in."""

    def __init__(self, state_out: dict):
        self._state_out = state_out

    def invoke(self, _state: dict, **kwargs) -> dict:
        return self._state_out

    async def ainvoke(self, _state: dict, **kwargs) -> dict:
        return self._state_out


class TestLangGraphWrapper:
    def _wrap(self, state_out: dict):
        from tvastar.adapters.langgraph import LangGraphWrapper

        return LangGraphWrapper(_FakeGraph(state_out))

    def test_invoke_returns_wrapped_result(self):
        wrapped = self._wrap({"messages": [_FakeLangChainAIMessage("All done.")]})
        r = wrapped.invoke({})
        assert isinstance(r, WrappedResult)
        assert r.text == "All done."

    @pytest.mark.asyncio
    async def test_ainvoke_returns_wrapped_result(self):
        wrapped = self._wrap({"messages": [_FakeLangChainAIMessage("Async done.")]})
        r = await wrapped.ainvoke({})
        assert r.text == "Async done."

    def test_extracts_text_from_output_key(self):
        wrapped = self._wrap({"output": "Final answer from output key"})
        r = wrapped.invoke({})
        assert r.text == "Final answer from output key"

    def test_extracts_text_from_result_key(self):
        wrapped = self._wrap({"result": "from result"})
        r = wrapped.invoke({})
        assert r.text == "from result"

    def test_human_message_converted(self):
        from tvastar.adapters.langgraph import _default_extract_messages

        msgs = [_FakeLangChainHumanMessage("hello")]
        result = _default_extract_messages({"messages": msgs})
        assert result[0].role == "user"
        assert result[0].text == "hello"

    def test_tool_message_converted_to_tool_result(self):
        from tvastar.adapters.langgraph import _default_extract_messages

        msgs = [_FakeLangChainToolMessage("output", tool_call_id="c1")]
        result = _default_extract_messages({"messages": msgs})
        assert result[0].role == "user"
        from tvastar.types import ToolResultBlock

        blocks = result[0].blocks
        assert any(isinstance(b, ToolResultBlock) for b in blocks)

    def test_ai_message_with_tool_calls_converted(self):
        from tvastar.adapters.langgraph import _default_extract_messages

        tc = {"name": "bash", "args": {"command": "ls"}, "id": "c1"}
        msgs = [_FakeLangChainAIMessage("", tool_calls=[tc])]
        result = _default_extract_messages({"messages": msgs})
        from tvastar.types import ToolUseBlock

        assert any(isinstance(b, ToolUseBlock) for m in result for b in m.blocks)

    def test_custom_extract_text_used(self):
        from tvastar.adapters.langgraph import LangGraphWrapper

        graph = _FakeGraph({"my_key": "custom result"})
        wrapped = LangGraphWrapper(graph, extract_text=lambda s: s.get("my_key", ""))
        r = wrapped.invoke({})
        assert r.text == "custom result"

    @pytest.mark.asyncio
    async def test_graph_exception_returns_error_result(self):
        from tvastar.adapters.langgraph import LangGraphWrapper

        class _FailGraph:
            async def ainvoke(self, state, **kwargs):
                raise RuntimeError("graph broke")

        wrapped = LangGraphWrapper(_FailGraph())
        r = await wrapped.ainvoke({})
        assert r.quality.grade == "FAIL"
        assert "error" in r.text

    def test_raw_is_state_dict(self):
        state_out = {"output": "done", "other": 42}
        wrapped = self._wrap(state_out)
        r = wrapped.invoke({})
        assert r.raw is state_out

    def test_dict_messages_in_state(self):
        from tvastar.adapters.langgraph import _default_extract_messages

        msgs = [{"role": "assistant", "content": "from dict"}]
        result = _default_extract_messages({"messages": msgs})
        assert result[0].text == "from dict"

    def test_empty_state_handled(self):
        wrapped = self._wrap({})
        r = wrapped.invoke({})
        assert isinstance(r, WrappedResult)


# ---------------------------------------------------------------------------
# adapters.agentcore
# ---------------------------------------------------------------------------


def _fake_agentcore_response(text: str) -> dict:
    """Build a minimal Bedrock invoke_agent response dict."""
    encoded = text.encode("utf-8")
    return {
        "completion": [
            {"chunk": {"bytes": encoded}},
        ]
    }


class _FakeBedrockClient:
    def __init__(self, response: dict):
        self._response = response

    def invoke_agent(self, *, agentId, agentAliasId, sessionId, inputText, **kwargs):
        return self._response


class TestAgentCoreWrapper:
    def test_invoke_returns_wrapped_result(self):
        from tvastar.adapters.agentcore import AgentCoreWrapper

        client = _FakeBedrockClient(_fake_agentcore_response("Task complete."))
        wrapper = AgentCoreWrapper(client)
        r = wrapper.invoke(
            agent_id="A1",
            agent_alias_id="AL1",
            session_id="s1",
            input_text="Fix tests.",
        )
        assert isinstance(r, WrappedResult)
        assert r.text == "Task complete."

    def test_clean_response_scores_pass(self):
        from tvastar.adapters.agentcore import AgentCoreWrapper

        client = _FakeBedrockClient(_fake_agentcore_response("All tests passing. Done."))
        r = AgentCoreWrapper(client).invoke(
            agent_id="A1",
            agent_alias_id="AL1",
            session_id="s1",
            input_text="go",
        )
        assert r.quality.grade in ("PASS", "WARN")

    def test_boto3_error_returns_error_result(self):
        from tvastar.adapters.agentcore import AgentCoreWrapper

        class _ErrorClient:
            def invoke_agent(self, **kwargs):
                raise RuntimeError("ThrottlingException")

        r = AgentCoreWrapper(_ErrorClient()).invoke(
            agent_id="A1",
            agent_alias_id="AL1",
            session_id="s1",
            input_text="go",
        )
        assert r.quality.grade == "FAIL"
        assert "ThrottlingException" in r.text

    def test_custom_detector_applied(self):
        from tvastar.adapters.agentcore import AgentCoreWrapper

        def _always_error(ctx):
            return [Finding("forced", Severity.ERROR, "forced", {})]

        client = _FakeBedrockClient(_fake_agentcore_response("ok"))
        r = AgentCoreWrapper(client, detectors=[_always_error]).invoke(
            agent_id="A1",
            agent_alias_id="AL1",
            session_id="s1",
            input_text="go",
        )
        assert any(f.detector == "forced" for f in r.findings)

    def test_score_agentcore_response_post_hoc(self):
        from tvastar.adapters.agentcore import score_agentcore_response

        r = score_agentcore_response(_fake_agentcore_response("Done."), input_text="Fix tests.")
        assert r.text == "Done."
        assert isinstance(r.quality.score, int)

    def test_trace_event_tool_call_extracted(self):
        from tvastar.adapters.agentcore import _parse_response

        response = {
            "completion": [
                {
                    "trace": {
                        "orchestrationTrace": {
                            "invocationInput": {
                                "actionGroupInvocationInput": {
                                    "function": "bash",
                                    "parameters": {"command": "pytest"},
                                }
                            }
                        }
                    }
                },
                {"chunk": {"bytes": b"Tests passed."}},
            ]
        }
        text, messages, stopped = _parse_response(response, input_text="run tests")
        assert text == "Tests passed."
        from tvastar.types import ToolUseBlock

        tool_uses = [b for m in messages for b in m.blocks if isinstance(b, ToolUseBlock)]
        assert any(u.name == "bash" for u in tool_uses)

    def test_bytearray_chunk_decoded(self):
        from tvastar.adapters.agentcore import score_agentcore_response

        response = {"completion": [{"chunk": {"bytes": bytearray(b"hello")}}]}
        r = score_agentcore_response(response)
        assert r.text == "hello"

    def test_empty_response_handled(self):
        from tvastar.adapters.agentcore import score_agentcore_response

        r = score_agentcore_response({"completion": []})
        assert isinstance(r, WrappedResult)
        assert r.text == ""
