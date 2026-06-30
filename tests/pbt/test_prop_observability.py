"""Property test: Tracer failure isolation (Property 29).

**Validates: Requirements 13.4**

Property 29: For any Tracer or Exporter that raises during span export,
the Session SHALL swallow the error and complete the run normally.

This test generates random exception types, creates exporters that raise those
exceptions on export(), runs the Session with them, and verifies the run
completes normally (result.stopped == "end_turn").
"""

from __future__ import annotations

import hypothesis.strategies as st
from hypothesis import given, settings

from tvastar import Harness, Tracer, create_agent
from tvastar.model.mock import MockModel
from tvastar.observability import Span


# ---------------------------------------------------------------------------
# Failing Exporter that raises a given exception type
# ---------------------------------------------------------------------------


class FailingExporter:
    """An exporter that raises a specified exception on every export() call."""

    def __init__(self, exc: BaseException):
        self._exc = exc

    def export(self, span: Span) -> None:
        raise self._exc


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Strategy: generate various exception types that a misbehaving exporter might raise
st_exception = st.one_of(
    st.builds(RuntimeError, st.text(min_size=0, max_size=50)),
    st.builds(ValueError, st.text(min_size=0, max_size=50)),
    st.builds(TypeError, st.text(min_size=0, max_size=50)),
    st.builds(IOError, st.text(min_size=0, max_size=50)),
    st.builds(OSError, st.text(min_size=0, max_size=50)),
    st.builds(AttributeError, st.text(min_size=0, max_size=50)),
    st.builds(KeyError, st.text(min_size=0, max_size=50)),
    st.builds(IndexError, st.text(min_size=0, max_size=50)),
    st.builds(PermissionError, st.text(min_size=0, max_size=50)),
    st.builds(ConnectionError, st.text(min_size=0, max_size=50)),
    st.builds(TimeoutError, st.text(min_size=0, max_size=50)),
    st.builds(MemoryError),
    st.builds(NotImplementedError, st.text(min_size=0, max_size=50)),
)

# Strategy: generate a response text for the mock model
st_response_text = st.text(
    alphabet=st.characters(categories=("L", "N", "P", "Z")),
    min_size=1,
    max_size=100,
)


# ---------------------------------------------------------------------------
# Property Test: Tracer failure isolation
# ---------------------------------------------------------------------------


@given(
    exc=st_exception,
    response_text=st_response_text,
)
@settings(max_examples=100, deadline=None)
async def test_tracer_failure_isolation(
    exc: BaseException,
    response_text: str,
):
    """Property 29: Tracer failure isolation.

    **Validates: Requirements 13.4**

    For any Tracer or Exporter that raises during span export, Session swallows
    the error and completes the run normally (result.stopped == "end_turn").
    """
    # Create a tracer with a failing exporter that will raise on every span export
    failing_tracer = Tracer(exporters=[FailingExporter(exc)])

    model = MockModel(script=[response_text])

    agent = create_agent(
        "test-tracer-isolation",
        model=model,
        instructions="You are a test agent.",
        tools=[],
        max_steps=5,
        detect=False,
    )

    # Run the session with the failing tracer — it must complete normally
    result = await Harness(agent, tracer=failing_tracer).run("Hello")

    # Verify: the run completed normally despite the exporter raising
    assert result.stopped == "end_turn", (
        f"Expected stopped='end_turn' but got stopped='{result.stopped}'. "
        f"Exporter raised {type(exc).__name__}('{exc}') which should have been swallowed."
    )

    # Verify: the response text was produced correctly
    assert result.text == response_text, (
        f"Expected response text '{response_text}' but got '{result.text}'. "
        f"The failing exporter should not affect session output."
    )
