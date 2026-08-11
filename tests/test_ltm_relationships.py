"""Tests for LTMStore relationships table — relate() and relationships_of().

Covers:
- Round-trip: relate() then relationships_of() returns the relationship
- Both direction modes ("outgoing" and "incoming")
- Migration from database without relationships table
"""

from __future__ import annotations

import sqlite3

import pytest

from tvastar.contrib.ltm.store import LTMStore


@pytest.fixture
def store(tmp_path):
    db_path = str(tmp_path / "test_relationships.db")
    with LTMStore(db_path) as s:
        yield s


# ---------------------------------------------------------------------------
# Round-trip tests
# ---------------------------------------------------------------------------


class TestRelate:
    def test_relate_creates_relationship(self, store: LTMStore):
        rel = store.relate("fact_a", "DEPENDS_ON", "fact_b")
        assert rel.source_key == "fact_a"
        assert rel.edge_type == "DEPENDS_ON"
        assert rel.target_key == "fact_b"
        assert rel.valid_from > 0
        assert rel.valid_until is None
        assert rel.confidence == 1.0
        assert rel.id is not None

    def test_relate_custom_confidence(self, store: LTMStore):
        rel = store.relate("a", "RELATED_TO", "b", confidence=0.7)
        assert rel.confidence == 0.7

    def test_relate_roundtrip_outgoing(self, store: LTMStore):
        """relate() then relationships_of() returns the relationship."""
        store.relate("src", "CAUSED", "tgt")
        rels = store.relationships_of("src", direction="outgoing")
        assert len(rels) == 1
        assert rels[0].source_key == "src"
        assert rels[0].edge_type == "CAUSED"
        assert rels[0].target_key == "tgt"

    def test_relate_roundtrip_incoming(self, store: LTMStore):
        """relate() then relationships_of(target, direction='incoming') works."""
        store.relate("src", "DEPENDS_ON", "tgt")
        rels = store.relationships_of("tgt", direction="incoming")
        assert len(rels) == 1
        assert rels[0].source_key == "src"
        assert rels[0].target_key == "tgt"

    def test_multiple_relationships(self, store: LTMStore):
        store.relate("a", "DEPENDS_ON", "b")
        store.relate("a", "CAUSED", "c")
        store.relate("d", "DEPENDS_ON", "a")
        outgoing = store.relationships_of("a", direction="outgoing")
        assert len(outgoing) == 2
        incoming = store.relationships_of("a", direction="incoming")
        assert len(incoming) == 1

    def test_filter_by_edge_type(self, store: LTMStore):
        store.relate("a", "DEPENDS_ON", "b")
        store.relate("a", "CAUSED", "c")
        rels = store.relationships_of("a", direction="outgoing", edge_type="DEPENDS_ON")
        assert len(rels) == 1
        assert rels[0].target_key == "b"

    def test_relationships_of_empty(self, store: LTMStore):
        rels = store.relationships_of("nonexistent")
        assert rels == []

    def test_invalid_direction_raises(self, store: LTMStore):
        with pytest.raises(ValueError, match="direction must be"):
            store.relationships_of("x", direction="both")


# ---------------------------------------------------------------------------
# Auto SUPERSEDES on remember()
# ---------------------------------------------------------------------------


class TestAutoSupersedes:
    def test_superseding_fact_creates_supersedes_relationship(self, store: LTMStore):
        """remember() with existing active fact auto-creates SUPERSEDES relationship."""
        store.remember("loc", "NYC", agent="a")
        store.remember("loc", "SF", agent="a")  # supersedes
        rels = store.relationships_of("loc", direction="outgoing", edge_type="SUPERSEDES")
        assert len(rels) == 1
        assert rels[0].source_key == "loc"
        assert rels[0].edge_type == "SUPERSEDES"
        assert rels[0].target_key == "loc"

    def test_first_remember_no_supersedes(self, store: LTMStore):
        """First remember() (no existing fact) does NOT create a SUPERSEDES relationship."""
        store.remember("city", "NYC", agent="a")
        rels = store.relationships_of("city", direction="outgoing", edge_type="SUPERSEDES")
        assert rels == []

    def test_supersedes_relationship_temporal_fields(self, store: LTMStore):
        """SUPERSEDES relationship has valid_from matching new fact and valid_until=None."""
        store.remember("lang", "Python", agent="a")
        new_fact = store.remember("lang", "Rust", agent="a")
        rels = store.relationships_of("lang", direction="outgoing", edge_type="SUPERSEDES")
        assert len(rels) == 1
        assert rels[0].valid_from == pytest.approx(new_fact.valid_from, abs=0.01)
        assert rels[0].valid_until is None


# ---------------------------------------------------------------------------
# Migration test
# ---------------------------------------------------------------------------


class TestRelationshipsMigration:
    def test_migrate_db_without_relationships_table(self, tmp_path):
        """A database created before relationships feature gets the table on open."""
        db_path = str(tmp_path / "old_db.db")
        # Create a database with only the facts table (bi-temporal but no relationships)
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""
            CREATE TABLE facts (
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                agent TEXT NOT NULL,
                confidence REAL NOT NULL DEFAULT 1.0,
                updated_at REAL NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                valid_from REAL NOT NULL,
                valid_until REAL
            );
            CREATE INDEX IF NOT EXISTS idx_facts_key ON facts(key);
            CREATE INDEX IF NOT EXISTS idx_facts_active ON facts(key, valid_until);

            CREATE TABLE episodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent TEXT NOT NULL,
                event TEXT NOT NULL,
                data TEXT NOT NULL,
                timestamp REAL NOT NULL
            );

            CREATE TABLE knowledge_content (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                source TEXT NOT NULL,
                agent TEXT NOT NULL,
                created_at REAL NOT NULL
            );
        """)
        conn.execute(
            "INSERT INTO facts VALUES (?, ?, ?, ?, ?, ?, ?, NULL)",
            ("city", '"NYC"', "a", 1.0, 1700000000.0, 1, 1700000000.0),
        )
        conn.commit()
        conn.close()

        # Open with LTMStore — should create relationships table via migration
        with LTMStore(db_path) as store:
            # Existing fact still works
            assert store.recall("city") == "NYC"
            # Can now use relationships
            rel = store.relate("city", "RELATED_TO", "country")
            assert rel.id is not None
            rels = store.relationships_of("city")
            assert len(rels) == 1

    def test_indices_exist(self, store: LTMStore):
        """Verify that the performance indices on relationships exist."""
        rows = store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='relationships'"
        ).fetchall()
        index_names = {r[0] for r in rows}
        assert "idx_rel_source_edge" in index_names
        assert "idx_rel_target_edge" in index_names
