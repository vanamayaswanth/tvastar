"""Property test: Context overflow triggers single retry (Property 4).

**Validates: Requirements 1.6**

Property 4: For any context overflow error when a CompactionPolicy is configured,
the Session SHALL compact the history and retry model.generate exactly once.

This test simulates context overflow on the first model call, verifies that
compaction is triggered (the session compacts and retries), and confirms the
model is called exactly twice in the main loop (overflow + retry), with the
run succeeding after compaction.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
import hypothesis.strategies as st

from tvastar import Harness, create_agent
from tvastar.compaction import CompactionPolicy
from tvastar.model.mock import MockModel
from tvastar.types import Message, TextBlock, ModelResponse, StopReason, Usage


# ---------------------------------------------------------------------------
# Custom model that raises overflow on first call, succeeds on second
# ---------------------------------------------------------------------------


class OverflowOnceModel(MockModel):
    """A mock model that raises a context overflow error on the first generate call,
    then returns a scripted success response on subsequent calls.

    It also supports being used as a summary model by compact_session (intermediate
    calls during compaction) — those calls are tracked separately.
    """

    def __init__(self, overflow_message: str, success_response: str):
        super().__init__()
        self._overflow_message = overflow_message
        self._success_response = success_response
        self._main_call_count = 0
        self._total_call_count = 0

    async def generate(
        self,
        messages,
        *,
        system=None,
        tools=None,
        max_tokens=4096,
        temperature=1.0,
        stop_sequences=None,
        thinking_level=None,
    ) -> ModelResponse:
        self._total_call_count += 1
        self.calls.append(list(messages))

        # Detect if this is a compaction summary call (no tools, short max_tokens=1024)
        # vs a main loop call (has tools or normal max_tokens)
        is_summary_call = (
            max_tokens == 1024
            and tools is None
            and any(
                "summarise" in m.text.lower() or "summary" in m.text.lower()
                for m in messages
                if hasattr(m, "text") and m.text
            )
        )

        if is_summary_call:
            # This is the compaction call — return a summary
            return ModelResponse(
                message=Message("assistant", [TextBlock(text="[Summary of earlier context]")]),
                stop_reason=StopReason.END_TURN,
                usage=Usage(input_tokens=50, output_tokens=20),
            )

        # Main loop calls
        self._main_call_count += 1
        if self._main_call_count == 1:
            # First main call: raise context overflow
            raise RuntimeError(self._overflow_message)
        else:
            # Second main call (after compaction): succeed
            return ModelResponse(
                message=Message("assistant", [TextBlock(text=self._success_response)]),
                stop_reason=StopReason.END_TURN,
                usage=Usage(input_tokens=100, output_tokens=30),
            )


# ---------------------------------------------------------------------------
# Strategies for generating overflow error messages
# ---------------------------------------------------------------------------

# Strategy: generates a valid context overflow message (must match _OVERFLOW_PHRASES)
st_overflow_phrase = st.sampled_from(
    [
        "context_length_exceeded",
        "prompt is too long",
        "context window exceeded",
        "maximum context length",
        "input is too long",
        "request too large",
        "token count exceeds",
    ]
)

# Strategy: builds a full error message containing an overflow phrase
st_overflow_message = st_overflow_phrase.map(lambda phrase: f"Error: {phrase} for this request")

# Strategy: generates a success response text
st_success_text = st.text(
    alphabet=st.characters(categories=("L", "N", "P", "Z")),
    min_size=1,
    max_size=100,
)

# Strategy: generates valid CompactionPolicy configurations
st_compaction_policy = st.builds(
    CompactionPolicy,
    max_messages=st.integers(min_value=5, max_value=100),
    keep_last=st.integers(min_value=1, max_value=10),
    min_messages=st.integers(min_value=1, max_value=5),
)


# ---------------------------------------------------------------------------
# Property Test: Context overflow triggers single retry
# ---------------------------------------------------------------------------


@given(
    overflow_msg=st_overflow_message,
    success_text=st_success_text,
    compaction_policy=st_compaction_policy,
)
@settings(max_examples=50, deadline=None)
async def test_context_overflow_compacts_and_retries_once(
    overflow_msg: str,
    success_text: str,
    compaction_policy: CompactionPolicy,
):
    """Property 4: Context overflow triggers single retry.

    **Validates: Requirements 1.6**

    For any context overflow error when CompactionPolicy is configured,
    Session compacts and retries exactly once.
    """
    model = OverflowOnceModel(
        overflow_message=overflow_msg,
        success_response=success_text,
    )

    agent = create_agent(
        "test-overflow",
        model=model,
        instructions="You are a test agent.",
        tools=[],
        max_steps=10,
        compaction=compaction_policy,
        detect=False,
    )

    h = Harness(agent)

    # We need some initial messages in the session to make compaction work
    # (compact_session needs messages to summarize). We'll add enough messages
    # to exceed min_messages so compaction can proceed.
    sess = h.session()
    async with sess:
        # Pre-populate messages so compaction has something to work with
        # We need at least min_messages messages for compaction to proceed
        for i in range(compaction_policy.min_messages + compaction_policy.keep_last + 1):
            sess.messages.append(Message("user", f"Previous message {i}"))
            sess.messages.append(Message("assistant", f"Previous response {i}"))

        result = await sess.prompt("Trigger the overflow")

    # Verify: model was called exactly twice in the main loop
    # (once overflow, once success after compaction)
    assert model._main_call_count == 2, (
        f"Expected exactly 2 main loop model calls (overflow + retry), got {model._main_call_count}"
    )

    # Verify: the run succeeded
    assert result.stopped == "end_turn"
    assert result.text == success_text


# ---------------------------------------------------------------------------
# Supplementary: overflow without CompactionPolicy propagates
# ---------------------------------------------------------------------------


async def test_context_overflow_without_compaction_propagates():
    """When CompactionPolicy is NOT configured, context overflow propagates.

    This is the control case — without compaction, the error should raise.
    """
    model = OverflowOnceModel(
        overflow_message="Error: context_length_exceeded",
        success_response="Should not reach here",
    )

    agent = create_agent(
        "test-no-compaction",
        model=model,
        instructions="You are a test agent.",
        tools=[],
        max_steps=10,
        # No compaction policy!
        detect=False,
    )

    h = Harness(agent)
    with pytest.raises(RuntimeError, match="context_length_exceeded"):
        await h.run("This should fail")


# ---------------------------------------------------------------------------
# Supplementary: double overflow still propagates (only one retry)
# ---------------------------------------------------------------------------


class DoubleOverflowModel(MockModel):
    """A model that always raises context overflow — even after compaction."""

    def __init__(self, overflow_message: str):
        super().__init__()
        self._overflow_message = overflow_message
        self._call_count = 0

    async def generate(
        self,
        messages,
        *,
        system=None,
        tools=None,
        max_tokens=4096,
        temperature=1.0,
        stop_sequences=None,
        thinking_level=None,
    ) -> ModelResponse:
        self._call_count += 1
        self.calls.append(list(messages))

        # Summary call for compaction
        is_summary_call = (
            max_tokens == 1024
            and tools is None
            and any(
                "summarise" in m.text.lower() or "summary" in m.text.lower()
                for m in messages
                if hasattr(m, "text") and m.text
            )
        )

        if is_summary_call:
            return ModelResponse(
                message=Message("assistant", [TextBlock(text="[Summary]")]),
                stop_reason=StopReason.END_TURN,
                usage=Usage(input_tokens=50, output_tokens=20),
            )

        # All main calls raise overflow
        raise RuntimeError(self._overflow_message)


async def test_double_overflow_propagates_after_single_retry():
    """If the retry after compaction also overflows, the error propagates.

    The session should only retry once — if compaction doesn't help,
    the error propagates to the caller.
    """
    model = DoubleOverflowModel(overflow_message="Error: context_length_exceeded")

    agent = create_agent(
        "test-double-overflow",
        model=model,
        instructions="You are a test agent.",
        tools=[],
        max_steps=10,
        compaction=CompactionPolicy(max_messages=5, keep_last=2, min_messages=1),
        detect=False,
    )

    h = Harness(agent)
    sess = h.session()
    async with sess:
        # Pre-populate messages
        for i in range(5):
            sess.messages.append(Message("user", f"Msg {i}"))
            sess.messages.append(Message("assistant", f"Reply {i}"))

        with pytest.raises(RuntimeError, match="context_length_exceeded"):
            await sess.prompt("Trigger double overflow")
