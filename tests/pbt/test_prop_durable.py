"""Property-based test for checkpoint round-trip (Property 26).

Property 26: Checkpoint round-trip
For any session with messages M and a Store, after checkpointing,
harness.resume(session_id) produces a session with messages equivalent to M.

**Validates: Requirements 11.1, 11.2**
"""

from __future__ import annotations

import pytest
import hypothesis.strategies as st
from hypothesis import given, settings

# Suppress the DeprecationWarning from Checkpointer (testing legacy code intentionally)
pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

from tvastar.durable import Checkpointer  # noqa: E402
from tvastar.memory.store import InMemoryStore  # noqa: E402
from tvastar.types import (  # noqa: E402
    Message,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)


# ---------------------------------------------------------------------------
# Strategies for generating message lists with text/tool_use/tool_result blocks
# ---------------------------------------------------------------------------

st_text_block = st.builds(
    TextBlock,
    text=st.text(
        alphabet=st.characters(categories=("L", "N", "P", "Z")),
        min_size=1,
        max_size=100,
    ),
)

st_tool_use_block = st.builds(
    ToolUseBlock,
    name=st.from_regex(r"[a-z][a-z0-9_]{0,19}", fullmatch=True),
    input=st.dictionaries(
        keys=st.from_regex(r"[a-z_]{1,10}", fullmatch=True),
        values=st.one_of(
            st.text(min_size=0, max_size=50),
            st.integers(min_value=-1000, max_value=1000),
            st.booleans(),
        ),
        min_size=0,
        max_size=3,
    ),
    id=st.from_regex(r"call_[a-f0-9]{12}", fullmatch=True),
)

st_tool_result_block = st.builds(
    ToolResultBlock,
    tool_use_id=st.from_regex(r"call_[a-f0-9]{12}", fullmatch=True),
    content=st.text(
        alphabet=st.characters(categories=("L", "N", "P", "Z")),
        min_size=0,
        max_size=100,
    ),
    is_error=st.booleans(),
)

st_content_block = st.one_of(st_text_block, st_tool_use_block, st_tool_result_block)


@st.composite
def st_message(draw: st.DrawFn) -> Message:
    """Generate a message with role-appropriate content blocks."""
    role = draw(st.sampled_from(["user", "assistant"]))
    if role == "user":
        # User messages: text blocks or tool_result blocks
        blocks = draw(
            st.one_of(
                st.lists(st_text_block, min_size=1, max_size=3),
                st.lists(st_tool_result_block, min_size=1, max_size=3),
            )
        )
    else:
        # Assistant messages: text blocks or tool_use blocks (or mix)
        blocks = draw(
            st.one_of(
                st.lists(st_text_block, min_size=1, max_size=3),
                st.lists(st_tool_use_block, min_size=1, max_size=3),
            )
        )
    return Message(role=role, content=blocks)


st_message_list = st.lists(st_message(), min_size=1, max_size=10)


# ---------------------------------------------------------------------------
# Helpers for equivalence checking
# ---------------------------------------------------------------------------


def _blocks_equivalent(original_blocks, loaded_blocks) -> bool:
    """Check that two lists of content blocks are equivalent."""
    if len(original_blocks) != len(loaded_blocks):
        return False
    for orig, loaded in zip(original_blocks, loaded_blocks):
        if type(orig) is not type(loaded):
            return False
        if isinstance(orig, TextBlock):
            if orig.text != loaded.text:
                return False
        elif isinstance(orig, ToolUseBlock):
            if orig.name != loaded.name or orig.input != loaded.input or orig.id != loaded.id:
                return False
        elif isinstance(orig, ToolResultBlock):
            if (
                orig.tool_use_id != loaded.tool_use_id
                or orig.content != loaded.content
                or orig.is_error != loaded.is_error
            ):
                return False
    return True


def _messages_equivalent(original: list[Message], loaded: list[Message]) -> bool:
    """Check that two message lists are equivalent (role and content blocks)."""
    if len(original) != len(loaded):
        return False
    for orig_msg, loaded_msg in zip(original, loaded):
        if orig_msg.role != loaded_msg.role:
            return False
        if not _blocks_equivalent(orig_msg.blocks, loaded_msg.blocks):
            return False
    return True


# ---------------------------------------------------------------------------
# Property 26: Checkpoint round-trip
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=None)
@given(
    messages=st_message_list,
    session_id=st.from_regex(r"sess-[a-z0-9]{4,12}", fullmatch=True),
)
def test_checkpoint_round_trip(messages: list[Message], session_id: str):
    """Property 26: Checkpoint round-trip.

    For any session with messages M and a Store, after checkpointing,
    loading produces messages equivalent to M (same roles, same content blocks).

    **Validates: Requirements 11.1, 11.2**
    """
    store = InMemoryStore()
    checkpointer = Checkpointer(store)

    # Save the messages
    checkpointer.save(session_id, messages=messages)

    # Load them back
    record = checkpointer.load(session_id)

    # Must successfully load
    assert record is not None, "Checkpointer.load() returned None after save()"
    assert record["session_id"] == session_id

    loaded_messages = record["messages"]

    # Loaded messages must be equivalent to the original
    assert _messages_equivalent(messages, loaded_messages), (
        f"Messages not equivalent after round-trip.\n"
        f"Original count: {len(messages)}, Loaded count: {len(loaded_messages)}"
    )
