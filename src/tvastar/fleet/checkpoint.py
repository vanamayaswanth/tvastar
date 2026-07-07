"""Fleet checkpoint manager — snapshots fleet state at compaction time.

Stores checkpoints in the loop's FileStore keyed by
``fleet_checkpoint:{loop_name}:{epoch_seconds}``. Retains only 3 most recent
per loop. Injects fleet context as a system message on resume.

Failure handling: log warning, never raise, allow loop to continue unchanged.
"""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..memory.store import Store
    from .observer import FleetObserver
    from .registry import FleetRegistry

logger = logging.getLogger(__name__)


class FleetCheckpointManager:
    """Snapshots fleet registry state at compaction time.

    Stores checkpoints in the loop's FileStore. Retains only 3 most
    recent per loop. Injects fleet context on resume.
    """

    MAX_CHECKPOINTS = 3
    MAX_INJECT_CHARS = 4096

    def __init__(self, registry: "FleetRegistry", observer: "FleetObserver") -> None:
        self._registry = registry
        self._observer = observer

    def checkpoint(self, loop_name: str, store: "Store") -> bool:
        """Snapshot current fleet state after successful compaction.

        Returns True on success, False on failure (logged, never raises).
        """
        try:
            snapshots = self._observer.health_snapshot()
            # Build a list: agent name, lifecycle state, health info
            agents = [
                {
                    "name": s.name,
                    "state": s.state.value if hasattr(s.state, "value") else str(s.state),
                    "health": {
                        "last_run_status": s.last_run_status,
                        "quality_score": s.quality_score,
                    },
                }
                for s in snapshots
            ]
            epoch = int(time.time())
            key = f"fleet_checkpoint:{loop_name}:{epoch}"
            store.set(key, json.dumps(agents))
            self._prune(loop_name, store)
            return True
        except Exception as exc:
            logger.warning("Fleet checkpoint failed for %s: %s", loop_name, exc)
            return False

    def inject_context(self, loop_name: str, store: "Store", messages: list) -> list:
        """Prepend most recent fleet checkpoint as system-context message.

        Returns messages unchanged if no checkpoint exists or on failure.
        """
        try:
            keys = sorted(
                store.keys(f"fleet_checkpoint:{loop_name}:"), reverse=True
            )
            if not keys:
                return messages
            raw = store.get(keys[0])
            if not raw:
                return messages
            content = f"[Fleet State]\n{raw}"
            if len(content) > self.MAX_INJECT_CHARS:
                content = content[: self.MAX_INJECT_CHARS]
            from ..types import Message

            return [Message(role="system", content=content)] + list(messages)
        except Exception as exc:
            logger.warning("Fleet context injection failed for %s: %s", loop_name, exc)
            return messages

    def _prune(self, loop_name: str, store: "Store") -> None:
        """Keep only the 3 most recent checkpoints, delete older."""
        try:
            keys = sorted(
                store.keys(f"fleet_checkpoint:{loop_name}:"), reverse=True
            )
            for old_key in keys[self.MAX_CHECKPOINTS :]:
                store.delete(old_key)
        except Exception as exc:
            logger.warning("Fleet checkpoint prune failed for %s: %s", loop_name, exc)
