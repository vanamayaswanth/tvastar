"""Tests for tvastar.loop.registry — thread-safe Loop coordination."""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass

import pytest

from tvastar.loop import LoopEvent, LoopRun, LoopState
from tvastar.loop.registry import LoopRegistry, RegistryMetrics


# ---------------------------------------------------------------------------
# Helpers — minimal fake Loop that satisfies the registry interface
# ---------------------------------------------------------------------------


@dataclass
class FakeConfig:
    """Minimal config stub with `then` for chaining tests."""
    name: str
    then: str | None = None


class FakeLoop:
    """Minimal Loop stub for registry tests."""

    def __init__(
        self,
        name: str,
        history: list[LoopRun] | None = None,
        cumulative_usd: float = 0.0,
        then: str | None = None,
        state: LoopState = LoopState.IDLE,
    ):
        self._name = name
        self._history = history or []
        self._cumulative_usd = cumulative_usd
        self._listeners: list = []
        self._config = FakeConfig(name=name, then=then)
        self._state = state
        self.trigger_calls: list[dict] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def config(self) -> FakeConfig:
        return self._config

    @property
    def state(self) -> LoopState:
        return self._state

    def history(self, limit: int = 50) -> list[LoopRun]:
        return self._history[-limit:]

    def on_event(self, fn) -> None:
        self._listeners.append(fn)

    def emit(self, event: LoopEvent) -> None:
        """Test helper — fire an event as if it came from the loop."""
        for fn in self._listeners:
            fn(event)

    async def trigger(self, context: dict | None = None) -> LoopRun:
        """Test helper — records trigger calls."""
        self.trigger_calls.append(context or {})
        return LoopRun(
            run_id="run_chained",
            loop_name=self._name,
            state=LoopState.TRIGGERED,
            iteration=1,
            started_at=time.time(),
        )


def _run(state: LoopState = LoopState.PASS) -> LoopRun:
    return LoopRun(
        run_id="run_abc",
        loop_name="test",
        state=state,
        iteration=1,
        started_at=time.time(),
    )


# ---------------------------------------------------------------------------
# Basic API
# ---------------------------------------------------------------------------


def test_register_and_get():
    reg = LoopRegistry()
    loop = FakeLoop("alpha")
    reg.register(loop)
    assert reg.get("alpha") is loop


def test_get_returns_none_for_missing():
    reg = LoopRegistry()
    assert reg.get("nope") is None


def test_all_returns_snapshot():
    reg = LoopRegistry()
    a = FakeLoop("a")
    b = FakeLoop("b")
    reg.register(a)
    reg.register(b)
    result = reg.all()
    assert result == {"a": a, "b": b}
    # Snapshot — mutating result doesn't affect registry
    result["c"] = FakeLoop("c")
    assert "c" not in reg


def test_unregister_removes_loop():
    reg = LoopRegistry()
    loop = FakeLoop("x")
    reg.register(loop)
    reg.unregister("x")
    assert reg.get("x") is None
    assert len(reg) == 0


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


def test_register_duplicate_raises_value_error():
    reg = LoopRegistry()
    reg.register(FakeLoop("dup"))
    with pytest.raises(ValueError, match="already registered"):
        reg.register(FakeLoop("dup"))


def test_unregister_nonexistent_raises_key_error():
    reg = LoopRegistry()
    with pytest.raises(KeyError, match="not found"):
        reg.unregister("ghost")


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def test_metrics_empty_registry():
    reg = LoopRegistry()
    m = reg.metrics()
    assert m == RegistryMetrics(0, 0, 0, 0.0)


def test_metrics_aggregates_across_loops():
    runs_a = [_run(LoopState.PASS), _run(LoopState.PASS), _run(LoopState.FAIL)]
    runs_b = [_run(LoopState.HANDOFF), _run(LoopState.PASS)]

    reg = LoopRegistry()
    reg.register(FakeLoop("a", history=runs_a, cumulative_usd=1.5))
    reg.register(FakeLoop("b", history=runs_b, cumulative_usd=0.3))

    m = reg.metrics()
    assert m.total_runs == 5
    assert m.total_passes == 3
    assert m.total_fails == 2  # FAIL + HANDOFF
    assert abs(m.total_cost_usd - 1.8) < 1e-9


def test_metrics_counts_handoff_failed_as_fail():
    runs = [_run(LoopState.HANDOFF_FAILED)]
    reg = LoopRegistry()
    reg.register(FakeLoop("x", history=runs))
    m = reg.metrics()
    assert m.total_fails == 1


# ---------------------------------------------------------------------------
# Event broadcasting
# ---------------------------------------------------------------------------


def test_on_event_broadcasts_from_registered_loops():
    reg = LoopRegistry()
    loop = FakeLoop("ev")
    reg.register(loop)

    received = []
    reg.on_event(lambda e: received.append(e))

    event = LoopEvent(loop_name="ev", run_id="r1", state=LoopState.PASS, at=time.time())
    loop.emit(event)

    assert len(received) == 1
    assert received[0] is event


def test_on_event_listener_error_does_not_propagate():
    reg = LoopRegistry()
    loop = FakeLoop("bad")
    reg.register(loop)

    def _explode(e):
        raise RuntimeError("boom")

    reg.on_event(_explode)

    # Should not raise
    event = LoopEvent(loop_name="bad", run_id="r2", state=LoopState.FAIL, at=time.time())
    loop.emit(event)


def test_on_event_multiple_listeners():
    reg = LoopRegistry()
    loop = FakeLoop("multi")
    reg.register(loop)

    results = []
    reg.on_event(lambda e: results.append("A"))
    reg.on_event(lambda e: results.append("B"))

    event = LoopEvent(loop_name="multi", run_id="r3", state=LoopState.PASS, at=time.time())
    loop.emit(event)

    assert results == ["A", "B"]


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------


def test_concurrent_register_unregister():
    """Concurrent register/unregister from multiple threads doesn't corrupt state."""
    reg = LoopRegistry()
    errors = []
    barrier = threading.Barrier(4)

    def _register_batch(prefix: str, count: int):
        barrier.wait()
        for i in range(count):
            try:
                reg.register(FakeLoop(f"{prefix}_{i}"))
            except ValueError:
                pass  # duplicate — expected in concurrent scenario

    def _unregister_batch(prefix: str, count: int):
        barrier.wait()
        for i in range(count):
            try:
                reg.unregister(f"{prefix}_{i}")
            except KeyError:
                pass  # already removed — expected

    # Pre-register some loops to unregister
    for i in range(20):
        reg.register(FakeLoop(f"pre_{i}"))

    threads = [
        threading.Thread(target=_register_batch, args=("a", 20)),
        threading.Thread(target=_register_batch, args=("b", 20)),
        threading.Thread(target=_unregister_batch, args=("pre", 20)),
        threading.Thread(target=_unregister_batch, args=("pre", 20)),  # double-unregister race
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # All "a_*" and "b_*" loops that registered successfully should be present
    # No "pre_*" loops should remain (all unregistered)
    for name in list(reg.all().keys()):
        assert not name.startswith("pre_"), f"pre-registered loop {name} should be gone"


# ---------------------------------------------------------------------------
# __contains__ / __len__
# ---------------------------------------------------------------------------


def test_contains():
    reg = LoopRegistry()
    reg.register(FakeLoop("yes"))
    assert "yes" in reg
    assert "no" not in reg


def test_len():
    reg = LoopRegistry()
    assert len(reg) == 0
    reg.register(FakeLoop("one"))
    assert len(reg) == 1
    reg.register(FakeLoop("two"))
    assert len(reg) == 2
    reg.unregister("one")
    assert len(reg) == 1


# ---------------------------------------------------------------------------
# Chaining: cycle detection
# ---------------------------------------------------------------------------


def test_chain_no_cycle_simple():
    """A→B is fine if B has no `then`."""
    reg = LoopRegistry()
    b = FakeLoop("B")
    reg.register(b)
    a = FakeLoop("A", then="B")
    reg.register(a)  # should not raise
    assert "A" in reg


def test_chain_cycle_a_to_b_to_a():
    """A→B→A is a cycle — rejected at registration of B."""
    reg = LoopRegistry()
    a = FakeLoop("A", then="B")
    reg.register(a)
    b = FakeLoop("B", then="A")
    with pytest.raises(ValueError, match="cycle detected"):
        reg.register(b)


def test_chain_cycle_three_nodes():
    """A→B→C→A is a cycle — rejected at registration of C."""
    reg = LoopRegistry()
    a = FakeLoop("A", then="B")
    reg.register(a)
    b = FakeLoop("B", then="C")
    reg.register(b)
    c = FakeLoop("C", then="A")
    with pytest.raises(ValueError, match="cycle detected"):
        reg.register(c)


def test_chain_self_cycle():
    """A→A is a self-cycle — rejected."""
    reg = LoopRegistry()
    a = FakeLoop("A", then="A")
    with pytest.raises(ValueError, match="cycle detected"):
        reg.register(a)


def test_chain_cycle_message_contains_path():
    """Error message includes the cycle path."""
    reg = LoopRegistry()
    reg.register(FakeLoop("X", then="Y"))
    reg.register(FakeLoop("Y", then="Z"))
    with pytest.raises(ValueError, match=r"Z → X → Y → Z"):
        reg.register(FakeLoop("Z", then="X"))


def test_chain_then_to_unregistered_target_is_allowed():
    """then pointing to a non-existent loop is OK at registration (checked at trigger time)."""
    reg = LoopRegistry()
    a = FakeLoop("A", then="ghost")
    reg.register(a)  # should not raise
    assert "A" in reg


# ---------------------------------------------------------------------------
# Chaining: trigger on PASS
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chain_trigger_on_pass():
    """When source loop emits PASS and has `then`, target is triggered."""
    reg = LoopRegistry()
    target = FakeLoop("target")
    reg.register(target)
    source = FakeLoop("source", then="target")
    reg.register(source)

    # Emit a PASS event from source
    event = LoopEvent(
        loop_name="source", run_id="run_1", state=LoopState.PASS, at=time.time()
    )
    source.emit(event)

    # Give the async task a moment to fire
    await asyncio.sleep(0.05)

    assert len(target.trigger_calls) == 1
    assert target.trigger_calls[0]["chained_from"] == "source"
    assert target.trigger_calls[0]["source_run_id"] == "run_1"


@pytest.mark.asyncio
async def test_chain_no_trigger_on_fail():
    """A FAIL event does not trigger chaining."""
    reg = LoopRegistry()
    target = FakeLoop("target")
    reg.register(target)
    source = FakeLoop("source", then="target")
    reg.register(source)

    event = LoopEvent(
        loop_name="source", run_id="run_2", state=LoopState.FAIL, at=time.time()
    )
    source.emit(event)

    await asyncio.sleep(0.05)
    assert len(target.trigger_calls) == 0


# ---------------------------------------------------------------------------
# Chaining: missing target at trigger time
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chain_missing_target_emits_warning():
    """If target doesn't exist at trigger time, emit warning LoopEvent, no exception."""
    reg = LoopRegistry()
    source = FakeLoop("source", then="ghost")
    reg.register(source)

    warnings_received: list[LoopEvent] = []
    reg.on_event(lambda e: warnings_received.append(e))

    event = LoopEvent(
        loop_name="source", run_id="run_3", state=LoopState.PASS, at=time.time()
    )
    source.emit(event)  # should NOT raise

    # Find the warning event (not the PASS itself)
    chain_warnings = [
        e for e in warnings_received if e.data.get("warning") == "chain_target_missing"
    ]
    assert len(chain_warnings) == 1
    assert chain_warnings[0].data["target"] == "ghost"


# ---------------------------------------------------------------------------
# Chaining: suspended target at trigger time
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chain_suspended_target_emits_warning():
    """If target is SUSPENDED at trigger time, emit warning, skip trigger."""
    reg = LoopRegistry()
    target = FakeLoop("target", state=LoopState.SUSPENDED)
    reg.register(target)
    source = FakeLoop("source", then="target")
    reg.register(source)

    warnings_received: list[LoopEvent] = []
    reg.on_event(lambda e: warnings_received.append(e))

    event = LoopEvent(
        loop_name="source", run_id="run_4", state=LoopState.PASS, at=time.time()
    )
    source.emit(event)

    await asyncio.sleep(0.05)

    # Target should NOT be triggered
    assert len(target.trigger_calls) == 0

    # Warning event should be emitted
    chain_warnings = [
        e for e in warnings_received if e.data.get("warning") == "chain_target_suspended"
    ]
    assert len(chain_warnings) == 1
    assert chain_warnings[0].data["target"] == "target"
