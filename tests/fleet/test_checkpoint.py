"""Unit tests for FleetCheckpointManager.

Validates: Requirements 6.1, 6.2, 6.3, 6.4, 6.5, 6.6
"""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock, patch

import pytest

from tvastar.fleet.checkpoint import FleetCheckpointManager
from tvastar.fleet.registry import FleetRegistry, AgentState
from tvastar.fleet.observer import FleetObserver
from tvastar.fleet.bus import EventBus
from tvastar.fleet import AgentHealthSnapshot
from tvastar.memory.store import InMemoryStore


@pytest.fixture
def registry() -> FleetRegistry:
    reg = FleetRegistry("test-fleet")
    loop_mock = MagicMock()
    loop_mock.name = "agent-a"
    loop_mock.last_run_status = "pass"
    loop_mock.last_run_at = 1700000000.0
    reg.register(loop_mock, name="agent-a", version="1.0.0", owner="team")
    reg.deploy("agent-a")
    return reg


@pytest.fixture
def observer(registry) -> FleetObserver:
    bus = EventBus("test-fleet")
    return FleetObserver(registry, bus)


@pytest.fixture
def manager(registry, observer) -> FleetCheckpointManager:
    return FleetCheckpointManager(registry, observer)


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()


class TestCheckpoint:
    def test_checkpoint_stores_fleet_state(self, manager, store):
        result = manager.checkpoint("my-loop", store)
        assert result is True
        keys = store.keys("fleet_checkpoint:my-loop:")
        assert len(keys) == 1
        data = json.loads(store.get(keys[0]))
        assert isinstance(data, list)
        assert any(a["name"] == "agent-a" for a in data)

    def test_checkpoint_key_format(self, manager, store):
        with patch("tvastar.fleet.checkpoint.time") as mock_time:
            mock_time.time.return_value = 1700000000.0
            manager.checkpoint("loop-x", store)
        keys = store.keys("fleet_checkpoint:loop-x:")
        assert keys == ["fleet_checkpoint:loop-x:1700000000"]

    def test_checkpoint_failure_returns_false(self, store):
        # Observer that raises on health_snapshot
        bad_observer = MagicMock()
        bad_observer.health_snapshot.side_effect = RuntimeError("boom")
        mgr = FleetCheckpointManager(MagicMock(), bad_observer)
        result = mgr.checkpoint("loop", store)
        assert result is False

    def test_checkpoint_failure_does_not_raise(self, store):
        bad_observer = MagicMock()
        bad_observer.health_snapshot.side_effect = RuntimeError("boom")
        mgr = FleetCheckpointManager(MagicMock(), bad_observer)
        # Should not raise — just returns False
        mgr.checkpoint("loop", store)


class TestPrune:
    def test_prune_keeps_only_3_most_recent(self, manager, store):
        # Manually insert 5 checkpoints
        for i in range(5):
            store.set(f"fleet_checkpoint:loop:{1000 + i}", f"data-{i}")
        manager._prune("loop", store)
        keys = store.keys("fleet_checkpoint:loop:")
        assert len(keys) == 3
        # Should keep the most recent (1004, 1003, 1002)
        assert "fleet_checkpoint:loop:1004" in keys
        assert "fleet_checkpoint:loop:1003" in keys
        assert "fleet_checkpoint:loop:1002" in keys

    def test_prune_no_op_when_3_or_fewer(self, manager, store):
        store.set("fleet_checkpoint:loop:100", "x")
        store.set("fleet_checkpoint:loop:200", "y")
        manager._prune("loop", store)
        assert len(store.keys("fleet_checkpoint:loop:")) == 2


class TestInjectContext:
    def test_inject_prepends_system_message(self, manager, store):
        store.set("fleet_checkpoint:loop:999", json.dumps([{"name": "a", "state": "active"}]))
        messages = [MagicMock()]
        result = manager.inject_context("loop", store, messages)
        assert len(result) == 2
        assert result[0].role == "system"
        assert "[Fleet State]" in result[0].content

    def test_inject_no_checkpoint_returns_unchanged(self, manager, store):
        messages = [MagicMock()]
        result = manager.inject_context("loop", store, messages)
        assert result is messages

    def test_inject_truncates_to_4096_chars(self, manager, store):
        # Store a very large checkpoint
        big_data = json.dumps([{"name": f"agent-{i}", "state": "active"} for i in range(500)])
        store.set("fleet_checkpoint:loop:999", big_data)
        result = manager.inject_context("loop", store, [])
        assert len(result[0].content) <= FleetCheckpointManager.MAX_INJECT_CHARS

    def test_inject_uses_most_recent_checkpoint(self, manager, store):
        store.set("fleet_checkpoint:loop:100", json.dumps([{"name": "old"}]))
        store.set("fleet_checkpoint:loop:999", json.dumps([{"name": "new"}]))
        result = manager.inject_context("loop", store, [])
        assert "new" in result[0].content
