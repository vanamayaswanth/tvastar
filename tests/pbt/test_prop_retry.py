"""Property-based tests for tool retry backoff formula.

Property 35: Tool retry backoff formula
- For any ToolRetryPolicy with backoff_base=B, backoff_max=M, jitter=J,
  the delay for attempt A = min(M, B * 2^A) + random(0, J).

**Validates: Requirements 23.2, 23.3**
"""

from __future__ import annotations

import hypothesis.strategies as st
from hypothesis import given, settings

from tvastar.tools import ToolRetryPolicy


# ---------------------------------------------------------------------------
# Property 35: Tool retry backoff formula
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=None)
@given(
    backoff_base=st.floats(min_value=0.01, max_value=10.0, allow_nan=False, allow_infinity=False),
    backoff_max=st.floats(min_value=1.0, max_value=120.0, allow_nan=False, allow_infinity=False),
    jitter=st.floats(min_value=0.0, max_value=5.0, allow_nan=False, allow_infinity=False),
    attempt=st.integers(min_value=0, max_value=10),
)
def test_tool_retry_backoff_formula(
    backoff_base: float, backoff_max: float, jitter: float, attempt: int
):
    """Property 35: Tool retry backoff formula.

    For any ToolRetryPolicy with backoff_base=B, backoff_max=M, jitter=J,
    the delay for attempt A SHALL be min(M, B * 2^A) + random(0, J).

    We verify that the computed delay falls within the expected bounds:
      lower = min(backoff_max, backoff_base * 2^attempt)
      upper = min(backoff_max, backoff_base * 2^attempt) + jitter

    **Validates: Requirements 23.2, 23.3**
    """
    policy = ToolRetryPolicy(
        max_attempts=attempt + 2,  # doesn't matter for sleep_for, just valid
        backoff_base=backoff_base,
        backoff_max=backoff_max,
        jitter=jitter,
    )

    delay = policy.sleep_for(attempt)

    # Expected deterministic component (capped exponential)
    expected_base = min(backoff_max, backoff_base * (2 ** attempt))

    # The delay must be at least the capped base (jitter >= 0)
    assert delay >= expected_base, (
        f"Delay {delay} is below expected minimum {expected_base} "
        f"(B={backoff_base}, M={backoff_max}, A={attempt})"
    )

    # The delay must not exceed capped base + jitter
    expected_upper = expected_base + jitter
    assert delay <= expected_upper, (
        f"Delay {delay} exceeds expected maximum {expected_upper} "
        f"(B={backoff_base}, M={backoff_max}, J={jitter}, A={attempt})"
    )
