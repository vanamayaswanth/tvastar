"""Degraded state tracking with rate-limited logging and lazy recovery.

Tracks which degraded states are active, emits structured WARNING logs
(at most once per 10 seconds per mode), and exits on next successful request
(no background polling).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from tvastar.errors import DegradedState
from tvastar.logging import StructuredLogger


@dataclass
class _DegradedEntry:
    """Internal record for an active degraded state."""

    state: DegradedState
    reason: str
    entered_at: float
    last_logged_at: float = 0.0


class DegradedStateTracker:
    """Tracks active degraded states with atomic transitions.

    - enter/exit are protected by asyncio.Lock so state change + log are atomic.
    - WARNING logs are rate-limited: at most once per 10 seconds per mode.
    - Recovery is lazy: call exit() on the next successful request.
    """

    _RATE_LIMIT_SECONDS: float = 10.0

    def __init__(self, logger: StructuredLogger) -> None:
        self._lock = asyncio.Lock()
        self._active: dict[DegradedState, _DegradedEntry] = {}
        self._logger = logger

    async def enter(self, state: DegradedState, reason: str) -> None:
        """Enter a degraded state. Emits WARNING log immediately."""
        now = time.monotonic()
        async with self._lock:
            self._active[state] = _DegradedEntry(
                state=state, reason=reason, entered_at=now, last_logged_at=now
            )
            self._logger.emit(
                "WARNING",
                f"Entering degraded state: {state.value}",
                degraded_state=state.value,
                reason=reason,
            )

    async def exit(self, state: DegradedState) -> None:
        """Exit a degraded state (lazy recovery). Emits INFO log with duration."""
        async with self._lock:
            entry = self._active.pop(state, None)
            if entry:
                duration = time.monotonic() - entry.entered_at
                self._logger.emit(
                    "INFO",
                    f"Exiting degraded state: {state.value}",
                    degraded_state=state.value,
                    duration_seconds=round(duration, 3),
                )

    async def warn_if_due(self, state: DegradedState) -> None:
        """Emit a rate-limited WARNING if state is active and >=10s since last log."""
        now = time.monotonic()
        async with self._lock:
            entry = self._active.get(state)
            if entry is None:
                return
            if now - entry.last_logged_at >= self._RATE_LIMIT_SECONDS:
                entry.last_logged_at = now
                self._logger.emit(
                    "WARNING",
                    f"Still in degraded state: {state.value}",
                    degraded_state=state.value,
                    reason=entry.reason,
                    duration_seconds=round(now - entry.entered_at, 3),
                )

    def active_states(self) -> set[DegradedState]:
        """Return the set of currently active degraded states."""
        return set(self._active.keys())
