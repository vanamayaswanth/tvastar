"""
tvastar.cycle_journal — Append-only log of cycle iterations.

CycleEntry records one iteration (or the final termination record).
CycleJournal exposes convergence and termination properties for
post-execution inspection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher

__all__ = ["CycleEntry", "CycleJournal"]


@dataclass(frozen=True, slots=True)
class CycleEntry:
    """One iteration record or the final termination record."""

    iteration: int
    timestamp: float
    result_text: str
    continued: bool

    def __post_init__(self) -> None:
        # Truncate result_text to 500 chars (frozen bypass via object.__setattr__)
        if len(self.result_text) > 500:
            object.__setattr__(self, "result_text", self.result_text[:500])


class CycleJournal:
    """
    Append-only log tracking a single cycle's iterations.

    Invariants:
      - entries are never modified or deleted during a graph run
      - The final entry records a termination reason (continued=False)
      - iterations == count of continued=True entries
    """

    __slots__ = ("_entries",)

    def __init__(self) -> None:
        self._entries: list[CycleEntry] = []

    def append(self, entry: CycleEntry) -> None:
        """Append an entry. Entries are never removed."""
        self._entries.append(entry)

    @property
    def entries(self) -> list[CycleEntry]:
        """Read-only copy of entries (append-only invariant)."""
        return list(self._entries)

    @property
    def iterations(self) -> int:
        """Count of continued=True entries (excludes termination record)."""
        return sum(1 for e in self._entries if e.continued)

    @property
    def terminated(self) -> bool:
        """True if the last entry has continued=False."""
        if not self._entries:
            return False
        return not self._entries[-1].continued

    @property
    def termination_reason(self) -> str | None:
        """result_text from last entry if terminated, else None."""
        if not self._entries:
            return None
        last = self._entries[-1]
        if not last.continued:
            return last.result_text
        return None

    @property
    def converging(self) -> bool:
        """
        True if the last 2 result texts differ (text distance > 0.1).

        Uses SequenceMatcher ratio: distance = 1 - ratio.
        If fewer than 2 entries exist, returns False (not enough data).
        """
        if len(self._entries) < 2:
            return False
        a = self._entries[-2].result_text
        b = self._entries[-1].result_text
        ratio = SequenceMatcher(None, a, b).ratio()
        # ponytail: distance > 0.1 means texts differ enough → converging
        return (1.0 - ratio) > 0.1
