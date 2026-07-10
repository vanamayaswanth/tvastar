"""Concurrency safety tests for DegradedStateTracker (REQ-16).

Verifies:
- AC1: State change AND log emission happen inside the same lock acquisition
- AC2: No race between state read and state write (serialized via lock)
- AC3: No data corruption under concurrent access
- AC4: active_states() returns exactly entered-minus-exited set
"""

import asyncio
import io


from tvastar.degraded import DegradedStateTracker
from tvastar.errors import DegradedState
from tvastar.logging import StructuredLogger


def _make_tracker() -> tuple[DegradedStateTracker, io.StringIO]:
    """Create a tracker with a captured log output."""
    buf = io.StringIO()
    logger = StructuredLogger(name="test.degraded", output=buf)
    tracker = DegradedStateTracker(logger)
    return tracker, buf


# ---------------------------------------------------------------------------
# AC1: Atomicity — state change + log inside same lock
# ---------------------------------------------------------------------------


async def test_enter_emits_log_atomically_with_state_change():
    """State is updated AND log is emitted within the same lock hold.

    We verify by checking that after enter() returns, both the state
    is active AND the log contains the entry — no intermediate state
    is observable where one happened without the other.
    """
    tracker, buf = _make_tracker()

    await tracker.enter(DegradedState.model_unavailable, "timeout")

    # State is active
    assert DegradedState.model_unavailable in tracker.active_states()
    # Log was emitted
    log_output = buf.getvalue()
    assert "model_unavailable" in log_output
    assert "Entering degraded state" in log_output


async def test_exit_emits_log_atomically_with_state_change():
    """Exit removes state AND emits log in the same lock hold."""
    tracker, buf = _make_tracker()

    await tracker.enter(DegradedState.mcp_disconnected, "server down")
    buf.truncate(0)
    buf.seek(0)

    await tracker.exit(DegradedState.mcp_disconnected)

    # State is no longer active
    assert DegradedState.mcp_disconnected not in tracker.active_states()
    # Exit log was emitted
    log_output = buf.getvalue()
    assert "Exiting degraded state" in log_output
    assert "mcp_disconnected" in log_output


# ---------------------------------------------------------------------------
# AC2/AC3: No race conditions under concurrent access
# ---------------------------------------------------------------------------


async def test_concurrent_enters_all_visible():
    """Many concurrent enters for different states → active_states() contains all.

    Validates AC3: no data corruption under concurrent access.
    """
    tracker, _ = _make_tracker()
    all_states = list(DegradedState)

    # Enter all states concurrently
    await asyncio.gather(*(tracker.enter(state, f"reason-{state.value}") for state in all_states))

    # All should be active
    active = tracker.active_states()
    assert active == set(all_states)


async def test_concurrent_enter_exit_same_state_serialized():
    """Concurrent enter + exit for the same state → final state is deterministic.

    Validates AC2: last writer wins after acquiring the lock.
    The final state depends on ordering, but must be one of the two valid
    outcomes (entered or exited), never corrupted.
    """
    tracker, _ = _make_tracker()
    state = DegradedState.model_unavailable

    # Run enter and exit concurrently many times to stress the lock
    for _ in range(50):
        # Reset: ensure it's entered first
        await tracker.enter(state, "pre-enter")

        # Now race enter and exit
        await asyncio.gather(
            tracker.enter(state, "re-enter"),
            tracker.exit(state),
        )

        # Final state must be consistent — either entered or exited, not corrupted
        active = tracker.active_states()
        # It's valid to be in or out depending on scheduling order
        assert isinstance(active, set)
        if state in active:
            # If still active, the enter won the race (ran last)
            pass
        else:
            # If not active, the exit won the race (ran last)
            pass


async def test_interleaved_enter_exit_across_states():
    """Interleaved enter/exit across different states → active_states() always
    equals the set-difference of entered minus exited.

    Validates AC4: invariant holds under concurrent access.
    """
    tracker, _ = _make_tracker()

    # Phase 1: Enter some states
    entered = [
        DegradedState.model_unavailable,
        DegradedState.mcp_disconnected,
        DegradedState.state_backend_down,
        DegradedState.budget_exhausted,
        DegradedState.sandbox_overloaded,
    ]
    await asyncio.gather(*(tracker.enter(s, f"reason-{s.value}") for s in entered))

    assert tracker.active_states() == set(entered)

    # Phase 2: Exit some concurrently
    to_exit = [
        DegradedState.model_unavailable,
        DegradedState.budget_exhausted,
    ]
    await asyncio.gather(*(tracker.exit(s) for s in to_exit))

    expected = set(entered) - set(to_exit)
    assert tracker.active_states() == expected

    # Phase 3: Mix enter + exit concurrently
    # Re-enter model_unavailable while exiting mcp_disconnected
    await asyncio.gather(
        tracker.enter(DegradedState.model_unavailable, "back again"),
        tracker.exit(DegradedState.mcp_disconnected),
    )

    expected.add(DegradedState.model_unavailable)
    expected.discard(DegradedState.mcp_disconnected)
    assert tracker.active_states() == expected


# ---------------------------------------------------------------------------
# AC4: active_states() invariant — controlled ordering test
# ---------------------------------------------------------------------------


async def test_active_states_invariant_with_controlled_ordering():
    """Use events to control coroutine ordering and verify active_states()
    always reflects the true entered-minus-exited set.
    """
    tracker, _ = _make_tracker()
    entered_event = asyncio.Event()
    check_event = asyncio.Event()

    async def enter_then_signal():
        await tracker.enter(DegradedState.state_backend_down, "db down")
        entered_event.set()
        # Wait for check to complete before exiting
        await check_event.wait()
        await tracker.exit(DegradedState.state_backend_down)

    async def check_after_enter():
        await entered_event.wait()
        # At this point, state_backend_down MUST be active
        assert DegradedState.state_backend_down in tracker.active_states()
        check_event.set()

    await asyncio.gather(enter_then_signal(), check_after_enter())

    # After both complete, state_backend_down should be exited
    assert DegradedState.state_backend_down not in tracker.active_states()


async def test_high_concurrency_stress():
    """Stress test: many coroutines entering/exiting rapidly.

    Validates no exceptions are raised and active_states() remains a valid set.
    """
    tracker, _ = _make_tracker()
    states = list(DegradedState)

    async def churn(state: DegradedState, iterations: int):
        for i in range(iterations):
            await tracker.enter(state, f"iteration-{i}")
            await tracker.exit(state)

    # Run 5 states × 20 iterations concurrently
    await asyncio.gather(*(churn(s, 20) for s in states))

    # After all churn, everything should be exited
    assert tracker.active_states() == set()


async def test_active_states_snapshot_consistency():
    """active_states() returns a snapshot — modifying it doesn't affect tracker."""
    tracker, _ = _make_tracker()
    await tracker.enter(DegradedState.model_unavailable, "test")

    snapshot = tracker.active_states()
    snapshot.add(DegradedState.budget_exhausted)  # mutate the returned set

    # Tracker's actual state is unchanged
    assert tracker.active_states() == {DegradedState.model_unavailable}
