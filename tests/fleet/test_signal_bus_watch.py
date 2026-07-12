"""Unit tests for SignalBus watch system (task 2.3).

Tests prefix-based async iteration, bounded queue drop behavior,
flush semantics, and watcher lifecycle.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from tvastar.fleet.signal_bus import SignalBus


@pytest.fixture
def bus():
    """SignalBus with deterministic clock and small queue for testing overflow."""
    counter = iter(float(i) for i in range(10_000))
    return SignalBus(
        max_entries_per_namespace=100,
        max_queue_per_consumer=5,
        clock=lambda: next(counter),
    )


async def test_watch_receives_matching_entries(bus: SignalBus):
    """Watcher receives entries whose namespace starts with prefix."""
    received: list = []

    async def consumer():
        async for entry in bus.watch("worker_"):
            received.append(entry)
            if len(received) >= 3:
                break

    task = asyncio.create_task(consumer())
    await asyncio.sleep(0)

    await bus.write("worker_1", "status", "running")
    await bus.write("worker_2", "status", "idle")
    await bus.write("coordinator", "goal", "test")  # should NOT match
    await bus.write("worker_1", "progress", "50%")

    await asyncio.wait_for(task, timeout=2.0)
    assert len(received) == 3
    assert all(e.namespace.startswith("worker_") for e in received)


async def test_watch_does_not_receive_non_matching(bus: SignalBus):
    """Watcher does not receive entries outside its prefix."""
    received: list = []

    async def consumer():
        async for entry in bus.watch("coordinator"):
            received.append(entry)
            if len(received) >= 1:
                break

    task = asyncio.create_task(consumer())
    await asyncio.sleep(0)

    await bus.write("worker_1", "status", "x")
    await bus.write("worker_2", "status", "y")
    await bus.write("coordinator", "goal", "match!")

    await asyncio.wait_for(task, timeout=2.0)
    assert len(received) == 1
    assert received[0].namespace == "coordinator"
    assert received[0].value == "match!"


async def test_watch_queue_full_drops_entry(bus: SignalBus, caplog):
    """When consumer queue is full, entries are dropped with a warning log."""
    # Register a watcher but never consume — let the queue fill.
    gen = bus.watch("ns")
    # Kick the async generator so it registers the watcher internally.
    consume_task = asyncio.ensure_future(gen.__anext__())
    await asyncio.sleep(0)

    # Write more entries than the queue can hold (maxsize=5).
    # The first call to __anext__ will consume one entry, leaving 4 free slots.
    # So writes 1 is consumed, 2-5 fill the queue, 6+ are dropped.
    with caplog.at_level(logging.WARNING, logger="tvastar.fleet.signal_bus"):
        for i in range(10):
            await bus.write("ns", "key", i)

    assert "Watch queue full" in caplog.text

    # Cleanup
    consume_task.cancel()
    try:
        await consume_task
    except (asyncio.CancelledError, StopAsyncIteration):
        pass
    await gen.aclose()


async def test_flush_drains_all_queues(bus: SignalBus):
    """flush() waits until all queued entries are consumed."""
    received: list = []

    async def consumer():
        async for entry in bus.watch("x"):
            received.append(entry)
            if len(received) >= 3:
                break

    task = asyncio.create_task(consumer())
    await asyncio.sleep(0)

    await bus.write("x", "a", 1)
    await bus.write("x", "b", 2)
    await bus.write("x", "c", 3)

    await asyncio.wait_for(bus.flush(), timeout=2.0)
    await asyncio.wait_for(task, timeout=2.0)

    assert len(received) == 3


async def test_multiple_watchers_independent(bus: SignalBus):
    """Multiple watchers receive entries independently based on their prefix."""
    worker_entries: list = []
    coord_entries: list = []

    async def worker_consumer():
        async for entry in bus.watch("worker_"):
            worker_entries.append(entry)
            if len(worker_entries) >= 2:
                break

    async def coord_consumer():
        async for entry in bus.watch("coord"):
            coord_entries.append(entry)
            if len(coord_entries) >= 1:
                break

    t1 = asyncio.create_task(worker_consumer())
    t2 = asyncio.create_task(coord_consumer())
    await asyncio.sleep(0)

    await bus.write("worker_1", "status", "go")
    await bus.write("coordinator", "goal", "do stuff")
    await bus.write("worker_2", "status", "go")

    await asyncio.wait_for(t1, timeout=2.0)
    await asyncio.wait_for(t2, timeout=2.0)

    assert len(worker_entries) == 2
    assert len(coord_entries) == 1
    assert coord_entries[0].namespace == "coordinator"


async def test_watch_cleanup_on_break(bus: SignalBus):
    """Breaking out of a watch loop cleans up the watcher."""
    received = []

    async def consumer():
        async for entry in bus.watch("x"):
            received.append(entry)
            break  # exit after first entry

    task = asyncio.create_task(consumer())
    await asyncio.sleep(0)

    await bus.write("x", "k", "v")
    await asyncio.wait_for(task, timeout=2.0)
    # Allow generator cleanup to run
    await asyncio.sleep(0)

    # Watcher should be cleaned up (removed from _watchers list)
    assert len(bus._watchers) == 0
    assert len(received) == 1


async def test_write_still_works_without_watchers(bus: SignalBus):
    """write() works fine when no watchers are registered."""
    entry = await bus.write("ns", "key", "val")
    assert entry.namespace == "ns"
    assert bus.read("ns", "key") == "val"


async def test_empty_prefix_matches_all(bus: SignalBus):
    """A watch with empty prefix receives all entries."""
    received: list = []

    async def consumer():
        async for entry in bus.watch(""):
            received.append(entry)
            if len(received) >= 3:
                break

    task = asyncio.create_task(consumer())
    await asyncio.sleep(0)

    await bus.write("a", "k", 1)
    await bus.write("b", "k", 2)
    await bus.write("c", "k", 3)

    await asyncio.wait_for(task, timeout=2.0)
    assert len(received) == 3
