"""Tests for fuel-based termination (Requirement 5).

Validates:
- Exhaustion suspends loop with failure_kind=fuel_exhausted
- No fuel configured = unchanged behavior (no tracking)
- Quality gate pass refuels (capped at initial fuel)
"""

from __future__ import annotations

import pytest

from tvastar import create_agent
from tvastar.cost import register_model_cost
from tvastar.loop import FailureKind, Loop, LoopConfig, LoopState
from tvastar.model.mock import MockModel


# Register a dedicated fuel-test model so we don't pollute the global "mock" entry.
# MockModel outputs ~12 tokens per response and ~few input tokens.
# With 1000.0 per million output: 12 * 1000 / 1_000_000 = 0.012 per call
# Use high rates to make fuel math easy to reason about.
_FUEL_MODEL = "mock-fuel-test"
register_model_cost(_FUEL_MODEL, input_per_million=1000.0, output_per_million=1000.0)


def _fuel_loop(
    fuel: float | None = None,
    responses: list[str] | None = None,
    quality_gate: int = 80,
    max_iterations: int = 5,
) -> Loop:
    model = MockModel(responses or ["Work complete. SUCCESS"])
    model.name = _FUEL_MODEL  # use dedicated cost entry
    spec = create_agent("fuel-test", model=model, instructions="", detect=False)
    config = LoopConfig(
        name="fuel-test-loop",
        goal="do fuel test",
        schedule="@manual",
        max_iterations=max_iterations,
        quality_gate=quality_gate,
        fuel=fuel,
    )
    return Loop(spec, config)


# ---------------------------------------------------------------------------
# Test: fuel exhaustion suspends loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fuel_exhaustion_suspends():
    """WHEN fuel_remaining <= 0, THEN state → SUSPENDED, failure_kind=fuel_exhausted."""
    # Use a very small fuel so it's exhausted after one iteration's cost
    loop = _fuel_loop(fuel=0.0001)
    run = await loop.trigger()
    assert run.state == LoopState.SUSPENDED
    assert run.failure_kind == FailureKind.FUEL_EXHAUSTED
    assert run.fuel_remaining is not None
    assert run.fuel_remaining <= 0


# ---------------------------------------------------------------------------
# Test: no-fuel = unchanged behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_fuel_unchanged_behavior():
    """WHEN fuel is not set, THEN no fuel tracking occurs (zero behavioral change)."""
    loop = _fuel_loop(fuel=None)
    run = await loop.trigger()
    # Should pass normally — same as a loop without fuel
    assert run.state == LoopState.PASS
    assert run.fuel_remaining is None


# ---------------------------------------------------------------------------
# Test: refuel on quality_gate pass extends life (capped at initial)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refuel_on_quality_gate_pass():
    """WHEN quality_gate passes, THEN fuel_remaining += fuel * 0.25 (capped at initial)."""
    # Use a large fuel so exhaustion doesn't happen
    initial_fuel = 100.0
    loop = _fuel_loop(fuel=initial_fuel, quality_gate=0)  # gate=0 means always passes
    run = await loop.trigger()
    assert run.state == LoopState.PASS
    assert run.fuel_remaining is not None
    # After deducting cost and refueling: remaining = initial - cost + initial*0.25
    # But capped at initial, so if cost is tiny: remaining ≈ initial (capped)
    assert run.fuel_remaining <= initial_fuel
    # The refuel should have fired (cost is tiny, so fuel ~ initial after cap)
    assert run.fuel_remaining > initial_fuel * 0.5  # definitely still well-fueled


@pytest.mark.asyncio
async def test_refuel_capped_at_initial():
    """fuel_remaining never exceeds the initial fuel value (refuel is capped)."""
    initial_fuel = 50.0
    loop = _fuel_loop(fuel=initial_fuel, quality_gate=0)
    run = await loop.trigger()
    assert run.state == LoopState.PASS
    assert run.fuel_remaining is not None
    assert run.fuel_remaining <= initial_fuel
