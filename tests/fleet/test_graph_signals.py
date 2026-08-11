"""Tests for Fleet graph_signals integration (Requirement 15).

Validates:
- graph_signals=False (default) produces IDENTICAL behavior to current Fleet
- graph_signals=True + event topic matching EdgeType verb writes relationship
- Health degradation propagates through DEPENDS_ON edges
- Only EdgeType-matching events create relationships
"""

from __future__ import annotations

import tempfile

import pytest

from tvastar.contrib.ltm.store import LTMStore
from tvastar.fleet import AlertConfig, EventBus, FleetConfig, FleetRegistry
from tvastar.fleet.observer import FleetObserver


@pytest.fixture
def ltm_store(tmp_path):
    """Create a fresh SQLite LTMStore for testing."""
    db_path = str(tmp_path / "test_graph.db")
    return LTMStore(db_path)


@pytest.fixture
def registry():
    return FleetRegistry("test-fleet")


@pytest.fixture
def event_bus():
    return EventBus("test-fleet")


class TestGraphSignalsDisabled:
    """graph_signals=False (default) produces IDENTICAL behavior to current Fleet."""

    def test_default_config_has_graph_signals_false(self):
        config = FleetConfig(name="test")
        assert config.graph_signals is False

    def test_no_relationships_written_when_disabled(self, registry, event_bus, ltm_store):
        """Events published when graph_signals=False must NOT write relationships."""
        observer = FleetObserver(
            registry,
            event_bus,
            graph_signals=False,
            ltm_store=ltm_store,
        )

        # Publish an event whose topic matches an EdgeType verb
        event_bus.publish(
            "fleet.depends_on",
            {"source": "agent_a", "target": "agent_b"},
            source_agent="test",
        )

        # No relationships should have been written
        rels = ltm_store.relationships_of("agent_a", direction="outgoing")
        assert rels == []

    def test_health_degradation_no_propagation_when_disabled(
        self, registry, event_bus, ltm_store
    ):
        """Health degradation with graph_signals=False does not propagate."""
        observer = FleetObserver(
            registry,
            event_bus,
            graph_signals=False,
            ltm_store=ltm_store,
        )

        # Set up a DEPENDS_ON relationship manually in LTM
        ltm_store.relate("agent_research", "DEPENDS_ON", "agent_scraper")

        # Trigger health degradation
        affected = observer.on_health_degradation("agent_scraper")
        assert affected == []
        assert observer.potentially_affected == set()


class TestGraphSignalsEnabled:
    """graph_signals=True writes relationships and propagates health."""

    def test_event_matching_edge_type_writes_relationship(
        self, registry, event_bus, ltm_store
    ):
        """When graph_signals=True and topic matches EdgeType, write relationship."""
        observer = FleetObserver(
            registry,
            event_bus,
            graph_signals=True,
            ltm_store=ltm_store,
        )

        # Publish an event matching DEPENDS_ON
        event_bus.publish(
            "fleet.depends_on",
            {"source": "agent_research", "target": "agent_scraper"},
            source_agent="coordinator",
        )

        # Relationship should have been written
        rels = ltm_store.relationships_of("agent_research", direction="outgoing")
        assert len(rels) == 1
        assert rels[0].source_key == "agent_research"
        assert rels[0].edge_type == "DEPENDS_ON"
        assert rels[0].target_key == "agent_scraper"

    def test_event_matching_caused_writes_relationship(
        self, registry, event_bus, ltm_store
    ):
        """Topic fleet.caused also matches EdgeType CAUSED."""
        observer = FleetObserver(
            registry,
            event_bus,
            graph_signals=True,
            ltm_store=ltm_store,
        )

        event_bus.publish(
            "fleet.caused",
            {"source": "action_x", "target": "effect_y"},
            source_agent="system",
        )

        rels = ltm_store.relationships_of("action_x", direction="outgoing")
        assert len(rels) == 1
        assert rels[0].edge_type == "CAUSED"

    def test_non_edge_type_event_does_not_write_relationship(
        self, registry, event_bus, ltm_store
    ):
        """Events with topics NOT matching EdgeType verbs are ignored."""
        observer = FleetObserver(
            registry,
            event_bus,
            graph_signals=True,
            ltm_store=ltm_store,
        )

        # "run_completed" is not an EdgeType
        event_bus.publish(
            "fleet.run_completed",
            {"source": "agent_a", "target": "agent_b"},
            source_agent="test",
        )

        rels = ltm_store.relationships_of("agent_a", direction="outgoing")
        assert rels == []

    def test_event_without_target_does_not_write(
        self, registry, event_bus, ltm_store
    ):
        """Event matching EdgeType but missing target payload is skipped."""
        observer = FleetObserver(
            registry,
            event_bus,
            graph_signals=True,
            ltm_store=ltm_store,
        )

        event_bus.publish(
            "fleet.depends_on",
            {"source": "agent_a"},  # no "target"
            source_agent="test",
        )

        rels = ltm_store.relationships_of("agent_a", direction="outgoing")
        assert rels == []

    def test_health_propagation_through_depends_on(
        self, registry, event_bus, ltm_store
    ):
        """Health degradation traverses DEPENDS_ON and marks dependents."""
        observer = FleetObserver(
            registry,
            event_bus,
            graph_signals=True,
            ltm_store=ltm_store,
        )

        # Set up: agent_research DEPENDS_ON agent_scraper
        ltm_store.relate("agent_research", "DEPENDS_ON", "agent_scraper")

        # Trigger health degradation on agent_scraper
        affected = observer.on_health_degradation("agent_scraper")

        assert "agent_research" in affected
        assert "agent_research" in observer.potentially_affected

    def test_health_propagation_multiple_dependents(
        self, registry, event_bus, ltm_store
    ):
        """Multiple dependents are all marked affected."""
        observer = FleetObserver(
            registry,
            event_bus,
            graph_signals=True,
            ltm_store=ltm_store,
        )

        # agent_research and agent_writer both depend on agent_scraper
        ltm_store.relate("agent_research", "DEPENDS_ON", "agent_scraper")
        ltm_store.relate("agent_writer", "DEPENDS_ON", "agent_scraper")

        affected = observer.on_health_degradation("agent_scraper")

        assert set(affected) == {"agent_research", "agent_writer"}
        assert observer.potentially_affected == {"agent_research", "agent_writer"}

    def test_cascading_alert_emitted_on_health_degradation(
        self, registry, event_bus, ltm_store
    ):
        """Cascading alert is published to EventBus on health degradation."""
        observer = FleetObserver(
            registry,
            event_bus,
            graph_signals=True,
            ltm_store=ltm_store,
        )

        ltm_store.relate("agent_research", "DEPENDS_ON", "agent_scraper")

        # Capture events
        alerts = []
        event_bus.subscribe(
            "fleet.alert.cascading_health",
            lambda e: alerts.append(e),
            agent="test",
        )

        observer.on_health_degradation("agent_scraper")

        assert len(alerts) == 1
        assert alerts[0].payload["degraded_agent"] == "agent_scraper"
        assert "agent_research" in alerts[0].payload["affected_agents"]

    def test_no_cascading_alert_when_no_dependents(
        self, registry, event_bus, ltm_store
    ):
        """No alert emitted if degraded agent has no dependents."""
        observer = FleetObserver(
            registry,
            event_bus,
            graph_signals=True,
            ltm_store=ltm_store,
        )

        alerts = []
        event_bus.subscribe(
            "fleet.alert.cascading_health",
            lambda e: alerts.append(e),
            agent="test",
        )

        affected = observer.on_health_degradation("agent_isolated")

        assert affected == []
        assert alerts == []

    def test_quality_degradation_triggers_health_propagation(
        self, registry, event_bus, ltm_store
    ):
        """When quality drops below threshold, health propagation fires automatically."""
        observer = FleetObserver(
            registry,
            event_bus,
            alert_config=AlertConfig(quality_threshold=50.0),
            graph_signals=True,
            ltm_store=ltm_store,
        )

        # Set up dependency: agent_writer DEPENDS_ON agent_research
        ltm_store.relate("agent_writer", "DEPENDS_ON", "agent_research")

        # Record a quality score below threshold
        alerts = []
        event_bus.subscribe(
            "fleet.alert.cascading_health",
            lambda e: alerts.append(e),
            agent="test",
        )

        observer.record_quality_score("agent_research", 30.0)

        # Should have triggered cascading alert
        assert len(alerts) == 1
        assert "agent_writer" in alerts[0].payload["affected_agents"]
        assert "agent_writer" in observer.potentially_affected

    def test_only_depends_on_edges_propagate(
        self, registry, event_bus, ltm_store
    ):
        """Health propagation only follows DEPENDS_ON edges, not other types."""
        observer = FleetObserver(
            registry,
            event_bus,
            graph_signals=True,
            ltm_store=ltm_store,
        )

        # RELATED_TO edge should NOT trigger propagation
        ltm_store.relate("agent_writer", "RELATED_TO", "agent_scraper")
        # Only this DEPENDS_ON edge should propagate
        ltm_store.relate("agent_research", "DEPENDS_ON", "agent_scraper")

        affected = observer.on_health_degradation("agent_scraper")

        # Only agent_research (via DEPENDS_ON), not agent_writer (via RELATED_TO)
        assert affected == ["agent_research"]
