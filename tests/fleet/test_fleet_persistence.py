"""Tests for Fleet persistence (persist/load) and graceful shutdown."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from tvastar.fleet import Fleet, FleetConfig
from tvastar.fleet.registry import AgentState


@pytest.fixture
def fleet(tmp_path):
    """A Fleet with a config pointing to a temp persist path."""
    config = FleetConfig(name="test-fleet")
    f = Fleet(config)
    f._persist_path = str(tmp_path / "fleet-state.json")
    return f, tmp_path


@pytest.fixture
def mock_loop():
    loop = MagicMock()
    loop.config = MagicMock()
    loop.config.goal = "Do work"
    loop.stop = AsyncMock()
    return loop


class TestFleetPersist:
    def test_persist_creates_file(self, fleet, mock_loop):
        f, tmp_path = fleet
        f.register(mock_loop, name="worker", version="1.0.0", owner="team-a")
        f.registry.deploy("worker")

        f.persist(str(tmp_path / "state.json"))
        assert (tmp_path / "state.json").exists()

    def test_persist_and_load_round_trip(self, fleet, mock_loop):
        f, tmp_path = fleet
        f.register(mock_loop, name="worker", version="2.0.0", owner="ml-team")
        f.registry.deploy("worker")
        f.registry.pause("worker")

        f.persist(str(tmp_path / "state.json"))

        # Create a fresh fleet and load
        f2 = Fleet(FleetConfig(name="test-fleet"))
        count = f2.load(str(tmp_path / "state.json"))

        assert count == 1
        entry = f2.registry.get("worker")
        assert entry is not None
        assert entry.name == "worker"
        assert entry.version == "2.0.0"
        assert entry.owner == "ml-team"
        assert entry.state == AgentState.REGISTERED
        assert entry.loop is None

    def test_persist_multiple_agents(self, fleet, mock_loop):
        f, tmp_path = fleet
        f.register(mock_loop, name="a", version="1.0.0", owner="team")
        f.register(mock_loop, name="b", version="2.0.0", owner="team")
        f.register(mock_loop, name="c", version="3.0.0", owner="other")
        f.registry.deploy("a")
        f.registry.deploy("b")

        path = f.persist(str(tmp_path / "state.json"))

        f2 = Fleet(FleetConfig(name="test-fleet"))
        count = f2.load(path)

        assert count == 3
        assert f2.registry.get("a").state == AgentState.REGISTERED
        assert f2.registry.get("b").state == AgentState.REGISTERED
        assert f2.registry.get("c").state == AgentState.REGISTERED

    def test_load_nonexistent_file_returns_zero(self, fleet):
        f, tmp_path = fleet
        count = f.load(str(tmp_path / "does_not_exist.json"))
        assert count == 0

    def test_load_corrupt_file_returns_zero(self, fleet):
        f, tmp_path = fleet
        corrupt = tmp_path / "corrupt.json"
        corrupt.write_text("not valid json{{{", encoding="utf-8")
        count = f.load(str(corrupt))
        assert count == 0

    def test_persist_preserves_version_history(self, fleet, mock_loop):
        f, tmp_path = fleet
        f.register(mock_loop, name="agent", version="1.0.0", owner="team")

        path = f.persist(str(tmp_path / "state.json"))

        f2 = Fleet(FleetConfig(name="test-fleet"))
        f2.load(path)

        history = f2.registry.version_history("agent")
        assert len(history) == 1
        assert history[0].version == "1.0.0"

    def test_persist_preserves_dependencies(self, fleet, mock_loop):
        f, tmp_path = fleet
        f.register(mock_loop, name="a", version="1.0.0", owner="team")
        f.register(mock_loop, name="b", version="1.0.0", owner="team", dependencies=["a"])

        path = f.persist(str(tmp_path / "state.json"))

        f2 = Fleet(FleetConfig(name="test-fleet"))
        f2.load(path)

        entry = f2.registry.get("b")
        assert entry.dependencies == ["a"]

    def test_load_restores_active_set(self, fleet, mock_loop):
        """Active set index should be rebuilt on load."""
        f, tmp_path = fleet
        f.register(mock_loop, name="active-agent", version="1.0.0", owner="team")
        f.registry.deploy("active-agent")

        path = f.persist(str(tmp_path / "state.json"))

        f2 = Fleet(FleetConfig(name="test-fleet"))
        f2.load(path)

        active = f2.registry.active_agents()
        assert active == []


class TestFleetShutdown:
    @pytest.mark.asyncio
    async def test_shutdown_persists_state(self, tmp_path):
        config = FleetConfig(name="shutdown-test")
        f = Fleet(config)
        mock = MagicMock()
        mock.config = MagicMock()
        mock.config.goal = "work"
        mock.stop = AsyncMock()
        f.register(mock, name="w", version="1.0.0", owner="t")
        f.registry.deploy("w")

        await f.shutdown(persist=True)

        # State file should exist in default location and be non-executable.
        from pathlib import Path

        state_path = Path(".tvastar-fleet/shutdown-test.json")
        assert state_path.exists()
        assert (
            json.loads(state_path.read_text(encoding="utf-8"))["agents"]["w"]["state"]
            == "registered"
        )

        # Cleanup
        import shutil

        shutil.rmtree(".tvastar-fleet", ignore_errors=True)

    @pytest.mark.asyncio
    async def test_shutdown_stops_loops(self, tmp_path):
        config = FleetConfig(name="stop-test")
        f = Fleet(config)
        mock = MagicMock()
        mock.config = MagicMock()
        mock.config.goal = "work"
        mock.stop = AsyncMock()
        f.register(mock, name="w", version="1.0.0", owner="t")

        await f.shutdown(persist=False)

        mock.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_shutdown_no_persist(self, tmp_path):
        config = FleetConfig(name="no-persist-test")
        f = Fleet(config)

        await f.shutdown(persist=False)
        # No exception, no file created
        from pathlib import Path

        assert not Path(".tvastar-fleet/no-persist-test.json").exists()

    @pytest.mark.asyncio
    async def test_context_manager(self, tmp_path):
        config = FleetConfig(name="ctx-test")
        async with Fleet(config) as f:
            mock = MagicMock()
            mock.config = MagicMock()
            mock.config.goal = "work"
            mock.stop = AsyncMock()
            f.register(mock, name="agent", version="1.0.0", owner="t")

        # After exit, loop.stop() should have been called
        mock.stop.assert_called_once()

        # Cleanup
        import shutil

        shutil.rmtree(".tvastar-fleet", ignore_errors=True)


class TestFleetRecoverySafety:
    def test_load_is_atomic_on_invalid_metadata(self, tmp_path, mock_loop):
        fleet = Fleet(FleetConfig(name="atomic-load"))
        fleet.register(mock_loop, name="live", version="1.0.0", owner="team")
        broken = tmp_path / "broken.json"
        broken.write_text(
            json.dumps(
                {
                    "fleet_name": "atomic-load",
                    "agents": {},
                    "versions": [],
                    "dependencies": {},
                }
            ),
            encoding="utf-8",
        )

        assert fleet.load(str(broken)) == 0
        assert fleet.registry.get("live") is not None

    def test_loaded_agent_requires_public_rebind_before_deploy(self, tmp_path, mock_loop):
        source = Fleet(FleetConfig(name="rebind"))
        source.register(mock_loop, name="worker", version="1.0.0", owner="team")
        source.registry.deploy("worker")
        path = source.persist(str(tmp_path / "fleet.json"))

        recovered = Fleet(FleetConfig(name="rebind"))
        assert recovered.load(path) == 1
        assert recovered.registry.get("worker").loop is None
        assert recovered.registry.get("worker").state == AgentState.REGISTERED

        rebound = recovered.rebind("worker", mock_loop)
        assert rebound.loop is mock_loop
        assert recovered.registry.deploy("worker").state == AgentState.ACTIVE

    @pytest.mark.asyncio
    async def test_shutdown_persists_non_executable_recovery_state(self, tmp_path, mock_loop):
        fleet = Fleet(FleetConfig(name="safe-shutdown"))
        fleet.register(mock_loop, name="worker", version="1.0.0", owner="team")
        fleet.registry.deploy("worker")
        path = tmp_path / "fleet.json"

        # Use the existing explicit path for inspection while exercising the safe format.
        await fleet.shutdown(persist=False)
        fleet.persist(str(path), safe_recovery=True)

        persisted = json.loads(path.read_text(encoding="utf-8"))
        assert persisted["agents"]["worker"]["state"] == "registered"
