"""Reactive namespaced key-value store for Swarm coordination.

SignalBus is the core coordination primitive: a namespaced KV store with
per-namespace monotonic timestamps, last-writer-wins reads, bounded buffers,
prefix-based async watches, and optional EventBus forwarding for observability.

Zero runtime dependencies — stdlib only (asyncio, time, typing, logging).
Python 3.10+.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

from tvastar.fleet.models import Entry

if TYPE_CHECKING:
    from tvastar.fleet.bus import EventBus

__all__ = ["SignalBus"]

logger = logging.getLogger(__name__)

# Smallest increment to guarantee strict monotonicity when clock returns same value.
_EPSILON = 1e-9


@dataclass
class _Watcher:
    """Internal bookkeeping for a single watch consumer."""

    prefix: str
    queue: asyncio.Queue[Entry]
    active: bool = True


class SignalBus:
    """Reactive namespaced key-value store for decoupled Swarm coordination.

    Stores entries keyed by (namespace, key) with per-namespace monotonic
    timestamps. Reads return the value with the highest timestamp (last-writer-wins).
    Each namespace has a bounded buffer — oldest entries are dropped when capacity
    is reached.

    Parameters
    ----------
    max_entries_per_namespace:
        Maximum entries stored per namespace. Oldest dropped on overflow.
    max_queue_per_consumer:
        Maximum pending entries in a watch queue before dropping (used by watch system).
    clock:
        Optional callable returning a float timestamp. Defaults to ``time.monotonic``.
        Injected for deterministic testing.
    event_bus:
        Optional EventBus reference. When set, every write is forwarded as a
        FleetEvent to topic ``"signal_bus.write"`` (best-effort, never blocking).
    """

    def __init__(
        self,
        *,
        max_entries_per_namespace: int = 1000,
        max_queue_per_consumer: int = 100,
        clock: Callable[[], float] | None = None,
        event_bus: EventBus | None = None,
    ) -> None:
        self._max_entries = max_entries_per_namespace
        self._max_queue = max_queue_per_consumer
        self._clock = clock or time.monotonic
        self._event_bus = event_bus

        # Internal storage: namespace → list of Entry, maintained sorted by timestamp.
        self._store: dict[str, list[Entry]] = {}

        # Per-namespace monotonic counter (last assigned timestamp).
        self._counters: dict[str, float] = {}

        # Watch consumers — each watcher has a prefix and a bounded queue.
        self._watchers: list[_Watcher] = []

    def _next_timestamp(self, namespace: str) -> float:
        """Return a strictly increasing timestamp for the given namespace."""
        raw = self._clock()
        last = self._counters.get(namespace, -1.0)
        # Ensure strict monotonicity: must be greater than last assigned.
        ts = max(raw, last + _EPSILON)
        self._counters[namespace] = ts
        return ts

    def _notify_watchers(self, entry: Entry) -> None:
        """Push entry to all active watchers whose prefix matches. Best-effort, never blocks."""
        for watcher in self._watchers:
            if not watcher.active:
                continue
            if not entry.namespace.startswith(watcher.prefix):
                continue
            try:
                watcher.queue.put_nowait(entry)
            except asyncio.QueueFull:
                logger.warning(
                    "Watch queue full for prefix %r — dropping entry (%s, %s, ts=%.9f)",
                    watcher.prefix,
                    entry.namespace,
                    entry.key,
                    entry.timestamp,
                )

    async def write(self, namespace: str, key: str, value: Any) -> Entry:
        """Write a value to the signal bus under (namespace, key).

        Assigns a monotonic timestamp, stores the entry, enforces the
        per-namespace capacity bound (drop-oldest on overflow), notifies
        matching watchers, and optionally forwards the write to an attached
        EventBus.

        Returns the stored Entry.
        """
        ts = self._next_timestamp(namespace)
        entry = Entry(namespace=namespace, key=key, value=value, timestamp=ts)

        entries = self._store.setdefault(namespace, [])

        # Enforce bounded buffer: drop oldest if at capacity.
        if len(entries) >= self._max_entries:
            entries.pop(0)

        # Append — entries are always in timestamp order since timestamps are monotonic.
        entries.append(entry)

        # Notify watchers (best-effort: drop on full queue, never block writer).
        self._notify_watchers(entry)

        # Best-effort EventBus forwarding (never blocking, never raising).
        if self._event_bus is not None:
            try:
                self._event_bus.publish(
                    "signal_bus.write",
                    {
                        "namespace": namespace,
                        "key": key,
                        "value": value,
                        "timestamp": ts,
                    },
                    source_agent="signal_bus",
                )
            except Exception:
                pass  # best-effort: swallow all errors

        return entry

    def read(self, namespace: str, key: str) -> Any | None:
        """Read the latest value for (namespace, key).

        Returns the value from the entry with the highest timestamp matching
        the given namespace and key, or None if no such entry exists.

        This is a synchronous lookup — no I/O involved.
        """
        entries = self._store.get(namespace)
        if not entries:
            return None

        # Iterate in reverse (highest timestamp first) for efficiency.
        for entry in reversed(entries):
            if entry.key == key:
                return entry.value

        return None

    async def watch(self, namespace_prefix: str) -> AsyncIterator[Entry]:
        """Watch for entries whose namespace starts with *namespace_prefix*.

        Returns an async iterator that yields Entry objects as they are written.
        Each consumer gets its own bounded queue (max_queue_per_consumer).
        When the queue is full, new entries for this consumer are dropped with a
        warning — the writer is never blocked.

        Usage::

            async for entry in signal_bus.watch("worker_"):
                print(entry)

        To stop watching, break out of the loop or let the generator be garbage-collected.
        """
        watcher = _Watcher(
            prefix=namespace_prefix,
            queue=asyncio.Queue(maxsize=self._max_queue),
        )
        self._watchers.append(watcher)
        try:
            while watcher.active:
                entry = await watcher.queue.get()
                watcher.queue.task_done()
                yield entry
        finally:
            # Cleanup: mark inactive and remove from list.
            watcher.active = False
            try:
                self._watchers.remove(watcher)
            except ValueError:
                pass  # already removed

    def _unwatch(self, watcher: _Watcher) -> None:
        """Stop a watcher. Marks it inactive and removes from the list."""
        watcher.active = False
        try:
            self._watchers.remove(watcher)
        except ValueError:
            pass

    async def flush(self) -> None:
        """Drain all pending notifications — waits until every watcher queue is empty.

        Test hook for deterministic testing. Ensures all queued entries have been
        consumed before returning.
        """
        for watcher in list(self._watchers):
            if watcher.active:
                await watcher.queue.join()

    def snapshot(self) -> dict:
        """Serialize all internal state to a JSON-compatible dict.

        Returns a snapshot containing all namespaces, their entries, and
        per-namespace monotonic counters. Used by Checkpointer for persistence.
        """
        namespaces: dict[str, list[dict]] = {}
        for ns, entries in self._store.items():
            namespaces[ns] = [
                {"namespace": e.namespace, "key": e.key, "value": e.value, "timestamp": e.timestamp}
                for e in entries
            ]
        return {
            "version": 1,
            "timestamp": self._clock(),
            "namespaces": namespaces,
            "counters": dict(self._counters),
        }

    def restore(self, snapshot: dict) -> None:
        """Rehydrate internal state from a snapshot dict (full replacement).

        Clears existing state, reconstructs Entry objects, and restores
        per-namespace monotonic counters so timestamps resume from where
        they left off.
        """
        self._store.clear()
        self._counters.clear()

        for ns, entries in snapshot.get("namespaces", {}).items():
            self._store[ns] = [
                Entry(namespace=e["namespace"], key=e["key"], value=e["value"], timestamp=e["timestamp"])
                for e in entries
            ]

        for ns, counter_val in snapshot.get("counters", {}).items():
            self._counters[ns] = counter_val
