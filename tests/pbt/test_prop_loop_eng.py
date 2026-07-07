"""Property-based tests for loop engineering.

Property 17: Loop state checkpointing
- For any Loop with a configured Store, every state transition is persisted
  to the Store such that a crash at any point allows recovery.

**Validates: Requirements 7.7**

Property 18: Exponential backoff calculation
- For any LoopConfig with backoff_base=B and failed iteration I,
  retry delay = B * 2^(I-1) seconds.

**Validates: Requirements 7.2**

Property 19: Circuit breaker activation
- For any Loop with consecutive_failures >= circuit_breaker_limit,
  state transitions to SUSPENDED.

**Validates: Requirements 7.4**
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid

import hypothesis.strategies as st
from hypothesis import given, settings

from tvastar import create_agent
from tvastar.loop import Loop, LoopConfig, LoopState, LoopRun
from tvastar.model.mock import MockModel
from tvastar.memory.store import InMemoryStore


# ---------------------------------------------------------------------------
# Property 17: Loop state checkpointing
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=None)
@given(
    loop_name=st.text(
        alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="-_"),
        min_size=1,
        max_size=20,
    ).filter(lambda s: s.strip()),
    goal=st.text(
        alphabet=st.characters(whitelist_categories=("L", "N", "Z"), whitelist_characters=" "),
        min_size=1,
        max_size=50,
    ).filter(lambda s: s.strip()),
)
async def test_loop_state_checkpointing_on_trigger(loop_name: str, goal: str):
    """Property 17: Loop state checkpointing.

    For any Loop with a configured Store, every state transition SHALL be
    persisted to the Store. After a trigger+run cycle completes, the Store
    SHALL contain the latest checkpointed state matching the run's final state.

    This test triggers a loop with a passing run and verifies:
    1. The store key "loop:{name}:last_run" exists after trigger
    2. The checkpointed state matches the run's final state
    3. The checkpoint contains required fields (run_id, state, iteration)

    **Validates: Requirements 7.7**
    """
    # Create a model that returns a simple success (no tool use, ends turn)
    model = MockModel(["Task completed successfully."])
    agent = create_agent(
        "checkpoint-test",
        model=model,
        instructions="Test agent",
        detect=False,
    )

    config = LoopConfig(
        name=loop_name,
        goal=goal,
        schedule="@manual",
        max_iterations=3,
    )

    store = InMemoryStore()
    loop = Loop(agent, config, store=store)

    # Trigger the loop — model will return a simple text response (PASS path)
    run = await loop.trigger(context={"test": True})

    # Verify the store has the checkpoint
    state_key = f"loop:{loop_name}:last_run"
    raw = store.get(state_key)
    assert raw is not None, (
        f"Store key '{state_key}' should exist after trigger, but store is empty. "
        f"Available keys: {store.keys('')}"
    )

    # Parse the checkpointed data
    checkpoint = json.loads(raw)

    # Verify required fields are present
    assert "run_id" in checkpoint, "Checkpoint must contain run_id"
    assert "state" in checkpoint, "Checkpoint must contain state"
    assert "iteration" in checkpoint, "Checkpoint must contain iteration"
    assert "started_at" in checkpoint, "Checkpoint must contain started_at"

    # Verify the checkpointed state matches the run's final state
    assert checkpoint["state"] == run.state.value, (
        f"Checkpointed state '{checkpoint['state']}' does not match "
        f"run's final state '{run.state.value}'"
    )

    # Verify the run_id in the checkpoint matches the run
    assert checkpoint["run_id"] == run.run_id, (
        f"Checkpointed run_id '{checkpoint['run_id']}' does not match run's run_id '{run.run_id}'"
    )


@settings(max_examples=100, deadline=None)
@given(
    loop_name=st.text(
        alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="-_"),
        min_size=1,
        max_size=20,
    ).filter(lambda s: s.strip()),
)
async def test_loop_state_checkpointing_captures_transitions(loop_name: str):
    """Property 17: Loop state checkpointing — transition tracking.

    For any Loop with a configured Store, checkpointing occurs on state
    transitions. We verify this by instrumenting the store and checking that
    set() is called with the correct state key during a trigger cycle.

    **Validates: Requirements 7.7**
    """
    # Use a model that returns success text (PASS path)
    model = MockModel(["Done."])
    agent = create_agent(
        "checkpoint-track-test",
        model=model,
        instructions="Test",
        detect=False,
    )

    config = LoopConfig(
        name=loop_name,
        goal="track checkpoints",
        schedule="@manual",
        max_iterations=3,
    )

    store = InMemoryStore()

    # Instrument the store to record all set() calls for our key
    state_key = f"loop:{loop_name}:last_run"
    recorded_states: list[str] = []
    original_set = store.set

    def tracking_set(key: str, value) -> None:
        original_set(key, value)
        if key == state_key:
            data = json.loads(value)
            recorded_states.append(data["state"])

    store.set = tracking_set

    loop = Loop(agent, config, store=store)

    # Trigger the loop
    run = await loop.trigger(context={})

    # The checkpoint should have been called at least twice:
    # 1. After TRIGGERED state is set
    # 2. In the finally block with the final state
    assert len(recorded_states) >= 2, (
        f"Expected at least 2 checkpoints (TRIGGERED + final), "
        f"got {len(recorded_states)}: {recorded_states}"
    )

    # First checkpoint should be TRIGGERED
    assert recorded_states[0] == LoopState.TRIGGERED.value, (
        f"First checkpoint should be 'triggered', got '{recorded_states[0]}'"
    )

    # Last checkpoint should match the run's final state
    assert recorded_states[-1] == run.state.value, (
        f"Last checkpoint should match final state '{run.state.value}', got '{recorded_states[-1]}'"
    )


# ---------------------------------------------------------------------------
# Property 18: Exponential backoff calculation
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=None)
@given(
    backoff_base=st.floats(min_value=0.1, max_value=60.0, allow_nan=False, allow_infinity=False),
    iteration=st.integers(min_value=1, max_value=10),
)
async def test_exponential_backoff_calculation(backoff_base: float, iteration: int):
    """Property 18: Exponential backoff calculation.

    For any LoopConfig with retry_backoff_base=B and a failed iteration I
    (where I < max_iterations), the retry delay SHALL be B * 2^(I-1) seconds.

    This test directly exercises the Loop's _handle_fail method by setting
    the internal _iteration counter and verifying the delay passed to
    _delayed_retry matches the formula B * 2^(I-1).

    **Validates: Requirements 7.2**
    """
    # Create a minimal agent (never actually called)
    model = MockModel(["unused"])
    agent = create_agent(
        "backoff-test",
        model=model,
        instructions="Test",
        detect=False,
    )

    config = LoopConfig(
        name="backoff-prop-test",
        goal="test backoff",
        schedule="@manual",
        max_iterations=iteration + 1,  # ensure iteration < max_iterations
        retry_backoff_base=backoff_base,
    )

    store = InMemoryStore()
    loop = Loop(agent, config, store=store)

    # Set the internal iteration counter to the target value
    loop._iteration = iteration

    # Capture the delay passed to _delayed_retry
    recorded_delay: list[float] = []

    async def mock_delayed_retry(run, delay):
        recorded_delay.append(delay)

    loop._delayed_retry = mock_delayed_retry

    # Create a dummy LoopRun for _handle_fail

    run = LoopRun(
        run_id=f"run_{uuid.uuid4().hex[:8]}",
        loop_name=config.name,
        state=LoopState.FAIL,
        iteration=iteration,
        started_at=time.time(),
        context={},
    )

    # Call _handle_fail directly (it's called under lock in production)
    # We acquire the lock for consistency with internal expectations
    async with loop._lock:
        await loop._handle_fail(run)

    # Allow the event loop to process the create_task scheduled by _handle_fail
    await asyncio.sleep(0)

    # Verify the backoff delay matches B * 2^(I-1)
    expected_delay = backoff_base * (2 ** (iteration - 1))

    assert len(recorded_delay) == 1, (
        f"Expected exactly one _delayed_retry call, got {len(recorded_delay)}"
    )
    assert abs(recorded_delay[0] - expected_delay) < 1e-9, (
        f"Backoff mismatch at iteration {iteration} with base {backoff_base}: "
        f"expected {expected_delay}, got {recorded_delay[0]}"
    )

    # Also verify the run's retry_after is set correctly
    assert run.retry_after is not None, "run.retry_after should be set after _handle_fail"
    assert run.state == LoopState.RETRY, f"Expected state RETRY after _handle_fail, got {run.state}"


# ---------------------------------------------------------------------------
# Property 19: Circuit breaker activation
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=None)
@given(
    circuit_breaker_limit=st.integers(min_value=2, max_value=10),
)
async def test_circuit_breaker_activation(circuit_breaker_limit: int):
    """Property 19: Circuit breaker activation.

    For any Loop with consecutive_failures >= circuit_breaker_limit,
    the state SHALL transition to SUSPENDED.

    This test sets the Loop's _consecutive_failures to exactly the
    circuit_breaker_limit, then calls _fire_handoff (which checks
    the circuit breaker after a successful handoff) and verifies
    the state transitions to SUSPENDED.

    **Validates: Requirements 7.4**
    """

    # Create a minimal agent (never actually called)
    model = MockModel(["unused"])
    agent = create_agent(
        "circuit-breaker-test",
        model=model,
        instructions="Test",
        detect=False,
    )

    config = LoopConfig(
        name="circuit-breaker-prop-test",
        goal="test circuit breaker",
        schedule="@manual",
        max_iterations=3,
        circuit_breaker_limit=circuit_breaker_limit,
    )

    store = InMemoryStore()
    loop = Loop(agent, config, store=store)

    # Set consecutive failures to exactly the circuit_breaker_limit
    loop._consecutive_failures = circuit_breaker_limit

    # Create a dummy LoopRun for _fire_handoff
    run = LoopRun(
        run_id=f"run_{uuid.uuid4().hex[:8]}",
        loop_name=config.name,
        state=LoopState.HANDOFF,
        iteration=1,
        started_at=time.time(),
        context={},
    )

    # Call _fire_handoff — it performs the handoff then checks circuit breaker
    await loop._fire_handoff(run)

    # Verify state transitions to SUSPENDED
    assert loop.state == LoopState.SUSPENDED, (
        f"Expected SUSPENDED when consecutive_failures ({circuit_breaker_limit}) "
        f">= circuit_breaker_limit ({circuit_breaker_limit}), "
        f"but got {loop.state}"
    )
    assert run.state == LoopState.SUSPENDED, f"Expected run state SUSPENDED, got {run.state}"


@settings(max_examples=100, deadline=None)
@given(
    circuit_breaker_limit=st.integers(min_value=3, max_value=10),
    failures_below_limit=st.integers(min_value=1, max_value=2),
)
async def test_circuit_breaker_not_activated_below_limit(
    circuit_breaker_limit: int, failures_below_limit: int
):
    """Property 19 (negative case): Circuit breaker NOT activated below limit.

    For any Loop with consecutive_failures < circuit_breaker_limit,
    the state SHALL NOT transition to SUSPENDED after handoff.

    **Validates: Requirements 7.4**
    """

    # Ensure failures_below_limit is actually below the limit
    actual_failures = min(failures_below_limit, circuit_breaker_limit - 1)

    model = MockModel(["unused"])
    agent = create_agent(
        "circuit-breaker-neg-test",
        model=model,
        instructions="Test",
        detect=False,
    )

    config = LoopConfig(
        name="circuit-breaker-neg-prop-test",
        goal="test circuit breaker not activated",
        schedule="@manual",
        max_iterations=3,
        circuit_breaker_limit=circuit_breaker_limit,
    )

    store = InMemoryStore()
    loop = Loop(agent, config, store=store)

    # Set consecutive failures below the circuit_breaker_limit
    loop._consecutive_failures = actual_failures

    # Create a dummy LoopRun for _fire_handoff
    run = LoopRun(
        run_id=f"run_{uuid.uuid4().hex[:8]}",
        loop_name=config.name,
        state=LoopState.HANDOFF,
        iteration=1,
        started_at=time.time(),
        context={},
    )

    # Call _fire_handoff — it performs the handoff then checks circuit breaker
    await loop._fire_handoff(run)

    # Verify state does NOT transition to SUSPENDED
    assert loop.state != LoopState.SUSPENDED, (
        f"Expected NOT SUSPENDED when consecutive_failures ({actual_failures}) "
        f"< circuit_breaker_limit ({circuit_breaker_limit}), "
        f"but got SUSPENDED"
    )
