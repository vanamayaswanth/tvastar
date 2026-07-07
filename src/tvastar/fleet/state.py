"""Fleet-scoped shared state store with conflict resolution.

Provides a key-value store scoped by fleet name, supporting two conflict
resolution strategies:

- **Last-writer-wins (LWW)**: concurrent writes are serialized by timestamp;
  the latest write always wins.
- **Optimistic locking**: writes must supply the expected version; if the key
  has been updated since the caller's last read, a ConflictError is raised and
  the conflict is recorded in an internal audit trail.

Fleet isolation is guaranteed: two SharedStateStore instances with different
fleet_name values cannot observe each other's keys, even when sharing the same
backend.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

from tvastar.fleet import ConflictError, ConflictStrategy, StateEntry
from tvastar.fleet._backends import StateBackend


# ---------------------------------------------------------------------------
# Audit record for conflicts (optimistic locking rejections)
# ---------------------------------------------------------------------------


@dataclass
class ConflictRecord:
    """Records a conflict event when optimistic locking rejects a write."""

    key: str
    agent: str
    expected_version: int
    actual_version: int
    timestamp: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# SharedStateStore
# ---------------------------------------------------------------------------


class SharedStateStore:
    """Fleet-scoped key-value store with conflict resolution.

    Each instance is bound to a single fleet_name, ensuring full isolation
    between fleets. Keys are strings; values can be any serializable object.

    Parameters
    ----------
    fleet_name:
        The fleet this store belongs to. Used as the namespace for all keys.
    strategy:
        Conflict resolution strategy. Defaults to last-writer-wins.
    backend:
        Optional persistent backend implementing the StateBackend protocol.
        When None, an in-memory dict is used.
    """

    def __init__(
        self,
        fleet_name: str,
        *,
        strategy: ConflictStrategy = ConflictStrategy.LAST_WRITER_WINS,
        backend: StateBackend | None = None,
    ) -> None:
        self._fleet_name = fleet_name
        self._strategy = strategy
        self._backend = backend

        # In-memory storage: key -> StateEntry
        self._store: dict[str, StateEntry] = {}

        # RLock for thread-safe concurrent access without blocking async event loop
        # (dict operations are O(1) and sub-microsecond, so lock hold time is negligible)
        self._lock = threading.RLock()

        # Audit trail for conflict events (optimistic locking rejections) — capped
        from collections import deque

        self._conflicts: deque[ConflictRecord] = deque(maxlen=1_000)

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def fleet_name(self) -> str:
        """The fleet this store is scoped to."""
        return self._fleet_name

    @property
    def strategy(self) -> ConflictStrategy:
        """The active conflict resolution strategy."""
        return self._strategy

    @property
    def conflicts(self) -> list[ConflictRecord]:
        """Read-only view of recorded conflict events."""
        return list(self._conflicts)

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def get(self, key: str) -> Any | None:
        """Retrieve the value for a key, or None if the key does not exist.

        Never raises for missing keys.
        """
        if self._backend is not None:
            return self._backend.get(self._fleet_name, key)

        with self._lock:
            entry = self._store.get(key)
            return entry.value if entry is not None else None

    def get_versioned(self, key: str) -> StateEntry | None:
        """Retrieve the full StateEntry (value + version metadata) for a key.

        Returns None if the key does not exist.
        """
        if self._backend is not None:
            # Backend only stores raw values; we keep version metadata locally
            # even when a backend is present.
            pass

        with self._lock:
            entry = self._store.get(key)
            return entry if entry is not None else None

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def set(
        self,
        key: str,
        value: Any,
        *,
        agent: str,
        expected_version: int | None = None,
    ) -> StateEntry:
        """Write a value, returning the new StateEntry.

        Parameters
        ----------
        key:
            The key to write.
        value:
            The value to store.
        agent:
            Name of the agent performing the write (attribution).
        expected_version:
            For optimistic locking: the version the caller expects the key to
            be at. If the actual version differs, ConflictError is raised.
            Ignored under last-writer-wins strategy.

        Returns
        -------
        The newly created StateEntry with incremented version.

        Raises
        ------
        ConflictError
            Under OPTIMISTIC_LOCKING strategy, if expected_version does not
            match the current version of the key.
        """
        now = time.time()

        with self._lock:
            existing = self._store.get(key)
            current_version = existing.version if existing is not None else 0

            # --- Conflict resolution ---
            if self._strategy == ConflictStrategy.OPTIMISTIC_LOCKING:
                if expected_version is not None and expected_version != current_version:
                    # Record conflict in audit trail
                    self._conflicts.append(
                        ConflictRecord(
                            key=key,
                            agent=agent,
                            expected_version=expected_version,
                            actual_version=current_version,
                            timestamp=now,
                        )
                    )
                    raise ConflictError(
                        key=key,
                        expected_version=expected_version,
                        actual_version=current_version,
                    )

            # Under LWW: if there's an existing entry with a later timestamp,
            # we still allow the write since this is the "latest" call in wall
            # clock time. The serialization is inherent: the lock ensures only
            # one writer proceeds at a time, and we use the current timestamp.

            new_version = current_version + 1
            entry = StateEntry(
                key=key,
                value=value,
                version=new_version,
                written_by=agent,
                written_at=now,
            )
            self._store[key] = entry

        # Persist to backend if available
        if self._backend is not None:
            self._backend.set(self._fleet_name, key, value, new_version)

        return entry

    # ------------------------------------------------------------------
    # Delete operation
    # ------------------------------------------------------------------

    def delete(self, key: str, *, agent: str) -> bool:
        """Delete a key from the store.

        Parameters
        ----------
        key:
            The key to delete.
        agent:
            Name of the agent performing the deletion (for audit purposes).

        Returns
        -------
        True if the key existed and was deleted, False if it did not exist.
        """
        with self._lock:
            if key in self._store:
                del self._store[key]
                deleted = True
            else:
                deleted = False

        # Propagate to backend if available
        if self._backend is not None:
            self._backend.delete(self._fleet_name, key)

        return deleted

    # ------------------------------------------------------------------
    # Key enumeration
    # ------------------------------------------------------------------

    def keys(self) -> list[str]:
        """Return a list of all keys currently in the store."""
        with self._lock:
            return list(self._store.keys())
