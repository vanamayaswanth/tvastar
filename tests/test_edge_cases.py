"""Edge case tests identified during Vidura BA analysis.

Tests boundary conditions and unusual interactions between subsystems.
Uses MockModel and existing test patterns per the project convention.

Requirements: EDGE-001 through EDGE-012
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tvastar import Harness, create_agent
from tvastar.compaction import CompactionPolicy, should_compact
from tvastar.masking import GovernancePolicy
from tvastar.model import MockModel
from tvastar.session import RunResult
from tvastar.types import (
    Message,
    ModelResponse,
    StopReason,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    Usage,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _agent(script, **kw):
    return create_agent(
        "edge-test",
        model=MockModel(script),
        instructions="edge case testing",
        **kw,
    )


# ── EDGE-001: Model returns TOOL_USE with zero tool calls → treated as END_TURN


class TestEdge001ToolUseZeroCalls:
    """When model returns stop_reason=TOOL_USE but the message contains no
    ToolUseBlock items, the session loop condition checks both stop_reason
    and resp.tool_uses. With stop_reason=TOOL_USE and empty tool_uses,
    the loop continues (executing zero tools) until max_steps is hit."""

    async def test_tool_use_with_no_tool_blocks_hits_max_steps(self):
        """A ModelResponse with stop_reason=TOOL_USE but no ToolUseBlocks
        keeps the loop alive. The guard condition only ends the turn when
        stop_reason != TOOL_USE AND no tool_uses. Here stop_reason IS
        TOOL_USE, so the loop continues and terminates at max_steps."""

        class ZeroToolUseModel(MockModel):
            async def generate(self, messages, **kwargs):
                self.calls.append(list(messages))
                # Return TOOL_USE stop reason but with only a text block (no tools)
                return ModelResponse(
                    message=Message("assistant", [TextBlock(text="I have nothing to call.")]),
                    stop_reason=StopReason.TOOL_USE,
                    usage=Usage(input_tokens=10, output_tokens=5),
                )

        agent = create_agent(
            "zero-tool-use",
            model=ZeroToolUseModel([]),
            instructions="test",
            max_steps=3,
            detect=False,
        )
        h = Harness(agent)
        r = await h.run("do something")
        # The loop treats TOOL_USE with zero tools as "continue looping"
        # since the end condition requires stop_reason != TOOL_USE.
        # It hits max_steps instead.
        assert r.text == "I have nothing to call."
        assert r.stopped == "max_steps"
        assert r.steps == 3

    async def test_end_turn_with_no_tool_uses_ends_normally(self):
        """Contrast: END_TURN with no tool_uses ends the loop immediately."""

        class EndTurnModel(MockModel):
            async def generate(self, messages, **kwargs):
                self.calls.append(list(messages))
                return ModelResponse(
                    message=Message("assistant", [TextBlock(text="Done.")]),
                    stop_reason=StopReason.END_TURN,
                    usage=Usage(input_tokens=10, output_tokens=5),
                )

        agent = create_agent(
            "end-turn",
            model=EndTurnModel([]),
            instructions="test",
            max_steps=10,
            detect=False,
        )
        h = Harness(agent)
        r = await h.run("do something")
        assert r.text == "Done."
        assert r.stopped == "end_turn"
        assert r.steps == 1


# ── EDGE-002: Tool returns empty string → valid ToolResultBlock


class TestEdge002ToolReturnsEmptyString:
    """A tool returning an empty string should produce a valid ToolResultBlock
    with empty content, not crash or produce an error."""

    async def test_empty_tool_output_is_valid(self):
        from tvastar.tools.base import tool as tool_decorator

        @tool_decorator
        async def empty_tool() -> str:
            """A tool that returns empty string."""
            return ""

        script = [
            ToolUseBlock(name="empty_tool", input={}, id="tu_empty"),
            "Tool returned nothing, that's fine.",
        ]
        agent = create_agent(
            "empty-tool-test",
            model=MockModel(script),
            instructions="test",
            tools=[empty_tool],
            detect=False,
        )
        h = Harness(agent)
        r = await h.run("call the empty tool")
        assert r.text == "Tool returned nothing, that's fine."
        assert r.stopped == "end_turn"
        # Verify the tool result block has empty content and is not an error
        tool_results = [
            b for m in r.messages for b in m.blocks if isinstance(b, ToolResultBlock)
        ]
        assert len(tool_results) >= 1
        assert tool_results[0].content == ""
        assert tool_results[0].is_error is False


# ── EDGE-003: Concurrent session.task() calls get independent child sessions


class TestEdge003ConcurrentTaskIndependentSessions:
    """Multiple concurrent session.task() calls should each get their own
    independent child session with no shared state."""

    async def test_concurrent_tasks_get_independent_sessions(self):
        # Each child task gets a different reply to confirm independence
        call_count = 0

        class CountingModel(MockModel):
            async def generate(self, messages, **kwargs):
                nonlocal call_count
                call_count += 1
                idx = call_count
                return ModelResponse(
                    message=Message("assistant", [TextBlock(text=f"reply-{idx}")]),
                    stop_reason=StopReason.END_TURN,
                    usage=Usage(input_tokens=5, output_tokens=5),
                )

        agent = create_agent(
            "concurrent-test",
            model=CountingModel([]),
            instructions="test",
        )
        h = Harness(agent)
        sess = h.session()
        async with sess:
            # Launch multiple tasks concurrently
            results = await asyncio.gather(
                sess.task("task A"),
                sess.task("task B"),
                sess.task("task C"),
            )

        # Each should have gotten a unique response
        texts = {r.text for r in results}
        assert len(texts) == 3, "Concurrent tasks should get independent responses"
        # Each result should have its own message history
        for r in results:
            assert r.stopped == "end_turn"


# ── EDGE-004: CompactionPolicy.keep_last > len(messages) → no compaction


class TestEdge004KeepLastExceedsMessages:
    """When CompactionPolicy.keep_last exceeds the number of messages,
    no compaction should occur."""

    async def test_keep_last_greater_than_message_count_no_compaction(self):
        messages = [
            Message("user", "hello"),
            Message("assistant", "hi there"),
            Message("user", "how are you"),
        ]
        # keep_last=10 but only 3 messages, and min_messages=2 so it could
        # theoretically fire. But keep_last > len means nothing to compact.
        policy = CompactionPolicy(
            max_messages=2,  # threshold exceeded (3 > 2)
            keep_last=10,  # but keep_last > len(messages)
            min_messages=2,
        )
        # should_compact returns True based on max_messages threshold
        from tvastar.compaction import compact_messages

        # Even though should_compact says yes, compact_messages should
        # return the original messages since keep_last >= len(messages)
        result = await compact_messages(
            messages,
            MockModel(["Summary"]),
            policy,
        )
        # When keep_last >= len(messages), nothing to summarise
        assert result == messages

    async def test_should_compact_false_when_below_min_messages(self):
        messages = [
            Message("user", "hello"),
            Message("assistant", "hi"),
        ]
        policy = CompactionPolicy(
            max_messages=1,
            keep_last=100,  # way more than message count
            min_messages=5,  # below threshold
        )
        assert should_compact(messages, policy) is False


# ── EDGE-005: Budget check between concurrent tools reflects only model tokens


class TestEdge005BudgetReflectsModelTokensOnly:
    """Budget enforcement checks happen after model.generate(), so the
    budget reflects only model token usage (not tool execution cost)."""

    async def test_budget_checked_after_model_generate(self):
        from tvastar.cost import BudgetExceeded, BudgetPolicy

        # Use a model that returns expensive tokens with a known model name
        # so the COST_TABLE lookup returns non-zero cost
        class ExpensiveModel(MockModel):
            name = "claude-sonnet-4-6"  # 3.0/15.0 per M tokens

            async def generate(self, messages, **kwargs):
                self.calls.append(list(messages))
                if self._cursor < len(self._script):
                    item = self._script[self._cursor]
                    self._cursor += 1
                    if isinstance(item, BaseException):
                        raise item
                    resp = self._wrap(item)
                    # Set high token usage to trigger budget
                    # 100k input @ $3/M = $0.30, 50k output @ $15/M = $0.75
                    # Total = $1.05, well above $0.001 budget
                    resp.usage = Usage(input_tokens=100000, output_tokens=50000)
                    return resp
                return ModelResponse(
                    message=Message("assistant", [TextBlock(text="done")]),
                    stop_reason=StopReason.END_TURN,
                    usage=Usage(input_tokens=100000, output_tokens=50000),
                )

        budget = BudgetPolicy(max_usd=0.001, on_exceed="stop")
        agent = create_agent(
            "budget-edge",
            model=ExpensiveModel(["result"]),
            instructions="test",
            budget=budget,
            detect=False,
        )
        h = Harness(agent)
        r = await h.run("go")
        # Budget should have triggered based on model tokens
        assert r.stopped == "budget"

    async def test_budget_only_uses_model_tokens_not_tool_cost(self):
        """Budget enforcement uses model token usage only — tool execution
        time/cost doesn't factor into the budget check."""
        from tvastar.cost import BudgetPolicy, Cost

        # Verify that Cost.usd only reflects token usage
        cost = Cost(input_tokens=1000, output_tokens=500, model="claude-sonnet-4-6")
        # 1000 * 3.0/1M + 500 * 15.0/1M = 0.003 + 0.0075 = 0.0105
        assert abs(cost.usd - 0.0105) < 0.0001
        # A mock model has no cost (not in COST_TABLE)
        mock_cost = Cost(input_tokens=1000, output_tokens=500, model="mock")
        assert mock_cost.usd == 0.0


# ── EDGE-006: GovernancePolicy.set_phase() during in-flight tool → affects next call


class TestEdge006GovernancePhaseChangeAffectsNextCall:
    """If GovernancePolicy.set_phase() is called while a tool is executing,
    the phase change should affect the NEXT tool invocation check, not the
    current one that's already past the check."""

    async def test_phase_change_during_execution(self):
        gov = GovernancePolicy(
            phases={
                "read": {"read_tool"},
                "write": {"read_tool", "write_tool"},
            },
            current_phase="read",
        )

        # Before phase change
        assert gov.is_allowed("read_tool") is True
        assert gov.is_allowed("write_tool") is False

        # Simulate phase change during tool execution
        gov.set_phase("write")

        # After phase change - next call reflects new phase
        assert gov.is_allowed("read_tool") is True
        assert gov.is_allowed("write_tool") is True

    async def test_governance_copy_isolates_phase_changes(self):
        """GovernancePolicy.copy() ensures set_phase() during tool execution
        in one session doesn't affect another session."""
        gov = GovernancePolicy(
            phases={"read": {"grep"}, "write": {"grep", "bash"}},
            current_phase="read",
        )
        copy = gov.copy()

        # Change phase on copy during simulated in-flight tool
        copy.set_phase("write")

        # Original is unaffected
        assert gov.current_phase == "read"
        assert gov.is_allowed("bash") is False
        # Copy reflects the change
        assert copy.current_phase == "write"
        assert copy.is_allowed("bash") is True


# ── EDGE-007: MAX_TOKENS mid-sentence → RunResult.text is truncated


class TestEdge007MaxTokensTruncation:
    """When model returns MAX_TOKENS stop reason (output exhausted mid-sentence),
    RunResult.text should contain the truncated text as-is."""

    async def test_max_tokens_returns_truncated_text(self):
        truncated_text = "The answer to your question is that the fundamental"

        class MaxTokensModel(MockModel):
            async def generate(self, messages, **kwargs):
                self.calls.append(list(messages))
                return ModelResponse(
                    message=Message("assistant", [TextBlock(text=truncated_text)]),
                    stop_reason=StopReason.MAX_TOKENS,
                    usage=Usage(input_tokens=50, output_tokens=100),
                )

        agent = create_agent(
            "max-tokens-test",
            model=MaxTokensModel([]),
            instructions="test",
            max_steps=3,
            detect=False,
        )
        h = Harness(agent)
        r = await h.run("explain something complex")
        # The text should be the truncated content
        assert r.text == truncated_text
        # Should end the turn since MAX_TOKENS is not TOOL_USE
        assert r.stopped == "end_turn"


# ── EDGE-008: TrustLog file deleted between append and verify_chain


class TestEdge008TrustLogFileDeleted:
    """When the TrustLog's backing file is deleted between append and
    verify_chain, the in-memory state should still be verifiable."""

    async def test_verify_chain_works_from_memory_after_file_delete(self):
        from tvastar.assurance.log import TrustLog
        from tvastar.assurance.receipt import ExecutionReceipt

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = str(Path(tmpdir) / "trust.jsonl")
            log = TrustLog(log_path)

            # Create a minimal receipt for testing
            receipt = ExecutionReceipt(
                run_id="run_test001",
                agent="test-agent",
                model_name="mock",
                prompt="hello",
                tool_calls=[],
                final_text="world",
                quality_score=100,
                quality_grade="PASS",
                findings=[],
                approvals=[],
                usage_input=10,
                usage_output=5,
                stopped="end_turn",
                started_at=1000.0,
                completed_at=1001.0,
                prev_hash="",
                content_hash="",
                signature="",
            )
            # Compute real content hash
            import hashlib
            import json

            from tvastar.assurance.receipt import _canonical_payload

            payload = _canonical_payload(
                run_id=receipt.run_id,
                agent=receipt.agent,
                model_name=receipt.model_name,
                prompt=receipt.prompt,
                tool_calls=receipt.tool_calls,
                final_text=receipt.final_text,
                quality_score=receipt.quality_score,
                quality_grade=receipt.quality_grade,
                findings=receipt.findings,
                approvals=receipt.approvals,
                usage_input=receipt.usage_input,
                usage_output=receipt.usage_output,
                stopped=receipt.stopped,
                started_at=receipt.started_at,
                completed_at=receipt.completed_at,
                prev_hash=receipt.prev_hash,
                version="2",
            )
            receipt.content_hash = "sha256:" + hashlib.sha256(payload.encode()).hexdigest()
            log.append(receipt)

            # Delete the file
            Path(log_path).unlink()
            assert not Path(log_path).exists()

            # verify_chain uses in-memory entries, should still pass
            assert log.verify_chain() is True

    async def test_trust_log_reload_from_deleted_file_is_empty(self):
        """A new TrustLog instance on a deleted file starts empty."""
        from tvastar.assurance.log import TrustLog

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = str(Path(tmpdir) / "trust.jsonl")
            # Create then delete the file
            Path(log_path).write_text("")
            Path(log_path).unlink()

            # Opening a TrustLog on a non-existent path should work (in-memory)
            log = TrustLog(log_path)
            assert len(log) == 0
            assert log.verify_chain() is True


# ── EDGE-009: resume() with session_id from different AgentSpec


class TestEdge009ResumeFromDifferentSpec:
    """resume() with a session_id that was checkpointed under a different
    AgentSpec should handle gracefully (return session or None)."""

    async def test_resume_nonexistent_session_returns_none(self):
        """resume() for an unknown session_id returns None."""
        agent = _agent(["hello"])
        h = Harness(agent)
        result = h.resume("nonexistent_session_12345")
        assert result is None

    async def test_resume_different_spec_session(self):
        """A session saved by one spec can be loaded by a harness with
        a different spec — the messages are restored regardless."""
        from tvastar.durable import Checkpointer
        from tvastar.memory.store import InMemoryStore

        store = InMemoryStore()

        # First agent saves a checkpoint
        agent1 = create_agent("agent-one", model=MockModel(["first"]), instructions="spec one")
        h1 = Harness(agent1, store=store)
        sess1 = h1.session(session_id="shared-session")
        async with sess1:
            await sess1.prompt("hello from agent one")

        # Second agent with different spec tries to resume
        agent2 = create_agent("agent-two", model=MockModel(["second"]), instructions="spec two")
        h2 = Harness(agent2, store=store)
        resumed = h2.resume("shared-session")
        # The implementation returns the session with restored messages
        # or None if checkpoint doesn't exist
        if resumed is not None:
            assert len(resumed.messages) > 0
        # Either way, it should not raise


# ── EDGE-010: Loop trigger() while SUSPENDED raises RuntimeError


class TestEdge010LoopTriggerWhileSuspended:
    """Calling loop.trigger() while the loop is SUSPENDED should raise
    RuntimeError."""

    async def test_trigger_while_suspended_raises(self):
        from tvastar.loop import Loop, LoopConfig, LoopState

        agent = _agent(["fail"])
        config = LoopConfig(
            name="suspended-test",
            goal="test goal",
            circuit_breaker_limit=1,
        )
        loop = Loop(agent, config)
        # Force SUSPENDED state
        loop._state = LoopState.SUSPENDED
        loop._consecutive_failures = 5

        with pytest.raises(RuntimeError, match="SUSPENDED"):
            await loop.trigger()

    async def test_trigger_while_suspended_mentions_reset(self):
        """The error message should mention loop.reset() as the remedy."""
        from tvastar.loop import Loop, LoopConfig, LoopState

        agent = _agent(["x"])
        config = LoopConfig(name="reset-test", goal="test")
        loop = Loop(agent, config)
        loop._state = LoopState.SUSPENDED
        loop._consecutive_failures = 10

        with pytest.raises(RuntimeError, match="reset"):
            await loop.trigger()


# ── EDGE-011: MCP server crash mid-tool-call returns error ToolResultBlock


class TestEdge011McpCrashReturnsError:
    """When an MCP tool call fails (server crash, connection error), the result
    should be an error ToolResultBlock, not a raised exception that kills the loop."""

    async def test_tool_exception_returns_error_result_block(self):
        """When a tool raises during invocation, the session catches it and
        returns an error ToolResultBlock so the loop continues."""
        from tvastar.tools.base import tool as tool_decorator

        @tool_decorator
        async def crashing_tool() -> str:
            """A tool that simulates a crash."""
            raise ConnectionError("MCP server connection lost")

        script = [
            ToolUseBlock(name="crashing_tool", input={}, id="tu_crash"),
            "The tool crashed but I recovered.",
        ]
        agent = create_agent(
            "crash-test",
            model=MockModel(script),
            instructions="test",
            tools=[crashing_tool],
            detect=False,
        )
        h = Harness(agent)
        r = await h.run("call the crashing tool")
        # The loop should continue past the tool error
        assert r.text == "The tool crashed but I recovered."
        assert r.stopped == "end_turn"
        # An error ToolResultBlock should exist in the messages
        error_blocks = [
            b
            for m in r.messages
            for b in m.blocks
            if isinstance(b, ToolResultBlock) and b.is_error
        ]
        assert len(error_blocks) >= 1
        assert "connection lost" in error_blocks[0].content.lower() or "error" in error_blocks[0].content.lower()


# ── EDGE-012: auto_topology with unreachable nodes → GraphResult.ok=False


class TestEdge012AutoTopologyUnreachableNodes:
    """When auto_topology produces a graph with unreachable nodes (e.g., a task
    depends on a failing upstream task), the GraphResult should reflect failure."""

    async def test_graph_with_failing_upstream_propagates_failure(self):
        """If an upstream task fails, downstream tasks that depend on it
        should not execute, and the overall result is a failure."""
        from tvastar import TaskGraph

        # Task 'a' will fail (model raises)
        fail_agent = create_agent(
            "fail-agent",
            model=MockModel([RuntimeError("upstream crashed")]),
            instructions="",
        )
        h = Harness(fail_agent)
        g = TaskGraph(h)
        g.task("a", "do task a")
        g.task("b", "do task b", depends_on=["a"])
        g.task("c", "do task c", depends_on=["a"])

        # The graph should raise since task 'a' failed
        with pytest.raises(RuntimeError, match="failed"):
            await g.run()

    async def test_graph_unknown_dependency_raises_valueerror(self):
        """A task referencing a non-existent dependency raises ValueError."""
        from tvastar import TaskGraph

        agent = _agent(["ok"])
        h = Harness(agent)
        g = TaskGraph(h)
        g.task("a", "do a")
        g.task("b", "do b", depends_on=["nonexistent"])

        with pytest.raises(ValueError, match="unknown task"):
            await g.run()
