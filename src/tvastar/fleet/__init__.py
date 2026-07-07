"""
tvastar.fleet — fleet engineering layer for managing multiple agent Loops.

A Fleet manages multiple agent Loops as a single cohesive unit with centralized
routing, shared state, unified cost governance, fleet-wide observability, and
safe versioned rollouts.

Core abstraction:
    Fleet = Loop[] + Gateway + SharedState + Budget

Usage:
    from tvastar.fleet import Fleet, FleetConfig, FleetBudgetConfig

    config = FleetConfig(
        name="my-fleet",
        budget=FleetBudgetConfig(max_fleet_usd=100.0),
    )
    fleet = Fleet(config)
    fleet.register(my_loop, name="researcher", version="1.0.0", owner="ml-team")
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------


class FleetError(Exception):
    """Base exception for fleet operations."""


class RegistrationError(FleetError):
    """Raised on invalid registration (duplicate, cycle)."""


class LifecycleError(FleetError):
    """Raised on invalid state transition."""

    def __init__(self, agent: str, current_state: str, attempted_action: str) -> None:
        self.agent = agent
        self.current_state = current_state
        self.attempted_action = attempted_action
        super().__init__(
            f"Cannot {attempted_action} agent {agent!r}: current state is {current_state!r}"
        )


class RoutingError(FleetError):
    """Raised when no suitable agent can be found."""


class RateLimitError(FleetError):
    """Raised when rate limit is exceeded."""

    def __init__(self, scope: str, reset_after: float) -> None:
        self.scope = scope
        self.reset_after = reset_after
        super().__init__(f"Rate limit exceeded for {scope!r}: retry after {reset_after:.1f}s")


class BudgetExhaustedError(FleetError):
    """Raised when fleet or agent budget is exhausted."""


class ConflictError(FleetError):
    """Raised on optimistic locking version mismatch."""

    def __init__(self, key: str, expected_version: int, actual_version: int) -> None:
        self.key = key
        self.expected_version = expected_version
        self.actual_version = actual_version
        super().__init__(
            f"Conflict on key {key!r}: expected version {expected_version}, actual {actual_version}"
        )


class BackendUnavailableError(FleetError):
    """Raised when optional backend cannot be reached."""


# ---------------------------------------------------------------------------
# Shared enums
# ---------------------------------------------------------------------------


class AgentState(str, Enum):
    """Lifecycle states for a registered agent."""

    REGISTERED = "registered"
    ACTIVE = "active"
    PAUSED = "paused"
    RETIRED = "retired"


class ConflictStrategy(str, Enum):
    """Conflict resolution strategies for SharedStateStore."""

    LAST_WRITER_WINS = "lww"
    OPTIMISTIC_LOCKING = "optimistic"


# ---------------------------------------------------------------------------
# Dataclasses — defined here for public API convenience, with full
# implementations in their respective sub-modules (registry.py, gateway.py,
# state.py, etc.)
# ---------------------------------------------------------------------------


@dataclass
class FleetDefaults:
    """Fleet-wide default configuration applied to agents without explicit overrides."""

    default_model: str | None = None
    default_budget_usd: float | None = None
    default_config: dict[str, Any] = field(default_factory=dict)


@dataclass
class FleetBudgetConfig:
    """Configuration for fleet-wide cost governance."""

    max_fleet_usd: float
    allocations: dict[str, float] = field(default_factory=dict)
    warn_threshold: float = 0.8
    throttle_threshold: float = 0.9
    exempt_agents: list[str] = field(default_factory=list)
    reporting_periods: list[str] = field(default_factory=lambda: ["hourly", "daily"])


@dataclass
class RateLimitConfig:
    """Rate limiting configuration (token bucket parameters)."""

    requests_per_window: int
    window_seconds: float = 60.0


@dataclass
class ModelRoutingPolicy:
    """Model assignment policy for fleet agents."""

    agent_models: dict[str, str] = field(default_factory=dict)
    fleet_default_model: str | None = None


@dataclass
class AlertConfig:
    """Threshold configuration for fleet-wide alerting."""

    error_rate_threshold: float = 0.5
    cost_spike_threshold: float = 2.0
    quality_threshold: float = 50.0
    window_seconds: float = 3600.0


@dataclass
class FleetConfig:
    """Top-level configuration for a Fleet instance.

    Attributes:
        alert_handlers: List of callables auto-subscribed to all fleet alert
            topics (quality, error_rate, cost_spike) at Fleet initialization.
    """

    name: str
    budget: FleetBudgetConfig | None = None
    fleet_rate_limit: RateLimitConfig | None = None
    agent_rate_limits: dict[str, RateLimitConfig] = field(default_factory=dict)
    model_policy: ModelRoutingPolicy | None = None
    routing_threshold: float = 0.3
    alert_config: AlertConfig | None = None
    state_backend: str | None = None  # None = in-memory, "redis" = Redis
    event_backend: str | None = None  # None = in-memory, "kafka" = Kafka
    defaults: FleetDefaults | None = None
    alert_handlers: list[Any] = field(
        default_factory=list
    )  # list of callables to subscribe to all alert topics


# ---------------------------------------------------------------------------
# Dataclass re-exports for public API convenience (actual implementations
# live in their respective sub-modules).
# ---------------------------------------------------------------------------


@dataclass
class AgentEntry:
    """A registered agent within a fleet."""

    name: str
    version: str
    owner: str
    loop: Any  # Loop instance — typed as Any to avoid circular import
    state: AgentState = AgentState.REGISTERED
    config_overrides: dict[str, Any] = field(default_factory=dict)
    dependencies: list[str] = field(default_factory=list)
    registered_at: float = 0.0


@dataclass
class AgentVersion:
    """A tagged snapshot of an agent's configuration."""

    version: str
    config_snapshot: dict[str, Any] = field(default_factory=dict)
    quality_score: float | None = None
    created_at: float = 0.0


@dataclass
class FleetEvent:
    """An event published to the fleet EventBus."""

    topic: str
    payload: Any
    source_agent: str
    timestamp: float = 0.0
    correlation_id: str | None = None


@dataclass
class BudgetAllocation:
    """Budget allocation for a single agent."""

    agent_name: str
    max_usd: float
    spent_usd: float = 0.0


@dataclass
class AgentHealthSnapshot:
    """Health status snapshot for a single agent."""

    name: str
    state: AgentState
    last_run_status: str | None = None
    last_run_at: float | None = None
    quality_score: float | None = None


@dataclass
class StateEntry:
    """A versioned entry in the SharedStateStore."""

    key: str
    value: Any
    version: int
    written_by: str
    written_at: float = 0.0


@dataclass
class AuditEntry:
    """An entry in the fleet audit trail."""

    timestamp: float
    event_type: str
    task_description: str
    agent_name: str | None = None
    routing_score: float | None = None
    details: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Sub-module imports — real implementations
# ---------------------------------------------------------------------------


from tvastar.fleet.registry import FleetRegistry as FleetRegistry  # noqa: E402


from tvastar.fleet.gateway import FleetGateway as FleetGateway  # noqa: E402


from tvastar.fleet.state import SharedStateStore as SharedStateStore  # noqa: E402
from tvastar.fleet.state import ConflictRecord as ConflictRecord  # noqa: E402


from tvastar.fleet.bus import EventBus as EventBus  # noqa: E402
from tvastar.fleet.bus import FleetEvent as _FleetEventReal  # noqa: E402, F401
from tvastar.fleet.bus import EventHandler as _EventHandlerReal  # noqa: E402, F401


from tvastar.fleet.budget import FleetBudget as FleetBudget  # noqa: E402
from tvastar.fleet.budget import BudgetWarningEvent as BudgetWarningEvent  # noqa: E402
from tvastar.fleet.budget import BudgetThrottleEvent as BudgetThrottleEvent  # noqa: E402


from tvastar.fleet.observer import FleetObserver as FleetObserver  # noqa: E402


class Fleet:
    """Top-level fleet orchestrator — wires all sub-components together.

    Instantiates and connects FleetRegistry, FleetGateway, SharedStateStore,
    EventBus, FleetBudget, and FleetObserver based on FleetConfig. Exposes
    each sub-component via properties and provides convenience methods for
    common operations (register, submit).

    Supports persistence (persist/load) to survive process restarts, graceful
    shutdown of all registered Loops, and async context manager usage for
    automatic shutdown on scope exit.

    Optional backends (Redis/SQLite for state, NATS/Kafka for events) are
    lazy-loaded. When the corresponding extras package is not installed, a
    descriptive ImportError is raised naming the missing package. When backends
    are None, in-memory implementations are used.

    Parameters
    ----------
    config:
        FleetConfig with all fleet parameters. The ``alert_handlers`` field
        accepts a list of callables that are auto-subscribed to all alert topics.
    tracer:
        Optional Tracer instance passed to all sub-components. Tracer
        exceptions are swallowed by each component independently.
    """

    def __init__(self, config: FleetConfig, *, tracer: Any | None = None) -> None:
        self._config = config
        self._tracer = tracer

        # --- Handle optional backends (lazy-load) ---
        state_backend = None
        if config.state_backend == "sqlite":
            from tvastar.fleet.backends.sqlite_state import SQLiteStateBackend

            state_backend = SQLiteStateBackend()
        elif config.state_backend == "redis":
            from tvastar.fleet.backends.redis_state import RedisStateBackend

            state_backend = RedisStateBackend()

        event_backend = None
        if config.event_backend == "nats":
            from tvastar.fleet.backends.nats_events import NATSEventBackend

            event_backend = NATSEventBackend()
        elif config.event_backend == "kafka":
            from tvastar.fleet._backends import require_backend

            require_backend("kafka", "tvastar[kafka]")
            # Validates that the kafka package is importable.
            event_backend = None

        # --- Instantiate sub-components ---
        self._registry = FleetRegistry(config.name, defaults=config.defaults, tracer=tracer)
        self._gateway = FleetGateway(
            self._registry,
            fleet_rate_limit=config.fleet_rate_limit,
            agent_rate_limits=config.agent_rate_limits or {},
            model_policy=config.model_policy,
            routing_threshold=config.routing_threshold,
            tracer=tracer,
        )
        self._state = SharedStateStore(config.name, backend=state_backend)
        self._bus = EventBus(config.name, backend=event_backend)
        self._budget: FleetBudget | None = (
            FleetBudget(config.budget, tracer=tracer) if config.budget else None
        )
        self._observer = FleetObserver(
            self._registry,
            self._bus,
            tracer=tracer,
            alert_config=config.alert_config,
        )

        # Auto-subscribe alert handlers to all fleet alert topics
        _alert_topics = ["fleet.alert.quality", "fleet.alert.error_rate", "fleet.alert.cost_spike"]
        for handler in config.alert_handlers:
            for topic in _alert_topics:
                self._bus.subscribe(topic, handler)

    # ------------------------------------------------------------------
    # Properties — expose sub-components
    # ------------------------------------------------------------------

    @property
    def config(self) -> FleetConfig:
        """The fleet configuration."""
        return self._config

    @property
    def registry(self) -> FleetRegistry:
        """The fleet agent registry."""
        return self._registry

    @property
    def gateway(self) -> FleetGateway:
        """The fleet gateway (routing, rate limiting, model routing)."""
        return self._gateway

    @property
    def state(self) -> SharedStateStore:
        """The fleet-scoped shared state store."""
        return self._state

    @property
    def bus(self) -> EventBus:
        """The fleet event bus (pub/sub)."""
        return self._bus

    @property
    def budget(self) -> FleetBudget | None:
        """The fleet budget manager, or None if no budget configured."""
        return self._budget

    @property
    def observer(self) -> FleetObserver:
        """The fleet observer (health, alerting, trace correlation)."""
        return self._observer

    # ------------------------------------------------------------------
    # Convenience methods
    # ------------------------------------------------------------------

    def _wire_loop_to_observer(self, name: str, loop: Any) -> None:
        """Auto-record loop outcomes in the observer for health tracking."""
        if loop is None or not hasattr(loop, "on_event"):
            return

        def _on_loop_event(event):
            from tvastar.loop import LoopState

            if event.state == LoopState.PASS:
                self._observer.record_outcome(is_error=False)
                # Update quality score
                self._observer.record_quality_score(name, 100.0)
            elif event.state in (LoopState.FAIL, LoopState.HANDOFF):
                self._observer.record_outcome(is_error=True)
                self._observer.record_quality_score(name, 0.0)

        loop.on_event(_on_loop_event)

    def register(
        self,
        loop: Any,
        *,
        name: str,
        version: str = "1.0.0",
        owner: str = "default",
        dependencies: list[str] | None = None,
    ) -> AgentEntry:
        """Register a Loop instance as an agent in this fleet.

        Convenience wrapper around self.registry.register(). Accepts the Loop
        without modification (composition over inheritance).

        Parameters
        ----------
        loop:
            The Loop instance to register.
        name:
            Unique agent name within the fleet.
        version:
            Version string for this agent entry (default "1.0.0").
        owner:
            Owner responsible for this agent (default "default").
        dependencies:
            Optional list of agent names this agent depends on.

        Returns
        -------
        AgentEntry with identity assigned and fleet defaults applied.
        """
        entry = self._registry.register(
            loop,
            name=name,
            version=version,
            owner=owner,
            dependencies=dependencies,
        )
        self._wire_loop_to_observer(name, loop)
        return entry

    async def submit(self, task: str, **kwargs: Any) -> Any:
        """Submit a task to the fleet for routing and execution.

        Convenience wrapper around self.gateway.submit(). Routes the task
        to the best-matching active agent (or an explicitly named agent).

        Parameters
        ----------
        task:
            The task description to route and execute.
        **kwargs:
            Additional keyword arguments passed to FleetGateway.submit()
            (e.g. agent=, context=).

        Returns
        -------
        The dispatch result from the gateway.
        """
        return await self._gateway.submit(task, **kwargs)

    # ------------------------------------------------------------------
    # Persistence — survive process restarts (#45)
    # ------------------------------------------------------------------

    def persist(self, path: str | None = None) -> str:
        """Persist fleet registry state to a JSON file.

        Saves all agent registrations, lifecycle states, version histories,
        and dependencies so the fleet can be reloaded after a process restart.

        Parameters
        ----------
        path:
            File path to write. Defaults to `.tvastar-fleet/{name}.json`.

        Returns
        -------
        The path the state was written to.
        """
        import json
        from pathlib import Path

        if path is None:
            dir_path = Path(".tvastar-fleet")
            dir_path.mkdir(parents=True, exist_ok=True)
            path = str(dir_path / f"{self._config.name}.json")

        state = {
            "fleet_name": self._config.name,
            "agents": {},
            "versions": {},
            "dependencies": {},
        }

        for name, entry in self._registry._agents.items():
            state["agents"][name] = {
                "name": entry.name,
                "version": entry.version,
                "owner": entry.owner,
                "state": entry.state.value,
                "config_overrides": entry.config_overrides,
                "dependencies": entry.dependencies,
                "registered_at": entry.registered_at,
            }

        for name, versions in self._registry._versions.items():
            state["versions"][name] = [
                {
                    "version": v.version,
                    "config_snapshot": v.config_snapshot,
                    "quality_score": v.quality_score,
                    "created_at": v.created_at,
                }
                for v in versions
            ]

        state["dependencies"] = dict(self._registry._dependencies)

        Path(path).write_text(json.dumps(state, indent=2), encoding="utf-8")
        return path

    def load(self, path: str | None = None) -> int:
        """Load fleet registry state from a persisted JSON file.

        Restores agent metadata (state, versions, dependencies) but NOT
        the Loop instances — those must be re-registered. Loaded agents
        are placed in their persisted lifecycle state.

        Parameters
        ----------
        path:
            File path to read. Defaults to `.tvastar-fleet/{name}.json`.

        Returns
        -------
        Number of agent entries restored.
        """
        import json
        from pathlib import Path

        if path is None:
            path = str(Path(".tvastar-fleet") / f"{self._config.name}.json")

        file_path = Path(path)
        if not file_path.exists():
            return 0

        try:
            state = json.loads(file_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return 0

        from tvastar.fleet.registry import AgentEntry, AgentState, AgentVersion

        count = 0
        for name, data in state.get("agents", {}).items():
            entry = AgentEntry(
                name=data["name"],
                version=data["version"],
                owner=data["owner"],
                loop=None,  # Loop must be re-registered
                state=AgentState(data["state"]),
                config_overrides=data.get("config_overrides", {}),
                dependencies=data.get("dependencies", []),
                registered_at=data.get("registered_at", 0.0),
            )
            self._registry._agents[name] = entry

            # Maintain active_set index
            if entry.state == AgentState.ACTIVE:
                self._registry._active_set.add(name)

            count += 1

        # Restore version histories
        for name, versions in state.get("versions", {}).items():
            self._registry._versions[name] = [
                AgentVersion(
                    version=v["version"],
                    config_snapshot=v.get("config_snapshot", {}),
                    quality_score=v.get("quality_score"),
                    created_at=v.get("created_at", 0.0),
                )
                for v in versions
            ]

        # Restore dependency graph
        self._registry._dependencies = state.get("dependencies", {})

        return count

    # ------------------------------------------------------------------
    # Graceful shutdown (#49)
    # ------------------------------------------------------------------

    async def shutdown(self, persist: bool = True) -> None:
        """Gracefully shut down the fleet.

        1. Persists registry state (if persist=True)
        2. Stops all registered Loops that have a stop() method
        3. Closes backend connections

        Parameters
        ----------
        persist:
            Whether to save fleet state before shutting down (default True).
        """
        # 1. Persist state
        if persist:
            try:
                self.persist()
            except Exception:
                pass  # persistence failure must not block shutdown

        # 2. Stop all registered loops
        for name in list(self._registry._agents.keys()):
            entry = self._registry.get(name)
            if entry is None:
                continue
            loop = entry.loop
            if loop is not None and hasattr(loop, "stop") and callable(loop.stop):
                try:
                    await loop.stop()
                except Exception:
                    pass  # individual loop stop failure must not block others

        # 3. Close backends
        if hasattr(self._state, "_backend") and self._state._backend is not None:
            backend = self._state._backend
            if hasattr(backend, "close"):
                try:
                    close_result = backend.close()
                    if hasattr(close_result, "__await__"):
                        await close_result
                except Exception:
                    pass

    async def __aenter__(self) -> "Fleet":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.shutdown()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # Top-level orchestrator
    "Fleet",
    "FleetConfig",
    # Sub-components
    "FleetRegistry",
    "FleetGateway",
    "SharedStateStore",
    "EventBus",
    "FleetBudget",
    "FleetObserver",
    # Dataclasses
    "AgentEntry",
    "AgentState",
    "AgentVersion",
    "RateLimitConfig",
    "ModelRoutingPolicy",
    "FleetEvent",
    "BudgetAllocation",
    "FleetBudgetConfig",
    "AlertConfig",
    "AgentHealthSnapshot",
    "StateEntry",
    "ConflictStrategy",
    "ConflictRecord",
    "AuditEntry",
    "FleetDefaults",
    # Budget events
    "BudgetWarningEvent",
    "BudgetThrottleEvent",
    # Exceptions
    "FleetError",
    "RegistrationError",
    "LifecycleError",
    "RoutingError",
    "RateLimitError",
    "BudgetExhaustedError",
    "ConflictError",
    "BackendUnavailableError",
]
