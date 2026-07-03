"""Tests for the context overflow → compact → retry recovery path.

Verifies:
  1. When a model generates a context-overflow exception on first call,
     the session triggers compaction and retries successfully.
  2. The cooldown mechanism prevents repeated compaction attempts within
     the configured window.

Requirements: 25.1, 25.2
"""

import pytest

from tvastar import Harness, create_agent, default_toolset
from tvastar.compaction import CompactionPolicy
from tvastar.model import MockModel
from tvastar.types import Message


# ── Helpers ───────────────────────────────────────────────────────────────────


def _overflow_error(msg: str = "context_length_exceeded") -> RuntimeError:
    """Create a RuntimeError that triggers overflow detection."""
    return RuntimeError(msg)


# ── Test 1: Overflow triggers compaction and retry succeeds ───────────────────


async def test_overflow_triggers_compaction_then_retry_succeeds():
    """When the model raises a context-overflow error on the first call,
    the session compacts context and retries. The retry succeeds.
    """
    overflow = _overflow_error("context_length_exceeded: prompt is too long")
    summary_model = MockModel(["Summarised earlier context."])
    policy = CompactionPolicy(
        max_messages=2,
        min_messages=2,
        keep_last=1,
        summary_model=summary_model,
    )

    # First generate() call raises overflow; second (after compaction) succeeds.
    main_model = MockModel([overflow, "Recovery successful."])
    agent = create_agent(
        "overflow-recovery",
        model=main_model,
        instructions="test agent",
        tools=default_toolset(),
        compaction=policy,
    )

    h = Harness(agent)
    sess = h.session()
    async with sess:
        # Pre-populate history so compaction has content to summarise
        sess.messages += [
            Message("user", "previous question"),
            Message("assistant", "previous answer"),
        ]
        result = await sess.prompt("trigger overflow")

    assert result.text == "Recovery successful."
    assert result.stopped == "end_turn"


async def test_overflow_compaction_uses_summary_model():
    """The compaction triggered by overflow uses the policy's summary_model."""
    overflow = _overflow_error("context_length_exceeded")
    summary_model = MockModel(["Custom summary text."])
    policy = CompactionPolicy(
        max_messages=2,
        min_messages=2,
        keep_last=1,
        summary_model=summary_model,
    )

    main_model = MockModel([overflow, "Post-compaction success."])
    agent = create_agent(
        "overflow-summary",
        model=main_model,
        instructions="test",
        tools=default_toolset(),
        compaction=policy,
    )

    h = Harness(agent)
    sess = h.session()
    async with sess:
        sess.messages += [
            Message("user", "old message 1"),
            Message("assistant", "old reply 1"),
        ]
        result = await sess.prompt("new prompt")

    assert result.text == "Post-compaction success."
    # Verify the summary model was called (cursor advanced)
    assert summary_model._cursor == 1


# ── Test 2: Cooldown prevents repeated compaction ─────────────────────────────


async def test_cooldown_prevents_repeated_compaction_within_window():
    """After a successful overflow recovery, a second overflow within the
    cooldown window raises the error instead of compacting again.
    """
    overflow = _overflow_error("context_length_exceeded: prompt too long")
    summary_model = MockModel(["Summary 1.", "Summary 2."])
    policy = CompactionPolicy(
        max_messages=2,
        min_messages=2,
        keep_last=1,
        summary_model=summary_model,
        cooldown=30.0,
    )

    # First call: overflow → compact → retry succeeds
    # Second call: overflow again (within cooldown) → should raise
    main_model = MockModel([overflow, "First success.", overflow])
    agent = create_agent(
        "cooldown-block",
        model=main_model,
        instructions="test",
        tools=default_toolset(),
        compaction=policy,
    )

    h = Harness(agent)
    sess = h.session()
    async with sess:
        sess.messages += [
            Message("user", "old1"),
            Message("assistant", "old2"),
        ]
        # First prompt: overflow → compaction → retry → success
        r1 = await sess.prompt("first prompt")
        assert r1.text == "First success."

        # Second prompt immediately (within cooldown) triggers overflow
        # Compaction is skipped due to cooldown → exception propagates
        with pytest.raises(RuntimeError, match="context_length_exceeded"):
            await sess.prompt("second prompt within cooldown")


async def test_cooldown_zero_allows_compaction_on_every_overflow():
    """When cooldown is 0.0, compaction is allowed on every overflow event."""
    overflow = _overflow_error("context_length_exceeded")
    summary_model = MockModel(["Summary 1.", "Summary 2."])
    policy = CompactionPolicy(
        max_messages=2,
        min_messages=2,
        keep_last=1,
        summary_model=summary_model,
        cooldown=0.0,
    )

    # Both prompts overflow; with cooldown=0 both should compact and retry
    main_model = MockModel([overflow, "First OK.", overflow, "Second OK."])
    agent = create_agent(
        "cooldown-zero",
        model=main_model,
        instructions="test",
        tools=default_toolset(),
        compaction=policy,
    )

    h = Harness(agent)
    sess = h.session()
    async with sess:
        sess.messages += [
            Message("user", "old1"),
            Message("assistant", "old2"),
        ]
        r1 = await sess.prompt("first")
        assert r1.text == "First OK."

        # Re-seed messages so second compaction has content
        sess.messages += [
            Message("user", "more old"),
            Message("assistant", "more old reply"),
        ]
        r2 = await sess.prompt("second")
        assert r2.text == "Second OK."


async def test_cooldown_expired_allows_second_compaction():
    """After the cooldown window expires, compaction fires again on overflow."""
    overflow = _overflow_error("context_length_exceeded: prompt too long")
    summary_model = MockModel(["Summary 1.", "Summary 2."])
    policy = CompactionPolicy(
        max_messages=2,
        min_messages=2,
        keep_last=1,
        summary_model=summary_model,
        cooldown=30.0,
    )

    # Both calls overflow; we manually expire the cooldown between them
    main_model = MockModel([overflow, "First success.", overflow, "Second success."])
    agent = create_agent(
        "cooldown-expired",
        model=main_model,
        instructions="test",
        tools=default_toolset(),
        compaction=policy,
    )

    h = Harness(agent)
    sess = h.session()
    async with sess:
        sess.messages += [
            Message("user", "old1"),
            Message("assistant", "old2"),
        ]
        r1 = await sess.prompt("first")
        assert r1.text == "First success."

        # Simulate cooldown expiry by rolling back _last_compact_at
        sess._last_compact_at = sess._last_compact_at - 31.0

        # Re-seed messages for second compaction
        sess.messages += [
            Message("user", "more content"),
            Message("assistant", "more reply"),
        ]
        r2 = await sess.prompt("second after cooldown")
        assert r2.text == "Second success."


async def test_overflow_without_compaction_policy_propagates():
    """Without a CompactionPolicy, overflow exceptions propagate immediately."""
    overflow = _overflow_error("context_length_exceeded: max tokens exceeded")
    main_model = MockModel([overflow])
    agent = create_agent(
        "no-policy",
        model=main_model,
        instructions="test",
    )

    with pytest.raises(RuntimeError, match="context_length_exceeded"):
        await Harness(agent).run("trigger overflow")
