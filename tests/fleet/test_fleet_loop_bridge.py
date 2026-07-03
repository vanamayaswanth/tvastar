"""Integration test proving Fleet.submit() triggers Loop.trigger()."""
import pytest
from tvastar.agent import create_agent
from tvastar.fleet import Fleet, FleetConfig
from tvastar.loop import Loop, LoopConfig, LoopState
from tvastar.model.mock import MockModel


@pytest.fixture
def fleet_with_real_loop():
    """Fleet with a real Loop registered."""
    model = MockModel(script=["Task completed successfully."])
    spec = create_agent("worker", model=model, instructions="You are a worker.", max_steps=3)
    loop = Loop(spec, LoopConfig(name="worker", goal="Do work"))

    fleet = Fleet(FleetConfig(name="bridge-test"))
    fleet.register(loop, name="worker", version="1.0.0", owner="test")
    fleet.registry.deploy("worker")
    return fleet, loop


class TestFleetLoopBridge:
    @pytest.mark.asyncio
    async def test_submit_triggers_loop(self, fleet_with_real_loop):
        """submit() should call loop.trigger() on the selected agent."""
        fleet, loop = fleet_with_real_loop

        result = await fleet.submit("Do the work", agent="worker")

        assert result["agent_name"] == "worker"
        assert result["status"] == "dispatched"
        # Loop should have been triggered — check history
        assert len(loop.history()) >= 1

    @pytest.mark.asyncio
    async def test_submit_returns_loop_run(self, fleet_with_real_loop):
        """submit() result should include loop_run when Loop was triggered."""
        fleet, loop = fleet_with_real_loop

        result = await fleet.submit("Do the work", agent="worker")

        assert "loop_run" in result
        run = result["loop_run"]
        assert run.loop_name == "worker"

    @pytest.mark.asyncio
    async def test_submit_works_with_mock_loop(self):
        """submit() should work even with a MagicMock loop (no trigger)."""
        from unittest.mock import MagicMock

        mock_loop = MagicMock()
        mock_loop.config = MagicMock()
        mock_loop.config.goal = "Do things"
        # MagicMock won't have a proper async trigger
        del mock_loop.trigger  # Remove trigger so it falls back

        fleet = Fleet(FleetConfig(name="mock-test"))
        fleet.register(mock_loop, name="mock-agent", version="1.0.0", owner="test")
        fleet.registry.deploy("mock-agent")

        result = await fleet.submit("Do things", agent="mock-agent")
        assert result["status"] == "dispatched"
        assert "loop_run" not in result
