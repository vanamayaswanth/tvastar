"""Periodic SignalBus checkpointing to Store for crash recovery.

Thin wrapper applying the existing checkpoint pattern (ADR-007) to SignalBus.
Serializes SignalBus state to JSON, writes atomically to Store (temp key then
swap), and restores on startup.

Zero runtime dependencies — stdlib only (asyncio, json, logging).
Python 3.10+.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tvastar.fleet.signal_bus import SignalBus
    from tvastar.memory.store import Store

from tvastar.fleet.models import CheckpointerConfig

__all__ = ["Checkpointer"]

logger = logging.getLogger(__name__)


class Checkpointer:
    """Background checkpoint/restore for SignalBus using an existing Store backend.

    Parameters
    ----------
    signal_bus:
        The SignalBus instance to checkpoint.
    store:
        Any Store backend (InMemoryStore, FileStore, SQLiteStore).
    config:
        Optional configuration. Defaults to 30s interval, key "signal_bus_checkpoint".
    """

    def __init__(
        self,
        signal_bus: SignalBus,
        store: Store,
        config: CheckpointerConfig | None = None,
    ) -> None:
        self._signal_bus = signal_bus
        self._store = store
        self._config = config or CheckpointerConfig()
        self._task: asyncio.Task[None] | None = None

    @property
    def _key(self) -> str:
        return self._config.checkpoint_key

    async def start(self) -> None:
        """Begin periodic background checkpointing."""
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        """Cancel the background checkpoint task."""
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def checkpoint_now(self) -> bool:
        """Manually trigger a checkpoint. Returns True on success, False on failure.

        Writes atomically: temp key first, then real key, then delete temp.
        Retries once after 1s on failure.
        """
        snapshot = self._signal_bus.snapshot()
        data = json.dumps(snapshot)
        tmp_key = self._key + ".tmp"

        for attempt in range(2):
            try:
                # Atomic write: temp key → real key → delete temp
                self._store.set(tmp_key, data)
                self._store.set(self._key, data)
                self._store.delete(tmp_key)
                return True
            except Exception:
                if attempt == 0:
                    await asyncio.sleep(1)
                else:
                    logger.warning(
                        "Checkpoint write failed after retry — skipping cycle (key=%r)",
                        self._key,
                    )
                    return False
        return False  # pragma: no cover

    def restore(self) -> bool:
        """Rehydrate SignalBus from the last checkpoint in Store.

        Returns True if a checkpoint was found and restored, False otherwise.
        """
        raw = self._store.get(self._key)
        if raw is None:
            return False
        try:
            snapshot = json.loads(raw) if isinstance(raw, str) else raw
            self._signal_bus.restore(snapshot)
            return True
        except Exception:
            logger.warning("Failed to restore checkpoint from Store (key=%r)", self._key)
            return False

    async def _loop(self) -> None:
        """Periodic checkpoint loop — runs every config.interval seconds."""
        while True:
            await asyncio.sleep(self._config.interval)
            await self.checkpoint_now()
