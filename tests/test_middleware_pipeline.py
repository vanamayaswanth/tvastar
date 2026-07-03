"""Unit tests for the middleware pipeline in Session (Requirement 16)."""

import warnings

import pytest

from tvastar import Harness, create_agent
from tvastar.model import MockModel
from tvastar.types import Message


# ---------------------------------------------------------------------------
# Helper middleware functions
# ---------------------------------------------------------------------------


def append_tag_middleware(messages):
    """Middleware that appends a tag message to the list."""
    return messages + [Message("user", "[middleware-tag]")]


def upper_last_middleware(messages):
    """Middleware that uppercases the last message's text content."""
    if not messages:
        return messages
    result = list(messages)
    last = result[-1]
    if isinstance(last.content, str):
        result[-1] = Message(last.role, last.content.upper())
    return result


def exploding_middleware(messages):
    """Middleware that always raises."""
    raise RuntimeError("boom from middleware")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_middleware_applied_in_order():
    """Middleware list is applied in order; model sees the transformed messages."""
    seen_messages = []

    class SpyModel(MockModel):
        async def generate(self, messages, **kw):
            seen_messages.append(list(messages))
            return await super().generate(messages, **kw)

    agent = create_agent(
        "mw-order",
        model=SpyModel(["done"]),
        instructions="",
        middleware=[append_tag_middleware, upper_last_middleware],
    )
    h = Harness(agent)
    await h.run("hello")

    # The model should have received the messages after both middleware ran:
    # 1) append_tag_middleware adds [middleware-tag]
    # 2) upper_last_middleware uppercases the last message -> "[MIDDLEWARE-TAG]"
    assert len(seen_messages) == 1
    last_msg = seen_messages[0][-1]
    assert last_msg.content == "[MIDDLEWARE-TAG]"


@pytest.mark.asyncio
async def test_middleware_exception_skipped_with_warning():
    """When a middleware raises, it is skipped and a warning is emitted."""
    seen_messages = []

    class SpyModel(MockModel):
        async def generate(self, messages, **kw):
            seen_messages.append(list(messages))
            return await super().generate(messages, **kw)

    agent = create_agent(
        "mw-err",
        model=SpyModel(["done"]),
        instructions="",
        middleware=[exploding_middleware, append_tag_middleware],
    )
    h = Harness(agent)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = await h.run("hi")

    # The run should complete successfully
    assert result.text == "done"

    # A warning should have been emitted about the exploding middleware
    mw_warnings = [w for w in caught if "raised; skipping" in str(w.message)]
    assert len(mw_warnings) >= 1

    # The second middleware should still have been applied
    last_msg = seen_messages[0][-1]
    assert last_msg.content == "[middleware-tag]"


@pytest.mark.asyncio
async def test_no_middleware_passes_original_messages():
    """When middleware is None, the model receives original messages unchanged."""
    seen_messages = []

    class SpyModel(MockModel):
        async def generate(self, messages, **kw):
            seen_messages.append(list(messages))
            return await super().generate(messages, **kw)

    agent = create_agent(
        "mw-none",
        model=SpyModel(["done"]),
        instructions="",
        middleware=None,
    )
    h = Harness(agent)
    await h.run("test input")

    # Model should get exactly the user message (no transformation)
    assert len(seen_messages) == 1
    user_msgs = [m for m in seen_messages[0] if m.role == "user"]
    assert any("test input" in str(m.content) for m in user_msgs)


@pytest.mark.asyncio
async def test_middleware_all_explode_uses_original():
    """When all middleware raise, model receives original messages."""
    seen_messages = []

    class SpyModel(MockModel):
        async def generate(self, messages, **kw):
            seen_messages.append(list(messages))
            return await super().generate(messages, **kw)

    def also_explodes(messages):
        raise ValueError("also broken")

    agent = create_agent(
        "mw-all-fail",
        model=SpyModel(["done"]),
        instructions="",
        middleware=[exploding_middleware, also_explodes],
    )
    h = Harness(agent)

    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        result = await h.run("hello")

    # Run completes successfully
    assert result.text == "done"

    # Model should have received messages — they won't be empty
    assert len(seen_messages[0]) > 0


@pytest.mark.asyncio
async def test_middleware_empty_list_passes_original():
    """An empty middleware list behaves the same as None (no-op)."""
    seen_messages = []

    class SpyModel(MockModel):
        async def generate(self, messages, **kw):
            seen_messages.append(list(messages))
            return await super().generate(messages, **kw)

    agent = create_agent(
        "mw-empty",
        model=SpyModel(["done"]),
        instructions="",
        middleware=[],
    )
    h = Harness(agent)
    await h.run("hello")

    # With an empty list, messages pass through unchanged
    assert len(seen_messages) == 1
    user_msgs = [m for m in seen_messages[0] if m.role == "user"]
    assert any("hello" in str(m.content) for m in user_msgs)
