"""SQLite-backed state backend for Fleet SharedStateStore.

Zero external dependencies — uses stdlib sqlite3. Provides persistent,
ACID-safe fleet state that survives process restarts.
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any


class SQLiteStateBackend:
    """Implements the StateBackend protocol using SQLite.

    Fleet state is stored in a single SQLite file, scoped by fleet_name.
    Uses WAL mode for concurrent read access.
    """

    def __init__(self, path: str = ".tvastar-fleet-state.db") -> None:
        self._path = path
        self._conn = sqlite3.connect(path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS fleet_state (
                fleet_name TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                updated_at REAL NOT NULL,
                PRIMARY KEY (fleet_name, key)
            )
        """)
        self._conn.commit()

    def get(self, fleet_name: str, key: str) -> Any | None:
        row = self._conn.execute(
            "SELECT value FROM fleet_state WHERE fleet_name = ? AND key = ?",
            (fleet_name, key),
        ).fetchone()
        if row is None:
            return None
        return json.loads(row[0])

    def set(self, fleet_name: str, key: str, value: Any, version: int) -> None:
        now = time.time()
        serialized = json.dumps(value)
        self._conn.execute(
            """INSERT INTO fleet_state (fleet_name, key, value, version, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(fleet_name, key)
               DO UPDATE SET value=excluded.value, version=excluded.version,
               updated_at=excluded.updated_at""",
            (fleet_name, key, serialized, version, now),
        )
        self._conn.commit()

    def delete(self, fleet_name: str, key: str) -> bool:
        cursor = self._conn.execute(
            "DELETE FROM fleet_state WHERE fleet_name = ? AND key = ?",
            (fleet_name, key),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def close(self) -> None:
        self._conn.close()
