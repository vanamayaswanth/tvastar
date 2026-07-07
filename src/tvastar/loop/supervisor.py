"""Loop overlap supervisor — prevents concurrent execution unless opted in.

ponytail: one asyncio.Lock per loop, held only for the state-check window.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from . import LoopEvent, LoopState

if TYPE_CHECKING:
    from . import Loop

logger = logging.getLogger(__name__)


class LoopSupervisor:
    """Detects schedule overlap and prevents concurrent execution.

    Uses one asyncio.Lock per loop instance. Lock is held only for
    the state-check-and-decision window, not for the run duration.
    """

    def __init__(self, loop: "Loop") -> None:
        self._loop = loop
        self._lock = asyncio.Lock()
        self._active_run_ids: list[str] = []
        self._max_concurrent: int = 4

    async def should_trigger(self) -> tuple[bool, str | None]:
        """Check if a trigger should proceed.

        Returns:
            (True, None) if trigger should proceed.
            (False, active_run_id) if trigger should be skipped.
        """
        async with self._lock:
            allow_concurrent = getattr(
                self._loop.config, "allow_concurrent", False
            )
            active_states = (LoopState.RUNNING, LoopState.VERIFYING)

            if not allow_concurrent:
                if self._loop.state in active_states:
                    return (
                        False,
                        self._active_run_ids[0]
                        if self._active_run_ids
                        else "unknown",
                    )
                return True, None
            else:
                if len(self._active_run_ids) >= self._max_concurrent:
                    return False, self._active_run_ids[0]
                return True, None

    def register_run(self, run_id: str) -> None:
        """Track a new active run (call after should_trigger returns True)."""
        self._active_run_ids.append(run_id)

    def unregister_run(self, run_id: str) -> None:
        """Remove a completed run from tracking."""
        try:
            self._active_run_ids.remove(run_id)
        except ValueError:
            pass

    def on_skip(self, active_run_id: str, skipped_at: float) -> None:
        """Emit skip event and log WARNING."""
        logger.warning(
            "Loop %s: trigger skipped (overlap), active_run_id=%s, skipped_at=%s",
            self._loop.name,
            active_run_id,
            skipped_at,
        )
        event = LoopEvent(
            loop_name=self._loop.name,
            run_id="",
            state=LoopState.IDLE,
            at=skipped_at,
            data={
                "skipped": True,
                "reason": "overlap",
                "active_run_id": active_run_id,
            },
        )
        for fn in self._loop._listeners:
            try:
                fn(event)
            except Exception:
                pass  # ponytail: fault isolation — listener errors never break runs
