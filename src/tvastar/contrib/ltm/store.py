"""Long-term memory for AI agents. SQLite-backed, zero external dependencies.

Uses stdlib sqlite3 with FTS5 for full-text search on knowledge entries.
Single-file database at a configurable path. ACID-safe writes.

Bi-temporal fact storage: every remember() creates a new row with valid_from/valid_until
intervals. Superseded facts have their valid_until closed — never deleted.

Usage:
    from tvastar.contrib.ltm import LTMStore

    memory = LTMStore(".tvastar-memory.db")
    memory.remember("user_preference", "prefers Python", agent="assistant")
    value = memory.recall("user_preference")  # "prefers Python"
    value_at = memory.recall("user_preference", at=1700000000.0)  # temporal query
    history = memory.recall_history("user_preference")  # all versions

    memory.store_knowledge("Transformers use self-attention...", source="paper.pdf", agent="researcher")
    results = memory.search_knowledge("attention mechanism")
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class EdgeType(str, Enum):
    """Typed vocabulary of relationship verbs for the LTM graph.

    Case-insensitive matching: EdgeType("supersedes") == EdgeType.SUPERSEDES.
    Extensible via subclassing but the base enum is fixed.
    """

    SUPERSEDES = "SUPERSEDES"
    DEPENDS_ON = "DEPENDS_ON"
    DECIDED_BY = "DECIDED_BY"
    CAUSED = "CAUSED"
    CONTRADICTS = "CONTRADICTS"
    RELATED_TO = "RELATED_TO"
    PART_OF = "PART_OF"
    DERIVED_FROM = "DERIVED_FROM"
    INVALIDATES = "INVALIDATES"
    SUPPORTS = "SUPPORTS"

    @classmethod
    def _missing_(cls, value: object) -> "EdgeType | None":
        """Allow case-insensitive construction: EdgeType('supersedes') works."""
        if isinstance(value, str):
            upper = value.upper()
            for member in cls:
                if member.value == upper:
                    return member
        return None


@dataclass
class Fact:
    key: str
    value: Any
    agent: str
    confidence: float
    updated_at: float
    version: int
    valid_from: float = 0.0
    valid_until: float | None = None


@dataclass
class Relationship:
    id: int
    source_key: str
    edge_type: str
    target_key: str
    valid_from: float
    valid_until: float | None = None
    confidence: float = 1.0


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
        Auto-migrates pre-temporal schemas to bi-temporal.
        """
        self._path = path
        self._conn = sqlite3.connect(path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._maybe_migrate_temporal()
        self._create_tables()
        self._maybe_migrate_relationships()
        self._has_relationships_table = True

    def _create_tables(self) -> None:
        """Create all tables and FTS virtual table if they don't exist."""
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS facts (
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

            CREATE TABLE IF NOT EXISTS relationships (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_key TEXT NOT NULL,
                edge_type TEXT NOT NULL,
                target_key TEXT NOT NULL,
                valid_from REAL NOT NULL,
                valid_until REAL,
                confidence REAL NOT NULL DEFAULT 1.0
            );

            CREATE INDEX IF NOT EXISTS idx_rel_source_edge ON relationships(source_key, edge_type);
            CREATE INDEX IF NOT EXISTS idx_rel_target_edge ON relationships(target_key, edge_type);
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

    def _maybe_migrate_temporal(self) -> None:
        """Auto-migrate pre-temporal facts schema (single-row PK) to bi-temporal (multi-row).

        Detects old schema by checking if facts has a PRIMARY KEY on 'key'.
        If so, recreates with new schema, defaulting valid_from=updated_at, valid_until=NULL.
        """
        # Check if facts table already has valid_from column
        cursor = self._conn.execute("PRAGMA table_info(facts)")
        columns = {row[1] for row in cursor.fetchall()}
        if "valid_from" in columns:
            # ponytail: already migrated or fresh schema — check if old PK-based schema
            # Old schema had PRIMARY KEY on key; new one doesn't.
            # If we have valid_from already, we're fine.
            return
        if "key" not in columns:
            # Table doesn't exist yet or is empty — _create_tables handled it
            return

        # Old schema detected: key TEXT PRIMARY KEY, no valid_from/valid_until
        logger.info("Migrating LTMStore facts to bi-temporal schema")
        self._conn.executescript("""
            ALTER TABLE facts RENAME TO _facts_old;

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

            INSERT INTO facts (key, value, agent, confidence, updated_at, version, valid_from, valid_until)
            SELECT key, value, agent, confidence, updated_at, version, updated_at, NULL
            FROM _facts_old;

            DROP TABLE _facts_old;

            CREATE INDEX IF NOT EXISTS idx_facts_key ON facts(key);
            CREATE INDEX IF NOT EXISTS idx_facts_active ON facts(key, valid_until);
        """)
        self._conn.commit()

    def _maybe_migrate_relationships(self) -> None:
        """Ensure relationships table exists for databases created before this feature.

        ponytail: _create_tables uses IF NOT EXISTS so this is a no-op for fresh DBs.
        Kept as explicit migration hook for clarity and future schema evolution.
        """
        # _create_tables already handles this via CREATE TABLE IF NOT EXISTS.
        # This method exists as a named migration step for traceability.
        pass

    # --- Facts API ---

    def remember(self, key: str, value: Any, *, agent: str, confidence: float = 1.0) -> Fact:
        """Store a new fact version. Closes old active row's valid_until before inserting.

        Never deletes rows — superseded facts retain their history.
        """
        now = time.time()
        serialized = json.dumps(value)

        # Find active row for this key (valid_until IS NULL)
        existing = self._conn.execute(
            "SELECT version FROM facts WHERE key = ? AND valid_until IS NULL",
            (key,),
        ).fetchone()

        if existing:
            new_version = existing[0] + 1
            # Close old row's valid_until — never delete
            self._conn.execute(
                "UPDATE facts SET valid_until = ? WHERE key = ? AND valid_until IS NULL",
                (now, key),
            )
            # Auto-create SUPERSEDES relationship (new version supersedes old)
            self.relate(key, "SUPERSEDES", key)
        else:
            new_version = 1

        # Insert new active row
        self._conn.execute(
            "INSERT INTO facts (key, value, agent, confidence, updated_at, version, valid_from, valid_until) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, NULL)",
            (key, serialized, agent, confidence, now, new_version, now),
        )
        self._conn.commit()
        return Fact(
            key=key,
            value=value,
            agent=agent,
            confidence=confidence,
            updated_at=now,
            version=new_version,
            valid_from=now,
            valid_until=None,
        )

    def recall(self, key: str, *, at: float | None = None) -> Any | None:
        """Retrieve a fact's value by key.

        Without `at`: returns the active fact (valid_until IS NULL).
        With `at=T`: returns the fact valid at time T (valid_from <= T < valid_until,
        or valid_until IS NULL and valid_from <= T).
        """
        if at is None:
            row = self._conn.execute(
                "SELECT value FROM facts WHERE key = ? AND valid_until IS NULL",
                (key,),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT value FROM facts WHERE key = ? AND valid_from <= ? "
                "AND (valid_until IS NULL OR valid_until > ?) "
                "ORDER BY valid_from DESC LIMIT 1",
                (key, at, at),
            ).fetchone()
        if row is None:
            return None
        return json.loads(row[0])

    def recall_fact(self, key: str, *, at: float | None = None) -> Fact | None:
        """Retrieve the full Fact record by key, optionally at a point in time."""
        if at is None:
            row = self._conn.execute(
                "SELECT key, value, agent, confidence, updated_at, version, valid_from, valid_until "
                "FROM facts WHERE key = ? AND valid_until IS NULL",
                (key,),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT key, value, agent, confidence, updated_at, version, valid_from, valid_until "
                "FROM facts WHERE key = ? AND valid_from <= ? "
                "AND (valid_until IS NULL OR valid_until > ?) "
                "ORDER BY valid_from DESC LIMIT 1",
                (key, at, at),
            ).fetchone()
        if row is None:
            return None
        return Fact(
            key=row[0],
            value=json.loads(row[1]),
            agent=row[2],
            confidence=row[3],
            updated_at=row[4],
            version=row[5],
            valid_from=row[6],
            valid_until=row[7],
        )

    def recall_history(self, key: str) -> list[Fact]:
        """Return all versions of a fact ordered by valid_from DESC (newest first)."""
        rows = self._conn.execute(
            "SELECT key, value, agent, confidence, updated_at, version, valid_from, valid_until "
            "FROM facts WHERE key = ? ORDER BY valid_from DESC",
            (key,),
        ).fetchall()
        return [
            Fact(
                key=r[0],
                value=json.loads(r[1]),
                agent=r[2],
                confidence=r[3],
                updated_at=r[4],
                version=r[5],
                valid_from=r[6],
                valid_until=r[7],
            )
            for r in rows
        ]

    def decay(self, key: str, *, factor: float = 0.9) -> Fact | None:
        """Multiply the active fact's confidence by factor. Never sets below 0.

        Returns the updated Fact, or None if no active fact exists for that key.
        """
        row = self._conn.execute(
            "SELECT confidence FROM facts WHERE key = ? AND valid_until IS NULL",
            (key,),
        ).fetchone()
        if row is None:
            return None
        new_confidence = max(row[0] * factor, 0.0)
        self._conn.execute(
            "UPDATE facts SET confidence = ? WHERE key = ? AND valid_until IS NULL",
            (new_confidence, key),
        )
        self._conn.commit()
        return self.recall_fact(key)

    def maintain(self, *, max_age_days: float = 90, decay_threshold: float = 0.3) -> None:
        """Expire low-confidence facts and clean up old relationships.

        1. Close valid_until on active facts where confidence < decay_threshold.
        2. Delete relationships where both endpoints are expired beyond max_age_days.
        """
        now = time.time()

        # Step 1: expire active facts with confidence below threshold
        self._conn.execute(
            "UPDATE facts SET valid_until = ? WHERE valid_until IS NULL AND confidence < ?",
            (now, decay_threshold),
        )

        # Step 2: clean up relationships where both endpoints expired > max_age_days
        cutoff = now - (max_age_days * 86400)
        if self._has_relationships_table:
            # A relationship is deletable when BOTH its source and target facts
            # have valid_until set (expired) AND that valid_until < cutoff.
            self._conn.execute(
                """
                DELETE FROM relationships WHERE id IN (
                    SELECT r.id FROM relationships r
                    JOIN facts fs ON fs.key = r.source_key
                    JOIN facts ft ON ft.key = r.target_key
                    WHERE NOT EXISTS (
                        SELECT 1 FROM facts WHERE key = r.source_key
                        AND (valid_until IS NULL OR valid_until > ?)
                    )
                    AND NOT EXISTS (
                        SELECT 1 FROM facts WHERE key = r.target_key
                        AND (valid_until IS NULL OR valid_until > ?)
                    )
                )
                """,
                (cutoff, cutoff),
            )

        self._conn.commit()

    def forget(self, key: str) -> bool:
        """Delete a fact. Returns True if it existed."""
        cursor = self._conn.execute("DELETE FROM facts WHERE key = ?", (key,))
        self._conn.commit()
        return cursor.rowcount > 0

    def all_facts(self, *, agent: str | None = None) -> list[Fact]:
        """List all active facts (valid_until IS NULL), optionally filtered by agent."""
        if agent:
            rows = self._conn.execute(
                "SELECT key, value, agent, confidence, updated_at, version, valid_from, valid_until "
                "FROM facts WHERE agent = ? AND valid_until IS NULL ORDER BY updated_at DESC",
                (agent,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT key, value, agent, confidence, updated_at, version, valid_from, valid_until "
                "FROM facts WHERE valid_until IS NULL ORDER BY updated_at DESC"
            ).fetchall()
        return [
            Fact(
                key=r[0],
                value=json.loads(r[1]),
                agent=r[2],
                confidence=r[3],
                updated_at=r[4],
                version=r[5],
                valid_from=r[6],
                valid_until=r[7],
            )
            for r in rows
        ]

    # --- Relationships API ---

    def relate(
        self,
        source: str,
        edge_type: str,
        target: str,
        *,
        confidence: float = 1.0,
    ) -> Relationship:
        """Insert a relationship between two keys.

        Creates a row with valid_from=now(), valid_until=NULL.
        Validates edge_type case-insensitively against EdgeType enum.

        Raises:
            ValueError: If edge_type is not a valid EdgeType value.
        """
        # Case-insensitive validation via EdgeType construction (OCP-compliant)
        try:
            resolved = EdgeType(edge_type)
        except ValueError:
            valid = sorted(e.value for e in EdgeType)
            raise ValueError(f"Unknown edge_type {edge_type!r}. Valid types: {valid}")
        # Normalize to uppercase for storage consistency
        normalized = resolved.value
        now = time.time()
        cursor = self._conn.execute(
            "INSERT INTO relationships (source_key, edge_type, target_key, valid_from, valid_until, confidence) "
            "VALUES (?, ?, ?, ?, NULL, ?)",
            (source, normalized, target, now, confidence),
        )
        self._conn.commit()
        assert cursor.lastrowid is not None
        return Relationship(
            id=cursor.lastrowid,
            source_key=source,
            edge_type=normalized,
            target_key=target,
            valid_from=now,
            valid_until=None,
            confidence=confidence,
        )

    def relationships_of(
        self,
        key: str,
        *,
        direction: str = "outgoing",
        edge_type: str | None = None,
    ) -> list[Relationship]:
        """Return relationships from/to a key.

        direction="outgoing": relationships where source_key=key.
        direction="incoming": relationships where target_key=key.
        """
        if direction == "outgoing":
            query = "SELECT id, source_key, edge_type, target_key, valid_from, valid_until, confidence FROM relationships WHERE source_key = ?"
        elif direction == "incoming":
            query = "SELECT id, source_key, edge_type, target_key, valid_from, valid_until, confidence FROM relationships WHERE target_key = ?"
        else:
            raise ValueError(f"direction must be 'outgoing' or 'incoming', got {direction!r}")

        params: list[Any] = [key]
        if edge_type is not None:
            query += " AND edge_type = ?"
            params.append(edge_type)

        rows = self._conn.execute(query, params).fetchall()
        return [
            Relationship(
                id=r[0],
                source_key=r[1],
                edge_type=r[2],
                target_key=r[3],
                valid_from=r[4],
                valid_until=r[5],
                confidence=r[6],
            )
            for r in rows
        ]

    def traverse(
        self,
        start_key: str,
        edge_type: str | None = None,
        max_hops: int = 3,
        include_expired: bool = False,
    ) -> list[tuple[str, str, str, int]]:
        """BFS traversal from start_key following outgoing edges.

        Returns list of (source, edge_type, target, depth) tuples.
        Never visits the same node twice (visited set prevents infinite loops).
        Skips expired edges (valid_until IS NOT NULL) unless include_expired=True.
        """
        from collections import deque

        visited: set[str] = set()
        frontier: deque[tuple[str, int]] = deque([(start_key, 0)])
        results: list[tuple[str, str, str, int]] = []

        while frontier:
            node, depth = frontier.popleft()
            if node in visited:
                continue
            visited.add(node)
            if depth >= max_hops:
                continue
            rels = self.relationships_of(node, direction="outgoing", edge_type=edge_type)
            for rel in rels:
                if not include_expired and rel.valid_until is not None:
                    continue
                results.append((rel.source_key, rel.edge_type, rel.target_key, depth + 1))
                frontier.append((rel.target_key, depth + 1))
        return results

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

    def recent_episodes(
        self, agent: str | None = None, *, limit: int = 20, event: str | None = None
    ) -> list[Episode]:
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
        return [
            Episode(id=r[0], agent=r[1], event=r[2], data=json.loads(r[3]), timestamp=r[4])
            for r in rows
        ]

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

    def search_knowledge(
        self, query: str, *, limit: int = 5, agent: str | None = None
    ) -> list[Knowledge]:
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
        return [
            Knowledge(id=r[0], text=r[1], source=r[2], agent=r[3], created_at=r[4], rank=abs(r[5]))
            for r in rows
        ]

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
