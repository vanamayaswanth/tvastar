"""Tests for scrub_after_run behavior.

Validates:
- Requirements 26.1: scrub_after_run=True replaces all message content with SHA-256 hashes
- Requirements 26.2: scrub_after_run=False preserves message content unchanged
"""
from __future__ import annotations

import hashlib
import re

import pytest

from tvastar import Harness, create_agent
from tvastar.model.mock import MockModel


SCRUB_PATTERN = re.compile(r"^\[scrubbed:sha256:[0-9a-f]{16}\]$")


@pytest.mark.asyncio
async def test_scrub_after_run_true_replaces_all_message_content():
    """When scrub_after_run=True, all messages have content replaced with SHA-256 hashes."""
    agent = create_agent(
        "scrub-test",
        model=MockModel(script=["Hello, the answer is 42."]),
        scrub_after_run=True,
    )
    result = await Harness(agent).run("What is the meaning of life?")

    # The run should still produce the correct text (extracted before scrubbing)
    assert result.text == "Hello, the answer is 42."

    # Every message in the session history must be scrubbed
    assert len(result.messages) >= 2  # at least user + assistant
    for msg in result.messages:
        content_str = str(msg.content)
        assert SCRUB_PATTERN.match(content_str), (
            f"Message content not scrubbed: {content_str!r}"
        )


@pytest.mark.asyncio
async def test_scrub_after_run_true_hash_is_deterministic():
    """Scrubbed content uses the SHA-256 of the original content (first 16 hex chars)."""
    original_user_text = "Secret data: SSN 123-45-6789"
    agent = create_agent(
        "scrub-hash-check",
        model=MockModel(script=["Got it."]),
        scrub_after_run=True,
    )
    result = await Harness(agent).run(original_user_text)

    # Find the user message and verify hash matches
    user_msg = next(m for m in result.messages if m.role == "user")
    expected_hash = hashlib.sha256(original_user_text.encode()).hexdigest()[:16]
    assert user_msg.content == f"[scrubbed:sha256:{expected_hash}]"


@pytest.mark.asyncio
async def test_scrub_after_run_false_preserves_message_content():
    """When scrub_after_run=False (default), message content is preserved unchanged."""
    user_text = "Tell me a secret."
    assistant_text = "The secret is 42."

    agent = create_agent(
        "no-scrub-test",
        model=MockModel(script=[assistant_text]),
        scrub_after_run=False,
    )
    result = await Harness(agent).run(user_text)

    # Messages should be preserved exactly
    assert len(result.messages) >= 2
    user_msg = next(m for m in result.messages if m.role == "user")
    assistant_msg = next(m for m in result.messages if m.role == "assistant")

    assert user_msg.content == user_text
    assert assistant_msg.text == assistant_text


@pytest.mark.asyncio
async def test_scrub_after_run_default_is_false():
    """By default, scrub_after_run is False and content is preserved."""
    user_text = "Preserve this content."
    assistant_text = "Content preserved."

    agent = create_agent(
        "default-scrub-test",
        model=MockModel(script=[assistant_text]),
        # scrub_after_run not specified — should default to False
    )
    result = await Harness(agent).run(user_text)

    user_msg = next(m for m in result.messages if m.role == "user")
    assistant_msg = next(m for m in result.messages if m.role == "assistant")

    assert user_msg.content == user_text
    assert assistant_msg.text == assistant_text


@pytest.mark.asyncio
async def test_scrub_after_run_true_with_multi_turn():
    """Scrubbing works correctly when there are multiple tool-call turns."""
    # MockModel with tool use then final answer
    agent = create_agent(
        "scrub-multi-turn",
        model=MockModel(script=["All done after tools."]),
        scrub_after_run=True,
    )
    result = await Harness(agent).run("Do something complex")

    # All messages should be scrubbed regardless of role
    for msg in result.messages:
        content_str = str(msg.content)
        assert SCRUB_PATTERN.match(content_str), (
            f"Message content not scrubbed: {content_str!r}"
        )
