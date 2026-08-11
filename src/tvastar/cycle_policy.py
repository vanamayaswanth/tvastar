"""
tvastar.cycle_policy — CyclePolicy for controlled back-edges in TaskGraph.

Three variants:
  - FORBID: reject the back-edge (current default behavior)
  - ALLOW_TTL(max_iterations): permit the cycle up to N iterations
  - ALLOW_PREDICATE(predicate): permit the cycle while predicate(result) is True
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

__all__ = ["CyclePolicy"]


@dataclass(frozen=True, slots=True)
class _AllowTTL:
    """Permit cycle up to max_iterations re-entries."""

    max_iterations: int

    def __post_init__(self) -> None:
        if self.max_iterations < 1:
            raise ValueError("max_iterations must be >= 1")

    def __repr__(self) -> str:
        return f"CyclePolicy.ALLOW_TTL({self.max_iterations})"


@dataclass(frozen=True, slots=True)
class _AllowPredicate:
    """Permit cycle while predicate(result) returns True."""

    predicate: Callable[[Any], bool]

    def __repr__(self) -> str:
        return f"CyclePolicy.ALLOW_PREDICATE({self.predicate!r})"


class CyclePolicy:
    """
    Enum-like namespace for cycle edge policies.

    Usage::

        CyclePolicy.FORBID
        CyclePolicy.ALLOW_TTL(3)
        CyclePolicy.ALLOW_PREDICATE(lambda r: "RETRY" in r.text)
    """

    # Singleton sentinel for FORBID
    class _Forbid:
        __slots__ = ()
        _instance: "_Forbid | None" = None

        def __new__(cls) -> "_Forbid":
            if cls._instance is None:
                cls._instance = super().__new__(cls)
            return cls._instance

        def __repr__(self) -> str:
            return "CyclePolicy.FORBID"

        def __eq__(self, other: object) -> bool:
            return isinstance(other, CyclePolicy._Forbid)

        def __hash__(self) -> int:
            return hash("CyclePolicy.FORBID")

    FORBID: "_Forbid" = _Forbid()
    ALLOW_TTL = _AllowTTL
    ALLOW_PREDICATE = _AllowPredicate
