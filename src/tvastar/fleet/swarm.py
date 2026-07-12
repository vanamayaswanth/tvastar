"""Swarm coordination — orchestrator composing Workers + Coordinator + SignalBus.

This module is part of the opt-in Swarm architecture. Importing it has no
side effects on existing Loop/Fleet behavior.

Zero runtime dependencies (stdlib only). Python 3.10+.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from tvastar.fleet.checkpointer import Checkpointer
from tvastar.fleet.coordinator import Coordinator
from tvastar.fleet.models import CheckpointerConfig, EscalationRule, SwarmResult
from tvastar.fleet.signal_bus import SignalBus

if TYPE_CHECKING:
    from tvastar.fleet.bus import EventBus
    from tvastar.memory.store import Store

__all__ = ["EscalationPolicy", "Swarm"]


@dataclass
class EscalationPolicy:
    """Configuration for the Loop escalation pathway (Swarm architecture).

    When attached to a LoopConfig, the Loop writes an escalation to SignalBus
    instead of transitioning to HANDOFF on retries exhausted.
    """

    signal_bus: "SignalBus"
    escalation_timeout: float = 60.0  # seconds to wait for directive
    namespace: str = ""  # auto-set by Swarm to worker_id


class Swarm:
    """Top-level orchestrator composing Coordinator + Workers + SignalBus + Checkpointer.

    Creates a SignalBus, a Coordinator, per-worker EscalationPolicy instances,
    and optionally a Checkpointer (when a Store is provided). Runs all tasks
    concurrently and aggregates results into a SwarmResult.

    Parameters
    ----------
    goal:
        The goal to accomplish (published to SignalBus for workers to read).
    tasks:
        Callables representing the work each worker does. Each is awaited
        concurrently during ``run()``.
    store:
        Optional Store backend for checkpoint persistence. When provided, a
        Checkpointer is created to periodically snapshot SignalBus state.
    rules:
        Optional custom escalation rules for the Coordinator. When None,
        DEFAULT_RULES are used.
    escalation_timeout:
        Per-worker escalation timeout in seconds (default 60).
    checkpoint_interval:
        Checkpointer interval in seconds (default 30).
    event_bus:
        Optional EventBus for observability — SignalBus forwards writes to it.
    clock:
        Optional clock callable for deterministic timestamps (test hook).
    """

    def __init__(
        self,
        goal: str,
        tasks: list[Callable[..., Awaitable[Any]]],
        *,
        store: Store | None = None,
        rules: list[EscalationRule] | None = None,
        escalation_timeout: float = 60.0,
        checkpoint_interval: float = 30.0,
        event_bus: EventBus | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._goal = goal
        self._tasks = tasks
        self._clock = clock or time.monotonic

        # Compose: SignalBus → Coordinator → EscalationPolicies → Checkpointer
        self._signal_bus = SignalBus(clock=clock, event_bus=event_bus)
        self._coordinator = Coordinator(self._signal_bus, rules=rules)

        self._escalation_policies = [
            EscalationPolicy(
                signal_bus=self._signal_bus,
                escalation_timeout=escalation_timeout,
                namespace=f"worker_{i}",
            )
            for i in range(len(tasks))
        ]

        self._checkpointer: Checkpointer | None = None
        if store is not None:
            self._checkpointer = Checkpointer(
                self._signal_bus,
                store,
                CheckpointerConfig(interval=checkpoint_interval),
            )

    @property
    def signal_bus(self) -> SignalBus:
        """The internal SignalBus instance (useful for testing/inspection)."""
        return self._signal_bus

    async def run(self) -> SwarmResult:
        """Execute the swarm: publish goal, run workers, aggregate results.

        Steps:
        1. Restore checkpoint (if Checkpointer exists and Store has data)
        2. Publish goal via Coordinator
        3. Start Checkpointer (if exists)
        4. Start Coordinator watch loop as background task
        5. Run all tasks concurrently
        6. Stop Coordinator + Checkpointer, do final checkpoint
        7. Return SwarmResult
        """
        start_time = self._clock()

        # 1. Restore from checkpoint if available.
        if self._checkpointer is not None:
            self._checkpointer.restore()

        # 2. Publish goal.
        await self._coordinator.publish_goal(self._goal)

        # 3. Start checkpointer.
        if self._checkpointer is not None:
            await self._checkpointer.start()

        # 4. Start coordinator watch loop as background task.
        watch_task = asyncio.create_task(self._coordinator.watch_and_respond())

        # 5. Run all workers concurrently.
        results = await asyncio.gather(
            *(self._run_worker(i, task) for i, task in enumerate(self._tasks)),
            return_exceptions=True,
        )

        # 6. Stop coordinator + checkpointer, final checkpoint.
        watch_task.cancel()
        try:
            await watch_task
        except asyncio.CancelledError:
            pass
        await self._coordinator.stop()

        if self._checkpointer is not None:
            await self._checkpointer.stop()
            try:
                await self._checkpointer.checkpoint_now()
            except Exception:
                pass  # best-effort final checkpoint

        # 7. Aggregate and return SwarmResult.
        worker_results: dict[str, Any] = {}
        worker_states: dict[str, str] = {}

        for i, result in enumerate(results):
            ns = f"worker_{i}"
            if isinstance(result, BaseException):
                worker_results[ns] = {"error": str(result)}
                worker_states[ns] = "failed"
            else:
                worker_results[ns] = result
                worker_states[ns] = "done"

        # Read final states from SignalBus (if workers wrote them).
        for i in range(len(self._tasks)):
            ns = f"worker_{i}"
            status = self._signal_bus.read(ns, "status")
            if status is not None:
                worker_states[ns] = status

        duration = self._clock() - start_time
        return SwarmResult(
            goal=self._goal,
            worker_results=worker_results,
            worker_states=worker_states,
            duration=duration,
        )

    async def _run_worker(self, index: int, task: Callable[..., Awaitable[Any]]) -> Any:
        """Execute a single worker task and record status to SignalBus.

        Parameters
        ----------
        index:
            Worker index (used for namespace ``worker_{index}``).
        task:
            Async callable representing the worker's work.

        Returns
        -------
        The result of awaiting the task, or raises if the task raises.
        """
        ns = f"worker_{index}"

        # Write initial running status.
        await self._signal_bus.write(ns, "status", "running")

        try:
            result = await task()
            await self._signal_bus.write(ns, "status", "done")
            return result
        except Exception as exc:
            await self._signal_bus.write(ns, "status", "failed")
            raise exc
