"""Tests for ChainTarget and LoopConfig.then conditional chaining (Requirement 13).

Tests:
  - str = current behavior (backward compat)
  - list routes by outcome state
  - max_cycles prevents infinite chains
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

import pytest

from tvastar.loop import ChainTarget, LoopEvent, LoopRun, LoopState
from tvastar.loop.registry import LoopRegistry


# ---------------------------------------------------------------------------
# Helpers — same FakeLoop pattern as test_loop_registry.py
# ---------------------------------------------------------------------------


@dataclass
class FakeConfig:
    name: str
    then: "str | list[ChainTarget] | None" = None


class FakeLoop:
    def __init__(
        self,
        name: str,
        then: "str | list[ChainTarget] | None" = None,
        state: LoopState = LoopState.IDLE,
    ):
        self._name = name
        self._config = FakeConfig(name=name, then=then)
        self._state = state
        self._listeners: list = []
        self._cumulative_usd = 0.0
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
        return []

    def on_event(self, fn) -> None:
        self._listeners.append(fn)

    def emit(self, event: LoopEvent) -> None:
        for fn in self._listeners:
            fn(event)

    async def trigger(self, context: dict | None = None) -> LoopRun:
        self.trigger_calls.append(context or {})
        return LoopRun(
            run_id="run_chained",
            loop_name=self._name,
            state=LoopState.TRIGGERED,
            iteration=1,
            started_at=time.time(),
        )


def _event(loop_name: str, state: LoopState) -> LoopEvent:
    return LoopEvent(loop_name=loop_name, run_id="run_1", state=state, at=time.time())


# ---------------------------------------------------------------------------
# ChainTarget dataclass construction tests
# ---------------------------------------------------------------------------


class TestChainTargetConstruction:
    def test_defaults(self):
        ct = ChainTarget(target="reviewer")
        assert ct.target == "reviewer"
        assert ct.on == "pass"
        assert ct.max_cycles is None

    def test_explicit_on(self):
        ct = ChainTarget(target="fixer", on="fail")
        assert ct.on == "fail"

    def test_any_on(self):
        ct = ChainTarget(target="logger", on="any")
        assert ct.on == "any"

    def test_invalid_on_raises(self):
        with pytest.raises(ValueError, match="must be 'pass', 'fail', or 'any'"):
            ChainTarget(target="x", on="invalid")

    def test_max_cycles_zero_raises(self):
        with pytest.raises(ValueError, match="max_cycles must be >= 1"):
            ChainTarget(target="x", max_cycles=0)

    def test_max_cycles_negative_raises(self):
        with pytest.raises(ValueError, match="max_cycles must be >= 1"):
            ChainTarget(target="x", max_cycles=-1)


# ---------------------------------------------------------------------------
# str then = current behavior (backward compat)
# ---------------------------------------------------------------------------


class TestStrThenBackwardCompat:
    """LoopConfig.then as plain str behaves IDENTICALLY to current."""

    @pytest.mark.asyncio
    async def test_str_then_triggers_on_pass(self):
        reg = LoopRegistry()
        source = FakeLoop("source", then="target")
        target = FakeLoop("target")
        reg.register(source)
        reg.register(target)

        source.emit(_event("source", LoopState.PASS))
        await asyncio.sleep(0.05)

        assert len(target.trigger_calls) == 1
        assert target.trigger_calls[0]["chained_from"] == "source"

    @pytest.mark.asyncio
    async def test_str_then_does_not_trigger_on_fail(self):
        reg = LoopRegistry()
        source = FakeLoop("source", then="target")
        target = FakeLoop("target")
        reg.register(source)
        reg.register(target)

        source.emit(_event("source", LoopState.FAIL))
        await asyncio.sleep(0.05)

        assert len(target.trigger_calls) == 0

    @pytest.mark.asyncio
    async def test_str_then_does_not_trigger_on_handoff(self):
        reg = LoopRegistry()
        source = FakeLoop("source", then="target")
        target = FakeLoop("target")
        reg.register(source)
        reg.register(target)

        source.emit(_event("source", LoopState.HANDOFF))
        await asyncio.sleep(0.05)

        assert len(target.trigger_calls) == 0


# ---------------------------------------------------------------------------
# list[ChainTarget] routes by outcome state
# ---------------------------------------------------------------------------


class TestListThenRoutesByOutcome:
    """list[ChainTarget] routes to correct targets based on outcome."""

    @pytest.mark.asyncio
    async def test_pass_routes_to_pass_target(self):
        reg = LoopRegistry()
        source = FakeLoop(
            "source",
            then=[
                ChainTarget("reviewer", on="pass"),
                ChainTarget("fixer", on="fail"),
            ],
        )
        reviewer = FakeLoop("reviewer")
        fixer = FakeLoop("fixer")
        reg.register(source)
        reg.register(reviewer)
        reg.register(fixer)

        source.emit(_event("source", LoopState.PASS))
        await asyncio.sleep(0.05)

        assert len(reviewer.trigger_calls) == 1
        assert len(fixer.trigger_calls) == 0

    @pytest.mark.asyncio
    async def test_fail_routes_to_fail_target(self):
        reg = LoopRegistry()
        source = FakeLoop(
            "source",
            then=[
                ChainTarget("reviewer", on="pass"),
                ChainTarget("fixer", on="fail"),
            ],
        )
        reviewer = FakeLoop("reviewer")
        fixer = FakeLoop("fixer")
        reg.register(source)
        reg.register(reviewer)
        reg.register(fixer)

        source.emit(_event("source", LoopState.FAIL))
        await asyncio.sleep(0.05)

        assert len(reviewer.trigger_calls) == 0
        assert len(fixer.trigger_calls) == 1

    @pytest.mark.asyncio
    async def test_handoff_routes_to_fail_target(self):
        """HANDOFF is treated as a failure outcome for chaining."""
        reg = LoopRegistry()
        source = FakeLoop(
            "source",
            then=[ChainTarget("fixer", on="fail")],
        )
        fixer = FakeLoop("fixer")
        reg.register(source)
        reg.register(fixer)

        source.emit(_event("source", LoopState.HANDOFF))
        await asyncio.sleep(0.05)

        assert len(fixer.trigger_calls) == 1

    @pytest.mark.asyncio
    async def test_any_fires_on_pass(self):
        reg = LoopRegistry()
        source = FakeLoop("source", then=[ChainTarget("logger", on="any")])
        logger_loop = FakeLoop("logger")
        reg.register(source)
        reg.register(logger_loop)

        source.emit(_event("source", LoopState.PASS))
        await asyncio.sleep(0.05)

        assert len(logger_loop.trigger_calls) == 1

    @pytest.mark.asyncio
    async def test_any_fires_on_fail(self):
        reg = LoopRegistry()
        source = FakeLoop("source", then=[ChainTarget("logger", on="any")])
        logger_loop = FakeLoop("logger")
        reg.register(source)
        reg.register(logger_loop)

        source.emit(_event("source", LoopState.FAIL))
        await asyncio.sleep(0.05)

        assert len(logger_loop.trigger_calls) == 1

    @pytest.mark.asyncio
    async def test_multiple_targets_all_fire_fan_out(self):
        """Multiple ChainTargets matching the same state ALL fire."""
        reg = LoopRegistry()
        source = FakeLoop(
            "source",
            then=[
                ChainTarget("a", on="pass"),
                ChainTarget("b", on="pass"),
                ChainTarget("c", on="any"),
            ],
        )
        a = FakeLoop("a")
        b = FakeLoop("b")
        c = FakeLoop("c")
        reg.register(source)
        reg.register(a)
        reg.register(b)
        reg.register(c)

        source.emit(_event("source", LoopState.PASS))
        await asyncio.sleep(0.05)

        assert len(a.trigger_calls) == 1
        assert len(b.trigger_calls) == 1
        assert len(c.trigger_calls) == 1


# ---------------------------------------------------------------------------
# max_cycles prevents infinite chains
# ---------------------------------------------------------------------------


class TestMaxCyclesPreventsInfiniteChains:
    """ChainTarget.max_cycles caps how many times a target fires."""

    @pytest.mark.asyncio
    async def test_max_cycles_stops_after_limit(self):
        reg = LoopRegistry()
        source = FakeLoop(
            "source",
            then=[ChainTarget("target", on="pass", max_cycles=2)],
        )
        target = FakeLoop("target")
        reg.register(source)
        reg.register(target)

        # Fire 3 times — only first 2 should trigger
        for _ in range(3):
            source.emit(_event("source", LoopState.PASS))
            await asyncio.sleep(0.05)

        assert len(target.trigger_calls) == 2

    @pytest.mark.asyncio
    async def test_max_cycles_logs_warning(self, caplog):
        reg = LoopRegistry()
        source = FakeLoop(
            "source",
            then=[ChainTarget("target", on="pass", max_cycles=1)],
        )
        target = FakeLoop("target")
        reg.register(source)
        reg.register(target)

        # First fires, second is skipped
        source.emit(_event("source", LoopState.PASS))
        await asyncio.sleep(0.05)

        with caplog.at_level(logging.WARNING):
            source.emit(_event("source", LoopState.PASS))
            await asyncio.sleep(0.05)

        assert len(target.trigger_calls) == 1
        assert "max_cycles=1 reached" in caplog.text

    @pytest.mark.asyncio
    async def test_no_max_cycles_fires_unlimited(self):
        reg = LoopRegistry()
        source = FakeLoop(
            "source",
            then=[ChainTarget("target", on="pass")],  # no max_cycles
        )
        target = FakeLoop("target")
        reg.register(source)
        reg.register(target)

        for _ in range(10):
            source.emit(_event("source", LoopState.PASS))
            await asyncio.sleep(0.05)

        assert len(target.trigger_calls) == 10

    @pytest.mark.asyncio
    async def test_max_cycles_per_target_independent(self):
        """Each target's max_cycles is tracked independently."""
        reg = LoopRegistry()
        source = FakeLoop(
            "source",
            then=[
                ChainTarget("a", on="pass", max_cycles=1),
                ChainTarget("b", on="pass", max_cycles=3),
            ],
        )
        a = FakeLoop("a")
        b = FakeLoop("b")
        reg.register(source)
        reg.register(a)
        reg.register(b)

        for _ in range(3):
            source.emit(_event("source", LoopState.PASS))
            await asyncio.sleep(0.05)

        assert len(a.trigger_calls) == 1  # capped at 1
        assert len(b.trigger_calls) == 3  # capped at 3


# ---------------------------------------------------------------------------
# Cycle detection with list[ChainTarget]
# ---------------------------------------------------------------------------


class TestCycleDetectionWithList:
    def test_cycle_detected_in_list(self):
        reg = LoopRegistry()
        a = FakeLoop("a", then=[ChainTarget("b", on="pass")])
        b = FakeLoop("b", then=[ChainTarget("a", on="pass")])
        reg.register(a)
        with pytest.raises(ValueError, match="cycle detected"):
            reg.register(b)

    def test_no_cycle_with_different_targets(self):
        reg = LoopRegistry()
        a = FakeLoop("a", then=[ChainTarget("b", on="pass")])
        b = FakeLoop("b")
        reg.register(a)
        reg.register(b)  # should not raise
