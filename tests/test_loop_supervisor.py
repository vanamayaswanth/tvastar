"""Unit tests for LoopSupervisor — overlap detection and skip handling."""

import asyncio
from dataclasses import dataclass, field
from unittest.mock import MagicMock

import pytest

from tvastar.loop import LoopEvent, LoopState
from tvastar.loop.supervisor import LoopSupervisor


# -- Minimal stub that satisfies LoopSupervisor's interface --


@dataclass
class _FakeConfig:
    name: str = "test-loop"
    goal: str = "test"
    allow_concurrent: bool = False


class _FakeLoop:
    def __init__(self, state=LoopState.IDLE, allow_concurrent=False):
        self._state = state
        self.config = _FakeConfig(allow_concurrent=allow_concurrent)
        self._listeners: list = []

    @property
    def name(self) -> str:
        return self.config.name

    @property
    def state(self) -> LoopState:
        return self._state


# -- Tests --


async def test_should_trigger_idle_loop():
    """Idle loop allows trigger."""
    loop = _FakeLoop(state=LoopState.IDLE)
    sup = LoopSupervisor(loop)
    allowed, run_id = await sup.should_trigger()
    assert allowed is True
    assert run_id is None


async def test_should_trigger_running_non_concurrent():
    """Non-concurrent loop in RUNNING state skips trigger."""
    loop = _FakeLoop(state=LoopState.RUNNING)
    sup = LoopSupervisor(loop)
    sup._active_run_ids = ["run-1"]
    allowed, run_id = await sup.should_trigger()
    assert allowed is False
    assert run_id == "run-1"


async def test_should_trigger_verifying_non_concurrent():
    """Non-concurrent loop in VERIFYING state skips trigger."""
    loop = _FakeLoop(state=LoopState.VERIFYING)
    sup = LoopSupervisor(loop)
    sup._active_run_ids = ["run-2"]
    allowed, run_id = await sup.should_trigger()
    assert allowed is False
    assert run_id == "run-2"


async def test_should_trigger_running_no_tracked_ids():
    """Non-concurrent RUNNING with no tracked IDs returns 'unknown'."""
    loop = _FakeLoop(state=LoopState.RUNNING)
    sup = LoopSupervisor(loop)
    allowed, run_id = await sup.should_trigger()
    assert allowed is False
    assert run_id == "unknown"


async def test_concurrent_allows_up_to_4():
    """Concurrent mode allows up to 4 parallel runs."""
    loop = _FakeLoop(state=LoopState.RUNNING, allow_concurrent=True)
    sup = LoopSupervisor(loop)
    sup._active_run_ids = ["r1", "r2", "r3"]
    allowed, run_id = await sup.should_trigger()
    assert allowed is True
    assert run_id is None


async def test_concurrent_skips_at_cap():
    """Concurrent mode skips at 4 active runs."""
    loop = _FakeLoop(state=LoopState.RUNNING, allow_concurrent=True)
    sup = LoopSupervisor(loop)
    sup._active_run_ids = ["r1", "r2", "r3", "r4"]
    allowed, run_id = await sup.should_trigger()
    assert allowed is False
    assert run_id == "r1"


async def test_register_and_unregister_run():
    """register_run/unregister_run manage _active_run_ids."""
    loop = _FakeLoop()
    sup = LoopSupervisor(loop)
    sup.register_run("run-a")
    assert "run-a" in sup._active_run_ids
    sup.unregister_run("run-a")
    assert "run-a" not in sup._active_run_ids


async def test_unregister_unknown_run_silent():
    """unregister_run for unknown ID does not raise."""
    loop = _FakeLoop()
    sup = LoopSupervisor(loop)
    sup.unregister_run("nonexistent")  # should not raise


async def test_on_skip_emits_event_and_logs(caplog):
    """on_skip emits LoopEvent to listeners and logs WARNING."""
    events = []
    loop = _FakeLoop()
    loop._listeners.append(events.append)
    sup = LoopSupervisor(loop)

    import logging

    with caplog.at_level(logging.WARNING):
        sup.on_skip("run-x", 1234567890.0)

    assert len(events) == 1
    evt = events[0]
    assert evt.loop_name == "test-loop"
    assert evt.state == LoopState.IDLE
    assert evt.data["skipped"] is True
    assert evt.data["reason"] == "overlap"
    assert evt.data["active_run_id"] == "run-x"
    assert evt.at == 1234567890.0
    assert "trigger skipped" in caplog.text


async def test_on_skip_listener_exception_isolated():
    """Listener exception in on_skip does not propagate."""

    def bad_listener(event):
        raise RuntimeError("boom")

    loop = _FakeLoop()
    loop._listeners.append(bad_listener)
    sup = LoopSupervisor(loop)
    # Should not raise
    sup.on_skip("run-y", 0.0)


async def test_lock_serializes_decisions():
    """Lock prevents race in should_trigger under concurrent access."""
    loop = _FakeLoop(state=LoopState.RUNNING, allow_concurrent=True)
    sup = LoopSupervisor(loop)

    # Fill to cap-1, then race two checks
    sup._active_run_ids = ["r1", "r2", "r3"]

    results = await asyncio.gather(
        sup.should_trigger(),
        sup.should_trigger(),
    )
    # Both should see 3 active (below cap) and return True
    assert all(allowed for allowed, _ in results)
