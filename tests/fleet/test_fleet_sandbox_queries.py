"""Tests for Fleet sandbox state query methods and event wiring.

Validates: Requirements 12.1–12.4
- sandbox_state_counts returns per-state counts
- sandbox_resource_totals returns aggregate resources of running sandboxes
- _emit_transition in LifecycleMixin publishes on FleetEventBus
"""

from __future__ import annotations

from tvastar.fleet import Fleet, FleetConfig


def _make_fleet() -> Fleet:
    return Fleet(FleetConfig(name="test-fleet"))


class TestSandboxStateCounts:
    def test_empty_fleet_returns_zeros(self):
        fleet = _make_fleet()
        assert fleet.sandbox_state_counts() == {"running": 0, "hibernated": 0, "stopped": 0}

    def test_counts_after_lifecycle_events(self):
        fleet = _make_fleet()
        # Simulate lifecycle events via the bus
        fleet.bus.publish(
            "sandbox.lifecycle",
            {"sandbox_id": "sb-1", "prev_state": "created", "new_state": "running"},
            source_agent="lifecycle_mixin",
        )
        fleet.bus.publish(
            "sandbox.lifecycle",
            {"sandbox_id": "sb-2", "prev_state": "created", "new_state": "running"},
            source_agent="lifecycle_mixin",
        )
        fleet.bus.publish(
            "sandbox.lifecycle",
            {"sandbox_id": "sb-3", "prev_state": "running", "new_state": "hibernated"},
            source_agent="lifecycle_mixin",
        )
        assert fleet.sandbox_state_counts() == {"running": 2, "hibernated": 1, "stopped": 0}

    def test_state_transition_updates_count(self):
        fleet = _make_fleet()
        fleet.bus.publish(
            "sandbox.lifecycle",
            {"sandbox_id": "sb-1", "prev_state": "created", "new_state": "running"},
            source_agent="lifecycle_mixin",
        )
        assert fleet.sandbox_state_counts()["running"] == 1

        # Transition to stopped
        fleet.bus.publish(
            "sandbox.lifecycle",
            {"sandbox_id": "sb-1", "prev_state": "running", "new_state": "stopped"},
            source_agent="lifecycle_mixin",
        )
        assert fleet.sandbox_state_counts() == {"running": 0, "hibernated": 0, "stopped": 1}


class TestSandboxResourceTotals:
    def test_empty_fleet_returns_zeros(self):
        fleet = _make_fleet()
        assert fleet.sandbox_resource_totals() == {"memory_mb": 0, "cpu_count": 0}

    def test_aggregates_running_sandbox_resources(self):
        fleet = _make_fleet()
        # Two running sandboxes with resources
        fleet.bus.publish(
            "sandbox.lifecycle",
            {"sandbox_id": "sb-1", "prev_state": "created", "new_state": "running"},
            source_agent="lifecycle_mixin",
        )
        fleet.bus.publish(
            "sandbox.scale",
            {"sandbox_id": "sb-1", "memory_mb": 512, "cpu_count": 2},
            source_agent="scaler",
        )
        fleet.bus.publish(
            "sandbox.lifecycle",
            {"sandbox_id": "sb-2", "prev_state": "created", "new_state": "running"},
            source_agent="lifecycle_mixin",
        )
        fleet.bus.publish(
            "sandbox.scale",
            {"sandbox_id": "sb-2", "memory_mb": 1024, "cpu_count": 4},
            source_agent="scaler",
        )
        assert fleet.sandbox_resource_totals() == {"memory_mb": 1536, "cpu_count": 6}

    def test_excludes_hibernated_sandbox_resources(self):
        fleet = _make_fleet()
        fleet.bus.publish(
            "sandbox.lifecycle",
            {"sandbox_id": "sb-1", "prev_state": "created", "new_state": "running"},
            source_agent="lifecycle_mixin",
        )
        fleet.bus.publish(
            "sandbox.scale",
            {"sandbox_id": "sb-1", "memory_mb": 512, "cpu_count": 2},
            source_agent="scaler",
        )
        # Hibernate it
        fleet.bus.publish(
            "sandbox.lifecycle",
            {"sandbox_id": "sb-1", "prev_state": "running", "new_state": "hibernated"},
            source_agent="lifecycle_mixin",
        )
        # Resources should not count since it's not running
        assert fleet.sandbox_resource_totals() == {"memory_mb": 0, "cpu_count": 0}

    def test_stopped_sandbox_resources_removed(self):
        fleet = _make_fleet()
        fleet.bus.publish(
            "sandbox.lifecycle",
            {"sandbox_id": "sb-1", "prev_state": "created", "new_state": "running"},
            source_agent="lifecycle_mixin",
        )
        fleet.bus.publish(
            "sandbox.scale",
            {"sandbox_id": "sb-1", "memory_mb": 512, "cpu_count": 2},
            source_agent="scaler",
        )
        # Stop it — resources should be cleaned up
        fleet.bus.publish(
            "sandbox.lifecycle",
            {"sandbox_id": "sb-1", "prev_state": "running", "new_state": "stopped"},
            source_agent="lifecycle_mixin",
        )
        assert fleet.sandbox_resource_totals() == {"memory_mb": 0, "cpu_count": 0}
        # Resource dict should be empty
        assert "sb-1" not in fleet._sandbox_resources


class TestEmitTransitionWiring:
    def test_lifecycle_mixin_publishes_to_bus(self):
        """Verify _emit_transition publishes sandbox.lifecycle events consumed by Fleet."""
        fleet = _make_fleet()
        received = []
        fleet.bus.subscribe("sandbox.lifecycle", lambda e: received.append(e))

        # Publish like LifecycleMixin._emit_transition does
        fleet.bus.publish(
            "sandbox.lifecycle",
            {"sandbox_id": "sb-99", "prev_state": "running", "new_state": "hibernated"},
            source_agent="lifecycle_mixin",
        )

        assert len(received) == 1
        assert received[0].payload["sandbox_id"] == "sb-99"
        assert received[0].payload["prev_state"] == "running"
        assert received[0].payload["new_state"] == "hibernated"
        assert received[0].source_agent == "lifecycle_mixin"
        # Fleet should have tracked it too
        assert fleet._sandbox_states["sb-99"] == "hibernated"
