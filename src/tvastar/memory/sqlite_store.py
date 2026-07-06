"""SQLite-backed Store with FTS5 full-text search.

Provides durable, searchable key/value storage using Python's stdlib sqlite3
module. Values are JSON-serialized; the full serialized form is indexed via an
FTS5 virtual table for full-text search.

Example::

    store = SQLiteStore("/tmp/agent-memory.db")
    store.set("user:prefs", {"theme": "dark", "lang": "en"})
    results = store.search("dark")  # [(key, value), ...]
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Optional

from .store import Store


class SQLiteStore(Store):
    """Persistent key/value store backed by SQLite with FTS5 search.

    Thread-safe via a threading.Lock; the underlying connection uses
    ``check_same_thread=False`` so it can be shared across threads.
    """

    def __init__(self, path: str | Path) -> None:
        """Open or create a SQLite DB at *path* with FTS5 table."""
        self._path = str(path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._create_tables()

    def _create_tables(self) -> None:
        with self._lock:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS kv ("
                "    key TEXT PRIMARY KEY,"
                "    value TEXT NOT NULL"
                ")"
            )
            self._conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS kv_fts USING fts5("
                "    key, content"
                ")"
            )
            self._conn.commit()

    def get(self, key: str) -> Optional[Any]:
        """Retrieve a value by key, or None if not found."""
        with self._lock:
            cur = self._conn.execute("SELECT value FROM kv WHERE key = ?", (key,))
            row = cur.fetchone()
        if row is None:
            return None
        return json.loads(row[0])

    def set(self, key: str, value: Any) -> None:
        """Store a value (JSON-serialized) and update the FTS index."""
        serialized = json.dumps(value)
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)",
                (key, serialized),
            )
            # Remove old FTS entry for this key (if any), then insert new one.
            self._conn.execute("DELETE FROM kv_fts WHERE key = ?", (key,))
            self._conn.execute(
                "INSERT INTO kv_fts (key, content) VALUES (?, ?)",
                (key, serialized),
            )
            self._conn.commit()

    def delete(self, key: str) -> None:
        """Remove a key from both the primary table and FTS index.

        No error if the key does not exist.
        """
        with self._lock:
            self._conn.execute("DELETE FROM kv WHERE key = ?", (key,))
            self._conn.execute("DELETE FROM kv_fts WHERE key = ?", (key,))
            self._conn.commit()

    def keys(self, prefix: str = "") -> list[str]:
        """Return all keys matching the given prefix."""
        with self._lock:
            if prefix:
                cur = self._conn.execute(
                    "SELECT key FROM kv WHERE key LIKE ?",
                    (prefix + "%",),
                )
            else:
                cur = self._conn.execute("SELECT key FROM kv")
            return [row[0] for row in cur.fetchall()]

    def search(self, query: str, limit: int = 10) -> list[tuple[str, Any]]:
        """Full-text search over stored values using FTS5 MATCH.

        Returns at most *limit* results ranked by FTS5 relevance, each as
        a ``(key, deserialized_value)`` tuple.
        """
        with self._lock:
            cur = self._conn.execute(
                "SELECT key, content FROM kv_fts WHERE kv_fts MATCH ? "
                "ORDER BY rank LIMIT ?",
                (query, limit),
            )
            rows = cur.fetchall()
        return [(row[0], json.loads(row[1])) for row in rows]
