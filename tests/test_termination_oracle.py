"""Tests for TerminationOracle — non-cooperative watchdog for stuck cycles.

Validates: Requirement 4
"""

import asyncio
import time

import pytest

from tvastar import (
    CycleEntry,
    CycleJournal,
    CyclePolicy,
    Harness,
    TaskGraph,
    TerminationOracle,
    create_agent,
)
from tvastar.model import MockModel


# ---------------------------------------------------------------------------
# Unit tests: TerminationOracle config
# ---------------------------------------------------------------------------


def test_poll_interval_clamped_to_1s():
    """poll_interval below 1s is clamped to 1.0."""
    oracle = TerminationOracle(poll_interval=0.1, max_wall_clock=10.0)
    assert oracle.poll_interval == 1.0


def test_poll_interval_preserved_when_above_1s():
    oracle = TerminationOracle(poll_interval=3.0, max_wall_clock=10.0)
    assert oracle.poll_interval == 3.0


def test_default_values():
    oracle = TerminationOracle()
    assert oracle.poll_interval == 5.0
    assert oracle.max_wall_clock == 60.0
    assert oracle.max_iterations is None


# ---------------------------------------------------------------------------
# Unit test: _poll_loop cancels task exceeding wall_clock
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_oracle_cancels_on_wall_clock_exceeded():
    """Oracle cancels a task when wall_clock exceeds max_wall_clock."""
    oracle = TerminationOracle(poll_interval=1.0, max_wall_clock=0.5)

    journal = CycleJournal()
    # Simulate a cycle that's been iterating
    journal.append(
        CycleEntry(iteration=0, timestamp=time.time() - 2.0, result_text="iter0", continued=True)
    )

    # Create a long-running task to cancel
    async def stuck_task():
        await asyncio.sleep(100)

    task = asyncio.create_task(stuck_task())
    cycle_tasks = {"edge->key": task}
    start_times = {"edge->key": time.time() - 2.0}  # started 2s ago
    journals = {"edge->key": journal}

    # Run oracle poll for one iteration then cancel it
    oracle_task = asyncio.create_task(oracle._poll_loop(journals, cycle_tasks, start_times))

    # Wait enough for at least one poll cycle
    await asyncio.sleep(1.5)
    oracle_task.cancel()
    try:
        await oracle_task
    except asyncio.CancelledError:
        pass

    # The stuck task should have been cancelled
    assert task.cancelled() or task.done()
    # Journal should record oracle_intervention
    assert journal.terminated
    assert journal.termination_reason == "oracle_intervention"


@pytest.mark.asyncio
async def test_oracle_cancels_on_max_iterations_exceeded():
    """Oracle cancels when iterations exceed max_iterations."""
    oracle = TerminationOracle(poll_interval=1.0, max_wall_clock=999.0, max_iterations=2)

    journal = CycleJournal()
    # 3 iterations (exceeds max_iterations=2)
    journal.append(CycleEntry(iteration=0, timestamp=time.time(), result_text="a", continued=True))
    journal.append(CycleEntry(iteration=1, timestamp=time.time(), result_text="b", continued=True))
    journal.append(CycleEntry(iteration=2, timestamp=time.time(), result_text="c", continued=True))

    async def stuck_task():
        await asyncio.sleep(100)

    task = asyncio.create_task(stuck_task())
    cycle_tasks = {"e->k": task}
    start_times = {"e->k": time.time()}
    journals = {"e->k": journal}

    oracle_task = asyncio.create_task(oracle._poll_loop(journals, cycle_tasks, start_times))
    await asyncio.sleep(1.5)
    oracle_task.cancel()
    try:
        await oracle_task
    except asyncio.CancelledError:
        pass

    assert task.cancelled() or task.done()
    assert journal.termination_reason == "oracle_intervention"


# ---------------------------------------------------------------------------
# Unit test: oracle failure is fail-open
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_oracle_failure_is_fail_open():
    """Oracle exception in _poll_loop doesn't propagate — fail-open."""

    class BrokenOracle(TerminationOracle):
        async def _poll_loop(self, journals, tasks, start_times):
            raise RuntimeError("oracle crashed!")

    oracle = BrokenOracle(poll_interval=1.0, max_wall_clock=10.0)

    # Simulate calling _run_oracle_poll on a graph
    agent = create_agent("t", model=MockModel(script=["ok"]))
    graph = TaskGraph(Harness(agent))
    graph.task("a", "do a")
    graph._validate()
    graph._oracle = oracle
    graph._cycle_tasks = {}
    graph._cycle_start_times = {}

    # _run_oracle_poll should catch the exception and not raise
    await graph._run_oracle_poll([None])
    # If we get here, fail-open worked


# ---------------------------------------------------------------------------
# Integration: oracle cancels a stuck cycle in a real graph run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_oracle_cancels_stuck_cycle_integration():
    """
    Full integration: oracle detects a stuck cycle and cancels it.

    **Validates: Requirement 4**

    We create a graph with a cycle that uses a slow model (0.3s per response).
    The oracle's max_wall_clock is 1.5s with 1s poll interval, so after
    ~5 iterations the oracle fires.
    """
    from tvastar.model import Model
    from tvastar.types import Message, ModelResponse, TextBlock, StopReason, Usage

    class SlowMockModel(Model):
        """Model that sleeps before responding — simulates a real LLM call."""

        name = "slow-mock"
        system = "mock"

        def __init__(self, delay: float = 0.3):
            self._delay = delay
            self._counter = 0

        async def generate(self, messages, **kwargs):
            await asyncio.sleep(self._delay)
            self._counter += 1
            text = f"iteration {self._counter}"
            msg = Message("assistant", [TextBlock(text=text)])
            return ModelResponse(
                message=msg,
                stop_reason=StopReason.END_TURN,
                usage=Usage(output_tokens=10),
            )

    model = SlowMockModel(delay=0.3)
    agent = create_agent("t", model=model)
    harness = Harness(agent)

    graph = TaskGraph(harness)
    # writer depends on reviewer with a high TTL (will never reach it)
    graph.task("writer", "write something", depends_on=[("reviewer", CyclePolicy.ALLOW_TTL(100))])
    graph.task("reviewer", "review it", depends_on=["writer"])

    # Attach oracle with very short wall_clock to trigger cancellation
    oracle = TerminationOracle(poll_interval=1.0, max_wall_clock=1.5)
    graph.attach_oracle(oracle)

    await graph.run()

    # The cycle should have been terminated by oracle
    assert len(graph._cycle_journals) == 1
    journal = list(graph._cycle_journals.values())[0]
    assert journal.terminated
    assert journal.termination_reason == "oracle_intervention"


# ---------------------------------------------------------------------------
# Integration: oracle failure doesn't crash graph
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_oracle_failure_doesnt_crash_graph():
    """
    Graph completes normally even when oracle raises during poll.

    **Validates: Requirement 4**
    """

    class CrashingOracle(TerminationOracle):
        """Oracle that raises on first poll."""

        async def _poll_loop(self, journals, tasks, start_times):
            raise RuntimeError("oracle internal error!")

    agent = create_agent("t", model=MockModel(script=["r1", "r2", "r3"]))
    harness = Harness(agent)

    graph = TaskGraph(harness)
    graph.task("writer", "write", depends_on=[("reviewer", CyclePolicy.ALLOW_TTL(2))])
    graph.task("reviewer", "review", depends_on=["writer"])
    graph.attach_oracle(CrashingOracle(poll_interval=1.0, max_wall_clock=10.0))

    # Graph should complete without raising
    await graph.run()
    # The cycle terminates via TTL (oracle died, fail-open)
    journal = list(graph._cycle_journals.values())[0]
    assert journal.terminated
    assert journal.termination_reason == "ttl_reached"


# ---------------------------------------------------------------------------
# attach_oracle returns self (fluent API)
# ---------------------------------------------------------------------------


def test_attach_oracle_returns_self():
    agent = create_agent("t", model=MockModel(script=["ok"]))
    graph = TaskGraph(Harness(agent))
    oracle = TerminationOracle()
    assert graph.attach_oracle(oracle) is graph
    assert graph._oracle is oracle
