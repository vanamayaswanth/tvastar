"""Unit tests for FleetGateway.submit() — semantic routing and explicit routing.

Tests cover:
- Explicit agent routing (bypasses semantic matching)
- Semantic routing (selects best match above threshold)
- Non-active agents excluded from routing
- RoutingError raised when no suitable agent found
- Dependency deferral
- Audit trail recording
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from tvastar.fleet import RoutingError
from tvastar.fleet.gateway import FleetGateway
from tvastar.fleet.registry import AgentState, FleetRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_loop(goal: str = "do work", description: str | None = None) -> MagicMock:
    """Create a mock Loop with a config.goal for routing."""
    mock = MagicMock()
    mock.config = MagicMock()
    mock.config.goal = goal
    if description:
        mock.description = description
    return mock


def _setup_registry_with_agents(
    agents: list[dict],
) -> FleetRegistry:
    """Set up a registry and register/deploy agents.

    Each agent dict: {"name": str, "goal": str, "state": AgentState}
    """
    registry = FleetRegistry(fleet_name="test")
    for agent in agents:
        loop = _make_loop(goal=agent.get("goal", ""))
        registry.register(
            loop,
            name=agent["name"],
            version="1.0.0",
            owner="test-team",
            dependencies=agent.get("dependencies"),
        )
        target_state = agent.get("state", AgentState.ACTIVE)
        if target_state == AgentState.ACTIVE:
            registry.deploy(agent["name"])
        elif target_state == AgentState.PAUSED:
            registry.deploy(agent["name"])
            registry.pause(agent["name"])
        elif target_state == AgentState.RETIRED:
            registry.retire(agent["name"])
        # REGISTERED: no action needed

    return registry


# ---------------------------------------------------------------------------
# Tests: Explicit routing
# ---------------------------------------------------------------------------


class TestExplicitRouting:
    """Property 7: Explicit agent routing bypasses matching."""

    @pytest.mark.asyncio
    async def test_explicit_routing_to_active_agent(self):
        """Explicit agent name routes directly without scoring."""
        registry = _setup_registry_with_agents(
            [
                {"name": "writer", "goal": "Write documents", "state": AgentState.ACTIVE},
                {"name": "coder", "goal": "Write Python code", "state": AgentState.ACTIVE},
            ]
        )
        gw = FleetGateway(registry, routing_threshold=0.3)

        result = await gw.submit("do something random", agent="writer")

        assert result["agent_name"] == "writer"
        assert result["routing_score"] is None  # no scoring performed
        assert result["status"] == "dispatched"
        assert result["dispatch_id"] is not None

    @pytest.mark.asyncio
    async def test_explicit_routing_nonexistent_agent_raises(self):
        """Explicit routing to unknown agent raises RoutingError."""
        registry = _setup_registry_with_agents(
            [
                {"name": "writer", "goal": "Write docs", "state": AgentState.ACTIVE},
            ]
        )
        gw = FleetGateway(registry, routing_threshold=0.3)

        with pytest.raises(RoutingError, match="not registered"):
            await gw.submit("some task", agent="ghost")

    @pytest.mark.asyncio
    async def test_explicit_routing_to_paused_agent_raises(self):
        """Explicit routing to a paused agent raises RoutingError."""
        registry = _setup_registry_with_agents(
            [
                {"name": "writer", "goal": "Write docs", "state": AgentState.PAUSED},
            ]
        )
        gw = FleetGateway(registry, routing_threshold=0.3)

        with pytest.raises(RoutingError, match="not active"):
            await gw.submit("write a doc", agent="writer")

    @pytest.mark.asyncio
    async def test_explicit_routing_to_retired_agent_raises(self):
        """Explicit routing to a retired agent raises RoutingError."""
        registry = _setup_registry_with_agents(
            [
                {"name": "writer", "goal": "Write docs", "state": AgentState.RETIRED},
            ]
        )
        gw = FleetGateway(registry, routing_threshold=0.3)

        with pytest.raises(RoutingError, match="not active"):
            await gw.submit("write a doc", agent="writer")


# ---------------------------------------------------------------------------
# Tests: Semantic routing
# ---------------------------------------------------------------------------


class TestSemanticRouting:
    """Property 6: Gateway routing selects best match above threshold."""

    @pytest.mark.asyncio
    async def test_routes_to_best_matching_agent(self):
        """Task about testing should route to the tester agent."""
        registry = _setup_registry_with_agents(
            [
                {"name": "tester", "goal": "Write and run unit tests for Python code"},
                {"name": "coder", "goal": "Write and fix Python application code"},
                {"name": "reviewer", "goal": "Review code for security and correctness"},
            ]
        )
        gw = FleetGateway(registry, routing_threshold=0.1)

        result = await gw.submit("Write unit tests for the auth module")

        assert result["agent_name"] == "tester"
        assert result["routing_score"] is not None
        assert result["routing_score"] > 0.0
        assert result["status"] == "dispatched"

    @pytest.mark.asyncio
    async def test_no_agents_above_threshold_raises(self):
        """When no agent scores above threshold, raise RoutingError."""
        registry = _setup_registry_with_agents(
            [
                {"name": "sql-agent", "goal": "Execute SQL database queries"},
            ]
        )
        # Set absurdly high threshold
        gw = FleetGateway(registry, routing_threshold=0.99)

        with pytest.raises(RoutingError, match="below routing threshold"):
            await gw.submit("xyzzy frobnicator nonsense")

    @pytest.mark.asyncio
    async def test_no_active_agents_raises(self):
        """When no agents are active, raise RoutingError."""
        registry = _setup_registry_with_agents(
            [
                {"name": "writer", "goal": "Write docs", "state": AgentState.PAUSED},
                {"name": "coder", "goal": "Write code", "state": AgentState.RETIRED},
            ]
        )
        gw = FleetGateway(registry, routing_threshold=0.1)

        with pytest.raises(RoutingError, match="no active agents"):
            await gw.submit("do anything")


# ---------------------------------------------------------------------------
# Tests: Non-active agents excluded (Property 5)
# ---------------------------------------------------------------------------


class TestNonActiveExclusion:
    """Property 5: Non-active agents excluded from routing."""

    @pytest.mark.asyncio
    async def test_paused_agent_not_routed_to(self):
        """Paused agent should not be selected even if it best matches."""
        registry = _setup_registry_with_agents(
            [
                {
                    "name": "writer",
                    "goal": "Write documents and reports",
                    "state": AgentState.PAUSED,
                },
                {"name": "coder", "goal": "Write Python code", "state": AgentState.ACTIVE},
            ]
        )
        gw = FleetGateway(registry, routing_threshold=0.0)

        result = await gw.submit("Write a document report")

        # Even though "writer" matches better, it's paused
        assert result["agent_name"] == "coder"

    @pytest.mark.asyncio
    async def test_registered_agent_not_routed_to(self):
        """Agent still in REGISTERED state should not be selected."""
        registry = _setup_registry_with_agents(
            [
                {
                    "name": "writer",
                    "goal": "Write documents and reports",
                    "state": AgentState.REGISTERED,
                },
                {"name": "coder", "goal": "Write Python code", "state": AgentState.ACTIVE},
            ]
        )
        gw = FleetGateway(registry, routing_threshold=0.0)

        result = await gw.submit("Write a document report")

        assert result["agent_name"] == "coder"


# ---------------------------------------------------------------------------
# Tests: Dependency deferral
# ---------------------------------------------------------------------------


class TestDependencyDeferral:
    """Property 14: Dependency execution ordering."""

    @pytest.mark.asyncio
    async def test_agent_with_unregistered_dependency_deferred(self):
        """Agent depending on unregistered agent gets deferred."""
        registry = _setup_registry_with_agents(
            [
                {"name": "deployer", "goal": "Deploy applications", "dependencies": ["builder"]},
            ]
        )
        gw = FleetGateway(registry, routing_threshold=0.0)

        result = await gw.submit("Deploy the app", agent="deployer")

        assert result["status"] == "deferred"
        assert result["dispatch_id"] is None

    @pytest.mark.asyncio
    async def test_agent_with_active_dependency_proceeds(self):
        """Agent whose dependencies are all active should proceed normally."""
        registry = _setup_registry_with_agents(
            [
                {"name": "builder", "goal": "Build applications", "state": AgentState.ACTIVE},
                {"name": "deployer", "goal": "Deploy applications", "dependencies": ["builder"]},
            ]
        )
        gw = FleetGateway(registry, routing_threshold=0.0)

        result = await gw.submit("Deploy the app", agent="deployer")

        assert result["status"] == "dispatched"
        assert result["dispatch_id"] is not None

    @pytest.mark.asyncio
    async def test_agent_with_paused_dependency_deferred(self):
        """Agent depending on a paused agent gets deferred."""
        registry = _setup_registry_with_agents(
            [
                {"name": "builder", "goal": "Build applications", "state": AgentState.PAUSED},
                {"name": "deployer", "goal": "Deploy applications", "dependencies": ["builder"]},
            ]
        )
        gw = FleetGateway(registry, routing_threshold=0.0)

        result = await gw.submit("Deploy the app", agent="deployer")

        assert result["status"] == "deferred"


# ---------------------------------------------------------------------------
# Tests: Audit trail
# ---------------------------------------------------------------------------


class TestAuditTrail:
    """Property 8: Audit trail completeness."""

    @pytest.mark.asyncio
    async def test_successful_route_recorded_in_audit(self):
        """Successful routing records an audit entry."""
        registry = _setup_registry_with_agents(
            [
                {"name": "coder", "goal": "Write Python code"},
            ]
        )
        gw = FleetGateway(registry, routing_threshold=0.0)

        await gw.submit("Write some code")

        log = gw.audit_log()
        assert len(log) == 1
        assert log[0].event_type == "route"
        assert log[0].agent_name == "coder"
        assert log[0].task_description == "Write some code"
        assert log[0].timestamp > 0

    @pytest.mark.asyncio
    async def test_failed_route_recorded_in_audit(self):
        """Failed routing (below threshold) records an audit entry."""
        registry = _setup_registry_with_agents(
            [
                {"name": "sql-agent", "goal": "Execute SQL queries"},
            ]
        )
        gw = FleetGateway(registry, routing_threshold=0.99)

        with pytest.raises(RoutingError):
            await gw.submit("xyzzy frobnicator")

        log = gw.audit_log()
        assert len(log) == 1
        assert log[0].event_type == "route_failed"
        assert log[0].task_description == "xyzzy frobnicator"

    @pytest.mark.asyncio
    async def test_context_passed_through(self):
        """Context dict is passed through in the result."""
        registry = _setup_registry_with_agents(
            [
                {"name": "coder", "goal": "Write Python code"},
            ]
        )
        gw = FleetGateway(registry, routing_threshold=0.0)

        ctx = {"repo": "my-repo", "branch": "main"}
        result = await gw.submit("Write code", context=ctx)

        assert result["context"] == ctx
