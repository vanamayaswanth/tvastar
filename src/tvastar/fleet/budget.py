"""
tvastar.fleet.budget — Fleet-wide cost governance with per-agent allocation and auto-throttle.

Composes with existing BudgetPolicy (per-Loop) to provide a fleet-level budget
ceiling. Both limits are enforced independently — whichever ceiling is reached
first prevents further execution.

Usage:
    from tvastar.fleet import FleetBudgetConfig, FleetBudget
    from tvastar.cost import Cost

    config = FleetBudgetConfig(
        max_fleet_usd=100.0,
        allocations={"researcher": 40.0, "writer": 30.0},
        warn_threshold=0.8,
        throttle_threshold=0.9,
        exempt_agents=["critical-monitor"],
    )
    budget = FleetBudget(config)

    cost = Cost(input_tokens=1000, output_tokens=500, model="claude-opus-4-6")
    budget.record_cost("researcher", "ml-team", cost)

    if budget.check_budget("researcher"):
        # agent is allowed to proceed
        ...
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from tvastar.fleet import BudgetAllocation, FleetBudgetConfig


# ---------------------------------------------------------------------------
# Internal data models
# ---------------------------------------------------------------------------


@dataclass
class _CostEvent:
    """Internal record of a single cost event."""

    agent_name: str
    owner: str
    usd: float
    timestamp: float


# ---------------------------------------------------------------------------
# Budget pressure / throttle events (stored for observer/event bus to consume)
# ---------------------------------------------------------------------------


@dataclass
class BudgetWarningEvent:
    """Emitted when fleet spend reaches the warning threshold."""

    fleet_spent: float
    max_fleet_usd: float
    threshold: float
    timestamp: float = field(default_factory=time.time)


@dataclass
class BudgetThrottleEvent:
    """Emitted when agents are auto-throttled due to budget pressure."""

    throttled_agents: list[str]
    fleet_spent: float
    max_fleet_usd: float
    threshold: float
    timestamp: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# FleetBudget
# ---------------------------------------------------------------------------


class FleetBudget:
    """Fleet-wide cost governance composing with existing BudgetPolicy.

    Enforces:
    - Fleet-wide maximum USD spend (cumulative across all agents)
    - Per-agent allocation caps (agent-level throttling)
    - Unallocated pool for agents without explicit allocations
    - Auto-throttle of highest-spending non-exempt agents at throttle threshold
    - Budget pressure warnings at warning threshold

    Parameters
    ----------
    config:
        FleetBudgetConfig with max_fleet_usd, allocations, thresholds, etc.
    tracer:
        Optional Tracer instance for emitting observability spans.
    """

    def __init__(
        self,
        config: FleetBudgetConfig,
        *,
        tracer: Any | None = None,
    ) -> None:
        self._config = config
        self._tracer = tracer

        # Cumulative spend tracking — capped to prevent unbounded memory growth
        from collections import deque

        self._cost_events: deque[_CostEvent] = deque(maxlen=100_000)
        self._spend_by_agent: dict[str, float] = {}
        self._spend_by_owner: dict[str, float] = {}
        self._total_spent: float = 0.0

        # Per-agent allocations
        self._allocations: dict[str, BudgetAllocation] = {}
        for agent_name, max_usd in config.allocations.items():
            self._allocations[agent_name] = BudgetAllocation(
                agent_name=agent_name,
                max_usd=max_usd,
                spent_usd=0.0,
            )

        # Auto-throttle state
        self._throttled_agents: set[str] = set()

        # Events (stored for observer/event bus consumption)
        self._warning_events: list[BudgetWarningEvent] = []
        self._throttle_events: list[BudgetThrottleEvent] = []

        # Track whether warning has been emitted (emit once per threshold crossing)
        self._warning_emitted: bool = False
        self._throttle_emitted: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record_cost(self, agent_name: str, owner: str, cost: Any) -> None:
        """Record a cost event attributed to an agent and owner.

        Parameters
        ----------
        agent_name:
            Name of the agent incurring the cost.
        owner:
            Team/owner identifier for cost attribution.
        cost:
            A Cost dataclass (from tvastar.cost), a float representing USD,
            or a dict with a "usd" key.
        """
        usd = self._extract_usd(cost)

        # Store the event
        event = _CostEvent(
            agent_name=agent_name,
            owner=owner,
            usd=usd,
            timestamp=time.time(),
        )
        self._cost_events.append(event)

        # Update cumulative totals
        self._total_spent += usd
        self._spend_by_agent[agent_name] = self._spend_by_agent.get(agent_name, 0.0) + usd
        self._spend_by_owner[owner] = self._spend_by_owner.get(owner, 0.0) + usd

        # Update per-agent allocation tracking
        if agent_name in self._allocations:
            self._allocations[agent_name].spent_usd += usd

        # Check thresholds and emit events/auto-throttle
        self._check_thresholds()

    def check_budget(self, agent_name: str) -> bool:
        """Check if an agent is allowed to proceed.

        Returns True if:
        - Fleet budget is not exhausted
        - Agent's per-agent allocation is not exhausted (if configured)
        - Agent's unallocated pool share is not exhausted (if no allocation)
        - Agent is not auto-throttled

        Returns False otherwise.
        """
        # Fleet budget exhausted → block all agents
        if self._total_spent >= self._config.max_fleet_usd:
            return False

        # Agent is auto-throttled
        if agent_name in self._throttled_agents:
            return False

        # Per-agent allocation check
        if agent_name in self._allocations:
            alloc = self._allocations[agent_name]
            if alloc.spent_usd >= alloc.max_usd:
                return False
        else:
            # Unallocated agent: draws from pool
            pool = self._unallocated_pool()
            agent_spend = self._spend_by_agent.get(agent_name, 0.0)
            if agent_spend >= pool:
                return False

        return True

    def fleet_spent(self) -> float:
        """Return total fleet-wide spend in USD."""
        return self._total_spent

    def agent_spent(self, agent_name: str) -> float:
        """Return total spend for a specific agent in USD."""
        return self._spend_by_agent.get(agent_name, 0.0)

    def cost_by_agent(self) -> dict[str, float]:
        """Return cost breakdown by agent name."""
        return dict(self._spend_by_agent)

    def cost_by_owner(self) -> dict[str, float]:
        """Return cost breakdown by owner (team)."""
        return dict(self._spend_by_owner)

    def cost_by_period(self, period: str) -> dict[str, float]:
        """Return cost breakdown grouped by time period.

        Parameters
        ----------
        period:
            One of "hourly", "daily", "weekly".

        Returns
        -------
        Dict mapping period bucket label to total USD in that bucket.
        """
        bucket_seconds = self._period_to_seconds(period)
        buckets: dict[str, float] = {}

        for event in self._cost_events:
            bucket_key = self._bucket_key(event.timestamp, bucket_seconds, period)
            buckets[bucket_key] = buckets.get(bucket_key, 0.0) + event.usd

        return buckets

    def reset(self) -> None:
        """Reset all budget tracking state."""
        self._cost_events.clear()
        self._spend_by_agent.clear()
        self._spend_by_owner.clear()
        self._total_spent = 0.0
        self._throttled_agents.clear()
        self._warning_events.clear()
        self._throttle_events.clear()
        self._warning_emitted = False
        self._throttle_emitted = False

        # Reset allocation spend tracking
        for alloc in self._allocations.values():
            alloc.spent_usd = 0.0

    def increase_budget(self, amount: float) -> None:
        """Increase the fleet budget maximum by the given amount.

        Parameters
        ----------
        amount:
            Additional USD to add to the fleet maximum budget.
        """
        if amount <= 0:
            return
        self._config = FleetBudgetConfig(
            max_fleet_usd=self._config.max_fleet_usd + amount,
            allocations=self._config.allocations,
            warn_threshold=self._config.warn_threshold,
            throttle_threshold=self._config.throttle_threshold,
            exempt_agents=self._config.exempt_agents,
            reporting_periods=self._config.reporting_periods,
        )
        # Re-evaluate thresholds — agents may be un-throttled
        self._reevaluate_throttle()

    # ------------------------------------------------------------------
    # Event access (for observer/event bus consumption)
    # ------------------------------------------------------------------

    @property
    def warning_events(self) -> list[BudgetWarningEvent]:
        """Budget pressure warning events emitted."""
        return list(self._warning_events)

    @property
    def throttle_events(self) -> list[BudgetThrottleEvent]:
        """Auto-throttle events emitted."""
        return list(self._throttle_events)

    @property
    def throttled_agents(self) -> set[str]:
        """Currently throttled agent names."""
        return set(self._throttled_agents)

    @property
    def config(self) -> FleetBudgetConfig:
        """Current budget configuration."""
        return self._config

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _extract_usd(self, cost: Any) -> float:
        """Extract USD value from various cost representations.

        Accepts:
        - Cost dataclass (uses .usd property)
        - float (treated as USD directly)
        - int (treated as USD directly)
        - dict with "usd" key
        """
        if isinstance(cost, (int, float)):
            return float(cost)
        if isinstance(cost, dict):
            return float(cost.get("usd", 0.0))
        # Duck typing: try .usd property (works with Cost dataclass)
        if hasattr(cost, "usd"):
            usd_val = cost.usd
            if callable(usd_val):
                return float(usd_val())
            return float(usd_val)
        return 0.0

    def _unallocated_pool(self) -> float:
        """Compute the unallocated pool: fleet_max minus sum of explicit allocations."""
        allocated_sum = sum(self._config.allocations.values())
        return max(0.0, self._config.max_fleet_usd - allocated_sum)

    def _check_thresholds(self) -> None:
        """Check warning and throttle thresholds after a cost event."""
        if self._config.max_fleet_usd <= 0:
            return

        spend_ratio = self._total_spent / self._config.max_fleet_usd

        # Warning threshold
        if spend_ratio >= self._config.warn_threshold and not self._warning_emitted:
            self._emit_warning()
            self._warning_emitted = True

        # Throttle threshold
        if spend_ratio >= self._config.throttle_threshold:
            self._apply_auto_throttle()

    def _emit_warning(self) -> None:
        """Emit a budget pressure warning event."""
        event = BudgetWarningEvent(
            fleet_spent=self._total_spent,
            max_fleet_usd=self._config.max_fleet_usd,
            threshold=self._config.warn_threshold,
        )
        self._warning_events.append(event)

        # Emit tracer span
        self._emit_span(
            "fleet.budget.warning",
            {
                "fleet_spent": self._total_spent,
                "max_fleet_usd": self._config.max_fleet_usd,
                "threshold": self._config.warn_threshold,
            },
        )

    def _apply_auto_throttle(self) -> None:
        """Auto-pause highest-spending non-exempt agents."""
        exempt = set(self._config.exempt_agents)

        # Find non-exempt agents sorted by spend (highest first)
        eligible_agents = [
            (agent, spend)
            for agent, spend in self._spend_by_agent.items()
            if agent not in exempt and agent not in self._throttled_agents
        ]
        eligible_agents.sort(key=lambda x: x[1], reverse=True)

        if not eligible_agents:
            return

        # Throttle the highest-spending non-exempt agent(s)
        # Strategy: throttle the top spender(s) that bring fleet back under threshold
        newly_throttled: list[str] = []
        for agent_name, _spend in eligible_agents:
            if agent_name not in self._throttled_agents:
                self._throttled_agents.add(agent_name)
                newly_throttled.append(agent_name)
                # Throttle at least one agent per invocation
                break

        if newly_throttled:
            throttle_event = BudgetThrottleEvent(
                throttled_agents=newly_throttled,
                fleet_spent=self._total_spent,
                max_fleet_usd=self._config.max_fleet_usd,
                threshold=self._config.throttle_threshold,
            )
            self._throttle_events.append(throttle_event)

            # Emit tracer span
            self._emit_span(
                "fleet.budget.throttle",
                {
                    "throttled_agents": newly_throttled,
                    "fleet_spent": self._total_spent,
                    "max_fleet_usd": self._config.max_fleet_usd,
                    "threshold": self._config.throttle_threshold,
                },
            )

    def _reevaluate_throttle(self) -> None:
        """Re-evaluate auto-throttle after budget increase.

        Unthrottle agents if spend ratio drops below throttle threshold.
        """
        if self._config.max_fleet_usd <= 0:
            return

        spend_ratio = self._total_spent / self._config.max_fleet_usd

        if spend_ratio < self._config.throttle_threshold:
            # Clear throttle state since we're back below threshold
            self._throttled_agents.clear()
            self._throttle_emitted = False

        # Also re-evaluate warning
        if spend_ratio < self._config.warn_threshold:
            self._warning_emitted = False

    def _emit_span(self, name: str, attributes: dict[str, Any]) -> None:
        """Emit a tracer span, swallowing any exceptions."""
        if self._tracer is None:
            return
        try:
            # Use tracer.span() context manager pattern if available
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

    @staticmethod
    def _period_to_seconds(period: str) -> float:
        """Convert period name to bucket size in seconds."""
        periods = {
            "hourly": 3600.0,
            "daily": 86400.0,
            "weekly": 604800.0,
        }
        return periods.get(period, 3600.0)

    @staticmethod
    def _bucket_key(timestamp: float, bucket_seconds: float, period: str) -> str:
        """Generate a human-readable bucket key for a timestamp."""
        import datetime

        dt = datetime.datetime.fromtimestamp(timestamp, tz=datetime.timezone.utc)

        if period == "hourly":
            return dt.strftime("%Y-%m-%d %H:00")
        elif period == "daily":
            return dt.strftime("%Y-%m-%d")
        elif period == "weekly":
            # Use ISO week
            iso_year, iso_week, _ = dt.isocalendar()
            return f"{iso_year}-W{iso_week:02d}"
        else:
            return dt.strftime("%Y-%m-%d %H:00")
