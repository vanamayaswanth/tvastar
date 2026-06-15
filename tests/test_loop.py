"""Tests for tvastar.loop — Werner-hardened loop engineering layer."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from io import StringIO

import pytest

from tvastar import create_agent
from tvastar.loop import FailureKind, Loop, LoopConfig, LoopRun, LoopState
from tvastar.loop.handoff import CallbackHandoff, LogHandoff, MultiHandoff
from tvastar.loop.schedule import next_run_time
from tvastar.model.mock import MockModel

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _loop(
    responses: list[str] | None = None,
    goal: str = "do work",
    max_iterations: int = 3,
    handoff=None,
) -> Loop:
    model = MockModel(responses or ["Work complete. SUCCESS"])
    spec = create_agent("test", model=model, instructions="", detect=False)
    config = LoopConfig(
        name="test-loop",
        goal=goal,
        schedule="@manual",
        max_iterations=max_iterations,
        handoff=handoff,
    )
    return Loop(spec, config)


def _run(state: LoopState = LoopState.HANDOFF) -> LoopRun:
    return LoopRun(
        run_id="run_abc",
        loop_name="test-loop",
        state=state,
        iteration=1,
        started_at=time.time(),
    )


# ---------------------------------------------------------------------------
# LoopConfig validation (fail at construction, not at 2am)
# ---------------------------------------------------------------------------


def test_loop_config_rejects_empty_name():
    with pytest.raises(ValueError, match="name"):
        LoopConfig(name="", goal="do work")


def test_loop_config_rejects_empty_goal():
    with pytest.raises(ValueError, match="goal"):
        LoopConfig(name="x", goal="")


def test_loop_config_rejects_zero_iterations():
    with pytest.raises(ValueError, match="max_iterations"):
        LoopConfig(name="x", goal="y", max_iterations=0)


def test_loop_config_rejects_invalid_cron():
    with pytest.raises(ValueError, match="schedule"):
        LoopConfig(name="x", goal="y", schedule="not-a-cron")


def test_loop_config_accepts_valid_cron():
    cfg = LoopConfig(name="x", goal="y", schedule="*/15 * * * *")
    assert cfg.schedule == "*/15 * * * *"


def test_loop_config_defaults():
    cfg = LoopConfig(name="x", goal="y")
    assert cfg.schedule == "@manual"
    assert cfg.max_iterations == 3
    assert cfg.retry_backoff_base == 30.0
    assert cfg.circuit_breaker_limit == 5


# ---------------------------------------------------------------------------
# Happy path lifecycle
# ---------------------------------------------------------------------------


async def test_trigger_pass_sets_correct_state():
    loop = _loop()
    run = await loop.trigger()
    assert run.ok
    assert run.state == LoopState.PASS
    assert loop.state == LoopState.PASS


async def test_trigger_resets_iteration_counter_on_pass():
    loop = _loop()
    loop._iteration = 2  # simulate previous failures
    await loop.trigger()
    assert loop._iteration == 0


async def test_trigger_records_timing():
    loop = _loop()
    run = await loop.trigger()
    assert run.run_id.startswith("run_")
    assert run.started_at > 0
    assert run.ended_at is not None
    assert run.ended_at >= run.started_at
    assert run.duration is not None and run.duration >= 0


async def test_history_accumulates():
    loop = _loop()
    await loop.trigger()
    await loop.trigger()
    assert len(loop.history()) == 2


async def test_last_run_returns_most_recent():
    loop = _loop()
    await loop.trigger()
    run2 = await loop.trigger()
    assert loop.last_run() is run2


async def test_consecutive_failures_reset_on_pass():
    loop = _loop()
    loop._consecutive_failures = 3
    await loop.trigger()
    assert loop._consecutive_failures == 0


# ---------------------------------------------------------------------------
# State guard — no concurrent runs
# ---------------------------------------------------------------------------


async def test_trigger_raises_when_running():
    loop = _loop()
    loop._state = LoopState.RUNNING
    with pytest.raises(RuntimeError, match="already"):
        await loop.trigger()
    loop._state = LoopState.IDLE


async def test_trigger_raises_when_suspended():
    loop = _loop()
    loop._state = LoopState.SUSPENDED
    with pytest.raises(RuntimeError, match="SUSPENDED"):
        await loop.trigger()


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------


def test_reset_clears_suspended():
    loop = _loop()
    loop._state = LoopState.SUSPENDED
    loop._consecutive_failures = 10
    loop.reset()
    assert loop.state == LoopState.IDLE
    assert loop._consecutive_failures == 0


# ---------------------------------------------------------------------------
# Event listeners
# ---------------------------------------------------------------------------


async def test_on_event_receives_all_transitions():
    states = []
    loop = _loop()
    loop.on_event(lambda e: states.append(e.state))
    await loop.trigger()
    assert LoopState.TRIGGERED in states
    assert LoopState.RUNNING in states
    assert LoopState.VERIFYING in states
    assert LoopState.PASS in states


async def test_on_event_listener_exception_does_not_crash_loop():
    def bad_listener(e):
        raise RuntimeError("listener bug")

    loop = _loop()
    loop.on_event(bad_listener)
    run = await loop.trigger()
    assert run.ok  # loop still completes despite bad listener


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------


def test_build_prompt_includes_goal():
    loop = _loop(goal="Fix the red build")
    assert "Fix the red build" in loop._build_prompt({})


def test_build_prompt_includes_context():
    loop = _loop()
    assert "main" in loop._build_prompt({"branch": "main"})


def test_build_prompt_includes_iteration_on_retry():
    loop = _loop()
    loop._iteration = 2
    prompt = loop._build_prompt({})
    assert "attempt 2" in prompt.lower() or "iteration 2" in prompt.lower()


# ---------------------------------------------------------------------------
# Forced-failure helpers for retry / handoff / circuit breaker tests
# ---------------------------------------------------------------------------


async def _force_fail(loop: Loop) -> LoopRun:
    """Trigger loop and force FAIL by patching _run_iteration."""
    original = loop._run_iteration

    async def fail_once(run, ctx):
        async with loop._lock:
            loop._set(run, LoopState.RUNNING)
            loop._set(run, LoopState.VERIFYING)
            loop._set(run, LoopState.FAIL)
            run.failure_kind = FailureKind.LOGIC_ERROR
            await loop._handle_fail(run)

    loop._run_iteration = fail_once
    run = await loop.trigger()
    loop._run_iteration = original
    return run


# ---------------------------------------------------------------------------
# Retry + backoff
# ---------------------------------------------------------------------------


async def test_fail_enters_retry_state_before_max():
    loop = _loop(max_iterations=3)
    run = await _force_fail(loop)
    assert run.state == LoopState.RETRY
    assert run.retry_after is not None
    assert run.retry_after > time.time()


async def test_backoff_increases_with_iteration():
    loop = _loop(max_iterations=3)
    loop._config.retry_backoff_base = 10.0

    loop._iteration = 1
    run1 = _run()
    async with loop._lock:
        await loop._handle_fail(run1)
    backoff1 = run1.retry_after - time.time()

    loop._iteration = 2
    run2 = _run()
    async with loop._lock:
        await loop._handle_fail(run2)
    backoff2 = run2.retry_after - time.time()

    assert backoff2 > backoff1  # 20s > 10s


# ---------------------------------------------------------------------------
# Handoff
# ---------------------------------------------------------------------------


async def test_handoff_fires_after_max_iterations():
    calls = []

    async def my_handoff(run, history):
        calls.append(run)

    loop = _loop(max_iterations=1, handoff=CallbackHandoff(fn=my_handoff))
    await _force_fail(loop)
    await asyncio.sleep(0.1)  # let handoff task fire
    assert len(calls) == 1
    assert calls[0].loop_name == "test-loop"


async def test_handoff_state_after_max_iterations():
    loop = _loop(max_iterations=1)
    await _force_fail(loop)
    await asyncio.sleep(0.1)
    assert loop.state in (LoopState.HANDOFF, LoopState.IDLE)


async def test_log_handoff_writes_to_stream():
    buf = StringIO()
    h = LogHandoff(stream=buf)
    run = _run()
    await h.escalate(run, [])
    output = buf.getvalue()
    assert "LOOP HANDOFF" in output
    assert "test-loop" in output


async def test_log_handoff_includes_error():
    buf = StringIO()
    h = LogHandoff(stream=buf)
    run = _run()
    run.error = "connection refused"
    await h.escalate(run, [])
    assert "connection refused" in buf.getvalue()


async def test_multi_handoff_fires_all():
    calls = []

    async def h1(run, hist):
        calls.append("h1")

    async def h2(run, hist):
        calls.append("h2")

    mh = MultiHandoff(policies=[CallbackHandoff(h1), CallbackHandoff(h2)])
    await mh.escalate(_run(), [])
    assert calls == ["h1", "h2"]


async def test_multi_handoff_raises_on_failure():
    async def bad(run, hist):
        raise RuntimeError("pager down")

    mh = MultiHandoff(policies=[CallbackHandoff(bad)])
    with pytest.raises(RuntimeError, match="pager down"):
        await mh.escalate(_run(), [])


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------


async def test_circuit_breaker_triggers_after_limit():
    calls = []

    async def handoff(run, hist):
        calls.append(1)

    loop = _loop(max_iterations=1, handoff=CallbackHandoff(fn=handoff))
    loop._config.circuit_breaker_limit = 2

    # First HANDOFF — consecutive_failures=1, not yet at limit
    loop._iteration = 0
    await _force_fail(loop)
    await asyncio.sleep(0.1)
    loop._state = LoopState.IDLE  # human resets; allows second trigger

    # Second HANDOFF — consecutive_failures=2, hits limit → SUSPENDED
    loop._iteration = 0
    await _force_fail(loop)
    await asyncio.sleep(0.1)
    # Do NOT reset state here — circuit breaker should have engaged
    assert loop.state == LoopState.SUSPENDED
    assert len(calls) == 2


# ---------------------------------------------------------------------------
# Crash recovery
# ---------------------------------------------------------------------------


async def test_recovery_detects_interrupted_run():
    import json

    from tvastar.memory.store import InMemoryStore

    store = InMemoryStore()
    # Simulate a run that was RUNNING when the process crashed
    store.set(
        "loop:test-loop:last_run",
        json.dumps(
            {
                "run_id": "run_crashed",
                "state": "running",
                "iteration": 2,
                "started_at": time.time() - 300,
            }
        ),
    )
    model = MockModel(["ok"])
    spec = create_agent("test", model=model, instructions="", detect=False)
    config = LoopConfig(name="test-loop", goal="do work", schedule="@manual")
    loop = Loop(spec, config, store=store)

    # Should have detected the orphaned run and added it to history
    history = loop.history()
    interrupted = [r for r in history if r.state == LoopState.INTERRUPTED]
    assert len(interrupted) == 1
    assert interrupted[0].run_id == "run_crashed"


# ---------------------------------------------------------------------------
# Scheduler — start / stop
# ---------------------------------------------------------------------------


async def test_start_manual_schedule_is_noop():
    loop = _loop()
    await loop.start()
    assert loop._task is None  # @manual — no background task


async def test_stop_is_idempotent():
    loop = _loop()
    await loop.stop()
    await loop.stop()  # must not raise


async def test_stop_cancels_scheduler():
    model = MockModel(["ok"] * 100)
    spec = create_agent("t", model=model, instructions="", detect=False)
    config = LoopConfig(name="sched-loop", goal="work", schedule="@hourly")
    loop = Loop(spec, config)
    await loop.start()
    assert loop._task is not None
    await loop.stop()
    assert loop._task is None


# ---------------------------------------------------------------------------
# schedule.next_run_time
# ---------------------------------------------------------------------------


def test_schedule_hourly():
    now = datetime(2026, 6, 15, 10, 30, tzinfo=timezone.utc)
    nxt = next_run_time("@hourly", now)
    assert nxt.hour == 11
    assert nxt.minute == 0


def test_schedule_daily():
    now = datetime(2026, 6, 15, 10, 30, tzinfo=timezone.utc)
    nxt = next_run_time("@daily", now)
    assert nxt.hour == 0 and nxt.minute == 0
    assert nxt.day == 16


def test_schedule_every_15_min():
    now = datetime(2026, 6, 15, 10, 7, tzinfo=timezone.utc)
    nxt = next_run_time("*/15 * * * *", now)
    assert nxt.minute == 15 and nxt.hour == 10


def test_schedule_specific_time_tomorrow():
    now = datetime(2026, 6, 15, 10, 0, tzinfo=timezone.utc)
    nxt = next_run_time("0 9 * * *", now)  # 9am — already passed today
    assert nxt.hour == 9 and nxt.day == 16


def test_schedule_weekday_only():
    # 0 9 * * 1-5 = 9am Mon–Fri. Pick a Friday.
    now = datetime(2026, 6, 12, 9, 1, tzinfo=timezone.utc)  # Friday 09:01
    nxt = next_run_time("0 9 * * 1-5", now)
    assert nxt.weekday() < 5  # Mon–Fri
    assert nxt.hour == 9 and nxt.minute == 0


def test_schedule_manual_raises():
    with pytest.raises(ValueError, match="@manual"):
        next_run_time("@manual", datetime.now(tz=timezone.utc))


def test_schedule_invalid_raises():
    with pytest.raises(ValueError):
        next_run_time("bad expr", datetime.now(tz=timezone.utc))


def test_schedule_five_field_invalid_raises():
    with pytest.raises(ValueError):
        next_run_time("99 * * * *", datetime.now(tz=timezone.utc))


# ---------------------------------------------------------------------------
# Patterns — smoke tests
# ---------------------------------------------------------------------------


def test_ci_sweeper_instantiates():
    from tvastar.loop.patterns import CISweeper

    loop = CISweeper(model=MockModel(["CI green. SUCCESS"]), schedule="@manual")
    assert loop.name == "ci-sweeper"
    assert loop.config.max_iterations == 3
    assert loop.config.schedule == "@manual"


def test_pr_babysitter_instantiates():
    from tvastar.loop.patterns import PRBabysitter

    loop = PRBabysitter(model=MockModel(["PRs ok. SUCCESS"]), schedule="@manual")
    assert loop.name == "pr-babysitter"


def test_daily_triage_instantiates():
    from tvastar.loop.patterns import DailyTriage

    loop = DailyTriage(model=MockModel(["No new issues. SUCCESS"]), schedule="@manual")
    assert loop.name == "daily-triage"


def test_dependency_sweeper_instantiates():
    from tvastar.loop.patterns import DependencySweeper

    loop = DependencySweeper(model=MockModel(["Deps current. SUCCESS"]), schedule="@manual")
    assert loop.name == "dependency-sweeper"


def test_post_merge_cleanup_instantiates():
    from tvastar.loop.patterns import PostMergeCleanup

    loop = PostMergeCleanup(model=MockModel(["Cleanup done. SUCCESS"]), schedule="@manual")
    assert loop.name == "post-merge-cleanup"


def test_changelog_drafter_instantiates():
    from tvastar.loop.patterns import ChangelogDrafter

    loop = ChangelogDrafter(model=MockModel(["Draft written. SUCCESS"]), schedule="@manual")
    assert loop.name == "changelog-drafter"


async def test_ci_sweeper_trigger_pass():
    from tvastar.loop.patterns import CISweeper

    loop = CISweeper(
        model=MockModel(["All 266 tests pass. Build is green. SUCCESS"]),
        schedule="@manual",
    )
    run = await loop.trigger()
    assert run.ok
    assert run.state == LoopState.PASS


def test_pattern_extra_instructions_appended():
    from tvastar.loop.patterns import CISweeper

    loop = CISweeper(
        model=MockModel(["ok"]),
        schedule="@manual",
        extra_instructions="Always run: pytest -x --tb=short",
    )
    assert "pytest -x" in loop._harness.spec.instructions


# ---------------------------------------------------------------------------
# Top-level tvastar exports
# ---------------------------------------------------------------------------


def test_loop_exported_from_tvastar():
    from tvastar import (
        FailureKind,
        Loop,
        LoopConfig,
        LoopEvent,
        LoopRun,
        LoopState,
    )

    assert all(
        x is not None for x in [Loop, LoopConfig, LoopState, LoopRun, LoopEvent, FailureKind]
    )


def test_handoff_exported_from_tvastar():
    from tvastar import CallbackHandoff, HandoffPolicy, LogHandoff, MultiHandoff

    assert all(x is not None for x in [HandoffPolicy, LogHandoff, CallbackHandoff, MultiHandoff])


def test_patterns_exported_from_tvastar():
    from tvastar import (
        ChangelogDrafter,
        CISweeper,
        DailyTriage,
        DependencySweeper,
        PostMergeCleanup,
        PRBabysitter,
    )

    assert all(
        x is not None
        for x in [
            CISweeper,
            PRBabysitter,
            DailyTriage,
            DependencySweeper,
            PostMergeCleanup,
            ChangelogDrafter,
        ]
    )
