"""Unit tests for CompactionPolicy — context compaction.

Tests verify:
  - Compaction triggers when message count exceeds max_messages
  - keep_last messages are preserved unchanged after compaction
  - min_messages threshold prevents compaction
  - Force-compact on context overflow and single retry
  - 30-second cooldown between forced compaction attempts

Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.7
"""

import time
from unittest.mock import patch

import pytest

from tvastar import Harness, create_agent, default_toolset
from tvastar.compaction import (
    CompactionPolicy,
    compact_messages,
    compact_session,
    should_compact,
)
from tvastar.model import MockModel
from tvastar.types import Message, TextBlock


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_messages(n: int) -> list[Message]:
    """Create n alternating user/assistant messages."""
    msgs = []
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        msgs.append(Message(role, [TextBlock(text=f"message-{i}")]))
    return msgs


# ── Requirement 10.1: Compaction triggers at threshold ────────────────────────


async def test_should_compact_true_when_exceeds_max_messages():
    """should_compact returns True when len(messages) > max_messages."""
    policy = CompactionPolicy(max_messages=5, min_messages=3, keep_last=2)
    msgs = _make_messages(6)  # 6 > 5
    assert should_compact(msgs, policy) is True


async def test_should_compact_false_when_at_or_below_max_messages():
    """should_compact returns False when len(messages) <= max_messages."""
    policy = CompactionPolicy(max_messages=10, min_messages=3, keep_last=2)
    msgs = _make_messages(10)  # 10 == 10, not exceeded
    assert should_compact(msgs, policy) is False


async def test_should_compact_false_when_max_messages_disabled():
    """should_compact returns False for message count when max_messages=0."""
    policy = CompactionPolicy(max_messages=0, max_tokens_estimate=0, min_messages=1)
    msgs = _make_messages(100)
    assert should_compact(msgs, policy) is False


async def test_compaction_triggers_via_compact_session():
    """compact_session actually compacts when threshold is met."""
    policy = CompactionPolicy(max_messages=4, keep_last=2, min_messages=2)
    spec = create_agent(
        "compact-trigger",
        model=MockModel(["Summary of earlier context."]),
        instructions="",
        compaction=policy,
    )
    h = Harness(spec)
    sess = h.session()
    await sess.start()
    # Seed 6 messages — exceeds max_messages=4
    sess.messages = _make_messages(6)
    result = await compact_session(sess)
    assert result is True
    # After compaction: notice + summary + keep_last=2 tail
    assert len(sess.messages) == 4  # 1 notice + 1 summary + 2 tail
    await sess.close()


# ── Requirement 10.2: keep_last messages preserved unchanged ──────────────────


async def test_keep_last_messages_preserved_after_compaction():
    """The last keep_last messages are identical after compaction."""
    policy = CompactionPolicy(max_messages=5, keep_last=3, min_messages=2)
    spec = create_agent(
        "keep-last",
        model=MockModel(["Summary text."]),
        instructions="",
        compaction=policy,
    )
    h = Harness(spec)
    sess = h.session()
    await sess.start()
    original_msgs = _make_messages(8)
    sess.messages = list(original_msgs)
    # Remember the last 3 messages before compaction
    expected_tail = original_msgs[-3:]

    result = await compact_session(sess, force=True)
    assert result is True

    # The last 3 messages should be identical to original tail
    actual_tail = sess.messages[-3:]
    for expected, actual in zip(expected_tail, actual_tail):
        assert expected.role == actual.role
        assert expected.text == actual.text
    await sess.close()


async def test_keep_last_content_unchanged_after_compaction():
    """Verifies that keep_last messages have their content preserved byte-for-byte."""
    policy = CompactionPolicy(max_messages=4, keep_last=2, min_messages=2)
    spec = create_agent(
        "preserve-content",
        model=MockModel(["Compacted summary."]),
        instructions="",
        compaction=policy,
    )
    h = Harness(spec)
    sess = h.session()
    await sess.start()
    messages = [
        Message("user", [TextBlock(text="early message 1")]),
        Message("assistant", [TextBlock(text="early reply 1")]),
        Message("user", [TextBlock(text="important recent user question")]),
        Message("assistant", [TextBlock(text="important recent answer")]),
        Message("user", [TextBlock(text="latest user input")]),
    ]
    sess.messages = list(messages)
    # keep_last=2 means last 2 messages preserved
    expected_tail = messages[-2:]

    await compact_session(sess, force=True)

    actual_tail = sess.messages[-2:]
    assert actual_tail[0].text == "important recent answer"
    assert actual_tail[1].text == "latest user input"
    await sess.close()


# ── Requirement 10.3: Earlier messages summarised into compact notice ─────────


async def test_earlier_messages_replaced_with_compact_notice():
    """Earlier messages are replaced by a compact notice + summary."""
    policy = CompactionPolicy(max_messages=4, keep_last=2, min_messages=2)
    spec = create_agent(
        "notice-test",
        model=MockModel(["This is the summary."]),
        instructions="",
        compaction=policy,
    )
    h = Harness(spec)
    sess = h.session()
    await sess.start()
    sess.messages = _make_messages(6)
    await compact_session(sess, force=True)

    # First message should be the compact notice
    assert "compacted" in sess.messages[0].text.lower() or "compact" in sess.messages[0].text.lower()
    assert sess.messages[0].role == "user"
    # Second message should be the summary from the model
    assert sess.messages[1].text == "This is the summary."
    assert sess.messages[1].role == "assistant"
    await sess.close()


# ── Requirement 10.4: min_messages prevents compaction ────────────────────────


async def test_should_compact_false_below_min_messages():
    """should_compact returns False when len(messages) < min_messages."""
    policy = CompactionPolicy(max_messages=5, min_messages=10, keep_last=2)
    msgs = _make_messages(6)  # 6 < 10 min_messages
    assert should_compact(msgs, policy) is False


async def test_compact_session_skips_when_below_min_messages():
    """compact_session returns False (no-op) when below min_messages."""
    policy = CompactionPolicy(max_messages=5, min_messages=20, keep_last=2)
    spec = create_agent(
        "min-msgs",
        model=MockModel(["Should not be called."]),
        instructions="",
        compaction=policy,
    )
    h = Harness(spec)
    sess = h.session()
    await sess.start()
    sess.messages = _make_messages(8)  # 8 < 20 min_messages
    result = await compact_session(sess)
    assert result is False
    # Messages unchanged
    assert len(sess.messages) == 8
    await sess.close()


async def test_min_messages_threshold_edge_case():
    """Exactly at min_messages boundary: len == min_messages allows compaction check."""
    policy = CompactionPolicy(max_messages=5, min_messages=6, keep_last=2)
    msgs = _make_messages(6)  # len == min_messages — NOT below
    # 6 is not < 6, so should_compact proceeds to check max_messages
    # 6 > 5, so should compact
    assert should_compact(msgs, policy) is True


# ── Requirement 10.5: Force-compact on context overflow and single retry ──────


async def test_overflow_triggers_force_compact_and_retries():
    """Context overflow error triggers compaction and a single retry."""
    overflow_error = RuntimeError("context_length_exceeded: prompt is too long")
    summary_model = MockModel(["Summary after overflow."])
    policy = CompactionPolicy(
        max_messages=2, min_messages=2, keep_last=1, summary_model=summary_model
    )
    # Main model: first call raises overflow, second call (after compaction) succeeds
    agent = create_agent(
        "overflow-compact",
        model=MockModel([overflow_error, "Success after compaction."]),
        instructions="test",
        tools=default_toolset(),
        compaction=policy,
    )
    h = Harness(agent)
    sess = h.session()
    async with sess:
        # Pre-populate history so compaction has content to summarise
        sess.messages += [Message("user", "old1"), Message("assistant", "old2")]
        r = await sess.prompt("new prompt")
    assert r.text == "Success after compaction."
    assert r.stopped == "end_turn"


async def test_overflow_retries_only_once():
    """After overflow + compaction + retry, a second overflow is not caught again."""
    overflow_error = RuntimeError("context_length_exceeded: prompt too long")
    summary_model = MockModel(["Summary."])
    policy = CompactionPolicy(
        max_messages=2, min_messages=2, keep_last=1, summary_model=summary_model
    )
    # Both calls raise overflow — the retry after compaction also fails
    agent = create_agent(
        "double-overflow",
        model=MockModel([overflow_error, overflow_error]),
        instructions="test",
        tools=default_toolset(),
        compaction=policy,
    )
    h = Harness(agent)
    sess = h.session()
    async with sess:
        sess.messages += [Message("user", "old1"), Message("assistant", "old2")]
        # The second overflow after retry propagates since we only retry once
        with pytest.raises(RuntimeError, match="context_length_exceeded"):
            await sess.prompt("trigger overflow")


async def test_overflow_without_compaction_policy_propagates():
    """Overflow without a CompactionPolicy re-raises immediately."""
    overflow_error = RuntimeError("context_length_exceeded: prompt too long")
    agent = create_agent(
        "no-compact-policy",
        model=MockModel([overflow_error]),
        instructions="test",
    )
    with pytest.raises(RuntimeError, match="context_length_exceeded"):
        await Harness(agent).run("hi")


# ── Requirement 10.7: 30-second cooldown between forced compaction attempts ──


async def test_cooldown_prevents_second_forced_compaction():
    """Within 30s of a forced compaction, a second overflow re-raises."""
    overflow_error = RuntimeError("context_length_exceeded: prompt too long")
    summary_model = MockModel(["Summary 1.", "Summary 2."])
    policy = CompactionPolicy(
        max_messages=2, min_messages=2, keep_last=1, summary_model=summary_model
    )
    # First call: overflow → compact → retry succeeds
    # Second call: overflow again, but cooldown blocks compaction → raises
    agent = create_agent(
        "cooldown-test",
        model=MockModel([overflow_error, "First success.", overflow_error]),
        instructions="test",
        tools=default_toolset(),
        compaction=policy,
    )
    h = Harness(agent)
    sess = h.session()
    async with sess:
        sess.messages += [Message("user", "old1"), Message("assistant", "old2")]
        # First prompt: overflow → compaction → retry succeeds
        r1 = await sess.prompt("first prompt")
        assert r1.text == "First success."
        # _last_compact_at is now set. Second prompt triggers overflow
        # within cooldown → re-raises without compaction
        with pytest.raises(RuntimeError, match="context_length_exceeded"):
            await sess.prompt("second prompt within cooldown")


async def test_cooldown_expires_allows_second_compaction():
    """After 30s cooldown expires, another forced compaction is allowed."""
    overflow_error = RuntimeError("context_length_exceeded: prompt too long")
    summary_model = MockModel(["Summary 1.", "Summary 2."])
    policy = CompactionPolicy(
        max_messages=2, min_messages=2, keep_last=1, summary_model=summary_model
    )
    # First call: overflow → compact → retry succeeds
    # After cooldown: overflow → compact again → retry succeeds
    agent = create_agent(
        "cooldown-expired",
        model=MockModel([overflow_error, "First success.", overflow_error, "Second success."]),
        instructions="test",
        tools=default_toolset(),
        compaction=policy,
    )
    h = Harness(agent)
    sess = h.session()
    async with sess:
        sess.messages += [Message("user", "old1"), Message("assistant", "old2")]
        r1 = await sess.prompt("first prompt")
        assert r1.text == "First success."

        # Simulate cooldown expiry by rolling back _last_compact_at
        sess._last_compact_at = sess._last_compact_at - 31.0

        # Re-seed messages for second compaction
        sess.messages += [Message("user", "more old"), Message("assistant", "more old reply")]
        r2 = await sess.prompt("second prompt after cooldown")
        assert r2.text == "Second success."


# ── Compact_messages unit tests ───────────────────────────────────────────────


async def test_compact_messages_returns_original_on_model_failure():
    """If the summariser model fails, original messages are returned unchanged."""

    class FailingModel(MockModel):
        async def generate(self, *args, **kwargs):
            raise RuntimeError("model crashed")

    policy = CompactionPolicy(keep_last=2, min_messages=2)
    msgs = _make_messages(6)
    result = await compact_messages(msgs, FailingModel([]), policy)
    # Should return original unchanged
    assert result is msgs


async def test_compact_messages_preserves_tail_on_success():
    """compact_messages keeps the last K messages from original."""
    policy = CompactionPolicy(keep_last=3, min_messages=2)
    model = MockModel(["The compacted summary."])
    msgs = _make_messages(8)
    expected_tail = msgs[-3:]

    result = await compact_messages(msgs, model, policy)

    # Result should end with the original tail
    actual_tail = result[-3:]
    for exp, act in zip(expected_tail, actual_tail):
        assert exp.role == act.role
        assert exp.text == act.text


async def test_compact_messages_no_op_when_messages_lte_keep_last():
    """compact_messages returns original when len(messages) <= keep_last."""
    policy = CompactionPolicy(keep_last=10, min_messages=2)
    model = MockModel(["Should not be called."])
    msgs = _make_messages(5)  # 5 <= 10
    result = await compact_messages(msgs, model, policy)
    assert result is msgs  # identity — no compaction
