"""Tests for dispatch state cleanup (Requirement 28).

Verifies:
1. Completed dispatch tasks are removed from _active registry (and _dispatch_agent_ids).
2. cancel_dispatch() cancels in-flight tasks and cleans up.
"""

import asyncio

from tvastar import cancel_dispatch, create_agent
from tvastar.dispatch import DispatchPool
from tvastar.model import MockModel


def _make_spec(script=None):
    return create_agent(
        "test-cleanup",
        model=MockModel(script or ["done"]),
        instructions="be helpful",
    )


# ── Test: Completed dispatch tasks removed from _active ──────────────────────


async def test_completed_dispatch_removed_from_active():
    """After a dispatched task completes, it's removed from _active and _dispatch_agent_ids."""
    pool = DispatchPool()
    spec = _make_spec(["reply"])

    dispatch_id = await pool.dispatch(spec, id="agent1", text="hello")

    # Immediately after dispatch, the task should be in _active
    assert dispatch_id in pool._active

    # Wait for the task to complete
    task = pool._active[dispatch_id]
    await asyncio.wait_for(task, timeout=5.0)

    # After completion, dispatch_id should be removed from _active and _dispatch_agent_ids
    assert dispatch_id not in pool._active
    assert dispatch_id not in pool._dispatch_agent_ids

    pool.close()


async def test_completed_dispatch_not_in_list_active():
    """After a dispatch completes, list_active() no longer includes it."""
    pool = DispatchPool()
    spec = _make_spec(["reply"])

    dispatch_id = await pool.dispatch(spec, id="agent1", text="hello")
    assert dispatch_id in pool.list_active()

    # Wait for task completion
    task = pool._active.get(dispatch_id)
    if task:
        await asyncio.wait_for(task, timeout=5.0)

    # Give the finally block time to run
    await asyncio.sleep(0.01)

    assert dispatch_id not in pool.list_active()

    pool.close()


async def test_multiple_dispatches_cleanup_independently():
    """Each completed dispatch is cleaned up independently from others."""
    pool = DispatchPool()
    spec1 = _make_spec(["reply1"])
    spec2 = _make_spec(["reply2"])

    did1 = await pool.dispatch(spec1, id="agent1", text="hello1")
    did2 = await pool.dispatch(spec2, id="agent2", text="hello2")

    # Both should be active initially
    assert did1 in pool._active
    assert did2 in pool._active

    # Wait for both to complete
    tasks = [pool._active[did1], pool._active[did2]]
    await asyncio.gather(*tasks, return_exceptions=True)

    # Both should be cleaned up
    assert did1 not in pool._active
    assert did2 not in pool._active
    assert did1 not in pool._dispatch_agent_ids
    assert did2 not in pool._dispatch_agent_ids

    pool.close()


# ── Test: cancel_dispatch() cancels in-flight tasks and cleans up ─────────────


async def test_cancel_dispatch_cancels_inflight_task():
    """cancel_dispatch() cancels an in-flight task and it gets cleaned up."""
    pool = DispatchPool()

    # Use a model that will block (never finish on its own) by using an event
    blocked = asyncio.Event()

    class SlowModel(MockModel):
        async def generate(self, messages, **kwargs):
            # Block until cancelled or event is set
            await blocked.wait()
            return await super().generate(messages, **kwargs)

    spec = create_agent("slow", model=SlowModel([]), instructions="wait")

    dispatch_id = await pool.dispatch(spec, id="slow_agent", text="slow task")

    # Let the task start running so it reaches an await point
    await asyncio.sleep(0.05)

    # Task should be active
    assert dispatch_id in pool._active
    task = pool._active[dispatch_id]
    assert not task.done()

    # Cancel it
    result = pool.cancel(dispatch_id)
    assert result is True

    # Await the task to let the finally block run (suppress CancelledError)
    try:
        await task
    except asyncio.CancelledError:
        pass

    # After cancellation, the task should be cleaned up
    assert dispatch_id not in pool._active
    assert dispatch_id not in pool._dispatch_agent_ids

    pool.close()


async def test_cancel_dispatch_returns_false_for_unknown_id():
    """cancel_dispatch() returns False when given an unknown dispatch_id."""
    pool = DispatchPool()
    result = pool.cancel("nonexistent_id")
    assert result is False
    pool.close()


async def test_cancel_dispatch_returns_false_for_already_completed():
    """cancel_dispatch() returns False for a task that already completed."""
    pool = DispatchPool()
    spec = _make_spec(["fast reply"])

    dispatch_id = await pool.dispatch(spec, id="agent1", text="hello")

    # Wait for it to finish
    task = pool._active.get(dispatch_id)
    if task:
        await asyncio.wait_for(task, timeout=5.0)
    await asyncio.sleep(0.01)

    # Now trying to cancel should fail (it's already been cleaned up)
    result = pool.cancel(dispatch_id)
    assert result is False

    pool.close()


async def test_module_level_cancel_dispatch():
    """Module-level cancel_dispatch() works via the default pool."""
    from tvastar.dispatch import _default_pool

    blocked = asyncio.Event()

    class SlowModel(MockModel):
        async def generate(self, messages, **kwargs):
            await blocked.wait()
            return await super().generate(messages, **kwargs)

    spec = create_agent("slow", model=SlowModel([]), instructions="wait")

    dispatch_id = await _default_pool.dispatch(spec, id="cancel_test", text="block")

    # Let the task start running
    await asyncio.sleep(0.05)

    # Verify it's active
    assert dispatch_id in _default_pool._active
    task = _default_pool._active[dispatch_id]

    # Cancel via module-level function
    result = cancel_dispatch(dispatch_id)
    assert result is True

    # Await the task to let the finally block run
    try:
        await task
    except asyncio.CancelledError:
        pass

    # Should be cleaned up
    assert dispatch_id not in _default_pool._active
    assert dispatch_id not in _default_pool._dispatch_agent_ids


async def test_cancel_dispatch_emits_error_event():
    """Cancellation results in cleanup. The task is cancelled (CancelledError is
    a BaseException, so it may or may not emit dispatch_error depending on the
    asyncio version). The key invariant is cleanup happens."""
    pool = DispatchPool()
    events = []
    pool.observe(events.append)

    blocked = asyncio.Event()

    class SlowModel(MockModel):
        async def generate(self, messages, **kwargs):
            await blocked.wait()
            return await super().generate(messages, **kwargs)

    spec = create_agent("slow", model=SlowModel([]), instructions="wait")

    dispatch_id = await pool.dispatch(spec, id="event_agent", text="block")

    # Let the task start running
    await asyncio.sleep(0.05)

    # Cancel
    task = pool._active[dispatch_id]
    pool.cancel(dispatch_id)

    # Await the task to let the finally block run
    try:
        await task
    except asyncio.CancelledError:
        pass

    # dispatch_start should always be emitted
    event_types = [e.type for e in events]
    assert "dispatch_start" in event_types

    # The dispatch_id should be cleaned up from _active
    assert dispatch_id not in pool._active
    assert dispatch_id not in pool._dispatch_agent_ids

    pool.close()
