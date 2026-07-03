"""Long-term memory for AI agents. SQLite-backed, zero external dependencies.

Uses stdlib sqlite3 with FTS5 for full-text search on knowledge entries.
Single-file database at a configurable path. ACID-safe writes.

Usage:
    from tvastar.contrib.ltm import LTMStore

    memory = LTMStore(".tvastar-memory.db")
    memory.remember("user_preference", "prefers Python", agent="assistant")
    value = memory.recall("user_preference")  # "prefers Python"

    memory.store_knowledge("Transformers use self-attention...", source="paper.pdf", agent="researcher")
    results = memory.search_knowledge("attention mechanism")
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from typing import Any


@dataclass
class Fact:
    key: str
    value: Any
    agent: str
    confidence: float
    updated_at: float
    version: int


@dataclass
class Episode:
    id: int
    agent: str
    event: str
    data: dict
    timestamp: float


@dataclass
class Knowledge:
    id: int
    text: str
    source: str
    agent: str
    created_at: float
    rank: float = 0.0  # BM25 relevance score from FTS5


class LTMStore:
    """SQLite-backed long-term memory with facts, episodes, and knowledge search."""

    def __init__(self, path: str = ".tvastar-memory.db") -> None:
        """Initialize the LTM store.

        Creates the database file and tables if they don't exist.
        Uses WAL mode for concurrent read access.
        """
        self._path = path
        self._conn = sqlite3.connect(path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._create_tables()

    def _create_tables(self) -> None:
        """Create all tables and FTS virtual table if they don't exist."""
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS facts (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                agent TEXT NOT NULL,
                confidence REAL NOT NULL DEFAULT 1.0,
                updated_at REAL NOT NULL,
                version INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS episodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent TEXT NOT NULL,
                event TEXT NOT NULL,
                data TEXT NOT NULL,
                timestamp REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_episodes_agent ON episodes(agent);
            CREATE INDEX IF NOT EXISTS idx_episodes_timestamp ON episodes(timestamp DESC);

            CREATE TABLE IF NOT EXISTS knowledge_content (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                source TEXT NOT NULL,
                agent TEXT NOT NULL,
                created_at REAL NOT NULL
            );
        """)
        # FTS5 virtual table must be created separately — executescript
        # can't handle IF NOT EXISTS for virtual tables in all SQLite builds.
        try:
            self._conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS knowledge USING fts5(
                    text, source, agent, created_at UNINDEXED,
                    content='knowledge_content',
                    content_rowid='id'
                )
            """)
        except sqlite3.OperationalError:
            pass  # Already exists
        self._conn.commit()

    # --- Facts API ---

    def remember(self, key: str, value: Any, *, agent: str, confidence: float = 1.0) -> Fact:
        """Store or update a fact. Returns the Fact record."""
        now = time.time()
        serialized = json.dumps(value)

        existing = self._conn.execute(
            "SELECT version FROM facts WHERE key = ?", (key,)
        ).fetchone()

        if existing:
            new_version = existing[0] + 1
            self._conn.execute(
                "UPDATE facts SET value=?, agent=?, confidence=?, updated_at=?, version=? WHERE key=?",
                (serialized, agent, confidence, now, new_version, key),
            )
        else:
            new_version = 1
            self._conn.execute(
                "INSERT INTO facts (key, value, agent, confidence, updated_at, version) VALUES (?, ?, ?, ?, ?, ?)",
                (key, serialized, agent, confidence, now, new_version),
            )
        self._conn.commit()
        return Fact(key=key, value=value, agent=agent, confidence=confidence, updated_at=now, version=new_version)

    def recall(self, key: str) -> Any | None:
        """Retrieve a fact's value by key. Returns None if not found."""
        row = self._conn.execute("SELECT value FROM facts WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        return json.loads(row[0])

    def recall_fact(self, key: str) -> Fact | None:
        """Retrieve the full Fact record by key."""
        row = self._conn.execute(
            "SELECT key, value, agent, confidence, updated_at, version FROM facts WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        return Fact(key=row[0], value=json.loads(row[1]), agent=row[2], confidence=row[3], updated_at=row[4], version=row[5])

    def forget(self, key: str) -> bool:
        """Delete a fact. Returns True if it existed."""
        cursor = self._conn.execute("DELETE FROM facts WHERE key = ?", (key,))
        self._conn.commit()
        return cursor.rowcount > 0

    def all_facts(self, *, agent: str | None = None) -> list[Fact]:
        """List all facts, optionally filtered by agent."""
        if agent:
            rows = self._conn.execute(
                "SELECT key, value, agent, confidence, updated_at, version FROM facts WHERE agent = ? ORDER BY updated_at DESC",
                (agent,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT key, value, agent, confidence, updated_at, version FROM facts ORDER BY updated_at DESC"
            ).fetchall()
        return [Fact(key=r[0], value=json.loads(r[1]), agent=r[2], confidence=r[3], updated_at=r[4], version=r[5]) for r in rows]

    # --- Episodes API ---

    def record_episode(self, agent: str, event: str, data: dict) -> Episode:
        """Record an episode (structured event). Returns the Episode record."""
        now = time.time()
        serialized = json.dumps(data)
        cursor = self._conn.execute(
            "INSERT INTO episodes (agent, event, data, timestamp) VALUES (?, ?, ?, ?)",
            (agent, event, serialized, now),
        )
        self._conn.commit()
        return Episode(id=cursor.lastrowid, agent=agent, event=event, data=data, timestamp=now)

    def recent_episodes(self, agent: str | None = None, *, limit: int = 20, event: str | None = None) -> list[Episode]:
        """Get recent episodes, optionally filtered by agent and/or event type."""
        query = "SELECT id, agent, event, data, timestamp FROM episodes"
        params: list[Any] = []
        conditions: list[str] = []

        if agent:
            conditions.append("agent = ?")
            params.append(agent)
        if event:
            conditions.append("event = ?")
            params.append(event)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        rows = self._conn.execute(query, params).fetchall()
        return [Episode(id=r[0], agent=r[1], event=r[2], data=json.loads(r[3]), timestamp=r[4]) for r in rows]

    # --- Knowledge API ---

    @staticmethod
    def _prepare_fts_query(query: str) -> str:
        """Convert a natural language query into an FTS5 OR expression with prefix matching."""
        # Split on whitespace, keep only alphanumeric tokens
        tokens = [t for t in query.split() if t.strip()]
        if not tokens:
            return query
        # Use prefix matching on each token and join with OR for broad recall
        return " OR ".join(f"{t}*" for t in tokens)

    def store_knowledge(self, text: str, *, source: str, agent: str) -> Knowledge:
        """Store a knowledge chunk for full-text search. Returns the Knowledge record."""
        now = time.time()
        cursor = self._conn.execute(
            "INSERT INTO knowledge_content (text, source, agent, created_at) VALUES (?, ?, ?, ?)",
            (text, source, agent, now),
        )
        row_id = cursor.lastrowid
        # Sync to FTS5 index
        self._conn.execute(
            "INSERT INTO knowledge (rowid, text, source, agent, created_at) VALUES (?, ?, ?, ?, ?)",
            (row_id, text, source, agent, str(now)),
        )
        self._conn.commit()
        return Knowledge(id=row_id, text=text, source=source, agent=agent, created_at=now)

    def search_knowledge(self, query: str, *, limit: int = 5, agent: str | None = None) -> list[Knowledge]:
        """Search knowledge using FTS5 BM25 ranking. Returns ranked results."""
        fts_query = self._prepare_fts_query(query)
        if agent:
            rows = self._conn.execute(
                """SELECT kc.id, kc.text, kc.source, kc.agent, kc.created_at, k.rank
                   FROM knowledge k
                   JOIN knowledge_content kc ON k.rowid = kc.id
                   WHERE knowledge MATCH ? AND kc.agent = ?
                   ORDER BY k.rank
                   LIMIT ?""",
                (fts_query, agent, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                """SELECT kc.id, kc.text, kc.source, kc.agent, kc.created_at, k.rank
                   FROM knowledge k
                   JOIN knowledge_content kc ON k.rowid = kc.id
                   WHERE knowledge MATCH ?
                   ORDER BY k.rank
                   LIMIT ?""",
                (fts_query, limit),
            ).fetchall()
        return [Knowledge(id=r[0], text=r[1], source=r[2], agent=r[3], created_at=r[4], rank=abs(r[5])) for r in rows]

    # --- Lifecycle ---

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    def __enter__(self) -> "LTMStore":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    @property
    def path(self) -> str:
        """The database file path."""
        return self._path
