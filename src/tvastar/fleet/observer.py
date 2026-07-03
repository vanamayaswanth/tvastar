"""
tvastar.fleet.observer — Fleet health monitoring, alerting, and cross-agent trace correlation.

Provides the FleetObserver class that aggregates health status, quality scores,
and traces across all agents in a fleet. Supports configurable alerting thresholds
for error rate, cost spikes, and quality degradation. Alert events are delivered
through the EventBus so other agents can subscribe and respond.

Cross-agent trace correlation attaches fleet-level correlation IDs (uuid4) to
task trace contexts, propagates them to downstream spans via the EventBus, and
supports querying all spans sharing a given correlation ID.

All Tracer interactions swallow exceptions — observability never breaks operations.

Usage:
    from tvastar.fleet import FleetObserver, AlertConfig
    from tvastar.fleet.registry import FleetRegistry
    from tvastar.fleet.bus import EventBus

    registry = FleetRegistry("my-fleet")
    bus = EventBus("my-fleet")
    observer = FleetObserver(registry, bus, alert_config=AlertConfig())

    # Health snapshot
    snapshot = observer.health_snapshot()

    # Fleet quality score
    score = observer.fleet_quality_score()

    # Correlation
    cid = observer.create_correlation_id()
    observer.attach_correlation(span, cid)
    spans = observer.spans_by_correlation(cid)
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from tvastar.fleet import AgentHealthSnapshot, AlertConfig
from tvastar.fleet.bus import EventBus
from tvastar.fleet.registry import FleetRegistry


# ---------------------------------------------------------------------------
# FleetObserver
# ---------------------------------------------------------------------------


class FleetObserver:
    """Fleet health monitoring, alerting, and cross-agent trace correlation.

    Aggregates health status across all registered agents, computes fleet-wide
    quality scores, emits alert events through the EventBus when configurable
    thresholds are breached, and manages correlation IDs for cross-agent tracing.

    Parameters
    ----------
    registry:
        The FleetRegistry containing agent registrations and lifecycle state.
    event_bus:
        The EventBus for publishing alert events.
    tracer:
        Optional Tracer instance for span emission. Exceptions from the tracer
        are swallowed to never break observer operations.
    alert_config:
        Optional AlertConfig with thresholds for error rate, cost spike,
        and quality alerts. Uses defaults if not provided.
    """

    def __init__(
        self,
        registry: FleetRegistry,
        event_bus: EventBus,
        *,
        tracer: Any | None = None,
        alert_config: AlertConfig | None = None,
    ) -> None:
        self._registry = registry
        self._event_bus = event_bus
        self._tracer = tracer
        self._alert_config = alert_config or AlertConfig()

        # Quality score tracking: agent_name -> score
        self._quality_scores: dict[str, float] = {}

        # Error/success tracking for error rate alerting — capped to prevent OOM
        from collections import deque
        self._outcomes: deque[tuple[float, bool]] = deque(maxlen=50_000)

        # Cost tracking for cost spike alerting — capped
        self._cost_events: deque[tuple[float, float]] = deque(maxlen=50_000)

        # Previous window cost for spike detection
        self._previous_window_cost: float | None = None

        # Correlation ID -> list of spans
        self._correlation_spans: dict[str, list[Any]] = {}

    # ------------------------------------------------------------------
    # Health snapshot (Requirement 14.1)
    # ------------------------------------------------------------------

    def health_snapshot(self) -> list[AgentHealthSnapshot]:
        """Return a health snapshot for all registered agents.

        Iterates over all agents in the registry, building an
        AgentHealthSnapshot for each that includes name, current state,
        last run status, last run timestamp, and quality score.

        Returns
        -------
        List of AgentHealthSnapshot, one per registered agent.
        """
        snapshots: list[AgentHealthSnapshot] = []

        # Iterate all agents in the registry
        for name in list(self._registry._agents.keys()):
            entry = self._registry.get(name)
            if entry is None:
                continue

            # Extract last_run_status and last_run_at from the agent's loop
            last_run_status: str | None = None
            last_run_at: float | None = None

            loop = entry.loop
            if loop is not None:
                # Try to get last run info from the loop
                if hasattr(loop, "last_run_status"):
                    last_run_status = getattr(loop, "last_run_status", None)
                if hasattr(loop, "last_run_at"):
                    last_run_at = getattr(loop, "last_run_at", None)

            quality_score = self._quality_scores.get(name)

            snapshot = AgentHealthSnapshot(
                name=name,
                state=entry.state,
                last_run_status=last_run_status,
                last_run_at=last_run_at,
                quality_score=quality_score,
            )
            snapshots.append(snapshot)

        self._emit_span(
            "fleet.observer.health_snapshot",
            {"agent_count": len(snapshots)},
        )

        return snapshots

    # ------------------------------------------------------------------
    # Fleet quality score (Requirement 14.2)
    # ------------------------------------------------------------------

    def fleet_quality_score(self) -> float:
        """Compute fleet-wide quality score as weighted average of individual scores.

        Uses equal weights for all agents with recorded quality scores.
        Returns 0.0 if no scores are available.

        Returns
        -------
        float: The fleet-wide quality score (0.0 if no scores available).
        """
        if not self._quality_scores:
            return 0.0

        scores = list(self._quality_scores.values())
        if not scores:
            return 0.0

        return sum(scores) / len(scores)

    def record_quality_score(self, agent_name: str, score: float) -> None:
        """Record a quality score for an agent.

        If the score drops below the configured quality threshold, an alert
        event is published on the EventBus topic "fleet.alert.quality".

        Parameters
        ----------
        agent_name:
            Name of the agent to record the score for.
        score:
            The quality score value.
        """
        self._quality_scores[agent_name] = score

        # Check quality threshold (Requirement 14.3)
        if score < self._alert_config.quality_threshold:
            self._emit_quality_alert(agent_name, score)

    # ------------------------------------------------------------------
    # Outcome tracking for error rate alerting (Requirement 15.1)
    # ------------------------------------------------------------------

    def record_outcome(self, is_error: bool, timestamp: float | None = None) -> None:
        """Record a task outcome (success or error) for error rate calculation.

        When the error rate within the configured window exceeds the threshold,
        an alert is published on "fleet.alert.error_rate".

        Parameters
        ----------
        is_error:
            True if the outcome was an error, False for success.
        timestamp:
            Optional timestamp; uses current time if not provided.
        """
        ts = timestamp if timestamp is not None else time.time()
        self._outcomes.append((ts, is_error))

        # Check error rate within window
        self._check_error_rate_alert()

    # ------------------------------------------------------------------
    # Cost tracking for cost spike alerting (Requirement 15.2)
    # ------------------------------------------------------------------

    def record_cost(self, usd: float, timestamp: float | None = None) -> None:
        """Record a cost event for cost spike detection.

        When cost in the current window exceeds cost_spike_threshold times
        the previous window's cost, an alert is published on
        "fleet.alert.cost_spike".

        Parameters
        ----------
        usd:
            The USD cost amount.
        timestamp:
            Optional timestamp; uses current time if not provided.
        """
        ts = timestamp if timestamp is not None else time.time()
        self._cost_events.append((ts, usd))

        # Check for cost spike
        self._check_cost_spike_alert()

    # ------------------------------------------------------------------
    # Correlation ID management (Requirements 16.1, 16.2, 16.3, 16.4)
    # ------------------------------------------------------------------

    def create_correlation_id(self) -> str:
        """Generate a fleet-level correlation ID (uuid4).

        Returns
        -------
        A unique correlation ID string.
        """
        correlation_id = str(uuid.uuid4())

        self._emit_span(
            "fleet.observer.create_correlation",
            {"correlation_id": correlation_id},
        )

        return correlation_id

    def attach_correlation(self, span: Any, correlation_id: str) -> None:
        """Attach a correlation ID to a span's trace context.

        Stores the span indexed by correlation_id for later querying,
        and sets the correlation_id attribute on the span if possible.

        Parameters
        ----------
        span:
            A Tracer Span instance to associate with the correlation ID.
        correlation_id:
            The fleet-level correlation ID to attach.
        """
        # Store span for correlation-based queries
        if correlation_id not in self._correlation_spans:
            self._correlation_spans[correlation_id] = []
        self._correlation_spans[correlation_id].append(span)

        # Try to set correlation_id on the span (attribute or dict-like)
        try:
            if hasattr(span, "attributes") and isinstance(span.attributes, dict):
                span.attributes["fleet.correlation_id"] = correlation_id
            elif hasattr(span, "set_attribute"):
                span.set_attribute("fleet.correlation_id", correlation_id)
        except Exception:
            # Swallow — observability never breaks operations
            pass

        self._emit_span(
            "fleet.observer.attach_correlation",
            {"correlation_id": correlation_id},
        )

    def spans_by_correlation(self, correlation_id: str) -> list[Any]:
        """Query all spans sharing a given correlation ID.

        Parameters
        ----------
        correlation_id:
            The fleet-level correlation ID to query.

        Returns
        -------
        List of spans associated with the given correlation ID.
        Returns an empty list if the correlation ID is not found.
        """
        return list(self._correlation_spans.get(correlation_id, []))

    # ------------------------------------------------------------------
    # Alert configuration access
    # ------------------------------------------------------------------

    @property
    def alert_config(self) -> AlertConfig:
        """Current alert configuration."""
        return self._alert_config

    @alert_config.setter
    def alert_config(self, config: AlertConfig) -> None:
        """Update the alert configuration."""
        self._alert_config = config

    @property
    def quality_scores(self) -> dict[str, float]:
        """Current quality scores by agent name."""
        return dict(self._quality_scores)

    # ------------------------------------------------------------------
    # Internal alert logic
    # ------------------------------------------------------------------

    def _emit_quality_alert(self, agent_name: str, score: float) -> None:
        """Emit a quality alert event on the EventBus (Requirement 14.3)."""
        payload = {
            "agent_name": agent_name,
            "quality_score": score,
            "threshold": self._alert_config.quality_threshold,
            "alert_type": "quality_degradation",
        }

        self._event_bus.publish(
            "fleet.alert.quality",
            payload,
            source_agent="fleet_observer",
        )

        self._emit_span(
            "fleet.observer.alert.quality",
            {
                "agent_name": agent_name,
                "quality_score": score,
                "threshold": self._alert_config.quality_threshold,
            },
        )

    def _check_error_rate_alert(self) -> None:
        """Check if error rate exceeds threshold within the configured window."""
        now = time.time()
        window_start = now - self._alert_config.window_seconds

        # Filter outcomes within the window
        window_outcomes = [(ts, is_error) for ts, is_error in self._outcomes if ts >= window_start]

        if not window_outcomes:
            return

        error_count = sum(1 for _, is_error in window_outcomes if is_error)
        total_count = len(window_outcomes)
        error_rate = error_count / total_count

        if error_rate > self._alert_config.error_rate_threshold:
            payload = {
                "error_rate": error_rate,
                "error_count": error_count,
                "total_count": total_count,
                "threshold": self._alert_config.error_rate_threshold,
                "window_seconds": self._alert_config.window_seconds,
                "alert_type": "error_rate",
            }

            self._event_bus.publish(
                "fleet.alert.error_rate",
                payload,
                source_agent="fleet_observer",
            )

            self._emit_span(
                "fleet.observer.alert.error_rate",
                {
                    "error_rate": error_rate,
                    "threshold": self._alert_config.error_rate_threshold,
                },
            )

    def _check_cost_spike_alert(self) -> None:
        """Check if cost in current window exceeds spike threshold vs previous window."""
        now = time.time()
        window = self._alert_config.window_seconds

        current_window_start = now - window
        previous_window_start = current_window_start - window

        # Sum cost in current window
        current_cost = sum(usd for ts, usd in self._cost_events if ts >= current_window_start)

        # Sum cost in previous window
        previous_cost = sum(
            usd
            for ts, usd in self._cost_events
            if previous_window_start <= ts < current_window_start
        )

        # Update stored previous window cost
        self._previous_window_cost = previous_cost

        # Only alert if there's a meaningful previous window to compare against
        if previous_cost <= 0:
            return

        cost_ratio = current_cost / previous_cost

        if cost_ratio > self._alert_config.cost_spike_threshold:
            payload = {
                "current_cost": current_cost,
                "previous_cost": previous_cost,
                "cost_ratio": cost_ratio,
                "threshold": self._alert_config.cost_spike_threshold,
                "window_seconds": window,
                "alert_type": "cost_spike",
            }

            self._event_bus.publish(
                "fleet.alert.cost_spike",
                payload,
                source_agent="fleet_observer",
            )

            self._emit_span(
                "fleet.observer.alert.cost_spike",
                {
                    "cost_ratio": cost_ratio,
                    "threshold": self._alert_config.cost_spike_threshold,
                },
            )

    # ------------------------------------------------------------------
    # Observability helpers
    # ------------------------------------------------------------------

    def _emit_span(self, name: str, attributes: dict[str, Any]) -> None:
        """Emit a tracer span, swallowing any exceptions.

        Observability must never break observer operations (Requirement 20.4).
        """
        if self._tracer is None:
            return
        try:
            if hasattr(self._tracer, "span"):
                with self._tracer.span(name, attributes=attributes):
                    pass
            elif hasattr(self._tracer, "start_span"):
                span = self._tracer.start_span(name, attributes=attributes)
                if hasattr(span, "end"):
                    span.end()
        except Exception:
            # Swallow tracer exceptions — observability never breaks operations
            pass
