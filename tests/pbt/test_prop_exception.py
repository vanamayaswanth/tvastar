"""Property test for non-overflow exception propagation (Property 3).

**Property 3: Non-overflow exceptions propagate**
For any exception raised by model.generate that is NOT a context overflow,
the Session SHALL propagate it to the caller without catching.

**Validates: Requirements 1.5**
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
import hypothesis.strategies as st

from tvastar import Harness, create_agent
from tvastar.model.mock import MockModel


# ---------------------------------------------------------------------------
# Strategy: generate non-overflow exception types with arbitrary messages
# ---------------------------------------------------------------------------

# Exception classes that are definitely NOT context overflow errors.
# These must not contain any of the overflow phrases used by _is_context_overflow.
_NON_OVERFLOW_EXCEPTION_TYPES = (
    ValueError,
    RuntimeError,
    TypeError,
    IOError,
    OSError,
    KeyError,
    AttributeError,
    NotImplementedError,
    PermissionError,
    TimeoutError,
    ConnectionError,
    IndexError,
)

# Messages that do NOT match any overflow phrase. We avoid words like
# "context", "token", "prompt", "too long", "too large", "exceeded" etc.
st_safe_messages = st.text(
    alphabet=st.characters(categories=("L", "N", "Z")),
    min_size=1,
    max_size=80,
).filter(
    lambda s: (
        not any(
            phrase in s.lower()
            for phrase in (
                "context_length_exceeded",
                "prompt is too long",
                "context window exceeded",
                "maximum context length",
                "input is too long",
                "request too large",
                "token count exceeds",
            )
        )
    )
)


@st.composite
def st_non_overflow_exceptions(draw: st.DrawFn) -> Exception:
    """Generate a non-overflow exception with a safe message."""
    exc_type = draw(st.sampled_from(_NON_OVERFLOW_EXCEPTION_TYPES))
    message = draw(st_safe_messages)
    return exc_type(message)


# ---------------------------------------------------------------------------
# Property 3: Non-overflow exceptions propagate
# ---------------------------------------------------------------------------


@given(exc=st_non_overflow_exceptions())
@settings(deadline=None)
async def test_non_overflow_exceptions_propagate(exc: Exception):
    """For any exception raised by model.generate that is NOT a context overflow,
    the Session propagates it to the caller unchanged.

    **Validates: Requirements 1.5**
    """
    # MockModel supports raising BaseException instances from its script.
    agent = create_agent(
        "exception-propagation-test",
        model=MockModel([exc]),
        instructions="Test agent for exception propagation property.",
        tools=[],
        max_steps=10,
        detect=False,
    )

    h = Harness(agent)

    # The exact exception type should propagate to the caller
    with pytest.raises(type(exc)) as exc_info:
        await h.run("trigger")

    # Verify same message propagated
    assert str(exc_info.value) == str(exc)
