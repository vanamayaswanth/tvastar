"""Property tests: Compaction (Properties 23, 24, 25).

**Validates: Requirements 10.2, 10.4, 10.6**

Property 23: For any message list M with len(M) > keep_last and CompactionPolicy
with keep_last=K, after compaction the last K messages SHALL be identical to the
last K messages of the original list.

Property 24: For any message list where len < min_messages, should_compact()
SHALL return False and no compaction occurs.

Property 25: For any compaction failure (exception during summarization), the
Session SHALL continue with the original message list unchanged.

This test generates random message lists and policy parameters, runs compaction
logic, and verifies correctness invariants hold.
"""

from __future__ import annotations

import hypothesis.strategies as st
from hypothesis import given, settings

from tvastar.compaction import CompactionPolicy, compact_messages, should_compact
from tvastar.model.mock import MockModel
from tvastar.types import Message


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

st_message_text = st.text(
    alphabet=st.characters(categories=("L", "N", "P", "Z")),
    min_size=1,
    max_size=100,
)

st_role = st.sampled_from(["user", "assistant"])


@st.composite
def st_message_list(draw: st.DrawFn) -> list[Message]:
    """Generate a list of 5 to 20 messages with alternating user/assistant roles."""
    count = draw(st.integers(min_value=5, max_value=20))
    messages = []
    for i in range(count):
        role = "user" if i % 2 == 0 else "assistant"
        text = draw(st_message_text)
        messages.append(Message(role=role, content=text))
    return messages


st_keep_last = st.integers(min_value=1, max_value=5)


# ---------------------------------------------------------------------------
# Property Test: Compaction preserves tail
# ---------------------------------------------------------------------------


@given(
    messages=st_message_list(),
    keep_last=st_keep_last,
)
@settings(max_examples=100, deadline=None)
async def test_compaction_preserves_tail(
    messages: list[Message],
    keep_last: int,
):
    """Property 23: Compaction preserves tail.

    **Validates: Requirements 10.2**

    For any message list M with len(M) > keep_last, after compaction the last K
    messages are identical to the original (by role and text).
    """
    # Only test when we have more messages than keep_last (compaction has work to do)
    if len(messages) <= keep_last:
        return

    policy = CompactionPolicy(
        max_messages=1,  # Force compaction to always trigger
        keep_last=keep_last,
        min_messages=1,  # Allow compaction on short lists
    )

    model = MockModel()

    # Record the original tail before compaction
    original_tail = messages[-keep_last:]

    result = await compact_messages(messages, model, policy)

    # Verify: the last K messages of the result are identical to the original tail
    result_tail = result[-keep_last:]

    assert len(result_tail) == len(original_tail), (
        f"Expected {keep_last} tail messages, got {len(result_tail)}"
    )

    for i, (res_msg, orig_msg) in enumerate(zip(result_tail, original_tail)):
        assert res_msg.role == orig_msg.role, (
            f"Tail message {i}: role mismatch — got {res_msg.role!r}, "
            f"expected {orig_msg.role!r}"
        )
        assert res_msg.text == orig_msg.text, (
            f"Tail message {i}: text mismatch — got {res_msg.text!r}, "
            f"expected {orig_msg.text!r}"
        )


# ---------------------------------------------------------------------------
# Property Test: Compaction respects min_messages (Property 24)
# ---------------------------------------------------------------------------


@st.composite
def st_short_message_list_with_min(draw: st.DrawFn) -> tuple[list[Message], int]:
    """Generate a message list and a min_messages value where len < min_messages.

    This ensures we always test the case where compaction should NOT fire.
    """
    # Generate a message list of length 1 to 19
    count = draw(st.integers(min_value=1, max_value=19))
    messages = []
    for i in range(count):
        role = "user" if i % 2 == 0 else "assistant"
        text = draw(st_message_text)
        messages.append(Message(role=role, content=text))

    # min_messages is strictly greater than len(messages)
    min_messages = draw(st.integers(min_value=count + 1, max_value=count + 50))
    return messages, min_messages


@given(data=st_short_message_list_with_min())
@settings(max_examples=100, deadline=None)
def test_compaction_respects_min_messages(
    data: tuple[list[Message], int],
):
    """Property 24: Compaction respects min_messages.

    **Validates: Requirements 10.4**

    For any message list where len < min_messages, should_compact() returns False.
    No compaction should occur regardless of other policy thresholds.
    """
    messages, min_messages = data

    # Create a policy that would otherwise trigger compaction (low max_messages)
    # but min_messages is set higher than the message count
    policy = CompactionPolicy(
        max_messages=1,  # Would normally trigger compaction
        keep_last=1,
        min_messages=min_messages,
    )

    assert len(messages) < policy.min_messages, (
        f"Test invariant violated: len(messages)={len(messages)} "
        f"should be < min_messages={min_messages}"
    )

    result = should_compact(messages, policy)

    assert result is False, (
        f"should_compact() returned True when len(messages)={len(messages)} < "
        f"min_messages={min_messages}. Compaction should NOT fire."
    )


# ---------------------------------------------------------------------------
# Property Test: Compaction failure does not break run (Property 25)
# ---------------------------------------------------------------------------


class _FailingModel:
    """A model that always raises on generate, simulating a summarization failure."""

    name = "failing-model"
    system = "failing"

    async def generate(self, messages, **kwargs):
        raise RuntimeError("Simulated model failure during summarization")


@given(
    messages=st_message_list(),
    keep_last=st_keep_last,
)
@settings(max_examples=100, deadline=None)
async def test_compaction_failure_returns_original_messages(
    messages: list[Message],
    keep_last: int,
):
    """Property 25: Compaction failure does not break run.

    **Validates: Requirements 10.6**

    For any compaction failure (exception during summarization), compact_messages
    returns the original message list unchanged (same object identity). The session
    continues as if compaction never happened.
    """
    # Only meaningful when there are enough messages to compact
    if len(messages) <= keep_last:
        return

    policy = CompactionPolicy(
        max_messages=1,  # Force compaction to trigger
        keep_last=keep_last,
        min_messages=1,
    )

    failing_model = _FailingModel()

    # compact_messages should catch the exception and return the original list
    result = await compact_messages(messages, failing_model, policy)

    # Verify: the result IS the original messages object (identity check)
    assert result is messages, (
        "compact_messages should return the original messages object (same identity) "
        "when the model raises during summarization"
    )

    # Verify: content is unchanged (belt-and-suspenders)
    assert len(result) == len(messages)
    for i, (res_msg, orig_msg) in enumerate(zip(result, messages)):
        assert res_msg.role == orig_msg.role, f"Message {i} role changed"
        assert res_msg.text == orig_msg.text, f"Message {i} text changed"
