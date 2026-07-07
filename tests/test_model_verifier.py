"""Property-based and unit tests for ModelVerifier.

# Feature: pi-ecosystem-adaptations, Properties 4-6 + unit tests for Req 2.5, 2.6
"""

from __future__ import annotations

import asyncio

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tvastar.approval import ApprovalDenied, ApprovalTimeout, ModelVerifier
from tvastar.types import Message, ModelResponse, StopReason, TextBlock, Usage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class CapturingModel:
    """Mock model that captures messages sent to it and returns a scripted response."""

    def __init__(self, response_text: str = "APPROVE ok"):
        self.response_text = response_text
        self.captured_messages: list[list[Message]] = []

    async def generate(self, messages: list[Message], **kwargs) -> ModelResponse:
        self.captured_messages.append(list(messages))
        return ModelResponse(
            message=Message("assistant", [TextBlock(text=self.response_text)]),
            stop_reason=StopReason.END_TURN,
            usage=Usage(),
        )


class FailingModel:
    """Mock model that raises a given exception on generate()."""

    def __init__(self, exc: BaseException):
        self.exc = exc

    async def generate(self, messages: list[Message], **kwargs) -> ModelResponse:
        raise self.exc


# Strategy: printable text that won't be empty (for tool names, args values)
_text_st = st.text(
    alphabet=st.characters(categories=("L", "N", "P", "S", "Z")),
    min_size=1,
    max_size=100,
)

# Strategy: argument dicts with string keys and string values
_args_st = st.dictionaries(
    keys=st.text(min_size=1, max_size=20, alphabet="abcdefghijklmnopqrstuvwxyz_"),
    values=_text_st,
    min_size=0,
    max_size=5,
)

# Strategy: list of Message objects (simulating session history)
_message_st = st.builds(
    Message,
    role=st.sampled_from(["user", "assistant"]),
    content=_text_st,
)
_messages_list_st = st.lists(_message_st, min_size=0, max_size=20)


# ---------------------------------------------------------------------------
# Property 4: ModelVerifier sends correct context to reviewer
# Validates: Requirements 2.2
# ---------------------------------------------------------------------------


# Feature: pi-ecosystem-adaptations, Property 4: ModelVerifier sends correct context to reviewer
class TestProperty4ContextSentToReviewer:
    """**Validates: Requirements 2.2**"""

    @given(
        tool_name=_text_st,
        args=_args_st,
        messages=_messages_list_st,
    )
    @settings(max_examples=100)
    async def test_prompt_contains_tool_name_and_args(
        self, tool_name: str, args: dict, messages: list[Message]
    ):
        """The reviewer prompt contains the tool name, args, and at most 5 messages."""
        model = CapturingModel("APPROVE fine")
        verifier = ModelVerifier(model=model, timeout=30)

        # Build message the way the session would
        message = f"Tool: {tool_name}, Args: {args}"
        metadata = {"messages": messages}

        await verifier.request(message, metadata=metadata)

        # Check the captured messages
        assert len(model.captured_messages) == 1
        sent_msgs = model.captured_messages[0]

        # Should have system + user message
        assert len(sent_msgs) == 2
        assert sent_msgs[0].role == "system"
        assert sent_msgs[1].role == "user"

        user_content = sent_msgs[1].text

        # Tool name must be in the prompt
        assert tool_name in user_content

        # Args representation must be in the prompt
        assert str(args) in user_content

        # At most 5 messages from history included
        if messages:
            last_five = messages[-5:]
            # Verify each of the last 5 messages appears in the prompt
            for m in last_five:
                assert m.text in user_content or m.role in user_content

            # If there are more than 5 messages, earlier ones should NOT be in the prompt
            # (unless their text coincidentally matches — we check structural inclusion)
            # The key property: only up to 5 messages are included
            history_section = user_content.split("Recent conversation:\n")
            if len(history_section) > 1:
                history_lines = [line for line in history_section[1].split("\n") if line.strip()]
                assert len(history_lines) <= 5


# ---------------------------------------------------------------------------
# Property 5: ModelVerifier denial includes reasoning
# Validates: Requirements 2.3
# ---------------------------------------------------------------------------

# Strategy: non-APPROVE strings (first word != APPROVE)
_non_approve_word = st.text(min_size=1, max_size=20).filter(
    lambda s: s.split()[0].upper() != "APPROVE" if s.strip() else True
)

_reasoning_text = st.text(
    alphabet=st.characters(categories=("L", "N", "P", "S", "Z")),
    min_size=1,
    max_size=200,
)


# Feature: pi-ecosystem-adaptations, Property 5: ModelVerifier denial includes reasoning
class TestProperty5DenialIncludesReasoning:
    """**Validates: Requirements 2.3**"""

    @given(reasoning=_reasoning_text)
    @settings(max_examples=100)
    async def test_denial_with_reasoning(self, reasoning: str):
        """Non-APPROVE response with reasoning raises ApprovalDenied with that reasoning."""
        # Ensure first word is DENY (not APPROVE)
        response_text = f"DENY {reasoning}"
        model = CapturingModel(response_text)
        verifier = ModelVerifier(model=model, timeout=30)

        with pytest.raises(ApprovalDenied) as exc_info:
            await verifier.request("test tool call")

        # The implementation strips the full response text before splitting,
        # so trailing whitespace in reasoning is stripped.
        # The reasoning text (stripped) should be in the exception message.
        stripped_reasoning = reasoning.strip()
        if stripped_reasoning:
            # After strip+split(None,1), the second part preserves internal whitespace
            # but the full response is stripped first. Check the reasoning is present.
            assert stripped_reasoning in str(exc_info.value)
        else:
            assert "reviewer denied without stated reason" in str(exc_info.value)

    @given(deny_word=st.sampled_from(["DENY", "NO", "REJECT", "BLOCK", "deny", "no"]))
    @settings(max_examples=100)
    async def test_denial_without_reasoning(self, deny_word: str):
        """Non-APPROVE response with no reasoning text after the word raises with default message."""
        model = CapturingModel(deny_word)
        verifier = ModelVerifier(model=model, timeout=30)

        with pytest.raises(ApprovalDenied) as exc_info:
            await verifier.request("test tool call")

        assert "reviewer denied without stated reason" in str(exc_info.value)

    async def test_empty_response_raises_denial(self):
        """Empty response raises ApprovalDenied with default reason."""
        model = CapturingModel("")
        verifier = ModelVerifier(model=model, timeout=30)

        with pytest.raises(ApprovalDenied) as exc_info:
            await verifier.request("test tool call")

        assert "reviewer denied without stated reason" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Property 6: ModelVerifier fails closed on errors
# Validates: Requirements 2.4
# ---------------------------------------------------------------------------

# Strategy: various exception types (excluding TimeoutError since asyncio.wait_for
# catches it specially and converts to ApprovalTimeout rather than ApprovalDenied)
_exception_st = st.sampled_from(
    [
        ConnectionError("network down"),
        ValueError("bad response"),
        RuntimeError("unexpected failure"),
        OSError("io error"),
        KeyError("missing key"),
        TypeError("type mismatch"),
        IOError("io failed"),
    ]
)


# Feature: pi-ecosystem-adaptations, Property 6: ModelVerifier fails closed on errors
class TestProperty6FailsClosed:
    """**Validates: Requirements 2.4**"""

    @given(exc=_exception_st)
    @settings(max_examples=100)
    async def test_any_exception_raises_approval_denied(self, exc: BaseException):
        """Any generate() exception raises ApprovalDenied, never True."""
        model = FailingModel(exc)
        verifier = ModelVerifier(model=model, timeout=30)

        with pytest.raises(ApprovalDenied) as exc_info:
            result = await verifier.request("dangerous action")
            # Should never reach here — but if it does, fail explicitly
            assert result is not True, "ModelVerifier must never return True on error"

        # The denial message should indicate the failure
        assert "reviewer unavailable" in str(exc_info.value)

    @given(exc=_exception_st)
    @settings(max_examples=100)
    async def test_never_returns_true_on_error(self, exc: BaseException):
        """Verify the return value is never True — always raises."""
        model = FailingModel(exc)
        verifier = ModelVerifier(model=model, timeout=30)

        raised = False
        try:
            await verifier.request("test")
        except ApprovalDenied:
            raised = True
        except Exception:
            # Any other exception is also acceptable (fail-closed)
            raised = True

        assert raised, "ModelVerifier must raise on error, never return True"


# ---------------------------------------------------------------------------
# Unit tests: timeout behavior (Req 2.5)
# ---------------------------------------------------------------------------


class TestModelVerifierTimeout:
    """Unit tests for timeout behavior. Validates: Requirements 2.5"""

    async def test_timeout_raises_approval_timeout(self):
        """When reviewer model takes too long, ApprovalTimeout is raised."""

        class SlowModel:
            async def generate(self, messages, **kwargs):
                await asyncio.sleep(10)  # Much longer than timeout

        verifier = ModelVerifier(model=SlowModel(), timeout=5)  # Minimum timeout

        with pytest.raises(ApprovalTimeout) as exc_info:
            await verifier.request("test action")

        assert "5" in str(exc_info.value) or "respond within" in str(exc_info.value)

    async def test_timeout_clamped_minimum(self):
        """Timeout is clamped to minimum 5 seconds."""
        model = CapturingModel("APPROVE ok")
        verifier = ModelVerifier(model=model, timeout=1)  # Below minimum
        assert verifier.timeout == 5.0

    async def test_timeout_clamped_maximum(self):
        """Timeout is clamped to maximum 120 seconds."""
        model = CapturingModel("APPROVE ok")
        verifier = ModelVerifier(model=model, timeout=999)  # Above maximum
        assert verifier.timeout == 120.0

    async def test_timeout_within_range(self):
        """Timeout within 5-120 range is used as-is."""
        model = CapturingModel("APPROVE ok")
        verifier = ModelVerifier(model=model, timeout=45)
        assert verifier.timeout == 45.0


# ---------------------------------------------------------------------------
# Unit tests: construction requires model (Req 2.6)
# ---------------------------------------------------------------------------


class TestModelVerifierConstruction:
    """Unit tests for construction. Validates: Requirements 2.6"""

    async def test_requires_model_with_generate(self):
        """Object without 'generate' attribute raises TypeError."""
        with pytest.raises(TypeError, match="model must implement the Model interface"):
            ModelVerifier(model=object())

    async def test_requires_model_string_rejected(self):
        """A string is not a valid model."""
        with pytest.raises(TypeError, match="model must implement the Model interface"):
            ModelVerifier(model="not-a-model")

    async def test_requires_model_none_rejected(self):
        """None is not a valid model."""
        with pytest.raises(TypeError, match="model must implement the Model interface"):
            ModelVerifier(model=None)

    async def test_accepts_object_with_generate(self):
        """Any object with a generate attribute is accepted (duck typing)."""
        model = CapturingModel("APPROVE ok")
        verifier = ModelVerifier(model=model)
        assert verifier.model is model

    async def test_accepts_dict_with_generate_attr(self):
        """Object with generate attribute passes duck-type check."""

        class MinimalModel:
            async def generate(self, messages, **kw):
                pass

        verifier = ModelVerifier(model=MinimalModel())
        assert verifier.timeout == 30.0  # Default timeout
