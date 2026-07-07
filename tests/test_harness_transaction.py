"""Tests for harness.transaction() message rollback behavior.

Validates:
- Requirement 27.1: transaction() rolls back session messages on failure
- Requirement 27.2: successful transactions preserve their messages
"""

import pytest

from tvastar import Harness, create_agent
from tvastar.model import MockModel
from tvastar.sandbox.virtual import VirtualSandbox
from tvastar.types import Message


async def test_transaction_rolls_back_messages_on_failure():
    """harness.transaction() rolls back session messages when the block raises.

    Validates: Requirement 27.1
    """
    sb = VirtualSandbox({"f.txt": "init"})
    model = MockModel(["first response", "second response"])
    agent = create_agent(
        "tx-msg-rollback",
        model=model,
        sandbox=lambda: sb,
        detect=False,
    )
    h = Harness(agent)

    async with h.session() as sess:
        # Add an initial message so we can verify rollback restores to this state
        await sess.prompt("setup")
        messages_before = list(sess.messages)
        assert len(messages_before) > 0  # should have user + assistant messages

        with pytest.raises(RuntimeError, match="fail"):
            async with h.transaction(sess) as s:
                # This prompt adds messages to the session
                await s.prompt("do something risky")
                # Verify messages grew during the transaction
                assert len(sess.messages) > len(messages_before)
                raise RuntimeError("fail")

        # After rollback, messages should be restored to pre-transaction state
        assert sess.messages == messages_before


async def test_transaction_preserves_messages_on_success():
    """harness.transaction() preserves session messages when the block succeeds.

    Validates: Requirement 27.2
    """
    sb = VirtualSandbox({"f.txt": "init"})
    model = MockModel(["first", "second"])
    agent = create_agent(
        "tx-msg-success",
        model=model,
        sandbox=lambda: sb,
        detect=False,
    )
    h = Harness(agent)

    async with h.session() as sess:
        await sess.prompt("setup")
        messages_before_tx = list(sess.messages)

        async with h.transaction(sess) as s:
            await s.prompt("add more content")

        # Messages should include everything added during the transaction
        assert len(sess.messages) > len(messages_before_tx)
        # The new messages should contain the user prompt from the transaction
        user_texts = [m.text for m in sess.messages if m.role == "user"]
        assert "add more content" in user_texts


async def test_transaction_rollback_restores_exact_message_state():
    """Messages after rollback are identical (by value) to pre-transaction state.

    Validates: Requirement 27.1
    """
    sb = VirtualSandbox({})
    model = MockModel(["a", "b", "c"])
    agent = create_agent(
        "tx-exact",
        model=model,
        sandbox=lambda: sb,
        detect=False,
    )
    h = Harness(agent)

    async with h.session() as sess:
        # Build up some history
        await sess.prompt("first")
        await sess.prompt("second")
        snapshot_messages = list(sess.messages)
        snapshot_count = len(snapshot_messages)

        with pytest.raises(ValueError, match="oops"):
            async with h.transaction(sess):
                # Directly append messages to simulate activity
                sess.messages.append(Message("user", "inside tx"))
                sess.messages.append(Message("assistant", "tx reply"))
                assert len(sess.messages) == snapshot_count + 2
                raise ValueError("oops")

        # After rollback: exact same count and content
        assert len(sess.messages) == snapshot_count
        for original, restored in zip(snapshot_messages, sess.messages):
            assert original.role == restored.role
            assert original.text == restored.text


async def test_transaction_rollback_with_no_sandbox():
    """Message rollback works even when no sandbox is present.

    Validates: Requirement 27.1
    """
    model = MockModel(["reply"])
    agent = create_agent(
        "tx-no-sandbox",
        model=model,
        detect=False,
    )
    h = Harness(agent)

    async with h.session() as sess:
        await sess.prompt("initial")
        messages_before = list(sess.messages)

        with pytest.raises(RuntimeError, match="error"):
            async with h.transaction(sess):
                sess.messages.append(Message("user", "extra"))
                raise RuntimeError("error")

        # Messages rolled back even without sandbox
        assert sess.messages == messages_before
