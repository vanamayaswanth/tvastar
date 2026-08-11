"""
tvastar.termination_oracle — Non-cooperative watchdog for stuck cycles.

Polls active CycleJournals at a configurable interval and cancels tasks
that exceed max_wall_clock or iteration limits. Failure is always
fail-open: oracle errors log a warning but never crash the graph.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from .cycle_journal import CycleEntry, CycleJournal

__all__ = ["TerminationOracle"]

_log = logging.getLogger("tvastar.termination_oracle")


@dataclass(frozen=True, slots=True)
class TerminationOracle:
    """
    External async watchdog that monitors CycleJournals and cancels stuck cycles.

    Parameters
    ----------
    poll_interval:
        Seconds between polls. Clamped to minimum 1s.
    max_wall_clock:
        Maximum wall-clock seconds a cycle may run before cancellation.
    max_iterations:
        Maximum iterations allowed (checked against journal.iterations).
        If None, only wall_clock is checked.
    """

    poll_interval: float = 5.0
    max_wall_clock: float = 60.0
    max_iterations: int | None = None

    def __post_init__(self) -> None:
        # ponytail: invariant — never faster than 1s
        if self.poll_interval < 1.0:
            object.__setattr__(self, "poll_interval", 1.0)

    async def _poll_loop(
        self,
        cycle_journals: dict[str, CycleJournal],
        cycle_tasks: dict[str, asyncio.Task],
        start_times: dict[str, float],
    ) -> None:
        """
        Poll active CycleJournals. Cancel tasks exceeding limits.

        Called internally by TaskGraph when oracle is attached.
        Runs until cancelled (graph completion cancels this task) or
        all cycles have terminated.
        """
        while True:
            try:
                await asyncio.sleep(self.poll_interval)
            except asyncio.CancelledError:
                return

            # Check if all cycles are done — if so, exit gracefully
            all_terminated = all(j.terminated for j in cycle_journals.values())
            if all_terminated and cycle_journals:
                return

            for edge_key, journal in list(cycle_journals.items()):
                if journal.terminated:
                    continue

                task = cycle_tasks.get(edge_key)
                if task is None or task.done():
                    continue

                # Check limits
                wall_clock = time.time() - start_times.get(edge_key, time.time())
                should_cancel = False

                if wall_clock > self.max_wall_clock:
                    should_cancel = True
                elif self.max_iterations is not None and journal.iterations > self.max_iterations:
                    should_cancel = True

                if should_cancel:
                    _log.info(
                        "Oracle cancelling cycle %s (wall_clock=%.1fs, iterations=%d)",
                        edge_key,
                        wall_clock,
                        journal.iterations,
                    )
                    task.cancel()
                    # Record oracle_intervention in journal
                    journal.append(
                        CycleEntry(
                            iteration=journal.iterations,
                            timestamp=time.time(),
                            result_text="oracle_intervention",
                            continued=False,
                        )
                    )
