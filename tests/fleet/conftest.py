"""Shared Hypothesis configuration, strategies, and pytest fixtures for fleet tests.

This conftest provides:
- Hypothesis settings profile for fleet PBT tests
- Custom composite strategies for fleet domain types
- Pytest fixtures for common fleet test setups (mock Loop, FleetRegistry, Fleet)
- Swarm-specific strategies for SignalBus property tests
- Deterministic clock and signal bus fixtures for Swarm testing

Strategies:
    agent_names       — valid agent name strings (alphanumeric + hyphens)
    version_strings   — semver-like version strings (e.g. "1.0.0", "2.3.1")
    fleet_with_agents — a FleetRegistry with N agents in various lifecycle states
    cost_sequences    — sequences of cost events with agent/owner metadata
    dependency_graphs — random DAGs (some acyclic, some with cycles for negative testing)
    rate_limit_scenarios — request sequences with timing info
    namespace_st      — valid SignalBus namespace strings
    key_st            — valid SignalBus key strings
    value_st          — valid SignalBus entry values (text | int | bool | None)
    entry_st          — builds Entry instances with valid fields
    escalation_rule_st — builds EscalationRule instances with sampled reasons/error types

Fixtures:
    mock_loop              — a minimal mock Loop instance
    fleet_registry         — a FleetRegistry instance
    minimal_fleet          — a Fleet instance with default config
    deterministic_clock    — a callable returning incrementing floats (0.0, 1.0, 2.0, ...)
    deterministic_signal_bus — a SignalBus instance using the deterministic clock

Validates: Requirements 5.3, 5.6
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
import hypothesis.strategies as st
from hypothesis import settings, HealthCheck

from tvastar.fleet import (
    AgentState,
    FleetConfig,
    FleetRegistry,
    Fleet,
)
from tvastar.fleet.models import Entry, EscalationRule

# Re-export fleet types that test modules may need via conftest
__all__ = [
    "AgentState",
    "FleetConfig",
    "FleetRegistry",
    "Fleet",
]


# ---------------------------------------------------------------------------
# Hypothesis settings profile — applied to all fleet PBT tests
# ---------------------------------------------------------------------------

settings.register_profile(
    "fleet",
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
settings.load_profile("fleet")


# ---------------------------------------------------------------------------
# Custom Hypothesis Strategies
# ---------------------------------------------------------------------------


@st.composite
def agent_names(draw: st.DrawFn) -> str:
    """Generate valid agent name strings.

    Valid names: alphanumeric + hyphens, 1-30 characters, must start with
    a letter, cannot start or end with a hyphen.
    """
    # Start with a lowercase letter
    first = draw(st.sampled_from("abcdefghijklmnopqrstuvwxyz"))
    # Remainder: lowercase alphanumeric + hyphens, but no consecutive hyphens
    rest_len = draw(st.integers(min_value=0, max_value=29))
    if rest_len == 0:
        return first

    chars = draw(
        st.text(
            alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz0123456789-"),
            min_size=rest_len,
            max_size=rest_len,
        )
    )
    # Clean up: no consecutive hyphens, no trailing hyphen
    result = first + chars.replace("--", "-a").rstrip("-")
    # Ensure non-empty after cleanup
    return result if result else first


@st.composite
def version_strings(draw: st.DrawFn) -> str:
    """Generate semver-like version strings (e.g. '1.0.0', '2.3.1', '10.0.42')."""
    major = draw(st.integers(min_value=0, max_value=99))
    minor = draw(st.integers(min_value=0, max_value=99))
    patch = draw(st.integers(min_value=0, max_value=99))
    return f"{major}.{minor}.{patch}"


@st.composite
def fleet_with_agents(
    draw: st.DrawFn,
    min_agents: int = 1,
    max_agents: int = 10,
) -> dict[str, Any]:
    """Generate a fleet registry description with N agents in various states.

    Returns a dict with:
        - "fleet_name": str
        - "agents": list of dicts with keys name, version, owner, state
    """
    n = draw(st.integers(min_value=min_agents, max_value=max_agents))
    fleet_name = draw(agent_names())

    agents = []
    used_names: set[str] = set()
    for _ in range(n):
        # Generate unique agent names
        name = draw(agent_names().filter(lambda n: n not in used_names))
        used_names.add(name)
        version = draw(version_strings())
        owner = draw(st.sampled_from(["ml-team", "platform", "infra", "product", "default"]))
        state = draw(st.sampled_from(list(AgentState)))
        agents.append(
            {
                "name": name,
                "version": version,
                "owner": owner,
                "state": state,
            }
        )

    return {"fleet_name": fleet_name, "agents": agents}


@st.composite
def cost_sequences(
    draw: st.DrawFn,
    min_events: int = 1,
    max_events: int = 20,
) -> list[dict[str, Any]]:
    """Generate sequences of cost events with agent/owner metadata.

    Each event is a dict with:
        - "agent_name": str
        - "owner": str
        - "usd": float (positive cost)
        - "timestamp": float (monotonically increasing)
    """
    n = draw(st.integers(min_value=min_events, max_value=max_events))
    agents = draw(st.lists(agent_names(), min_size=1, max_size=5, unique=True))
    owners = draw(
        st.lists(
            st.sampled_from(["ml-team", "platform", "infra", "product"]),
            min_size=len(agents),
            max_size=len(agents),
        )
    )
    agent_owner_map = dict(zip(agents, owners))

    base_time = 1700000000.0
    events = []
    for i in range(n):
        agent = draw(st.sampled_from(agents))
        usd = draw(
            st.floats(min_value=0.001, max_value=50.0, allow_nan=False, allow_infinity=False)
        )
        events.append(
            {
                "agent_name": agent,
                "owner": agent_owner_map[agent],
                "usd": round(usd, 4),
                "timestamp": base_time + i * 60.0,
            }
        )

    return events


@st.composite
def dependency_graphs(
    draw: st.DrawFn,
    min_nodes: int = 2,
    max_nodes: int = 8,
    allow_cycles: bool = True,
) -> dict[str, list[str]]:
    """Generate random dependency graphs (adjacency lists).

    When allow_cycles=True, some generated graphs may contain cycles
    (useful for negative testing of cycle detection).
    When allow_cycles=False, only DAGs are produced.

    Returns a dict mapping agent_name -> list of dependency agent names.
    """
    n = draw(st.integers(min_value=min_nodes, max_value=max_nodes))
    names = [f"agent-{i}" for i in range(n)]

    graph: dict[str, list[str]] = {name: [] for name in names}

    if allow_cycles:
        # Allow arbitrary edges (may form cycles)
        for i, name in enumerate(names):
            # Each node can depend on any other node
            possible_deps = [n for n in names if n != name]
            if possible_deps:
                deps = draw(
                    st.lists(
                        st.sampled_from(possible_deps),
                        min_size=0,
                        max_size=min(3, len(possible_deps)),
                        unique=True,
                    )
                )
                graph[name] = deps
    else:
        # Only allow edges from higher-index to lower-index (guarantees DAG)
        for i, name in enumerate(names):
            if i > 0:
                possible_deps = names[:i]
                deps = draw(
                    st.lists(
                        st.sampled_from(possible_deps),
                        min_size=0,
                        max_size=min(2, len(possible_deps)),
                        unique=True,
                    )
                )
                graph[name] = deps

    return graph


@st.composite
def rate_limit_scenarios(
    draw: st.DrawFn,
    min_requests: int = 1,
    max_requests: int = 30,
) -> dict[str, Any]:
    """Generate request sequences with timing info for rate limit testing.

    Returns a dict with:
        - "config": RateLimitConfig parameters
        - "requests": list of dicts with "agent_name" and "timestamp"
    """
    requests_per_window = draw(st.integers(min_value=1, max_value=20))
    window_seconds = draw(st.sampled_from([1.0, 5.0, 10.0, 30.0, 60.0]))
    n = draw(st.integers(min_value=min_requests, max_value=max_requests))

    agents = draw(st.lists(agent_names(), min_size=1, max_size=3, unique=True))

    base_time = 1700000000.0
    requests = []
    for i in range(n):
        agent = draw(st.sampled_from(agents))
        # Timestamps within 0-2 windows (some will exceed, some won't)
        offset = draw(
            st.floats(
                min_value=0.0,
                max_value=window_seconds * 2,
                allow_nan=False,
                allow_infinity=False,
            )
        )
        requests.append(
            {
                "agent_name": agent,
                "timestamp": base_time + offset,
            }
        )

    # Sort by timestamp for realistic ordering
    requests.sort(key=lambda r: r["timestamp"])

    return {
        "config": {
            "requests_per_window": requests_per_window,
            "window_seconds": window_seconds,
        },
        "requests": requests,
    }


# ---------------------------------------------------------------------------
# Pytest Fixtures
# ---------------------------------------------------------------------------


def _make_mock_loop(name: str = "test-agent", goal: str = "do work") -> MagicMock:
    """Create a mock Loop instance matching the Loop interface.

    Uses unittest.mock.MagicMock to stub the Loop class so fleet tests
    don't depend on a real AgentSpec/Model/Store stack.
    """
    mock = MagicMock()
    mock.name = name
    mock.state = "idle"
    mock.config = MagicMock()
    mock.config.name = name
    mock.config.goal = goal
    mock.config.budget = None
    mock.config.metadata = {}
    mock.history.return_value = []
    mock.last_run.return_value = None
    mock.trigger = MagicMock()
    mock.start = MagicMock()
    mock.stop = MagicMock()
    mock.reset = MagicMock()
    return mock


@pytest.fixture
def mock_loop() -> MagicMock:
    """A minimal mock Loop instance suitable for fleet registration.

    The mock has the key Loop interface attributes:
    - name: str property
    - state: str property
    - config: object with name, goal, budget, metadata
    - history(), last_run(), trigger(), start(), stop(), reset()
    """
    return _make_mock_loop()


@pytest.fixture
def fleet_registry() -> FleetRegistry:
    """A FleetRegistry instance with default configuration.

    Uses a simple fleet name and no tracer for test isolation.
    """
    return FleetRegistry()


@pytest.fixture
def minimal_fleet() -> Fleet:
    """A Fleet instance with minimal default config.

    Name: 'test-fleet', no budget, no rate limits, no model policy.
    """
    # config = FleetConfig(name="test-fleet")
    return Fleet()


# ---------------------------------------------------------------------------
# Helper factory for creating populated fleets in tests
# ---------------------------------------------------------------------------


def make_mock_loops(count: int, prefix: str = "agent") -> list[MagicMock]:
    """Create multiple mock Loop instances with unique names."""
    return [_make_mock_loop(name=f"{prefix}-{i}") for i in range(count)]


# ---------------------------------------------------------------------------
# Swarm / SignalBus Hypothesis Strategies
# ---------------------------------------------------------------------------

namespace_st = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="_-"),
    min_size=1,
    max_size=20,
)
"""Valid SignalBus namespace strings: letters, digits, underscore, hyphen."""

key_st = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="_-."),
    min_size=1,
    max_size=30,
)
"""Valid SignalBus key strings: letters, digits, underscore, hyphen, dot."""

value_st = st.one_of(st.text(max_size=100), st.integers(), st.booleans(), st.none())
"""Valid SignalBus entry values: text, integers, booleans, or None."""

entry_st = st.builds(
    Entry,
    namespace=namespace_st,
    key=key_st,
    value=value_st,
    timestamp=st.floats(min_value=0, max_value=1e9),
)
"""Builds Entry instances with valid namespace, key, value, and timestamp."""

escalation_rule_st = st.builds(
    EscalationRule,
    match_reason=st.one_of(
        st.none(),
        st.sampled_from(["retries_exhausted", "permanent_error", "unknown"]),
    ),
    match_error_type=st.one_of(
        st.none(),
        st.sampled_from(["timeout", "rate_limit", "auth", "novel"]),
    ),
    directive=st.fixed_dictionaries(
        {"action": st.sampled_from(["wait_and_retry", "skip_and_continue", "proceed_autonomously"])}
    ),
)
"""Builds EscalationRule instances with sampled reasons and error types."""


# ---------------------------------------------------------------------------
# Swarm / SignalBus Pytest Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def deterministic_clock():
    """A callable that returns monotonically incrementing floats: 0.0, 1.0, 2.0, ...

    Inject into SignalBus(clock=deterministic_clock) for deterministic timestamps in tests.

    Validates: Requirements 5.3
    """
    counter = iter(float(i) for i in range(10_000))
    return lambda: next(counter)


@pytest.fixture
def deterministic_signal_bus(deterministic_clock):
    """A SignalBus instance using the deterministic clock for reproducible tests.

    NOTE: SignalBus is implemented in src/tvastar/fleet/signal_bus.py.
    This fixture uses a conditional import so that tests can be collected even
    before signal_bus.py is fully implemented — it will skip gracefully.

    Validates: Requirements 5.3, 5.6
    """
    try:
        from tvastar.fleet.signal_bus import SignalBus
    except ImportError:
        pytest.skip("SignalBus not yet implemented (src/tvastar/fleet/signal_bus.py)")
    return SignalBus(clock=deterministic_clock)
