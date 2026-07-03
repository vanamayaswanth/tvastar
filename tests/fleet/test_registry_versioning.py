"""Unit tests for FleetRegistry version_history() and rollback() methods (task 10.2).

Validates:
- version_history() returns recorded versions for an agent
- version_history() raises RegistrationError for non-existent agent
- rollback() restores agent to specified version's config_snapshot
- rollback() raises RegistrationError for non-existent agent
- rollback() raises RegistrationError for non-existent version
- Tracer span emitted on rollback
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tvastar.fleet import RegistrationError
from tvastar.fleet.registry import AgentVersion, FleetRegistry


@pytest.fixture
def registry() -> FleetRegistry:
    """A FleetRegistry with no tracer for basic tests."""
    return FleetRegistry("test-fleet")


@pytest.fixture
def mock_loop() -> MagicMock:
    """A mock Loop instance."""
    loop = MagicMock()
    loop.name = "test-loop"
    return loop


# ---------------------------------------------------------------------------
# version_history() tests
# ---------------------------------------------------------------------------


class TestVersionHistory:
    """Tests for version_history()."""

    def test_returns_initial_version_after_register(self, registry, mock_loop):
        registry.register(mock_loop, name="agent-a", version="1.0.0", owner="team")
        history = registry.version_history("agent-a")
        assert len(history) == 1
        assert history[0].version == "1.0.0"

    def test_returns_list_of_agent_version(self, registry, mock_loop):
        registry.register(mock_loop, name="agent-a", version="1.0.0", owner="team")
        history = registry.version_history("agent-a")
        assert all(isinstance(v, AgentVersion) for v in history)

    def test_version_contains_config_snapshot(self, registry, mock_loop):
        registry.register(mock_loop, name="agent-a", version="1.0.0", owner="team")
        history = registry.version_history("agent-a")
        assert isinstance(history[0].config_snapshot, dict)

    def test_multiple_versions_tracked(self, registry, mock_loop):
        """Manually adding versions to simulate multi-version history."""
        registry.register(mock_loop, name="agent-a", version="1.0.0", owner="team")
        # Simulate adding a second version (as would happen during canary deploy)
        v2 = AgentVersion(version="2.0.0", config_snapshot={"model": "gpt-4"})
        registry._versions["agent-a"].append(v2)

        history = registry.version_history("agent-a")
        assert len(history) == 2
        assert history[0].version == "1.0.0"
        assert history[1].version == "2.0.0"

    def test_raises_registration_error_for_nonexistent_agent(self, registry):
        with pytest.raises(RegistrationError, match="not found"):
            registry.version_history("ghost-agent")

    def test_returns_copy_not_internal_list(self, registry, mock_loop):
        """Returned list should be a copy to prevent external mutation."""
        registry.register(mock_loop, name="agent-a", version="1.0.0", owner="team")
        history = registry.version_history("agent-a")
        history.clear()
        # Internal state should be unaffected
        assert len(registry.version_history("agent-a")) == 1


# ---------------------------------------------------------------------------
# rollback() tests
# ---------------------------------------------------------------------------


class TestRollback:
    """Tests for rollback()."""

    def test_rollback_restores_version_string(self, registry, mock_loop):
        registry.register(mock_loop, name="agent-a", version="1.0.0", owner="team")
        # Add a second version and update agent to it
        v2 = AgentVersion(version="2.0.0", config_snapshot={"model": "gpt-4"})
        registry._versions["agent-a"].append(v2)
        entry = registry.get("agent-a")
        entry.version = "2.0.0"
        entry.config_overrides = {"model": "gpt-4"}

        # Rollback to v1
        result = registry.rollback("agent-a", "1.0.0")
        assert result.version == "1.0.0"

    def test_rollback_restores_config_snapshot(self, registry, mock_loop):
        registry.register(mock_loop, name="agent-a", version="1.0.0", owner="team")
        # Add a second version with different config
        v2 = AgentVersion(version="2.0.0", config_snapshot={"model": "gpt-4", "temp": 0.7})
        registry._versions["agent-a"].append(v2)
        entry = registry.get("agent-a")
        entry.version = "2.0.0"
        entry.config_overrides = {"model": "gpt-4", "temp": 0.7}

        # Rollback to v1 (which has empty config_snapshot from initial registration)
        result = registry.rollback("agent-a", "1.0.0")
        assert result.config_overrides == {}

    def test_rollback_to_version_with_config(self, registry, mock_loop):
        registry.register(mock_loop, name="agent-a", version="1.0.0", owner="team")
        # Add versions with configs
        v2 = AgentVersion(version="2.0.0", config_snapshot={"model": "gpt-4"})
        v3 = AgentVersion(version="3.0.0", config_snapshot={"model": "gpt-5"})
        registry._versions["agent-a"].extend([v2, v3])
        entry = registry.get("agent-a")
        entry.version = "3.0.0"
        entry.config_overrides = {"model": "gpt-5"}

        # Rollback to v2
        result = registry.rollback("agent-a", "2.0.0")
        assert result.version == "2.0.0"
        assert result.config_overrides == {"model": "gpt-4"}

    def test_rollback_returns_agent_entry(self, registry, mock_loop):
        registry.register(mock_loop, name="agent-a", version="1.0.0", owner="team")
        v2 = AgentVersion(version="2.0.0", config_snapshot={"x": 1})
        registry._versions["agent-a"].append(v2)
        entry = registry.get("agent-a")
        entry.version = "2.0.0"

        result = registry.rollback("agent-a", "1.0.0")
        assert result.name == "agent-a"
        assert result.owner == "team"

    def test_rollback_nonexistent_agent_raises_error(self, registry):
        with pytest.raises(RegistrationError, match="not found in registry"):
            registry.rollback("ghost-agent", "1.0.0")

    def test_rollback_nonexistent_version_raises_error(self, registry, mock_loop):
        registry.register(mock_loop, name="agent-a", version="1.0.0", owner="team")
        with pytest.raises(RegistrationError, match="not found in history"):
            registry.rollback("agent-a", "9.9.9")

    def test_rollback_emits_tracer_span(self, mock_loop):
        tracer = MagicMock()
        reg = FleetRegistry("test-fleet", tracer=tracer)
        reg.register(mock_loop, name="agent-a", version="1.0.0", owner="team")
        v2 = AgentVersion(version="2.0.0", config_snapshot={"m": "gpt-4"})
        reg._versions["agent-a"].append(v2)
        entry = reg.get("agent-a")
        entry.version = "2.0.0"

        tracer.start_span.reset_mock()
        reg.rollback("agent-a", "1.0.0")

        # Should emit rollback span
        rollback_calls = [
            c for c in tracer.start_span.call_args_list
            if "fleet.registry.rollback" in str(c)
        ]
        assert len(rollback_calls) == 1
        attrs = rollback_calls[0][1]["attributes"]
        assert attrs["agent.name"] == "agent-a"
        assert attrs["agent.version"] == "1.0.0"

    def test_rollback_tracer_exception_swallowed(self, mock_loop):
        """Tracer raising exception should not break rollback."""
        tracer = MagicMock()
        tracer.start_span.side_effect = RuntimeError("tracer exploded")
        reg = FleetRegistry("test-fleet", tracer=tracer)
        reg.register(mock_loop, name="agent-a", version="1.0.0", owner="team")
        v2 = AgentVersion(version="2.0.0", config_snapshot={"m": "x"})
        reg._versions["agent-a"].append(v2)
        entry = reg.get("agent-a")
        entry.version = "2.0.0"

        # Should not raise despite tracer failure
        result = reg.rollback("agent-a", "1.0.0")
        assert result.version == "1.0.0"
