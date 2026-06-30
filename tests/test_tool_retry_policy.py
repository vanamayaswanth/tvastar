"""Unit tests for ToolRetryPolicy.

Tests:
- Retry up to max_attempts on tool failure
- Retryable predicate filtering
- Tool-level policy overrides harness-wide default
- Final exception returned as ToolResultBlock with is_error=True

Requirements: 23.1, 23.4, 23.5, 23.6
"""

from __future__ import annotations

import pytest

from tvastar import Harness, create_agent
from tvastar.errors import ToolError, ToolNotFound
from tvastar.model import MockModel
from tvastar.tools import ToolRetryPolicy, tool
from tvastar.tools.base import Tool, ToolRegistry
from tvastar.types import ToolResultBlock, ToolUseBlock


# ── Helpers ───────────────────────────────────────────────────────────────────


def _counting_tool(call_counter: list, fail_times: int, exc_cls=RuntimeError):
    """Create a tool that fails `fail_times` then succeeds."""

    @tool
    async def flaky(x: str) -> str:
        """A flaky tool."""
        call_counter.append(1)
        if len(call_counter) <= fail_times:
            raise exc_cls(f"fail #{len(call_counter)}")
        return f"ok after {len(call_counter)} attempts"

    return flaky


# ── Requirement 23.1: Retry up to max_attempts on tool failure ────────────────


async def test_retry_succeeds_within_max_attempts():
    """Tool retries and eventually succeeds when failures < max_attempts."""
    calls: list = []
    t = _counting_tool(calls, fail_times=2)
    t.retry = ToolRetryPolicy(max_attempts=3, backoff_base=0.0, jitter=0.0)

    result = await t.invoke({"x": "test"})
    assert result == "ok after 3 attempts"
    assert len(calls) == 3


async def test_retry_exhausts_max_attempts():
    """Tool raises ToolError after exhausting all max_attempts."""
    calls: list = []
    t = _counting_tool(calls, fail_times=5)
    t.retry = ToolRetryPolicy(max_attempts=3, backoff_base=0.0, jitter=0.0)

    with pytest.raises(ToolError, match="failed after 3 attempts"):
        await t.invoke({"x": "test"})
    assert len(calls) == 3


async def test_no_retry_when_max_attempts_is_one():
    """With max_attempts=1, no retry occurs — failure raises immediately."""
    calls: list = []
    t = _counting_tool(calls, fail_times=1)
    t.retry = ToolRetryPolicy(max_attempts=1, backoff_base=0.0, jitter=0.0)

    with pytest.raises(ToolError, match="failed"):
        await t.invoke({"x": "test"})
    assert len(calls) == 1


async def test_retry_exact_max_attempts_boundary():
    """Tool succeeds on the very last allowed attempt."""
    calls: list = []
    t = _counting_tool(calls, fail_times=4)
    t.retry = ToolRetryPolicy(max_attempts=5, backoff_base=0.0, jitter=0.0)

    result = await t.invoke({"x": "test"})
    assert result == "ok after 5 attempts"
    assert len(calls) == 5


# ── Requirement 23.4: Retryable predicate filtering ──────────────────────────


async def test_retryable_predicate_allows_retry():
    """When retryable returns True, the tool is retried."""
    calls: list = []
    t = _counting_tool(calls, fail_times=1, exc_cls=ConnectionError)
    t.retry = ToolRetryPolicy(
        max_attempts=3,
        backoff_base=0.0,
        jitter=0.0,
        retryable=lambda e: isinstance(e, ConnectionError),
    )

    result = await t.invoke({"x": "test"})
    assert result == "ok after 2 attempts"
    assert len(calls) == 2


async def test_retryable_predicate_blocks_retry():
    """When retryable returns False, no retry — error raised immediately."""
    calls: list = []
    t = _counting_tool(calls, fail_times=3, exc_cls=ValueError)
    t.retry = ToolRetryPolicy(
        max_attempts=5,
        backoff_base=0.0,
        jitter=0.0,
        retryable=lambda e: isinstance(e, ConnectionError),  # only ConnectionError
    )

    with pytest.raises(ToolError, match="failed"):
        await t.invoke({"x": "test"})
    # Only one call — no retry for ValueError when predicate says no
    assert len(calls) == 1


async def test_default_retryable_skips_tool_not_found():
    """Default retryable does not retry ToolNotFound exceptions."""
    calls: list = []

    @tool
    async def raises_not_found(x: str) -> str:
        """Raises ToolNotFound."""
        calls.append(1)
        raise ToolNotFound("no such tool")

    raises_not_found.retry = ToolRetryPolicy(max_attempts=3, backoff_base=0.0, jitter=0.0)

    # ToolNotFound is a subclass of ToolError — the invoke catches it differently
    # The default retryable returns False for ToolNotFound, so it should not retry
    # But ToolError is caught and re-raised directly (line: except ToolError: raise)
    # So the ToolNotFound propagates immediately
    with pytest.raises(ToolNotFound):
        await raises_not_found.invoke({"x": "test"})
    assert len(calls) == 1


async def test_default_retryable_skips_type_error():
    """Default retryable does not retry TypeError (argument errors)."""
    calls: list = []

    @tool
    async def bad_args(x: str) -> str:
        """Raises TypeError."""
        calls.append(1)
        raise TypeError("bad argument type")

    bad_args.retry = ToolRetryPolicy(max_attempts=3, backoff_base=0.0, jitter=0.0)

    # TypeError triggers the specific handling in invoke: raises ToolError immediately
    with pytest.raises(ToolError, match="Invalid arguments"):
        await bad_args.invoke({"x": "test"})
    assert len(calls) == 1


# ── Requirement 23.5: Tool-level policy overrides harness-wide default ────────


async def test_tool_level_retry_overrides_default():
    """Tool-specific retry policy takes precedence over default_retry."""
    calls: list = []
    t = _counting_tool(calls, fail_times=2)
    # Tool-level: allows 3 attempts
    t.retry = ToolRetryPolicy(max_attempts=3, backoff_base=0.0, jitter=0.0)
    # Harness-wide default: only 1 attempt (would fail)
    harness_default = ToolRetryPolicy(max_attempts=1, backoff_base=0.0, jitter=0.0)

    result = await t.invoke({"x": "test"}, default_retry=harness_default)
    # Should succeed because tool-level policy (3 attempts) is used, not harness default (1)
    assert result == "ok after 3 attempts"
    assert len(calls) == 3


async def test_harness_default_used_when_no_tool_level():
    """When tool has no retry policy, default_retry is used."""
    calls: list = []
    t = _counting_tool(calls, fail_times=2)
    t.retry = None  # No tool-level policy
    harness_default = ToolRetryPolicy(max_attempts=3, backoff_base=0.0, jitter=0.0)

    result = await t.invoke({"x": "test"}, default_retry=harness_default)
    assert result == "ok after 3 attempts"
    assert len(calls) == 3


async def test_no_retry_when_both_none():
    """When neither tool-level nor default_retry is set, no retry occurs."""
    calls: list = []
    t = _counting_tool(calls, fail_times=1)
    t.retry = None

    with pytest.raises(ToolError, match="failed"):
        await t.invoke({"x": "test"}, default_retry=None)
    assert len(calls) == 1


# ── Requirement 23.6: Final exception as ToolResultBlock(is_error=True) ──────


async def test_final_error_as_tool_result_block_via_session():
    """After retries exhausted, session returns ToolResultBlock with is_error=True."""
    call_count = []

    @tool(retry=ToolRetryPolicy(max_attempts=2, backoff_base=0.0, jitter=0.0))
    async def always_fails(msg: str) -> str:
        """Always fails."""
        call_count.append(1)
        raise RuntimeError("network timeout")

    # Model asks to call the tool, then receives the error result and responds
    script = [
        ToolUseBlock(name="always_fails", input={"msg": "hello"}, id="tu_1"),
        "Got it, the tool failed.",
    ]
    agent = create_agent(
        "retry-test",
        model=MockModel(script),
        instructions="test",
        tools=[always_fails],
    )
    h = Harness(agent)
    r = await h.run("call always_fails")

    # Tool was called max_attempts times
    assert len(call_count) == 2
    # The run should still complete (model handles the error)
    assert r.text == "Got it, the tool failed."
    # Verify the tool result in messages is an error
    tool_result_msgs = [
        m for m in r.messages if m.role == "tool" or any(
            getattr(b, "is_error", False) for b in m.blocks
        )
    ]
    # Find the ToolResultBlock with is_error=True in the message history
    found_error_block = False
    for msg in r.messages:
        for block in msg.blocks:
            if isinstance(block, ToolResultBlock) and block.is_error:
                found_error_block = True
                assert "failed after 2 attempts" in block.content
    assert found_error_block, "Expected a ToolResultBlock with is_error=True in message history"


async def test_final_error_harness_wide_retry_as_tool_result_block():
    """Harness-wide retry policy: exhausted retries produce ToolResultBlock(is_error=True)."""
    call_count = []

    @tool
    async def flaky_api(url: str) -> str:
        """A flaky API call."""
        call_count.append(1)
        raise ConnectionError("server unreachable")

    script = [
        ToolUseBlock(name="flaky_api", input={"url": "https://api.example.com"}, id="tu_2"),
        "The API is down.",
    ]
    agent = create_agent(
        "harness-retry-test",
        model=MockModel(script),
        instructions="test",
        tools=[flaky_api],
        tool_retry=ToolRetryPolicy(max_attempts=3, backoff_base=0.0, jitter=0.0),
    )
    h = Harness(agent)
    r = await h.run("call flaky_api")

    assert len(call_count) == 3
    assert r.text == "The API is down."
    # Find the ToolResultBlock with is_error=True
    found_error_block = False
    for msg in r.messages:
        for block in msg.blocks:
            if isinstance(block, ToolResultBlock) and block.is_error:
                found_error_block = True
                assert "failed after 3 attempts" in block.content
    assert found_error_block
