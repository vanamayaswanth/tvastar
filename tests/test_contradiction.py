"""Tests for tvastar.memory.contradiction — contradiction detection and resolution."""

from __future__ import annotations

from tvastar.memory.store import InMemoryStore
from tvastar.memory.contradiction import (
    ContradictionDetector,
    MAX_CONTRADICTION_LOG,
)


class _MetadataStore(InMemoryStore):
    """InMemoryStore extended with metadata protocol for testing."""

    def __init__(self):
        super().__init__()
        self._metadata: dict[str, dict] = {}

    def set_metadata(self, key: str, meta: dict) -> None:
        self._metadata[key] = meta

    def get_metadata(self, key: str) -> dict | None:
        return self._metadata.get(key)


def test_first_write_no_contradiction():
    store = InMemoryStore()
    cd = ContradictionDetector(store)
    assert cd.write("k", {"a": 1}) is False
    assert store.get("k") == {"a": 1}


def test_same_value_no_contradiction():
    store = InMemoryStore()
    cd = ContradictionDetector(store)
    cd.write("k", {"a": 1})
    assert cd.write("k", {"a": 1}) is False
    assert cd.contradiction_log() == []


def test_different_value_triggers_contradiction():
    store = InMemoryStore()
    cd = ContradictionDetector(store)
    cd.write("k", {"a": 1})
    assert cd.write("k", {"a": 2}) is True
    # last-writer-wins
    assert store.get("k") == {"a": 2}


def test_contradiction_log_contains_required_fields():
    store = InMemoryStore()
    cd = ContradictionDetector(store)
    cd.write("k", "old")
    cd.write("k", "new", source_ref="turn-5")
    log = cd.contradiction_log()
    assert len(log) == 1
    entry = log[0]
    assert entry["key"] == "k"
    assert entry["old_value"] == "old"
    assert entry["new_value"] == "new"
    assert entry["source_ref"] == "turn-5"
    assert "timestamp" in entry


def test_source_ref_defaults_to_unknown():
    store = InMemoryStore()
    cd = ContradictionDetector(store)
    cd.write("k", 1)
    cd.write("k", 2)
    log = cd.contradiction_log()
    assert log[0]["source_ref"] == "unknown"


def test_contradiction_log_capped_at_1000():
    store = InMemoryStore()
    cd = ContradictionDetector(store)
    cd.write("k", 0)
    for i in range(1, 1100):
        cd.write("k", i)
    log = cd.contradiction_log()
    assert len(log) == MAX_CONTRADICTION_LOG


def test_namespace_scoped_log():
    """Each namespace has its own contradiction log."""
    store = InMemoryStore()
    cd_a = ContradictionDetector(store, namespace="a")
    cd_b = ContradictionDetector(store, namespace="b")
    # Use different keys so they don't see each other's values
    cd_a.write("ka", 1)
    cd_a.write("ka", 2)
    cd_b.write("kb", 10)
    cd_b.write("kb", 20)
    assert len(cd_a.contradiction_log()) == 1
    assert len(cd_b.contradiction_log()) == 1
    # Logs are stored under different keys
    assert cd_a.contradiction_log()[0]["key"] == "ka"
    assert cd_b.contradiction_log()[0]["key"] == "kb"


def test_metadata_updated_when_store_supports_protocol():
    store = _MetadataStore()
    cd = ContradictionDetector(store)
    cd.write("k", 1)
    cd.write("k", 2)
    cd.write("k", 3)
    meta = store.get_metadata("k")
    assert meta["contradiction_count"] == 2
    assert "last_contradiction_at" in meta


def test_metadata_not_touched_without_protocol():
    store = InMemoryStore()
    cd = ContradictionDetector(store)
    cd.write("k", 1)
    cd.write("k", 2)
    # No error — just silently skips metadata
    assert not hasattr(store, "set_metadata")


def test_json_comparison_sort_keys():
    """Order of dict keys doesn't matter — JSON comparison uses sort_keys."""
    store = InMemoryStore()
    cd = ContradictionDetector(store)
    cd.write("k", {"b": 2, "a": 1})
    # Same content, different key order — should NOT be a contradiction
    assert cd.write("k", {"a": 1, "b": 2}) is False
    assert cd.contradiction_log() == []


def test_exact_key_matching():
    """Different keys are independent — no fuzzy matching."""
    store = InMemoryStore()
    cd = ContradictionDetector(store)
    cd.write("city", "Paris")
    cd.write("city_name", "London")  # different key, no contradiction
    assert cd.contradiction_log() == []
