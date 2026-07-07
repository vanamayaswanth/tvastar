"""Loop Registry — thread-safe coordination layer for multiple Loop instances.

Werner principle: everything that can be discovered should be discoverable.
A registry is a phonebook, not a god object. It coordinates; it does not control.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from . import Loop, LoopEvent

logger = logging.getLogger(__name__)


@dataclass
class RegistryMetrics:
    """Aggregate metrics across all registered loops."""

    total_runs: int
    total_passes: int
    total_fails: int
    total_cost_usd: float


class LoopRegistry:
    """Thread-safe, async-compatible registry for Loop instances.

    All public methods are callable from both sync and async contexts.
    Mutations (register, unregister) are serialized via threading.Lock.
    Reads (get, all) are lock-free — dict ops are atomic in CPython.
    """

    def __init__(self) -> None:
        self._loops: dict[str, Loop] = {}
        self._lock = threading.Lock()
        self._listeners: list[Callable[[LoopEvent], None]] = []

    def register(self, loop: "Loop") -> None:
        """Register a loop. Raises ValueError on duplicate name or cycle."""
        with self._lock:
            if loop.name in self._loops:
                raise ValueError(f"Loop '{loop.name}' already registered")
            # Cycle detection before committing
            if loop.config.then is not None:
                self._detect_cycle(loop)
            self._loops[loop.name] = loop
            # Forward events from this loop to all registry listeners
            loop.on_event(self._broadcast)

    def unregister(self, name: str) -> None:
        """Unregister a loop by name. Raises KeyError if not found."""
        with self._lock:
            if name not in self._loops:
                raise KeyError(f"Loop '{name}' not found in registry")
            del self._loops[name]

    def get(self, name: str) -> "Loop | None":
        """Retrieve a loop by name, or None if not found."""
        return self._loops.get(name)

    def all(self) -> "dict[str, Loop]":
        """Return a snapshot of all registered loops as {name: Loop}."""
        return dict(self._loops)

    def metrics(self) -> RegistryMetrics:
        """Compute aggregate metrics from all registered loops' histories."""
        from . import LoopState

        total_runs = 0
        total_passes = 0
        total_fails = 0
        total_cost = 0.0
        for loop in self._loops.values():
            history = loop.history(limit=200)
            total_runs += len(history)
            total_passes += sum(1 for r in history if r.state == LoopState.PASS)
            total_fails += sum(
                1
                for r in history
                if r.state
                in (LoopState.FAIL, LoopState.HANDOFF, LoopState.HANDOFF_FAILED)
            )
            total_cost += loop._cumulative_usd
        return RegistryMetrics(total_runs, total_passes, total_fails, total_cost)

    def on_event(self, fn: "Callable[[LoopEvent], None]") -> None:
        """Register a listener called on every event from any registered loop."""
        self._listeners.append(fn)

    def _broadcast(self, event: "LoopEvent") -> None:
        """Forward an event to all registry-level listeners, then handle chaining."""
        from . import LoopState

        for fn in self._listeners:
            try:
                fn(event)
            except Exception:
                pass  # ponytail: listener failure must never break the loop

        # Chain trigger: on PASS, fire the `then` target
        if event.state == LoopState.PASS:
            source = self._loops.get(event.loop_name)
            if source and source.config.then:
                self._chain_trigger(source, event)

    # ------------------------------------------------------------------
    # Chain trigger
    # ------------------------------------------------------------------

    def _chain_trigger(self, source: "Loop", event: "LoopEvent") -> None:
        """Trigger the chained target loop on source PASS."""
        from . import LoopEvent as _LE, LoopState

        target_name = source.config.then
        target = self.get(target_name)

        if target is None:
            # Warning: target not found — emit event, skip
            warning = _LE(
                loop_name=source.name,
                run_id=event.run_id,
                state=LoopState.IDLE,
                at=time.time(),
                data={
                    "warning": "chain_target_missing",
                    "target": target_name,
                },
            )
            self._broadcast_warning(warning)
            return

        if target.state == LoopState.SUSPENDED:
            # Warning: target suspended — emit event, skip
            warning = _LE(
                loop_name=source.name,
                run_id=event.run_id,
                state=LoopState.IDLE,
                at=time.time(),
                data={
                    "warning": "chain_target_suspended",
                    "target": target_name,
                },
            )
            self._broadcast_warning(warning)
            return

        # Fire the target loop within 1s (async, fire-and-forget)
        context = {"chained_from": source.name, "source_run_id": event.run_id}

        async def _fire() -> None:
            await asyncio.sleep(0)  # yield, triggers promptly (well within 1s)
            try:
                await target.trigger(context=context)
            except Exception:
                pass  # ponytail: chain trigger failure is non-fatal

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_fire())
        except RuntimeError:
            pass  # no event loop — skip silently

    def _broadcast_warning(self, event: "LoopEvent") -> None:
        """Emit a warning event to all listeners (no chain recursion)."""
        for fn in self._listeners:
            try:
                fn(event)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Cycle detection (DFS at registration time)
    # ------------------------------------------------------------------

    def _detect_cycle(self, new_loop: "Loop") -> None:
        """Detect chain cycles via DFS. Raises ValueError with cycle path."""
        # Build adjacency: name → then target
        graph: dict[str, str | None] = {}
        for lp in self._loops.values():
            graph[lp.name] = lp.config.then
        graph[new_loop.name] = new_loop.config.then

        # Walk the chain from new_loop following `then` links
        visited: set[str] = set()
        path: list[str] = []
        current: str | None = new_loop.name

        while current and current in graph:
            if current in visited:
                cycle_start = path.index(current)
                cycle = path[cycle_start:] + [current]
                raise ValueError(
                    f"Loop chain cycle detected: {' → '.join(cycle)}"
                )
            visited.add(current)
            path.append(current)
            current = graph.get(current)

    # ------------------------------------------------------------------
    # Dunder
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._loops)

    def __contains__(self, name: str) -> bool:
        return name in self._loops
