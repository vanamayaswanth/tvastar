"""Tests for bi-temporal fact storage in tvastar.contrib.ltm.store.

Unit tests + Hypothesis property test verifying the core invariant:
for any sequence of remember() calls on the same key, at most one row
has valid_until=NULL at any point in time.
"""

from __future__ import annotations

import time

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tvastar.contrib.ltm.store import LTMStore


@pytest.fixture
def store(tmp_path):
    db_path = str(tmp_path / "test_bitemporal.db")
    with LTMStore(db_path) as s:
        yield s


# ---------------------------------------------------------------------------
# Unit tests — bi-temporal remember/recall
# ---------------------------------------------------------------------------


class TestBitemporalRemember:
    def test_remember_new_key_creates_active_row(self, store: LTMStore):
        fact = store.remember("city", "NYC", agent="a")
        assert fact.valid_from > 0
        assert fact.valid_until is None
        assert fact.version == 1

    def test_remember_existing_key_closes_old_inserts_new(self, store: LTMStore):
        store.remember("city", "NYC", agent="a")
        fact2 = store.remember("city", "SF", agent="b")
        assert fact2.version == 2
        assert fact2.valid_until is None
        # Old row should now have valid_until set
        history = store.recall_history("city")
        assert len(history) == 2
        old = history[1]  # oldest by valid_from DESC
        assert old.value == "NYC"
        assert old.valid_until is not None
        assert old.valid_from < old.valid_until

    def test_remember_never_deletes(self, store: LTMStore):
        """No row is ever deleted by remember() — only valid_until is closed."""
        store.remember("k", "v1", agent="a")
        store.remember("k", "v2", agent="a")
        store.remember("k", "v3", agent="a")
        history = store.recall_history("k")
        assert len(history) == 3


class TestBitemporalRecall:
    def test_recall_returns_active_fact(self, store: LTMStore):
        store.remember("lang", "Python", agent="a")
        assert store.recall("lang") == "Python"
        store.remember("lang", "Rust", agent="a")
        assert store.recall("lang") == "Rust"

    def test_recall_missing_key(self, store: LTMStore):
        assert store.recall("nope") is None

    def test_recall_at_timestamp(self, store: LTMStore):
        """recall(key, at=T) returns the fact valid at that time."""
        f1 = store.remember("city", "NYC", agent="a")
        t1 = f1.valid_from
        # Small sleep to separate timestamps
        time.sleep(0.01)
        f2 = store.remember("city", "SF", agent="b")
        t2 = f2.valid_from

        # At t1, NYC was active
        assert store.recall("city", at=t1) == "NYC"
        # At t2, SF is active
        assert store.recall("city", at=t2) == "SF"

    def test_recall_at_before_any_fact(self, store: LTMStore):
        store.remember("k", "v", agent="a")
        assert store.recall("k", at=0.0) is None

    def test_recall_fact_at_timestamp(self, store: LTMStore):
        f1 = store.remember("x", "old", agent="a")
        time.sleep(0.01)
        store.remember("x", "new", agent="a")
        fact = store.recall_fact("x", at=f1.valid_from)
        assert fact is not None
        assert fact.value == "old"


class TestRecallHistory:
    def test_recall_history_ordered_by_valid_from_desc(self, store: LTMStore):
        store.remember("k", "v1", agent="a")
        time.sleep(0.01)
        store.remember("k", "v2", agent="a")
        time.sleep(0.01)
        store.remember("k", "v3", agent="a")
        history = store.recall_history("k")
        assert len(history) == 3
        assert history[0].value == "v3"  # newest
        assert history[2].value == "v1"  # oldest
        # valid_from should be in descending order
        for i in range(len(history) - 1):
            assert history[i].valid_from >= history[i + 1].valid_from

    def test_recall_history_empty_key(self, store: LTMStore):
        assert store.recall_history("nonexistent") == []


class TestAutoMigration:
    def test_migrate_old_schema(self, tmp_path):
        """Pre-temporal schema gets auto-migrated with valid_from=updated_at, valid_until=NULL."""
        import sqlite3

        db_path = str(tmp_path / "old_schema.db")
        # Create old-style schema manually
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""
            CREATE TABLE facts (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                agent TEXT NOT NULL,
                confidence REAL NOT NULL DEFAULT 1.0,
                updated_at REAL NOT NULL,
                version INTEGER NOT NULL DEFAULT 1
            );
        """)
        conn.execute(
            "INSERT INTO facts VALUES (?, ?, ?, ?, ?, ?)",
            ("city", '"NYC"', "a", 1.0, 1700000000.0, 1),
        )
        conn.commit()
        conn.close()

        # Open with new LTMStore — should auto-migrate
        with LTMStore(db_path) as store:
            fact = store.recall_fact("city")
            assert fact is not None
            assert fact.value == "NYC"
            assert fact.valid_from == 1700000000.0
            assert fact.valid_until is None


class TestAllFacts:
    def test_all_facts_returns_only_active(self, store: LTMStore):
        store.remember("k", "v1", agent="a")
        store.remember("k", "v2", agent="a")
        store.remember("other", "x", agent="a")
        facts = store.all_facts()
        # Should have 2 active: latest "k" and "other"
        assert len(facts) == 2
        keys = {f.key for f in facts}
        assert keys == {"k", "other"}


class TestContradictionDetectorTemporal:
    def test_temporal_mode_logs_without_overwriting(self):
        """ContradictionDetector with temporal=True logs but doesn't call store.set()."""
        from tvastar.memory.store import InMemoryStore
        from tvastar.memory.contradiction import ContradictionDetector

        store = InMemoryStore()
        store.set("k", "old_value")
        cd = ContradictionDetector(store, temporal=True)
        result = cd.write("k", "new_value")
        assert result is True
        # Store still has old value — temporal mode doesn't overwrite
        assert store.get("k") == "old_value"
        # But contradiction is logged
        log = cd.contradiction_log()
        assert len(log) == 1
        assert log[0]["old_value"] == "old_value"
        assert log[0]["new_value"] == "new_value"


# ---------------------------------------------------------------------------
# Property-based test — core invariant
# ---------------------------------------------------------------------------


@settings(max_examples=200, deadline=None)
@given(
    values=st.lists(
        st.tuples(
            st.sampled_from(["key_a", "key_b", "key_c"]),
            st.text(min_size=1, max_size=20),
            st.sampled_from(["agent_1", "agent_2"]),
        ),
        min_size=1,
        max_size=30,
    )
)
def test_at_most_one_active_row_per_key(values):
    """**Validates: Requirements 1.2**

    Property: For any sequence of remember() calls on the same key,
    at most one row has valid_until=NULL at any point in time.

    Also validates: FOR ALL superseded facts, valid_from < valid_until.
    """
    import tempfile
    import os

    db_path = tempfile.mktemp(suffix=".db")
    try:
        with LTMStore(db_path) as store:
            for key, value, agent in values:
                store.remember(key, value, agent=agent)

            # Invariant 1: at most one active row per key
            all_keys = {key for key, _, _ in values}
            for key in all_keys:
                cursor = store._conn.execute(
                    "SELECT COUNT(*) FROM facts WHERE key = ? AND valid_until IS NULL",
                    (key,),
                )
                active_count = cursor.fetchone()[0]
                assert active_count <= 1, (
                    f"Key {key!r} has {active_count} active rows (valid_until IS NULL)"
                )

            # Invariant 2: all superseded facts have valid_from < valid_until
            rows = store._conn.execute(
                "SELECT key, valid_from, valid_until FROM facts WHERE valid_until IS NOT NULL"
            ).fetchall()
            for key, vf, vu in rows:
                assert vf < vu, f"Key {key!r}: valid_from={vf} >= valid_until={vu}"
    finally:
        for suffix in ("", "-wal", "-shm"):
            p = db_path + suffix
            if os.path.exists(p):
                os.unlink(p)
