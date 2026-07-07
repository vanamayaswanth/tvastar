"""Tests for Fleet backend implementations."""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from tvastar.fleet.backends.sqlite_state import SQLiteStateBackend


# ---------------------------------------------------------------------------
# SQLiteStateBackend tests
# ---------------------------------------------------------------------------


class TestSQLiteStateBackend:
    """Tests for SQLiteStateBackend get/set/delete operations."""

    def test_get_returns_none_for_missing_key(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        backend = SQLiteStateBackend(path=db_path)
        try:
            assert backend.get("fleet-1", "missing") is None
        finally:
            backend.close()

    def test_set_and_get_roundtrip(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        backend = SQLiteStateBackend(path=db_path)
        try:
            backend.set("fleet-1", "key1", {"hello": "world"}, version=1)
            result = backend.get("fleet-1", "key1")
            assert result == {"hello": "world"}
        finally:
            backend.close()

    def test_set_overwrites_existing_value(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        backend = SQLiteStateBackend(path=db_path)
        try:
            backend.set("fleet-1", "key1", "old", version=1)
            backend.set("fleet-1", "key1", "new", version=2)
            assert backend.get("fleet-1", "key1") == "new"
        finally:
            backend.close()

    def test_delete_existing_key(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        backend = SQLiteStateBackend(path=db_path)
        try:
            backend.set("fleet-1", "key1", "value", version=1)
            assert backend.delete("fleet-1", "key1") is True
            assert backend.get("fleet-1", "key1") is None
        finally:
            backend.close()

    def test_delete_missing_key_returns_false(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        backend = SQLiteStateBackend(path=db_path)
        try:
            assert backend.delete("fleet-1", "nonexistent") is False
        finally:
            backend.close()

    def test_fleet_isolation(self, tmp_path):
        """Different fleet_names cannot see each other's keys."""
        db_path = str(tmp_path / "test.db")
        backend = SQLiteStateBackend(path=db_path)
        try:
            backend.set("fleet-alpha", "shared-key", "alpha-value", version=1)
            backend.set("fleet-beta", "shared-key", "beta-value", version=1)

            assert backend.get("fleet-alpha", "shared-key") == "alpha-value"
            assert backend.get("fleet-beta", "shared-key") == "beta-value"

            # Deleting from one fleet doesn't affect the other
            backend.delete("fleet-alpha", "shared-key")
            assert backend.get("fleet-alpha", "shared-key") is None
            assert backend.get("fleet-beta", "shared-key") == "beta-value"
        finally:
            backend.close()

    def test_stores_various_json_types(self, tmp_path):
        """Verifies serialization of different JSON-compatible types."""
        db_path = str(tmp_path / "test.db")
        backend = SQLiteStateBackend(path=db_path)
        try:
            backend.set("fleet-1", "int_val", 42, version=1)
            backend.set("fleet-1", "list_val", [1, 2, 3], version=1)
            backend.set("fleet-1", "null_val", None, version=1)
            backend.set("fleet-1", "bool_val", True, version=1)

            assert backend.get("fleet-1", "int_val") == 42
            assert backend.get("fleet-1", "list_val") == [1, 2, 3]
            assert backend.get("fleet-1", "null_val") is None  # stored as JSON null
            assert backend.get("fleet-1", "bool_val") is True
        finally:
            backend.close()

    def test_persistence_across_instances(self, tmp_path):
        """Data persists when backend is closed and re-opened."""
        db_path = str(tmp_path / "test.db")
        backend = SQLiteStateBackend(path=db_path)
        backend.set("fleet-1", "persist", "survives", version=1)
        backend.close()

        backend2 = SQLiteStateBackend(path=db_path)
        try:
            assert backend2.get("fleet-1", "persist") == "survives"
        finally:
            backend2.close()


# ---------------------------------------------------------------------------
# RedisStateBackend import validation
# ---------------------------------------------------------------------------


class TestRedisStateBackendImport:
    """Tests that RedisStateBackend raises ImportError when redis is unavailable."""

    def test_raises_import_error_without_redis(self):
        """RedisStateBackend raises descriptive ImportError when redis not installed."""
        with patch.dict(sys.modules, {"redis": None}):
            # Need to reload the module to pick up the patched import
            from tvastar.fleet.backends import redis_state

            with pytest.raises(ImportError, match="tvastar\\[redis\\]"):
                redis_state.RedisStateBackend()


# ---------------------------------------------------------------------------
# NATSEventBackend import validation
# ---------------------------------------------------------------------------


class TestNATSEventBackendImport:
    """Tests that NATSEventBackend raises ImportError when nats is unavailable."""

    def test_raises_import_error_without_nats(self):
        """NATSEventBackend raises descriptive ImportError when nats not installed."""
        with patch.dict(sys.modules, {"nats": None}):
            from tvastar.fleet.backends import nats_events

            with pytest.raises(ImportError, match="tvastar\\[nats\\]"):
                nats_events.NATSEventBackend()
