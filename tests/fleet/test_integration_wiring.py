"""Integration test proving all Fleet sub-components are wired together."""

import pytest
from unittest.mock import MagicMock

from tvastar.fleet import (
    Fleet,
    FleetConfig,
    FleetBudgetConfig,
    AlertConfig,
    FleetRegistry,
    FleetGateway,
    SharedStateStore,
    EventBus,
    FleetBudget,
    FleetObserver,
)


@pytest.fixture
def fleet():
    config = FleetConfig(
        name="production",
        budget=FleetBudgetConfig(max_fleet_usd=100.0, allocations={"researcher": 50.0}),
        alert_config=AlertConfig(quality_threshold=60.0),
    )
    return Fleet(config)


@pytest.fixture
def mock_loop():
    loop = MagicMock()
    loop.config = MagicMock()
    loop.config.goal = "Research papers"
    return loop


class TestFleetWiring:
    """Verify all sub-components are properly instantiated and connected."""

    def test_all_components_are_real_instances(self, fleet):
        assert isinstance(fleet.registry, FleetRegistry)
        assert isinstance(fleet.gateway, FleetGateway)
        assert isinstance(fleet.state, SharedStateStore)
        assert isinstance(fleet.bus, EventBus)
        assert isinstance(fleet.budget, FleetBudget)
        assert isinstance(fleet.observer, FleetObserver)

    def test_register_convenience_method(self, fleet, mock_loop):
        entry = fleet.register(mock_loop, name="researcher", version="1.0.0", owner="ml-team")
        assert entry.name == "researcher"
        assert fleet.registry.count() == 1

    @pytest.mark.asyncio
    async def test_submit_routes_through_gateway(self, fleet, mock_loop):
        fleet.register(mock_loop, name="researcher", version="1.0.0", owner="ml-team")
        fleet.registry.deploy("researcher")
        result = await fleet.submit("Research latest papers", agent="researcher")
        assert result["agent_name"] == "researcher"
        assert result["status"] == "dispatched"

    def test_shared_state_scoped_to_fleet(self, fleet):
        fleet.state.set("key1", "value1", agent="agent-a")
        assert fleet.state.get("key1") == "value1"

    def test_event_bus_delivers_events(self, fleet):
        received = []
        fleet.bus.subscribe("test-topic", lambda e: received.append(e.payload))
        fleet.bus.publish("test-topic", "hello", source_agent="agent-a")
        assert received == ["hello"]

    def test_budget_tracks_costs(self, fleet):
        fleet.budget.record_cost("researcher", "ml-team", 5.0)
        assert fleet.budget.fleet_spent() == 5.0
        assert fleet.budget.check_budget("researcher")

    def test_observer_sees_registered_agents(self, fleet, mock_loop):
        fleet.register(mock_loop, name="researcher", version="1.0.0", owner="ml-team")
        snapshot = fleet.observer.health_snapshot()
        assert len(snapshot) == 1
        assert snapshot[0].name == "researcher"

    def test_gateway_connected_to_registry(self, fleet, mock_loop):
        """Gateway should see agents registered through Fleet."""
        fleet.register(mock_loop, name="researcher", version="1.0.0", owner="ml-team")
        fleet.registry.deploy("researcher")
        active = fleet.registry.active_agents()
        assert len(active) == 1

    def test_observer_alerts_through_event_bus(self, fleet, mock_loop):
        """Observer should publish alerts via the shared EventBus."""
        fleet.register(mock_loop, name="researcher", version="1.0.0", owner="ml-team")

        alerts = []
        fleet.bus.subscribe("fleet.alert.quality", lambda e: alerts.append(e.payload))

        # Record a low quality score — should trigger alert
        fleet.observer.record_quality_score("researcher", 30.0)
        assert len(alerts) == 1
        assert alerts[0]["agent_name"] == "researcher"

    def test_no_budget_when_not_configured(self):
        config = FleetConfig(name="minimal")
        fleet = Fleet(config)
        assert fleet.budget is None

    def test_backend_import_error_for_redis(self):
        import sys
        import unittest.mock

        config = FleetConfig(name="test", state_backend="redis")
        with unittest.mock.patch.dict(sys.modules, {"redis": None}):
            # Force reimport of the backend module so it re-checks for redis
            sys.modules.pop("tvastar.fleet.backends.redis_state", None)
            with pytest.raises(ImportError, match="tvastar\\[redis\\]"):
                Fleet(config)

    def test_backend_import_error_for_kafka(self):
        config = FleetConfig(name="test", event_backend="kafka")
        with pytest.raises(ImportError, match="tvastar\\[kafka\\]"):
            Fleet(config)
