"""Return on Cognitive Spend (ROCS) metric tracker.

Computes value_delivered / tokens_consumed per loop run.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    from .memory.store import Store

logger = logging.getLogger(__name__)

ROCSPolicy = Callable[[], float]  # returns 0.0–1.0


@dataclass
class ROCSScore:
    loop_name: str
    run_id: str
    value_delivered: float
    tokens_consumed: int
    score: float  # value_delivered / tokens_consumed (or 0.0)


class ROCSTracker:
    """Computes and stores Return on Cognitive Spend per loop run."""

    def __init__(self, store: "Store", loop_name: str, policy: Optional[ROCSPolicy] = None):
        self._store = store
        self._loop_name = loop_name
        self._policy = policy

    def record(self, run_id: str, tokens_consumed: int, quality_score: int = 0) -> ROCSScore:
        """Compute and store ROCS for a completed run."""
        value = self._compute_value(quality_score)
        score = value / tokens_consumed if tokens_consumed > 0 else 0.0
        entry = ROCSScore(self._loop_name, run_id, value, tokens_consumed, score)
        key = f"rocs:{self._loop_name}:{run_id}"
        self._store.set(key, {"score": score, "value": value, "tokens": tokens_consumed})
        return entry

    def aggregate(self, n: int = 10) -> float:
        """Arithmetic mean of last N ROCS scores. Returns 0.0 if none."""
        n = max(1, min(1000, n))
        keys = sorted(self._store.keys(f"rocs:{self._loop_name}:"), reverse=True)[:n]
        if not keys:
            return 0.0
        scores = []
        for k in keys:
            data = self._store.get(k)
            if data:
                scores.append(data.get("score", 0.0))
        return sum(scores) / len(scores) if scores else 0.0

    def _compute_value(self, quality_score: int) -> float:
        """Compute value_delivered: use policy if set, else quality_score/100."""
        if self._policy:
            try:
                v = self._policy()
                return max(0.0, min(1.0, v))
            except Exception as exc:
                logger.error("ROCS policy raised: %s", exc)
                return 0.0
        return quality_score / 100.0
