"""Compliance cost tracking — overhead ratio per loop and fleet.

Tracks tokens consumed by compliance checks vs. business logic,
computes overhead ratios, and emits INFO alerts when threshold exceeded.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, List

from .models import ComplianceAlert, ComplianceCostReport

if TYPE_CHECKING:
    from .alert import AlertEngine


@dataclass
class _TokenRecord:
    """Single token recording with metadata."""

    loop_name: str
    run_id: str
    tokens: int
    category: str  # "compliance" | "business"
    timestamp: float


class CostTracker:
    """Tracks compliance vs. business token spend per loop per run.

    Provides per-loop and fleet-wide overhead ratio.
    Emits INFO alert when overhead exceeds threshold.
    """

    def __init__(
        self,
        *,
        alert_engine: "AlertEngine | None" = None,
        threshold: float = 0.15,
    ) -> None:
        self._alert_engine = alert_engine
        self._threshold = threshold
        # ponytail: simple list storage, no persistence needed for MVP
        self._records: List[_TokenRecord] = []

    def record_compliance_tokens(
        self, loop_name: str, run_id: str, tokens: int
    ) -> None:
        """Record tokens consumed by a compliance check."""
        self._records.append(
            _TokenRecord(loop_name, run_id, tokens, "compliance", time.time())
        )
        self._check_threshold(loop_name)

    def record_business_tokens(
        self, loop_name: str, run_id: str, tokens: int
    ) -> None:
        """Record tokens consumed by business logic."""
        self._records.append(
            _TokenRecord(loop_name, run_id, tokens, "business", time.time())
        )

    def overhead_ratio(self, loop_name: str) -> float:
        """compliance_tokens / (compliance_tokens + business_tokens) for a loop.

        Returns 0.0 when total is 0.
        """
        compliance = 0
        business = 0
        for r in self._records:
            if r.loop_name == loop_name:
                if r.category == "compliance":
                    compliance += r.tokens
                else:
                    business += r.tokens
        total = compliance + business
        if total == 0:
            return 0.0
        return compliance / total

    def fleet_overhead(self) -> Dict[str, float]:
        """Dict of loop_name → overhead_ratio for all known loops."""
        loops: set[str] = {r.loop_name for r in self._records}
        return {name: self.overhead_ratio(name) for name in sorted(loops)}

    def report(
        self,
        loop_name: str | None = None,
        *,
        window_hours: float = 24.0,
    ) -> list[ComplianceCostReport]:
        """Generate ComplianceCostReport(s) filtered by loop and time window."""
        now = time.time()
        window_start = now - (window_hours * 3600)
        window_end = now

        # Filter records within time window
        in_window = [r for r in self._records if r.timestamp >= window_start]

        # Group by loop
        loops: Dict[str, Dict[str, int]] = {}
        for r in in_window:
            if loop_name is not None and r.loop_name != loop_name:
                continue
            bucket = loops.setdefault(r.loop_name, {"compliance": 0, "business": 0})
            bucket[r.category] += r.tokens

        reports: list[ComplianceCostReport] = []
        for name in sorted(loops):
            c = loops[name]["compliance"]
            b = loops[name]["business"]
            total = c + b
            ratio = c / total if total > 0 else 0.0
            reports.append(
                ComplianceCostReport(
                    loop_name=name,
                    compliance_tokens=c,
                    total_tokens=total,
                    overhead_ratio=ratio,
                    window_start=window_start,
                    window_end=window_end,
                )
            )
        return reports

    def _check_threshold(self, loop_name: str) -> None:
        """Emit INFO alert if overhead exceeds threshold."""
        if self._alert_engine is None:
            return
        ratio = self.overhead_ratio(loop_name)
        if ratio > self._threshold:
            alert = ComplianceAlert(
                severity="INFO",
                alert_type="COMPLIANCE_COST",
                loop_name=loop_name,
                run_id="",
                timestamp=time.time(),
                description=(
                    f"Compliance overhead ratio {ratio:.2%} exceeds "
                    f"threshold {self._threshold:.2%} for loop '{loop_name}'"
                ),
            )
            self._alert_engine.emit(alert)
