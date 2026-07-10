"""Fleet-wide compliance dashboard for tvastar.comply."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict
from typing import Any, Dict, List, Optional

from .models import AuditResult, FleetSummary, LoopStatus


class ComplianceDashboard:
    """Aggregates compliance status across all registered Loops.

    Thread-safe: uses a lock around state mutations.
    Staleness: marks loops as STALE if not checked within 2x interval.
    """

    def __init__(self, *, check_interval: float = 60.0) -> None:
        self._check_interval = check_interval
        self._lock = threading.Lock()
        # loop_name → latest AuditResult
        self._results: Dict[str, AuditResult] = {}
        # loop_name → consecutive compliant count
        self._consecutive: Dict[str, int] = {}
        # loop_name → overhead ratio (optional cost data)
        self._overhead: Dict[str, float] = {}

    def update(self, loop_name: str, result: AuditResult) -> None:
        """Store the latest audit result for a loop and update consecutive count."""
        with self._lock:
            self._results[loop_name] = result
            if result.status == "COMPLIANT":
                self._consecutive[loop_name] = self._consecutive.get(loop_name, 0) + 1
            else:
                self._consecutive[loop_name] = 0

    def set_overhead(self, loop_name: str, ratio: float) -> None:
        """Set compliance overhead ratio for a loop (called by CostTracker)."""
        with self._lock:
            self._overhead[loop_name] = ratio

    def query(self) -> FleetSummary:
        """Compute and return the current fleet compliance summary.

        Staleness is evaluated at query time: any loop whose last_check
        is older than 2 × check_interval is marked STALE.
        """
        now = time.time()
        stale_threshold = 2 * self._check_interval

        with self._lock:
            per_loop: List[LoopStatus] = []
            compliant = 0
            non_compliant = 0
            stale = 0

            for loop_name, result in self._results.items():
                age = now - result.timestamp
                if age > stale_threshold:
                    status = "STALE"
                    stale += 1
                elif result.status == "COMPLIANT":
                    status = "COMPLIANT"
                    compliant += 1
                else:
                    status = "NON_COMPLIANT"
                    non_compliant += 1

                per_loop.append(
                    LoopStatus(
                        loop_name=loop_name,
                        last_check=result.timestamp,
                        status=status,
                        articles=result.checks,
                        consecutive_compliant=self._consecutive.get(loop_name, 0),
                    )
                )

            total = compliant + non_compliant + stale
            pct = (compliant / total) * 100 if total > 0 else 0.0

            overhead: Optional[Dict[str, float]] = dict(self._overhead) if self._overhead else None

        return FleetSummary(
            total=total,
            compliant=compliant,
            non_compliant=non_compliant,
            stale=stale,
            fleet_compliance_pct=pct,
            per_loop=per_loop,
            compliance_overhead=overhead,
        )

    def to_json(self) -> str:
        """Serialize the current fleet summary to JSON."""
        summary = self.query()
        return json.dumps(asdict(summary), default=_json_fallback)


def _json_fallback(obj: Any) -> Any:
    """Fallback serializer for dataclass fields that aren't natively JSON-able."""
    if hasattr(obj, "__dict__"):
        return obj.__dict__
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")
