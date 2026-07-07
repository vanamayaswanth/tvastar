"""Property-based tests for cost tracking and budget enforcement.

**Validates: Requirements 12.1, 12.2, 12.3, 12.6**

Property 27: Cost monotonicity
- For any sequence of model.generate calls within a run, cumulative cost.usd
  is monotonically non-decreasing.
- Cost increments always have non-negative tokens, so the cumulative USD
  total can only stay the same or increase.

Property 28: Budget enforcement
- For any BudgetPolicy with max_usd=X, when cost.usd >= X:
  on_exceed="raise" raises BudgetExceeded;
  on_exceed="stop" does not raise (session handles stop externally).
- For costs below the limit, check() never raises regardless of on_exceed mode.
"""

from __future__ import annotations

import hypothesis.strategies as st
from hypothesis import given, settings

import pytest

from tvastar.cost import Cost, BudgetExceeded, BudgetPolicy, COST_TABLE


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Pick a model that has pricing info so .usd is non-zero
st_model_names = st.sampled_from(list(COST_TABLE.keys()))

# Non-negative token counts for a single model.generate call
st_token_pair = st.tuples(
    st.integers(min_value=0, max_value=500_000),
    st.integers(min_value=0, max_value=500_000),
)

# A sequence of Cost increments (1 to 20 model.generate calls in a run)
st_cost_sequence = st.lists(st_token_pair, min_size=1, max_size=20)


# ---------------------------------------------------------------------------
# Property 27: Cost monotonicity
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=None)
@given(model=st_model_names, increments=st_cost_sequence)
def test_cumulative_cost_monotonically_non_decreasing(
    model: str, increments: list[tuple[int, int]]
):
    """For any sequence of model.generate calls within a run, the cumulative
    cost.usd is monotonically non-decreasing.

    Each increment represents one model.generate call with (input_tokens,
    output_tokens). Since tokens are always non-negative and pricing rates
    are non-negative, adding a new cost can only increase or maintain the
    cumulative total.

    **Validates: Requirements 12.1, 12.6**
    """
    cumulative = Cost(input_tokens=0, output_tokens=0, model=model)
    prev_usd = 0.0

    for input_tokens, output_tokens in increments:
        step_cost = Cost(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=model,
        )
        cumulative = cumulative + step_cost
        current_usd = cumulative.usd

        # Monotonically non-decreasing: current >= previous
        assert current_usd >= prev_usd, (
            f"Cost decreased from ${prev_usd:.6f} to ${current_usd:.6f} "
            f"after adding Cost(input={input_tokens}, output={output_tokens}, "
            f"model={model!r})"
        )
        prev_usd = current_usd


# ---------------------------------------------------------------------------
# Strategies for Property 28
# ---------------------------------------------------------------------------

# Budget limit in a realistic range
st_max_usd = st.floats(min_value=0.001, max_value=100.0, allow_nan=False, allow_infinity=False)

# on_exceed mode for the two testable behaviours
st_on_exceed = st.sampled_from(["raise", "stop"])


def _make_cost_exceeding(max_usd: float, model: str) -> Cost:
    """Create a Cost whose .usd is guaranteed >= max_usd for the given model."""
    rates = COST_TABLE[model]
    # Use output tokens to exceed — output rate is always >= input rate
    rate_per_token = rates["output"] / 1_000_000
    if rate_per_token == 0:
        # Shouldn't happen with current COST_TABLE, but be safe
        return Cost(input_tokens=10_000_000, output_tokens=10_000_000, model=model)
    # Compute minimum tokens needed to hit max_usd, then add a buffer
    tokens_needed = int(max_usd / rate_per_token) + 1
    return Cost(input_tokens=0, output_tokens=tokens_needed, model=model)


def _make_cost_below(max_usd: float, model: str) -> Cost:
    """Create a Cost whose .usd is guaranteed < max_usd for the given model."""
    rates = COST_TABLE[model]
    # Use a fraction of the budget
    target_usd = max_usd * 0.5  # 50% of budget
    rate_per_token = rates["output"] / 1_000_000
    if rate_per_token == 0:
        return Cost(input_tokens=0, output_tokens=0, model=model)
    tokens = max(0, int(target_usd / rate_per_token) - 1)
    return Cost(input_tokens=0, output_tokens=tokens, model=model)


# ---------------------------------------------------------------------------
# Property 28: Budget enforcement — on_exceed="raise" raises BudgetExceeded
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=None)
@given(max_usd=st_max_usd, model=st_model_names)
def test_budget_raise_on_exceed(max_usd: float, model: str):
    """For any BudgetPolicy with on_exceed='raise' and cost.usd >= max_usd,
    check() SHALL raise BudgetExceeded.

    **Validates: Requirements 12.2**
    """
    policy = BudgetPolicy(max_usd=max_usd, on_exceed="raise")
    cost = _make_cost_exceeding(max_usd, model)

    # Precondition: cost must actually exceed
    assert cost.usd >= max_usd, f"Test setup error: cost.usd={cost.usd:.6f} < max_usd={max_usd:.6f}"

    with pytest.raises(BudgetExceeded) as exc_info:
        policy.check(cost)

    assert exc_info.value.spent == cost.usd
    assert exc_info.value.limit == max_usd


# ---------------------------------------------------------------------------
# Property 28: Budget enforcement — on_exceed="stop" does NOT raise
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=None)
@given(max_usd=st_max_usd, model=st_model_names)
def test_budget_stop_no_exception(max_usd: float, model: str):
    """For any BudgetPolicy with on_exceed='stop' and cost.usd >= max_usd,
    check() SHALL NOT raise. The session handles stopping externally.

    **Validates: Requirements 12.3**
    """
    policy = BudgetPolicy(max_usd=max_usd, on_exceed="stop")
    cost = _make_cost_exceeding(max_usd, model)

    # Precondition: cost must actually exceed
    assert cost.usd >= max_usd

    # Should not raise — session handles stop behaviour
    policy.check(cost)  # no exception expected


# ---------------------------------------------------------------------------
# Property 28: Budget enforcement — costs below limit never raise
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=None)
@given(max_usd=st_max_usd, model=st_model_names, on_exceed=st_on_exceed)
def test_budget_below_limit_no_exception(max_usd: float, model: str, on_exceed: str):
    """For any BudgetPolicy with any on_exceed mode, when cost.usd < max_usd,
    check() SHALL NOT raise.

    **Validates: Requirements 12.2, 12.3**
    """
    policy = BudgetPolicy(max_usd=max_usd, on_exceed=on_exceed)
    cost = _make_cost_below(max_usd, model)

    # Precondition: cost must be below limit
    assert cost.usd < max_usd, f"Test setup error: cost.usd={cost.usd:.6f} >= max_usd={max_usd:.6f}"

    # Should never raise for costs below limit
    policy.check(cost)  # no exception expected
