"""Property-based tests for Fleet registry and state invariants.

Properties tested:
- Property 2: FleetRegistry rejects duplicate (name, version) pairs
- Property 3: Dependency cycle detection rejects cyclic graphs
- Property 4: Optimistic locking preserves consistency
- Property 8: Registry checkpoint round-trip preserves data

Uses Hypothesis with @settings(max_examples=30) per REQ-3 AC8.

Validates: Requirements 3.1, 3.2, 3.3, 3.7, 3.8
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from hypothesis import given, settings, assume, HealthCheck
import hypothesis.strategies as st

from tvastar.fleet import (
    ConflictError,
    ConflictStrategy,
    Fleet,
    FleetConfig,
    FleetRegistry,
    RegistrationError,
)
from tvastar.fleet.state import SharedStateStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_loop() -> MagicMock:
    """Minimal mock Loop for registration."""
    m = MagicMock()
    m.name = "mock"
    m.config = MagicMock()
    m.config.goal = "test"
    return m


# Strategies
_agent_names = st.text(
    alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz0123456789"),
    min_size=1,
    max_size=20,
).filter(lambda s: s[0].isalpha())

_versions = st.from_regex(r"[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}", fullmatch=True)


# ---------------------------------------------------------------------------
# Property 2: FleetRegistry rejects duplicate (name, version) pairs
# **Validates: Requirements 3.1**
# ---------------------------------------------------------------------------


class TestProperty2DuplicateRejection:
    """FleetRegistry rejects duplicate (name, version) pairs."""

    @settings(max_examples=30, suppress_health_check=[HealthCheck.too_slow])
    @given(name=_agent_names, version=_versions)
    def test_duplicate_name_version_raises(self, name: str, version: str) -> None:
        """Registering the same (name, version) twice raises RegistrationError."""
        registry = FleetRegistry("prop-test")
        registry.register(_mock_loop(), name=name, version=version, owner="team")

        with pytest.raises(RegistrationError):
            registry.register(_mock_loop(), name=name, version=version, owner="team")

        # Registry contains exactly one entry for that name
        assert registry.get(name) is not None
        assert registry.count() == 1


# ---------------------------------------------------------------------------
# Property 3: Dependency cycle detection rejects cyclic graphs
# **Validates: Requirements 3.2**
# ---------------------------------------------------------------------------


class TestProperty3CycleDetection:
    """Dependency cycle detection rejects cyclic graphs."""

    @settings(max_examples=30, suppress_health_check=[HealthCheck.too_slow])
    @given(cycle_len=st.integers(min_value=2, max_value=10))
    def test_cyclic_dependency_raises(self, cycle_len: int) -> None:
        """A cycle of length 2..10 raises RegistrationError on the closing edge.

        Strategy: register agent-0 with a forward dep on agent-(n-1) (not yet
        registered — the registry allows deps on non-existent names). Then
        register agents 1..n-2 chaining through 0. Finally, registering
        agent-(n-1) with dep on the previous agent triggers cycle detection
        because the DFS from that dep reaches agent-(n-1) via the stored graph.
        """
        names = [f"agent-{i}" for i in range(cycle_len)]
        registry = FleetRegistry("cycle-test")

        # Agent-0 has a forward dep on the last agent (creates the back-edge)
        registry.register(
            _mock_loop(),
            name=names[0],
            version="1.0.0",
            owner="team",
            dependencies=[names[cycle_len - 1]],
        )

        # Register agents 1 through n-2 in a chain (each depends on previous)
        for i in range(1, cycle_len - 1):
            registry.register(
                _mock_loop(),
                name=names[i],
                version="1.0.0",
                owner="team",
                dependencies=[names[i - 1]],
            )

        # Registering the last agent closes the cycle
        last_dep = [names[cycle_len - 2]] if cycle_len > 2 else [names[0]]
        with pytest.raises(RegistrationError):
            registry.register(
                _mock_loop(),
                name=names[cycle_len - 1],
                version="1.0.0",
                owner="team",
                dependencies=last_dep,
            )


# ---------------------------------------------------------------------------
# Property 4: Optimistic locking preserves consistency
# **Validates: Requirements 3.3**
# ---------------------------------------------------------------------------


class TestProperty4OptimisticLocking:
    """Optimistic locking preserves consistency on version mismatch."""

    @settings(max_examples=30, suppress_health_check=[HealthCheck.too_slow])
    @given(
        key=st.text(
            min_size=1, max_size=20, alphabet=st.characters(whitelist_categories=("L", "N"))
        ),
        value_a=st.text(min_size=1, max_size=50),
        value_b=st.text(min_size=1, max_size=50),
        wrong_version=st.integers(min_value=2, max_value=100),
    )
    def test_version_mismatch_raises_conflict(
        self, key: str, value_a: str, value_b: str, wrong_version: int
    ) -> None:
        """A write with wrong expected_version raises ConflictError; value unchanged."""
        store = SharedStateStore("prop-test", strategy=ConflictStrategy.OPTIMISTIC_LOCKING)

        # Initial write (version goes to 1)
        store.set(key, value_a, agent="writer-a", expected_version=0)

        # Conflicting write with wrong version (version is 1, we supply wrong_version != 1)
        assume(wrong_version != 1)

        with pytest.raises(ConflictError) as exc_info:
            store.set(key, value_b, agent="writer-b", expected_version=wrong_version)

        # Value unchanged
        assert store.get(key) == value_a

        # ConflictError has correct attributes
        assert exc_info.value.key == key
        assert exc_info.value.expected_version == wrong_version
        assert exc_info.value.actual_version == 1


# ---------------------------------------------------------------------------
# Property 8: Registry checkpoint round-trip preserves data
# **Validates: Requirements 3.7**
# ---------------------------------------------------------------------------


@st.composite
def _dag_agents(draw: st.DrawFn) -> list[dict]:
    """Generate 1-15 agents with names, versions, owners, and valid DAG deps."""
    n = draw(st.integers(min_value=1, max_value=15))
    agents = []
    for i in range(n):
        name = f"agent-{i}"
        version = draw(_versions)
        owner = draw(st.sampled_from(["ml-team", "platform", "infra", "product"]))
        # Dependencies can only point to earlier agents (ensures DAG)
        if i > 0:
            possible = [f"agent-{j}" for j in range(i)]
            deps = draw(st.lists(st.sampled_from(possible), max_size=min(3, i), unique=True))
        else:
            deps = []
        agents.append({"name": name, "version": version, "owner": owner, "deps": deps})
    return agents


class TestProperty8CheckpointRoundTrip:
    """Registry checkpoint round-trip preserves data."""

    @settings(
        max_examples=30,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
    )
    @given(agents=_dag_agents())
    def test_persist_load_preserves_entries(self, agents: list[dict], tmp_path) -> None:
        """Checkpoint + load preserves agent name, state, and health fields."""
        config = FleetConfig(name="roundtrip-test")
        fleet = Fleet(config)

        for agent in agents:
            fleet.register(
                _mock_loop(),
                name=agent["name"],
                version=agent["version"],
                owner=agent["owner"],
                dependencies=agent["deps"],
            )

        # Persist
        path = str(tmp_path / "state.json")
        fleet.persist(path)

        # Load into fresh fleet
        fleet2 = Fleet(FleetConfig(name="roundtrip-test"))
        count = fleet2.load(path)

        assert count == len(agents)

        for agent in agents:
            entry = fleet2.registry.get(agent["name"])
            assert entry is not None, f"Missing agent {agent['name']}"
            assert entry.name == agent["name"]
            assert entry.version == agent["version"]
            assert entry.owner == agent["owner"]
            # State should be REGISTERED (default, since we didn't deploy)
            assert entry.state.value == "registered"
