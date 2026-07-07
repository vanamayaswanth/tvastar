"""Unit tests for FleetRegistry lifecycle transitions (task 2.3).

Tests deploy(), pause(), resume(), retire() methods and validates:
- Valid FSM transitions produce expected states
- Invalid transitions raise LifecycleError with correct attributes
- Non-existent agent raises LifecycleError
- Tracer span emission on transitions
- Tracer exceptions are swallowed
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tvastar.fleet import LifecycleError
from tvastar.fleet.registry import AgentState, FleetRegistry


@pytest.fixture
def registry() -> FleetRegistry:
    """A FleetRegistry with no tracer for basic tests."""
    return FleetRegistry("test-fleet")


@pytest.fixture
def registry_with_tracer():
    """A FleetRegistry with a mock tracer."""
    tracer = MagicMock()
    reg = FleetRegistry("test-fleet", tracer=tracer)
    return reg, tracer


@pytest.fixture
def mock_loop() -> MagicMock:
    """A mock Loop instance."""
    loop = MagicMock()
    loop.name = "test-loop"
    return loop


# ---------------------------------------------------------------------------
# Valid transitions
# ---------------------------------------------------------------------------


class TestDeploy:
    """Tests for deploy() — registered → active."""

    def test_deploy_transitions_registered_to_active(self, registry, mock_loop):
        registry.register(mock_loop, name="agent-a", version="1.0.0", owner="team")
        entry = registry.deploy("agent-a")
        assert entry.state == AgentState.ACTIVE

    def test_deploy_returns_agent_entry(self, registry, mock_loop):
        registry.register(mock_loop, name="agent-a", version="1.0.0", owner="team")
        entry = registry.deploy("agent-a")
        assert entry.name == "agent-a"
        assert entry.version == "1.0.0"
        assert entry.owner == "team"

    def test_deploy_invalid_from_active(self, registry, mock_loop):
        registry.register(mock_loop, name="agent-a", version="1.0.0", owner="team")
        registry.deploy("agent-a")
        with pytest.raises(LifecycleError) as exc_info:
            registry.deploy("agent-a")
        assert exc_info.value.agent == "agent-a"
        assert exc_info.value.current_state == "active"
        assert exc_info.value.attempted_action == "deploy"

    def test_deploy_invalid_from_paused(self, registry, mock_loop):
        registry.register(mock_loop, name="agent-a", version="1.0.0", owner="team")
        registry.deploy("agent-a")
        registry.pause("agent-a")
        with pytest.raises(LifecycleError) as exc_info:
            registry.deploy("agent-a")
        assert exc_info.value.current_state == "paused"

    def test_deploy_invalid_from_retired(self, registry, mock_loop):
        registry.register(mock_loop, name="agent-a", version="1.0.0", owner="team")
        registry.retire("agent-a")
        with pytest.raises(LifecycleError) as exc_info:
            registry.deploy("agent-a")
        assert exc_info.value.current_state == "retired"


class TestPause:
    """Tests for pause() — active → paused."""

    def test_pause_transitions_active_to_paused(self, registry, mock_loop):
        registry.register(mock_loop, name="agent-a", version="1.0.0", owner="team")
        registry.deploy("agent-a")
        entry = registry.pause("agent-a")
        assert entry.state == AgentState.PAUSED

    def test_pause_invalid_from_registered(self, registry, mock_loop):
        registry.register(mock_loop, name="agent-a", version="1.0.0", owner="team")
        with pytest.raises(LifecycleError) as exc_info:
            registry.pause("agent-a")
        assert exc_info.value.current_state == "registered"
        assert exc_info.value.attempted_action == "pause"

    def test_pause_invalid_from_retired(self, registry, mock_loop):
        registry.register(mock_loop, name="agent-a", version="1.0.0", owner="team")
        registry.retire("agent-a")
        with pytest.raises(LifecycleError) as exc_info:
            registry.pause("agent-a")
        assert exc_info.value.current_state == "retired"


class TestResume:
    """Tests for resume() — paused → active."""

    def test_resume_transitions_paused_to_active(self, registry, mock_loop):
        registry.register(mock_loop, name="agent-a", version="1.0.0", owner="team")
        registry.deploy("agent-a")
        registry.pause("agent-a")
        entry = registry.resume("agent-a")
        assert entry.state == AgentState.ACTIVE

    def test_resume_invalid_from_registered(self, registry, mock_loop):
        registry.register(mock_loop, name="agent-a", version="1.0.0", owner="team")
        with pytest.raises(LifecycleError) as exc_info:
            registry.resume("agent-a")
        assert exc_info.value.current_state == "registered"
        assert exc_info.value.attempted_action == "resume"

    def test_resume_invalid_from_active(self, registry, mock_loop):
        registry.register(mock_loop, name="agent-a", version="1.0.0", owner="team")
        registry.deploy("agent-a")
        with pytest.raises(LifecycleError) as exc_info:
            registry.resume("agent-a")
        assert exc_info.value.current_state == "active"

    def test_resume_invalid_from_retired(self, registry, mock_loop):
        registry.register(mock_loop, name="agent-a", version="1.0.0", owner="team")
        registry.retire("agent-a")
        with pytest.raises(LifecycleError) as exc_info:
            registry.resume("agent-a")
        assert exc_info.value.current_state == "retired"


class TestRetire:
    """Tests for retire() — {registered, active, paused} → retired."""

    def test_retire_from_registered(self, registry, mock_loop):
        registry.register(mock_loop, name="agent-a", version="1.0.0", owner="team")
        entry = registry.retire("agent-a")
        assert entry.state == AgentState.RETIRED

    def test_retire_from_active(self, registry, mock_loop):
        registry.register(mock_loop, name="agent-a", version="1.0.0", owner="team")
        registry.deploy("agent-a")
        entry = registry.retire("agent-a")
        assert entry.state == AgentState.RETIRED

    def test_retire_from_paused(self, registry, mock_loop):
        registry.register(mock_loop, name="agent-a", version="1.0.0", owner="team")
        registry.deploy("agent-a")
        registry.pause("agent-a")
        entry = registry.retire("agent-a")
        assert entry.state == AgentState.RETIRED

    def test_retire_invalid_from_retired(self, registry, mock_loop):
        registry.register(mock_loop, name="agent-a", version="1.0.0", owner="team")
        registry.retire("agent-a")
        with pytest.raises(LifecycleError) as exc_info:
            registry.retire("agent-a")
        assert exc_info.value.current_state == "retired"
        assert exc_info.value.attempted_action == "retire"


# ---------------------------------------------------------------------------
# Non-existent agent
# ---------------------------------------------------------------------------


class TestNonExistentAgent:
    """Tests for transitions on non-existent agents."""

    def test_deploy_nonexistent_raises_lifecycle_error(self, registry):
        with pytest.raises(LifecycleError) as exc_info:
            registry.deploy("ghost-agent")
        assert exc_info.value.agent == "ghost-agent"
        assert exc_info.value.current_state == "unknown"
        assert exc_info.value.attempted_action == "deploy"

    def test_pause_nonexistent_raises_lifecycle_error(self, registry):
        with pytest.raises(LifecycleError) as exc_info:
            registry.pause("ghost-agent")
        assert exc_info.value.agent == "ghost-agent"
        assert exc_info.value.attempted_action == "pause"

    def test_resume_nonexistent_raises_lifecycle_error(self, registry):
        with pytest.raises(LifecycleError) as exc_info:
            registry.resume("ghost-agent")
        assert exc_info.value.agent == "ghost-agent"
        assert exc_info.value.attempted_action == "resume"

    def test_retire_nonexistent_raises_lifecycle_error(self, registry):
        with pytest.raises(LifecycleError) as exc_info:
            registry.retire("ghost-agent")
        assert exc_info.value.agent == "ghost-agent"
        assert exc_info.value.attempted_action == "retire"

    def test_error_message_includes_agent_name(self, registry):
        with pytest.raises(LifecycleError, match="ghost-agent"):
            registry.deploy("ghost-agent")


# ---------------------------------------------------------------------------
# Tracer span emission
# ---------------------------------------------------------------------------


class TestTracerEmission:
    """Tests for tracer span emission on lifecycle transitions."""

    def test_deploy_emits_span(self, registry_with_tracer, mock_loop):
        reg, tracer = registry_with_tracer
        reg.register(mock_loop, name="agent-a", version="1.0.0", owner="team")
        reg.deploy("agent-a")
        # Tracer should have been called for both register and deploy
        assert tracer.start_span.call_count >= 1

    def test_deploy_span_contains_agent_info(self, registry_with_tracer, mock_loop):
        reg, tracer = registry_with_tracer
        reg.register(mock_loop, name="agent-a", version="1.0.0", owner="team")
        reg.deploy("agent-a")

        # Find the lifecycle span call
        deploy_calls = [
            c for c in tracer.start_span.call_args_list if "fleet.lifecycle.deploy" in str(c)
        ]
        assert len(deploy_calls) >= 1
        call_kwargs = deploy_calls[0]
        attrs = call_kwargs[1]["attributes"]  # keyword arg
        assert attrs["agent.name"] == "agent-a"
        assert attrs["lifecycle.old_state"] == "registered"
        assert attrs["lifecycle.new_state"] == "active"

    def test_pause_emits_span(self, registry_with_tracer, mock_loop):
        reg, tracer = registry_with_tracer
        reg.register(mock_loop, name="agent-a", version="1.0.0", owner="team")
        reg.deploy("agent-a")
        tracer.start_span.reset_mock()
        reg.pause("agent-a")
        assert tracer.start_span.called

    def test_retire_emits_span(self, registry_with_tracer, mock_loop):
        reg, tracer = registry_with_tracer
        reg.register(mock_loop, name="agent-a", version="1.0.0", owner="team")
        tracer.start_span.reset_mock()
        reg.retire("agent-a")
        assert tracer.start_span.called

    def test_tracer_exception_swallowed(self, mock_loop):
        """Tracer raising an exception should not break lifecycle operations."""
        tracer = MagicMock()
        tracer.start_span.side_effect = RuntimeError("tracer exploded")
        reg = FleetRegistry("test-fleet", tracer=tracer)
        reg.register(mock_loop, name="agent-a", version="1.0.0", owner="team")
        # Despite tracer error, deploy should succeed
        entry = reg.deploy("agent-a")
        assert entry.state == AgentState.ACTIVE

    def test_tracer_exception_swallowed_on_all_transitions(self, mock_loop):
        """All lifecycle transitions swallow tracer exceptions."""
        tracer = MagicMock()
        tracer.start_span.side_effect = RuntimeError("boom")
        reg = FleetRegistry("test-fleet", tracer=tracer)
        reg.register(mock_loop, name="a", version="1.0.0", owner="t")
        reg.deploy("a")
        assert reg.get("a").state == AgentState.ACTIVE
        reg.pause("a")
        assert reg.get("a").state == AgentState.PAUSED
        reg.resume("a")
        assert reg.get("a").state == AgentState.ACTIVE
        reg.retire("a")
        assert reg.get("a").state == AgentState.RETIRED
