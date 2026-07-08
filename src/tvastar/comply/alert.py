"""Alert engine with configurable sinks and suppression logic.

Delivers ComplianceAlerts to configured sinks (stderr, file, callback).
Suppresses duplicate (loop_name, alert_type) pairs within a configurable
time window, tracking suppression count for the next delivery.
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import asdict
from typing import Callable, Dict, List, Protocol, Tuple

from .models import ComplianceAlert


class AlertSink(Protocol):
    """Protocol for alert delivery targets."""

    def deliver(self, alert: ComplianceAlert) -> None: ...


class StderrSink:
    """Default sink — writes JSON-formatted alerts to stderr."""

    def deliver(self, alert: ComplianceAlert) -> None:
        sys.stderr.write(json.dumps(asdict(alert)) + "\n")
        sys.stderr.flush()


class FileSink:
    """Appends JSON alerts to a file."""

    def __init__(self, path: str) -> None:
        self._path = path

    def deliver(self, alert: ComplianceAlert) -> None:
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(alert)) + "\n")


class CallbackSink:
    """Wraps a user-provided callable."""

    def __init__(self, fn: Callable[[ComplianceAlert], None]) -> None:
        self._fn = fn

    def deliver(self, alert: ComplianceAlert) -> None:
        self._fn(alert)


class AlertEngine:
    """Manages alert delivery with suppression logic.

    Suppression: same (loop_name, alert_type) within suppression_window
    seconds is counted but not re-delivered. When the window expires and
    the next alert arrives, it is delivered with suppression_count set to
    the number of suppressed alerts since the last delivery.
    """

    def __init__(
        self,
        sinks: List[AlertSink] | None = None,
        suppression_window: float = 300.0,
    ) -> None:
        self._sinks: List[AlertSink] = sinks if sinks else [StderrSink()]
        self._suppression_window = suppression_window
        # (loop_name, alert_type) → timestamp of last delivered alert
        self._last_alert: Dict[Tuple[str, str], float] = {}
        # (loop_name, alert_type) → count of suppressed alerts since last delivery
        self._suppression_count: Dict[Tuple[str, str], int] = {}

    def emit(self, alert: ComplianceAlert) -> bool:
        """Deliver or suppress. Returns True if delivered."""
        key = (alert.loop_name, alert.alert_type)
        now = alert.timestamp if alert.timestamp else time.time()

        last_time = self._last_alert.get(key)

        if last_time is not None and (now - last_time) < self._suppression_window:
            # Within suppression window — count but don't deliver
            self._suppression_count[key] = self._suppression_count.get(key, 0) + 1
            return False

        # Outside window (or first alert for this key) — deliver
        # Attach suppression count from prior window
        suppressed = self._suppression_count.get(key, 0)
        if suppressed > 0:
            alert.suppression_count = suppressed

        # Deliver to all sinks
        for sink in self._sinks:
            sink.deliver(alert)

        # Reset tracking
        self._last_alert[key] = now
        self._suppression_count[key] = 0

        return True
