"""Contradiction detection and resolution for memory writes.

Detects conflicting facts via JSON comparison and resolves with
last-writer-wins semantics. Logs all contradictions to a dedicated
namespace-scoped log (max 1000 entries). Exact key matching only.
"""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .store import Store

logger = logging.getLogger(__name__)

CONTRADICTION_LOG_PREFIX = "_contradictions:"
MAX_CONTRADICTION_LOG = 1000


class ContradictionDetector:
    """Detects and resolves conflicting memory writes.

    Resolution: last-writer-wins (always writes the new value).
    Exact key matching only — no semantic similarity.
    """

    def __init__(self, store: "Store", namespace: str = "default") -> None:
        self._store = store
        self._namespace = namespace
        self._log_key = f"{CONTRADICTION_LOG_PREFIX}{namespace}"

    def write(self, key: str, new_value: Any, *, source_ref: str = "unknown") -> bool:
        """Write with contradiction detection.

        Returns True if a contradiction was detected and resolved.
        Always writes new_value (last-writer-wins).
        """
        old_value = self._store.get(key)

        # No existing value — first write, no contradiction
        if old_value is None:
            self._store.set(key, new_value)
            return False

        # Compare by JSON serialization (sort_keys for determinism)
        old_json = json.dumps(old_value, sort_keys=True)
        new_json = json.dumps(new_value, sort_keys=True)

        if old_json == new_json:
            # Same value — idempotent write, no contradiction
            self._store.set(key, new_value)
            return False

        # Contradiction detected — resolve with last-writer-wins
        self._store.set(key, new_value)
        self._log_contradiction(key, old_value, new_value, source_ref)
        self._update_metadata(key)
        return True

    def _log_contradiction(self, key: str, old_value: Any, new_value: Any, source_ref: str) -> None:
        """Append to dedicated contradiction log, evict oldest at 1000."""
        entry = {
            "key": key,
            "old_value": old_value,
            "new_value": new_value,
            "source_ref": source_ref,
            "timestamp": time.time(),
        }
        log = self._store.get(self._log_key) or []
        log.append(entry)
        if len(log) > MAX_CONTRADICTION_LOG:
            log = log[-MAX_CONTRADICTION_LOG:]
        self._store.set(self._log_key, log)

    def _update_metadata(self, key: str) -> None:
        """If store supports metadata protocol, update contradiction_count and last_contradiction_at."""
        if hasattr(self._store, "set_metadata"):
            meta = getattr(self._store, "get_metadata", lambda k: {})(key) or {}
            meta["contradiction_count"] = meta.get("contradiction_count", 0) + 1
            meta["last_contradiction_at"] = time.time()
            self._store.set_metadata(key, meta)  # type: ignore[attr-defined]

    def contradiction_log(self) -> list[dict]:
        """Return the full contradiction log for this namespace."""
        return self._store.get(self._log_key) or []
