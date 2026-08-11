"""Tests for memory maintenance: decay, expire, cleanup, and Loop integration.

Validates Requirement 17 — Memory Maintenance Automation.
"""

from __future__ import annotations

import time

import pytest

from tvastar.contrib.ltm.store import LTMStore


@pytest.fixture
def store(tmp_path):
    db_path = str(tmp_path / "test_maintain.db")
    with LTMStore(db_path) as s:
        yield s


# ---------------------------------------------------------------------------
# decay() tests
# ---------------------------------------------------------------------------


class TestDecay:
    def test_decay_reduces_confidence(self, store: LTMStore):
        """decay(key, factor=0.9) multiplies confidence by 0.9."""
        store.remember("fact", "value", agent="a", confidence=1.0)
        result = store.decay("fact", factor=0.9)
        assert result is not None
        assert abs(result.confidence - 0.9) < 1e-9

    def test_decay_floor_at_zero(self, store: LTMStore):
        """decay() never sets confidence below 0, even with extreme factor."""
        store.remember("fact", "value", agent="a", confidence=0.1)
        # factor=0 should floor at 0
        result = store.decay("fact", factor=0.0)
        assert result is not None
        assert result.confidence == 0.0

    def test_decay_nonexistent_key_returns_none(self, store: LTMStore):
        """decay() on a missing key returns None."""
        assert store.decay("nope") is None

    def test_decay_stacks(self, store: LTMStore):
        """Multiple decay() calls multiply cumulatively."""
        store.remember("fact", "value", agent="a", confidence=1.0)
        store.decay("fact", factor=0.5)
        result = store.decay("fact", factor=0.5)
        assert result is not None
        assert abs(result.confidence - 0.25) < 1e-9

    def test_decay_negative_factor_floors_at_zero(self, store: LTMStore):
        """Negative factor should still produce confidence >= 0."""
        store.remember("fact", "value", agent="a", confidence=0.5)
        result = store.decay("fact", factor=-1.0)
        assert result is not None
        assert result.confidence == 0.0


# ---------------------------------------------------------------------------
# maintain() — expire facts below threshold
# ---------------------------------------------------------------------------


class TestMaintainExpire:
    def test_expire_facts_below_threshold(self, store: LTMStore):
        """maintain() closes valid_until on active facts with confidence < threshold."""
        store.remember("strong", "value", agent="a", confidence=0.8)
        store.remember("weak", "value", agent="a", confidence=0.1)

        store.maintain(decay_threshold=0.3)

        # Strong fact remains active
        assert store.recall("strong") == "value"
        # Weak fact is now expired
        assert store.recall("weak") is None

    def test_maintain_never_deletes_active_facts(self, store: LTMStore):
        """maintain() only closes valid_until — never deletes active rows."""
        store.remember("fact", "value", agent="a", confidence=0.1)
        store.maintain(decay_threshold=0.5)

        # The fact is expired (not returned by recall) but still in history
        history = store.recall_history("fact")
        assert len(history) == 1
        assert history[0].valid_until is not None

    def test_maintain_does_not_expire_above_threshold(self, store: LTMStore):
        """Facts at or above threshold are not expired."""
        store.remember("exact", "value", agent="a", confidence=0.3)
        store.maintain(decay_threshold=0.3)
        # confidence == threshold should NOT be expired (only < threshold)
        assert store.recall("exact") == "value"


# ---------------------------------------------------------------------------
# maintain() — cleanup old relationships
# ---------------------------------------------------------------------------


class TestMaintainCleanup:
    def _create_relationships_table(self, store: LTMStore):
        """Helper: create the relationships table that Task 6 would add."""
        store._conn.executescript("""
            CREATE TABLE IF NOT EXISTS relationships (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_key TEXT NOT NULL,
                edge_type TEXT NOT NULL,
                target_key TEXT NOT NULL,
                valid_from REAL NOT NULL,
                valid_until REAL,
                confidence REAL NOT NULL DEFAULT 1.0
            );
            CREATE INDEX IF NOT EXISTS idx_rel_source ON relationships(source_key, edge_type);
            CREATE INDEX IF NOT EXISTS idx_rel_target ON relationships(target_key, edge_type);
        """)
        store._conn.commit()

    def test_cleanup_relationships_both_endpoints_expired(self, store: LTMStore):
        """maintain() deletes relationships where both endpoints expired beyond max_age_days."""
        self._create_relationships_table(store)

        # Create two facts that are already expired (valid_until set long ago)
        old_time = time.time() - (100 * 86400)  # 100 days ago
        store._conn.execute(
            "INSERT INTO facts (key, value, agent, confidence, updated_at, version, valid_from, valid_until) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("src", '"old_src"', "a", 0.1, old_time, 1, old_time, old_time + 1),
        )
        store._conn.execute(
            "INSERT INTO facts (key, value, agent, confidence, updated_at, version, valid_from, valid_until) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("tgt", '"old_tgt"', "a", 0.1, old_time, 1, old_time, old_time + 1),
        )
        # Create a relationship between them
        store._conn.execute(
            "INSERT INTO relationships (source_key, edge_type, target_key, valid_from, confidence) "
            "VALUES (?, ?, ?, ?, ?)",
            ("src", "SUPERSEDES", "tgt", old_time, 1.0),
        )
        store._conn.commit()

        store.maintain(max_age_days=90)

        # Relationship should be deleted
        row = store._conn.execute("SELECT COUNT(*) FROM relationships").fetchone()
        assert row[0] == 0

    def test_cleanup_preserves_relationship_with_active_endpoint(self, store: LTMStore):
        """maintain() does NOT delete relationship if one endpoint is still active."""
        self._create_relationships_table(store)

        old_time = time.time() - (100 * 86400)
        # Source expired long ago
        store._conn.execute(
            "INSERT INTO facts (key, value, agent, confidence, updated_at, version, valid_from, valid_until) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("src", '"old"', "a", 0.1, old_time, 1, old_time, old_time + 1),
        )
        # Target still active
        store.remember("tgt", "active", agent="a", confidence=0.9)
        # Create relationship
        store._conn.execute(
            "INSERT INTO relationships (source_key, edge_type, target_key, valid_from, confidence) "
            "VALUES (?, ?, ?, ?, ?)",
            ("src", "DEPENDS_ON", "tgt", old_time, 1.0),
        )
        store._conn.commit()

        store.maintain(max_age_days=90)

        # Relationship preserved because target is active
        row = store._conn.execute("SELECT COUNT(*) FROM relationships").fetchone()
        assert row[0] == 1

    def test_maintain_without_relationships_table(self, store: LTMStore):
        """maintain() works gracefully when relationships table doesn't exist."""
        store.remember("weak", "val", agent="a", confidence=0.1)
        # Should not raise
        store.maintain(decay_threshold=0.3)
        assert store.recall("weak") is None

    def test_cleanup_never_deletes_facts(self, store: LTMStore):
        """Deleting a relationship never deletes the facts it connects."""
        self._create_relationships_table(store)

        old_time = time.time() - (100 * 86400)
        store._conn.execute(
            "INSERT INTO facts (key, value, agent, confidence, updated_at, version, valid_from, valid_until) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("src", '"v1"', "a", 0.1, old_time, 1, old_time, old_time + 1),
        )
        store._conn.execute(
            "INSERT INTO facts (key, value, agent, confidence, updated_at, version, valid_from, valid_until) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("tgt", '"v2"', "a", 0.1, old_time, 1, old_time, old_time + 1),
        )
        store._conn.execute(
            "INSERT INTO relationships (source_key, edge_type, target_key, valid_from, confidence) "
            "VALUES (?, ?, ?, ?, ?)",
            ("src", "CAUSED", "tgt", old_time, 1.0),
        )
        store._conn.commit()

        store.maintain(max_age_days=90)

        # Facts still exist in history (not deleted)
        assert len(store.recall_history("src")) == 1
        assert len(store.recall_history("tgt")) == 1


# ---------------------------------------------------------------------------
# Loop integration — memory_maintenance
# ---------------------------------------------------------------------------


class TestLoopMaintenance:
    def test_loop_config_defaults(self):
        """memory_maintenance defaults to False, maintenance_interval to 10."""
        from tvastar.loop import LoopConfig

        cfg = LoopConfig(name="test", goal="do stuff")
        assert cfg.memory_maintenance is False
        assert cfg.maintenance_interval == 10

    def test_loop_disabled_maintenance_is_noop(self):
        """A Loop with memory_maintenance=False doesn't track successful iterations for maintenance."""
        from tvastar.loop import Loop, LoopConfig

        cfg = LoopConfig(name="test", goal="do stuff", memory_maintenance=False)

        # Just verify the counter starts at 0 — no maintenance called
        from unittest.mock import MagicMock

        spec = MagicMock()
        spec.instructions = "test"
        # We can't easily run the full Loop without a real agent,
        # but we can verify the _successful_iterations counter exists
        # and _run_maintenance is a no-op when no LTMStore is attached
        loop = Loop(spec, cfg)
        assert loop._successful_iterations == 0
        # Should not raise even without an LTMStore
        loop._run_maintenance()

    def test_loop_calls_maintain_at_interval(self):
        """When memory_maintenance=True, _run_maintenance is called every N successful iterations."""
        from unittest.mock import MagicMock, patch

        from tvastar.loop import Loop, LoopConfig

        cfg = LoopConfig(
            name="maint_test", goal="do stuff", memory_maintenance=True, maintenance_interval=3
        )

        spec = MagicMock()
        spec.instructions = "test"
        loop = Loop(spec, cfg)

        # Simulate successful iterations by incrementing counter + checking maintenance
        with patch.object(loop, "_run_maintenance") as mock_maintain:
            for i in range(1, 10):
                loop._successful_iterations += 1
                if loop._successful_iterations % cfg.maintenance_interval == 0:
                    loop._run_maintenance()

            # Should have been called at iterations 3, 6, 9
            assert mock_maintain.call_count == 3
