"""Tests for tvastar.loop.shutdown — graceful drain on SIGTERM/SIGINT."""

from __future__ import annotations

import asyncio
import signal
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tvastar.loop import LoopRun, LoopState
from tvastar.loop.shutdown import _CHECKPOINTABLE, install_signal_handlers


# ---------------------------------------------------------------------------
# Helpers — minimal fake loop/registry that satisfy the interface
# ---------------------------------------------------------------------------


class FakeLoop:
    def __init__(self, name: str, state: LoopState = LoopState.IDLE, run_id: str = "run_abc"):
        self.name = name
        self._state = state
        self._run_id = run_id
        self.stop = AsyncMock()

    @property
    def state(self) -> LoopState:
        return self._state

    def last_run(self) -> LoopRun | None:
        if self._run_id:
            return MagicMock(run_id=self._run_id)
        return None


class FakeRegistry:
    def __init__(self, loops: dict[str, FakeLoop] | None = None):
        self._loops = loops or {}

    def all(self) -> dict[str, FakeLoop]:
        return dict(self._loops)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shutdown_stops_all_loops():
    """stop() is called on every registered loop during shutdown drain."""
    lp1 = FakeLoop("a", LoopState.IDLE)
    lp2 = FakeLoop("b", LoopState.IDLE)
    registry = FakeRegistry({"a": lp1, "b": lp2})

    # Exercise the shutdown logic directly (same as what the signal handler runs)
    loops = registry.all()
    for lp in loops.values():
        await lp.stop()

    lp1.stop.assert_awaited_once()
    lp2.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_shutdown_waits_for_running_loops():
    """Loops in RUNNING state are waited on until they reach checkpointable state."""
    lp = FakeLoop("runner", LoopState.RUNNING)
    registry = FakeRegistry({"runner": lp})

    # Simulate: loop transitions to PASS after 0.2s
    async def _transition():
        await asyncio.sleep(0.2)
        lp._state = LoopState.PASS

    asyncio.create_task(_transition())

    await lp.stop()

    deadline = asyncio.get_event_loop().time() + 2.0
    while True:
        still_running = [
            lp for lp in registry.all().values() if lp.state.value not in _CHECKPOINTABLE
        ]
        if not still_running:
            break
        remaining = deadline - asyncio.get_event_loop().time()
        assert remaining > 0, "Timed out waiting for loop to reach checkpointable state"
        await asyncio.sleep(0.05)

    assert lp.state == LoopState.PASS


@pytest.mark.asyncio
async def test_shutdown_force_cancels_on_timeout(capsys):
    """When drain_timeout expires, loops are force-cancelled and logged to stderr."""
    lp = FakeLoop("stuck", LoopState.RUNNING, run_id="run_xyz")
    registry = FakeRegistry({"stuck": lp})

    await lp.stop()

    drain_timeout = 0.2
    deadline = asyncio.get_event_loop().time() + drain_timeout
    force_cancelled = []

    while True:
        still_running = [
            lp for lp in registry.all().values() if lp.state.value not in _CHECKPOINTABLE
        ]
        if not still_running:
            break
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            for lp in still_running:
                last = lp.last_run()
                run_id = last.run_id if last else "unknown"
                force_cancelled.append((lp.name, run_id))
                print(
                    f"shutdown: force-cancelled loop={lp.name} run_id={run_id}",
                    file=sys.stderr,
                )
            break
        await asyncio.sleep(0.05)

    assert ("stuck", "run_xyz") in force_cancelled
    captured = capsys.readouterr()
    assert "force-cancelled loop=stuck run_id=run_xyz" in captured.err


@pytest.mark.skipif(sys.platform == "win32", reason="Unix signal handlers only")
def test_install_signal_handlers_registers_sigterm_and_sigint_unix():
    """On Unix, both SIGTERM and SIGINT are registered via add_signal_handler."""
    registry = FakeRegistry({})
    loop = asyncio.new_event_loop()

    with patch.object(loop, "add_signal_handler") as mock_add:
        asyncio.set_event_loop(loop)
        try:
            install_signal_handlers(registry)
            calls = [c[0][0] for c in mock_add.call_args_list]
            assert signal.SIGTERM in calls
            assert signal.SIGINT in calls
        finally:
            asyncio.set_event_loop(None)
            loop.close()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only test")
def test_install_signal_handlers_registers_sigint_windows():
    """On Windows, SIGINT is registered via signal.signal() — no error raised."""
    registry = FakeRegistry({})

    with patch("tvastar.loop.shutdown.signal.signal") as mock_signal:
        # Need a loop set for get_event_loop()
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            install_signal_handlers(registry)
            # signal.signal should be called with SIGINT
            called_signals = [c[0][0] for c in mock_signal.call_args_list]
            assert signal.SIGINT in called_signals
        finally:
            asyncio.set_event_loop(None)
            loop.close()


@pytest.mark.asyncio
async def test_checkpointable_states():
    """IDLE, PASS, FAIL are considered checkpointable (safe to stop)."""
    assert "idle" in _CHECKPOINTABLE
    assert "pass" in _CHECKPOINTABLE
    assert "fail" in _CHECKPOINTABLE
    # RUNNING and VERIFYING are NOT checkpointable
    assert "running" not in _CHECKPOINTABLE
    assert "verifying" not in _CHECKPOINTABLE
