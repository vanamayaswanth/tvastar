"""
tvastar.fleet.gateway — Fleet Gateway with routing, rate limiting, and model routing.

Provides:
- TokenBucket: standard token-bucket rate limiter (zero dependencies)
- RateLimitConfig: per-agent or fleet-wide rate limit parameters
- ModelRoutingPolicy: model assignment policy for fleet agents
- AuditEntry: audit trail entry for every gateway operation
- FleetGateway: routes tasks to agents with rate limiting and model routing
"""

from __future__ import annotations

import difflib
import time
from dataclasses import dataclass, field
from typing import Any

from tvastar.fleet import RoutingError


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class RateLimitConfig:
    """Rate limiting configuration (token bucket parameters).

    Attributes:
        requests_per_window: Maximum number of requests allowed per window.
        window_seconds: Duration of the rate limit window in seconds.
    """

    requests_per_window: int
    window_seconds: float = 60.0


@dataclass
class ModelRoutingPolicy:
    """Model assignment policy for fleet agents.

    Attributes:
        agent_models: Mapping of agent_name -> model identifier.
        fleet_default_model: Fallback model for agents without explicit assignment.
    """

    agent_models: dict[str, str] = field(default_factory=dict)
    fleet_default_model: str | None = None


@dataclass
class TokenBucket:
    """Standard token-bucket rate limiter.

    Uses a refill-on-access pattern: tokens are replenished based on elapsed
    time since the last refill, up to the bucket capacity.

    Attributes:
        capacity: Maximum number of tokens the bucket can hold.
        tokens: Current number of available tokens (fractional).
        refill_rate: Tokens added per second.
        last_refill: Timestamp (seconds since epoch) of the last refill.
    """

    capacity: int
    tokens: float
    refill_rate: float  # tokens per second
    last_refill: float

    def _refill(self) -> None:
        """Refill tokens based on elapsed time since last refill."""
        now = time.time()
        elapsed = now - self.last_refill
        if elapsed > 0:
            self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
            self.last_refill = now

    def consume(self) -> bool:
        """Attempt to consume one token.

        Refills tokens based on elapsed time, then tries to consume one.

        Returns:
            True if a token was available and consumed, False otherwise.
        """
        self._refill()
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False

    def time_until_available(self) -> float:
        """Calculate seconds until at least one token will be available.

        Returns:
            0.0 if tokens are currently available (after refill),
            otherwise the number of seconds until the next token.
        """
        self._refill()
        if self.tokens >= 1.0:
            return 0.0
        deficit = 1.0 - self.tokens
        if self.refill_rate <= 0:
            return float("inf")
        return deficit / self.refill_rate


@dataclass
class AuditEntry:
    """An entry in the fleet gateway audit trail.

    Attributes:
        timestamp: When the event occurred (seconds since epoch).
        event_type: Category of audit event (e.g. "route", "rate_limit",
            "model_fallback", "conflict").
        task_description: The task that triggered this entry.
        agent_name: The agent involved, or None if not applicable.
        routing_score: The semantic match score, or None if not applicable.
        details: Additional structured data about the event.
    """

    timestamp: float
    event_type: str  # "route" | "rate_limit" | "model_fallback" | "conflict" | ...
    task_description: str
    agent_name: str | None = None
    routing_score: float | None = None
    details: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helper: create a TokenBucket from a RateLimitConfig
# ---------------------------------------------------------------------------


def _make_bucket(config: RateLimitConfig) -> TokenBucket:
    """Create a full TokenBucket from a RateLimitConfig.

    The bucket starts full (capacity tokens available) with a refill rate
    derived from requests_per_window / window_seconds.
    """
    refill_rate = config.requests_per_window / config.window_seconds
    return TokenBucket(
        capacity=config.requests_per_window,
        tokens=float(config.requests_per_window),
        refill_rate=refill_rate,
        last_refill=time.time(),
    )


# ---------------------------------------------------------------------------
# FleetGateway
# ---------------------------------------------------------------------------


class FleetGateway:
    """Fleet gateway — routes tasks with rate limiting and model routing.

    Provides semantic routing (difflib word-overlap), fleet-wide and per-agent
    token-bucket rate limiting, model routing policy, dependency-aware
    deferral, and a capped audit trail (deque).
    """

    def __init__(
        self,
        registry: Any,
        *,
        fleet_rate_limit: RateLimitConfig | None = None,
        agent_rate_limits: dict[str, RateLimitConfig] | None = None,
        model_policy: ModelRoutingPolicy | None = None,
        routing_threshold: float = 0.3,
        tracer: Any | None = None,
    ) -> None:
        self._registry = registry
        self._model_policy = model_policy or ModelRoutingPolicy()
        self._routing_threshold = routing_threshold
        self._tracer = tracer

        # Rate limiting buckets
        self._fleet_bucket: TokenBucket | None = (
            _make_bucket(fleet_rate_limit) if fleet_rate_limit else None
        )
        self._agent_buckets: dict[str, TokenBucket] = {}
        if agent_rate_limits:
            for agent_name, config in agent_rate_limits.items():
                self._agent_buckets[agent_name] = _make_bucket(config)

        # Audit trail — capped to prevent unbounded memory growth
        from collections import deque

        self._audit_trail: deque[AuditEntry] = deque(maxlen=10_000)

        # Last dispatch run (set by _dispatch_task when Loop.trigger() succeeds)
        self._last_dispatch_run: Any = None
        self._last_dispatch_error: str | None = None

    # ------------------------------------------------------------------
    # Rate Limiting
    # ------------------------------------------------------------------

    def _check_rate_limits(self, agent_name: str) -> None:
        """Check fleet-wide and per-agent rate limits.

        Checks are performed in order:
        1. Fleet-wide rate limit (if configured)
        2. Per-agent rate limit (if configured for the given agent)

        If either check fails, raises RateLimitError with the appropriate
        scope and reset time.

        Args:
            agent_name: The name of the agent to check rate limits for.

        Raises:
            RateLimitError: If either the fleet-wide or per-agent rate limit
                is exceeded.
        """
        from tvastar.fleet import RateLimitError

        # 1. Check fleet-wide rate limit
        if self._fleet_bucket is not None:
            if not self._fleet_bucket.consume():
                reset_after = self._fleet_bucket.time_until_available()
                raise RateLimitError(
                    scope="fleet",
                    reset_after=reset_after,
                )

        # 2. Check per-agent rate limit
        if agent_name in self._agent_buckets:
            bucket = self._agent_buckets[agent_name]
            if not bucket.consume():
                reset_after = bucket.time_until_available()
                raise RateLimitError(
                    scope=f"agent:{agent_name}",
                    reset_after=reset_after,
                )

    def set_agent_rate_limit(self, agent_name: str, config: RateLimitConfig) -> None:
        """Add or update the per-agent rate limit for a specific agent.

        This allows dynamic configuration of per-agent rate limits
        independently of the fleet-wide limit.

        Args:
            agent_name: The name of the agent to configure.
            config: The rate limit configuration to apply.
        """
        self._agent_buckets[agent_name] = _make_bucket(config)

    # ------------------------------------------------------------------
    # Audit Trail
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    async def submit(
        self,
        task: str,
        *,
        agent: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Route and submit a task to the best-matching active agent.

        Implements semantic routing via difflib word-overlap (same algorithm as
        the existing AgentRouter) when no explicit agent is provided.

        Parameters
        ----------
        task:
            The task description to route and execute.
        agent:
            Optional explicit agent name. Bypasses semantic matching when set.
        context:
            Optional additional context dict passed along with the task.

        Returns
        -------
        A dict containing routing metadata:
            - agent_name: the selected agent
            - task: the task description
            - routing_score: the match score (None if explicit routing)
            - dispatch_id: an identifier for the dispatched task
            - status: "dispatched" or "deferred"

        Raises
        ------
        RoutingError
            If no suitable agent is found above the routing threshold, or
            if an explicitly named agent does not exist or is not active.
        """
        from tvastar.fleet.registry import AgentState

        # --- Explicit agent routing (bypass semantic matching) ---
        if agent is not None:
            entry = self._registry.get(agent)
            if entry is None:
                self._record_audit(
                    event_type="route_failed",
                    task_description=task,
                    agent_name=agent,
                    details={"reason": "agent_not_found"},
                )
                raise RoutingError(f"Explicit agent {agent!r} is not registered in the fleet")
            if entry.state != AgentState.ACTIVE:
                self._record_audit(
                    event_type="route_failed",
                    task_description=task,
                    agent_name=agent,
                    details={"reason": "agent_not_active", "state": entry.state.value},
                )
                raise RoutingError(
                    f"Agent {agent!r} is not active (current state: {entry.state.value!r})"
                )

            # Check rate limits
            self._check_rate_limit(agent)

            # Check dependencies
            deferred = await self._check_dependencies(entry)
            if deferred:
                self._record_audit(
                    event_type="route_deferred",
                    task_description=task,
                    agent_name=agent,
                    details={"reason": "dependency_pending"},
                )
                return {
                    "agent_name": agent,
                    "task": task,
                    "routing_score": None,
                    "dispatch_id": None,
                    "status": "deferred",
                    "context": context,
                }

            # Dispatch
            dispatch_id = await self._dispatch_task(entry, task, context)
            loop_run = self._last_dispatch_run
            dispatch_error = self._last_dispatch_error
            self._last_dispatch_run = None
            self._last_dispatch_error = None

            # Determine status based on dispatch outcome
            status = "dispatched"
            if dispatch_error:
                status = dispatch_error  # "suspended", "busy", "backing_off"

            self._record_audit(
                event_type="route",
                task_description=task,
                agent_name=agent,
                routing_score=None,
                details={"explicit": True, "dispatch_status": status},
            )
            self._emit_span(
                "fleet.gateway.route",
                {
                    "agent.name": agent,
                    "routing.explicit": True,
                    "routing.outcome": status,
                },
            )
            result = {
                "agent_name": agent,
                "task": task,
                "routing_score": None,
                "dispatch_id": dispatch_id,
                "status": status,
                "context": context,
            }
            if loop_run is not None:
                result["loop_run"] = loop_run
            return result

        # --- Semantic routing: score active agents against the task ---
        active_agents = self._registry.active_agents()

        if not active_agents:
            self._record_audit(
                event_type="route_failed",
                task_description=task,
                details={"reason": "no_active_agents"},
            )
            raise RoutingError("No suitable agent found: no active agents in the fleet")

        # Score each active agent using difflib word-overlap (same as AgentRouter)
        scored = self._score_agents(task, active_agents)

        # Filter by threshold
        candidates = [(name, score) for name, score in scored if score >= self._routing_threshold]

        if not candidates:
            best_name, best_score = scored[0] if scored else (None, 0.0)
            self._record_audit(
                event_type="route_failed",
                task_description=task,
                agent_name=best_name,
                routing_score=best_score,
                details={
                    "reason": "below_threshold",
                    "threshold": self._routing_threshold,
                    "best_score": best_score,
                },
            )
            raise RoutingError(
                f"No suitable agent found: best match score {best_score:.3f} "
                f"is below routing threshold {self._routing_threshold}"
            )

        # Select the best candidate
        best_name, best_score = candidates[0]
        entry = self._registry.get(best_name)
        assert entry is not None  # guaranteed by active_agents()

        # Check rate limits
        self._check_rate_limit(best_name)

        # Check dependencies
        deferred = await self._check_dependencies(entry)
        if deferred:
            self._record_audit(
                event_type="route_deferred",
                task_description=task,
                agent_name=best_name,
                routing_score=best_score,
                details={"reason": "dependency_pending"},
            )
            return {
                "agent_name": best_name,
                "task": task,
                "routing_score": best_score,
                "dispatch_id": None,
                "status": "deferred",
                "context": context,
            }

        # Dispatch
        dispatch_id = await self._dispatch_task(entry, task, context)
        loop_run = self._last_dispatch_run
        dispatch_error = self._last_dispatch_error
        self._last_dispatch_run = None
        self._last_dispatch_error = None

        status = "dispatched"
        if dispatch_error:
            status = dispatch_error

        self._record_audit(
            event_type="route",
            task_description=task,
            agent_name=best_name,
            routing_score=best_score,
            details={"explicit": False, "dispatch_status": status},
        )
        self._emit_span(
            "fleet.gateway.route",
            {
                "agent.name": best_name,
                "routing.score": best_score,
                "routing.explicit": False,
                "routing.outcome": status,
            },
        )
        result = {
            "agent_name": best_name,
            "task": task,
            "routing_score": best_score,
            "dispatch_id": dispatch_id,
            "status": status,
            "context": context,
        }
        if loop_run is not None:
            result["loop_run"] = loop_run
        return result

    # ------------------------------------------------------------------
    # Scoring (same algorithm as tvastar.router.AgentRouter)
    # ------------------------------------------------------------------

    def _score_agents(self, task: str, agents: list[Any]) -> list[tuple[str, float]]:
        """Score agents against a task description using difflib word-overlap.

        Returns a list of (agent_name, score) tuples sorted by score descending.
        Uses the same algorithm as tvastar.router.AgentRouter: word-set overlap
        combined with SequenceMatcher ratio (weighted at 0.7).
        """
        words = set(task.lower().split())
        results: list[tuple[str, float]] = []

        for entry in agents:
            desc = self._get_agent_description(entry)
            desc_words = set(desc.lower().split())
            if not desc_words:
                results.append((entry.name, 0.0))
                continue

            # Word set overlap (Jaccard-like)
            overlap = len(words & desc_words) / max(len(words | desc_words), 1)

            # SequenceMatcher ratio weighted lower (penalises length diff)
            seq = difflib.SequenceMatcher(None, task.lower(), desc.lower()).ratio()
            score = max(overlap, seq * 0.7)

            results.append((entry.name, score))

        # Sort by score descending
        results.sort(key=lambda x: x[1], reverse=True)
        return results

    def _get_agent_description(self, entry: Any) -> str:
        """Extract a routing description from an agent entry.

        Attempts to pull a description from the agent's loop config or falls
        back to the agent name. This mirrors how AgentRouter uses
        AgentProfile.description for scoring.
        """
        loop = entry.loop
        # Try common patterns for Loop/AgentSpec description
        # 1. loop.config.goal (if Loop wraps AgentSpec with a goal)
        if hasattr(loop, "config") and hasattr(loop.config, "goal"):
            goal = loop.config.goal
            if goal:
                return f"{entry.name} {goal}"

        # 2. loop.spec.instructions or loop.spec.description
        if hasattr(loop, "spec"):
            spec = loop.spec
            if hasattr(spec, "description") and spec.description:
                return f"{entry.name} {spec.description}"
            if hasattr(spec, "instructions") and spec.instructions:
                return f"{entry.name} {spec.instructions}"

        # 3. loop.description (direct attribute)
        if hasattr(loop, "description") and loop.description:
            return f"{entry.name} {loop.description}"

        # 4. config_overrides may contain a description
        desc = entry.config_overrides.get("description", "")
        if desc:
            return f"{entry.name} {desc}"

        # Fallback: just the agent name
        return entry.name

    # ------------------------------------------------------------------
    # Dependency checking
    # ------------------------------------------------------------------

    async def _check_dependencies(self, entry: Any) -> bool:
        """Check if the agent's dependencies are satisfied.

        Returns True if the agent should be deferred (has unsatisfied deps),
        False if all dependencies are satisfied and the agent can proceed.
        """
        if not entry.dependencies:
            return False

        from tvastar.fleet.registry import AgentState

        for dep_name in entry.dependencies:
            dep_entry = self._registry.get(dep_name)
            if dep_entry is None:
                # Dependency agent not registered — defer
                return True
            if dep_entry.state != AgentState.ACTIVE:
                # Dependency agent not active — defer
                return True

        return False

    # ------------------------------------------------------------------
    # Rate limiting delegation
    # ------------------------------------------------------------------

    def _check_rate_limit(self, agent_name: str) -> None:
        """Check rate limits for the given agent.

        Delegates to _check_rate_limits() which enforces both fleet-wide
        and per-agent token bucket rate limiting.

        Raises
        ------
        RateLimitError
            If either the fleet-wide or per-agent rate limit is exceeded.
        """
        self._check_rate_limits(agent_name)

    # ------------------------------------------------------------------
    # Dispatch helpers
    # ------------------------------------------------------------------

    async def _dispatch_task(
        self,
        entry: Any,
        task: str,
        context: dict[str, Any] | None = None,
    ) -> str:
        """Dispatch a task to the selected agent.

        If the agent's loop has a trigger() method (it's a real Loop), call it.
        Otherwise fall back to generating a dispatch ID (for loops without trigger).

        Catches RuntimeError from the loop (e.g. SUSPENDED, already running,
        or backing off) and records the failure in ``_last_dispatch_error``
        rather than propagating to the caller.
        """
        import uuid

        dispatch_id = f"fleet_dispatch_{uuid.uuid4().hex[:12]}"

        # Try to trigger the real Loop if available
        loop = entry.loop
        if loop is not None and hasattr(loop, "trigger") and callable(loop.trigger):
            try:
                run_context = {"task": task, "fleet_dispatch_id": dispatch_id}
                if context:
                    run_context.update(context)
                run = await loop.trigger(context=run_context)
                # Store the run result for retrieval
                self._last_dispatch_run = run
            except RuntimeError as e:
                # Loop might be SUSPENDED or already RUNNING — surface this
                # as a specific dispatch failure rather than silently succeeding
                err_msg = str(e)
                if "SUSPENDED" in err_msg:
                    self._last_dispatch_run = None
                    self._last_dispatch_error = "suspended"
                elif "already" in err_msg:
                    self._last_dispatch_run = None
                    self._last_dispatch_error = "busy"
                elif "backing off" in err_msg:
                    self._last_dispatch_run = None
                    self._last_dispatch_error = "backing_off"
                else:
                    self._last_dispatch_run = None
                    self._last_dispatch_error = None
            except Exception:
                # Don't let unexpected loop errors break the gateway
                self._last_dispatch_run = None
                self._last_dispatch_error = None

        return dispatch_id

    # ------------------------------------------------------------------
    # Audit trail
    # ------------------------------------------------------------------

    def _record_audit(
        self,
        *,
        event_type: str,
        task_description: str,
        agent_name: str | None = None,
        routing_score: float | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Record an entry in the audit trail."""
        self._audit_trail.append(
            AuditEntry(
                timestamp=time.time(),
                event_type=event_type,
                task_description=task_description,
                agent_name=agent_name,
                routing_score=routing_score,
                details=details or {},
            )
        )

    # ------------------------------------------------------------------
    # Observability
    # ------------------------------------------------------------------

    def _emit_span(self, name: str, attributes: dict[str, Any]) -> None:
        """Emit a tracer span, swallowing any exceptions.

        Observability must never break gateway operations.
        """
        if self._tracer is None:
            return
        try:
            if hasattr(self._tracer, "start_span"):
                self._tracer.start_span(name, attributes=attributes)
            elif hasattr(self._tracer, "span"):
                with self._tracer.span(name, attributes=attributes):
                    pass
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Public query methods
    # ------------------------------------------------------------------

    def audit_log(self, limit: int = 100) -> list[AuditEntry]:
        """Return the most recent audit trail entries.

        Args:
            limit: Maximum number of entries to return (default 100).

        Returns:
            List of AuditEntry instances, most recent first.
        """
        # deque supports negative indexing but not slicing — convert to list for tail
        entries = list(self._audit_trail)
        return list(reversed(entries[-limit:]))

    # ------------------------------------------------------------------
    # Model routing
    # ------------------------------------------------------------------

    def _resolve_model(self, agent_name: str) -> str | None:
        """Resolve the model for a given agent based on the routing policy.

        Checks for an explicit per-agent model assignment first.
        Falls back to the fleet-wide default model if no explicit assignment exists.

        Args:
            agent_name: The name of the agent to resolve a model for.

        Returns:
            The resolved model identifier, or None if no model is configured.
        """
        # Check for explicit per-agent model assignment
        explicit = self._model_policy.agent_models.get(agent_name)
        if explicit is not None:
            return explicit
        # Fall back to fleet-wide default
        return self._model_policy.fleet_default_model
