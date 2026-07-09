"""
tvastar.fleet.registry — Fleet agent registry with lifecycle FSM.

Manages agent registration, lifecycle transitions, versioning, dependency
tracking with cycle detection, and fleet-wide default resolution.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from tvastar.fleet import LifecycleError, RegistrationError


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class AgentState(str, Enum):
    """Lifecycle states for a registered agent."""

    REGISTERED = "registered"
    ACTIVE = "active"
    PAUSED = "paused"
    RETIRED = "retired"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class AgentEntry:
    """A registered agent within a fleet.

    Holds the Loop instance, identity metadata, lifecycle state, and
    configuration overrides for a single agent managed by the FleetRegistry.
    """

    name: str
    version: str
    owner: str
    loop: Any  # Loop instance — typed as Any to avoid coupling to tvastar.loop
    state: AgentState = AgentState.REGISTERED
    config_overrides: dict[str, Any] = field(default_factory=dict)
    dependencies: list[str] = field(default_factory=list)
    registered_at: float = field(default_factory=time.time)


@dataclass
class AgentVersion:
    """A tagged snapshot of an agent's configuration.

    Used for version history, rollback, and canary/A/B deployment tracking.
    """

    version: str
    config_snapshot: dict[str, Any] = field(default_factory=dict)
    quality_score: float | None = None
    created_at: float = field(default_factory=time.time)


@dataclass
class FleetDefaults:
    """Fleet-wide default configuration applied to agents without explicit overrides.

    When an agent is registered without specific config_overrides, the registry
    resolves its effective configuration from these defaults.
    """

    default_model: str | None = None
    default_budget_usd: float | None = None
    default_config: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# FleetRegistry
# ---------------------------------------------------------------------------


class FleetRegistry:
    """Fleet agent registry — manages registration, lifecycle, and versioning.

    The registry is the source of truth for all agents within a fleet. It
    enforces lifecycle FSM transitions, detects circular dependencies at
    registration time, and maintains version history for rollback/canary support.

    Parameters
    ----------
    fleet_name:
        The name of the fleet this registry belongs to.
    defaults:
        Optional fleet-wide default configuration.
    tracer:
        Optional Tracer instance for span emission. Exceptions from the tracer
        are swallowed to never break registry operations.
    """

    # Valid FSM transitions: state -> { action -> target_state }
    _VALID_TRANSITIONS: dict[AgentState, dict[str, AgentState]] = {
        AgentState.REGISTERED: {
            "deploy": AgentState.ACTIVE,
            "retire": AgentState.RETIRED,
        },
        AgentState.ACTIVE: {
            "pause": AgentState.PAUSED,
            "retire": AgentState.RETIRED,
        },
        AgentState.PAUSED: {
            "resume": AgentState.ACTIVE,
            "retire": AgentState.RETIRED,
        },
        AgentState.RETIRED: {},
    }

    def __init__(
        self,
        fleet_name: str = "default",
        *,
        defaults: FleetDefaults | None = None,
        tracer: Any | None = None,
        max_versions: int = 50,
    ) -> None:
        self._fleet_name = fleet_name
        self._defaults = defaults or FleetDefaults()
        self._tracer = tracer
        self._max_versions = max_versions

        # Agent storage: name -> AgentEntry
        self._agents: dict[str, AgentEntry] = {}

        # Secondary index: O(1) active agent lookup instead of O(n) scan
        self._active_set: set[str] = set()

        # Version history: name -> list[AgentVersion]
        self._versions: dict[str, list[AgentVersion]] = {}

        # Version index: name -> {version_str -> AgentVersion} for O(1) rollback
        self._version_index: dict[str, dict[str, AgentVersion]] = {}

        # Dependency graph: name -> list of dependency names
        self._dependencies: dict[str, list[str]] = {}

    @property
    def fleet_name(self) -> str:
        """The name of the fleet this registry manages."""
        return self._fleet_name

    @property
    def defaults(self) -> FleetDefaults:
        """Fleet-wide default configuration."""
        return self._defaults

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(
        self,
        loop: Any,
        *,
        name: str,
        version: str,
        owner: str,
        dependencies: list[str] | None = None,
    ) -> AgentEntry:
        """Register a Loop instance as an agent in this fleet.

        Parameters
        ----------
        loop:
            The Loop instance to register (accepted without modification).
        name:
            Unique agent name within the fleet.
        version:
            Version string for this agent entry.
        owner:
            Owner (team/individual) responsible for this agent.
        dependencies:
            Optional list of agent names this agent depends on.

        Returns
        -------
        AgentEntry with identity assigned and fleet defaults applied.

        Raises
        ------
        RegistrationError
            If an agent with the same (name, version) pair already exists,
            or if the dependencies would introduce a circular dependency.
        """
        # Check for duplicate (name, version) pair
        existing = self._agents.get(name)
        if existing is not None and existing.version == version:
            raise RegistrationError(f"Agent {name!r} version {version!r} is already registered")

        deps = dependencies or []

        # Check for circular dependencies before registering
        if deps:
            cycle = self._detect_cycle(name, deps)
            if cycle is not None:
                cycle_path = " -> ".join(cycle)
                raise RegistrationError(f"Circular dependency detected: {cycle_path}")

        # Build config_overrides — apply fleet defaults if agent has none
        config_overrides: dict[str, Any] = {}
        if self._defaults.default_config:
            config_overrides = dict(self._defaults.default_config)

        # Create the agent entry
        entry = AgentEntry(
            name=name,
            version=version,
            owner=owner,
            loop=loop,
            state=AgentState.REGISTERED,
            config_overrides=config_overrides,
            dependencies=deps,
            registered_at=time.time(),
        )

        # Store agent
        self._agents[name] = entry

        # Store initial version snapshot
        version_entry = AgentVersion(
            version=version,
            config_snapshot=dict(config_overrides),
        )
        self._versions.setdefault(name, []).append(version_entry)

        # Update version index for O(1) rollback (Bug 4 fix)
        self._version_index.setdefault(name, {})[version] = version_entry

        # Cap version history (Bug 3 fix)
        if len(self._versions[name]) > self._max_versions:
            self._versions[name] = self._versions[name][-self._max_versions:]
            # Rebuild index from trimmed list
            self._version_index[name] = {v.version: v for v in self._versions[name]}

        # Record dependencies
        self._dependencies[name] = list(deps)

        # Emit Tracer span (swallow tracer errors per design)
        self._emit_span(
            "fleet.registry.register",
            {
                "fleet.name": self._fleet_name,
                "agent.name": name,
                "agent.version": version,
                "agent.owner": owner,
            },
        )

        return entry

    # ------------------------------------------------------------------
    # Query methods
    # ------------------------------------------------------------------

    def get(self, name: str) -> AgentEntry | None:
        """Return the agent entry for the given name, or None if not found."""
        return self._agents.get(name)

    def active_agents(self) -> list[AgentEntry]:
        """Return all agents with state == ACTIVE. Uses O(k) index instead of O(n) scan."""
        return [self._agents[name] for name in self._active_set if name in self._agents]

    def count(self) -> int:
        """Return the total number of registered agents."""
        return len(self._agents)

    # ------------------------------------------------------------------
    # Lifecycle transitions
    # ------------------------------------------------------------------

    def _transition(self, name: str, action: str) -> AgentEntry:
        """Validate and execute a lifecycle transition.

        Parameters
        ----------
        name:
            The agent name to transition.
        action:
            The lifecycle action (deploy, pause, resume, retire).

        Returns
        -------
        AgentEntry:
            The updated agent entry after a successful transition.

        Raises
        ------
        LifecycleError:
            If the agent does not exist or the transition is invalid.
        """
        entry = self._agents.get(name)
        if entry is None:
            raise LifecycleError(
                agent=name,
                current_state="unknown",
                attempted_action=action,
            )

        valid_actions = self._VALID_TRANSITIONS.get(entry.state, {})
        target_state = valid_actions.get(action)

        if target_state is None:
            raise LifecycleError(
                agent=name,
                current_state=entry.state.value,
                attempted_action=action,
            )

        old_state = entry.state
        entry.state = target_state

        # Maintain active_set index
        if target_state == AgentState.ACTIVE:
            self._active_set.add(name)
        elif old_state == AgentState.ACTIVE:
            self._active_set.discard(name)

        # Emit tracer span — swallow exceptions
        self._emit_span(
            f"fleet.lifecycle.{action}",
            {
                "fleet.name": self._fleet_name,
                "agent.name": name,
                "lifecycle.action": action,
                "lifecycle.old_state": old_state.value,
                "lifecycle.new_state": target_state.value,
            },
        )

        return entry

    def deploy(self, name: str) -> AgentEntry:
        """Transition an agent from REGISTERED to ACTIVE.

        Parameters
        ----------
        name:
            The registered agent name to deploy.

        Returns
        -------
        AgentEntry:
            The updated agent entry with state=ACTIVE.

        Raises
        ------
        LifecycleError:
            If the agent does not exist or is not in REGISTERED state.
        """
        return self._transition(name, "deploy")

    def pause(self, name: str) -> AgentEntry:
        """Transition an agent from ACTIVE to PAUSED.

        Parameters
        ----------
        name:
            The active agent name to pause.

        Returns
        -------
        AgentEntry:
            The updated agent entry with state=PAUSED.

        Raises
        ------
        LifecycleError:
            If the agent does not exist or is not in ACTIVE state.
        """
        return self._transition(name, "pause")

    def resume(self, name: str) -> AgentEntry:
        """Transition an agent from PAUSED to ACTIVE.

        Parameters
        ----------
        name:
            The paused agent name to resume.

        Returns
        -------
        AgentEntry:
            The updated agent entry with state=ACTIVE.

        Raises
        ------
        LifecycleError:
            If the agent does not exist or is not in PAUSED state.
        """
        return self._transition(name, "resume")

    def retire(self, name: str) -> AgentEntry:
        """Transition an agent from REGISTERED, ACTIVE, or PAUSED to RETIRED.

        Parameters
        ----------
        name:
            The agent name to retire.

        Returns
        -------
        AgentEntry:
            The updated agent entry with state=RETIRED.

        Raises
        ------
        LifecycleError:
            If the agent does not exist or is already in RETIRED state.
        """
        return self._transition(name, "retire")

    # ------------------------------------------------------------------
    # Dependency tracking
    # ------------------------------------------------------------------

    def _detect_cycle(self, agent_name: str, dependencies: list[str]) -> list[str] | None:
        """Detect if adding agent_name with given dependencies creates a cycle.

        Uses DFS to traverse the existing dependency graph starting from each
        dependency of the new agent. If any path leads back to agent_name, a
        cycle exists.

        Returns
        -------
        A list representing the cycle path if a cycle is detected, or None.
        """
        for dep in dependencies:
            stack: list[tuple[str, list[str]]] = [(dep, [agent_name, dep])]
            visited: set[str] = set()

            while stack:
                current, path = stack.pop()

                if current == agent_name:
                    return path

                if current in visited:
                    continue
                visited.add(current)

                for neighbor in self._dependencies.get(current, []):
                    stack.append((neighbor, path + [neighbor]))

        return None

    # ------------------------------------------------------------------
    # Version history and rollback
    # ------------------------------------------------------------------

    def version_history(self, name: str) -> list[AgentVersion]:
        """Return version history for an agent.

        Parameters
        ----------
        name:
            The agent name whose version history to retrieve.

        Returns
        -------
        list[AgentVersion]:
            All recorded versions, ordered oldest to newest.

        Raises
        ------
        RegistrationError:
            If the agent does not exist in the registry.
        """
        entry = self._agents.get(name)
        if entry is None:
            raise RegistrationError(f"Agent {name!r} not found in registry")

        return list(self._versions.get(name, []))

    def rollback(self, name: str, version: str) -> AgentEntry:
        """Rollback agent to a specified previous version.

        Restores the agent's configuration from that version's config_snapshot
        and updates the agent's version string.

        Parameters
        ----------
        name:
            The agent name to roll back.
        version:
            The version string to roll back to (must exist in history).

        Returns
        -------
        AgentEntry:
            The updated agent entry after rollback.

        Raises
        ------
        RegistrationError:
            If the agent does not exist or the version is not in history.
        """
        entry = self._agents.get(name)
        if entry is None:
            raise RegistrationError(f"Agent {name!r} not found in registry")

        versions = self._versions.get(name, [])
        # O(1) dict-index lookup (Bug 4 fix)
        agent_index = self._version_index.get(name, {})
        target = agent_index.get(version)

        if target is None:
            raise RegistrationError(f"Version {version!r} not found in history for agent {name!r}")

        # Restore agent to the target version
        entry.version = target.version
        entry.config_overrides = dict(target.config_snapshot)

        # Emit tracer span for the rollback operation
        self._emit_span(
            "fleet.registry.rollback",
            {
                "fleet.name": self._fleet_name,
                "agent.name": name,
                "agent.version": target.version,
            },
        )

        return entry

    # ------------------------------------------------------------------
    # Configuration resolution
    # ------------------------------------------------------------------

    def resolve_config(self, name: str) -> dict[str, Any]:
        """Return the effective configuration for an agent.

        Agent-level config_overrides take precedence over fleet defaults.
        """
        entry = self._agents.get(name)
        if entry is None:
            return dict(self._defaults.default_config) if self._defaults.default_config else {}

        if entry.config_overrides:
            return dict(entry.config_overrides)

        return dict(self._defaults.default_config) if self._defaults.default_config else {}

    # ------------------------------------------------------------------
    # Observability helpers
    # ------------------------------------------------------------------

    def _emit_span(self, name: str, attributes: dict[str, Any]) -> None:
        """Emit a tracer span, swallowing any exceptions.

        Observability must never break registry operations.
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
