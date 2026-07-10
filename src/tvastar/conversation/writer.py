"""ConversationWriter — append-only event log writer with write-through persistence.

Appends typed records to a Store-backed event log. On store failure, degrades
gracefully to in-memory-only operation and exposes the error via ``last_error``.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

from ..errors import DurableError
from ..memory.store import Store
from .records import Record, RecordType, record_to_dict

_EVENT_LOG_PREFIX = "event_log:"


class ConversationWriter:
    """Writes records to an append-only event log backed by a Store."""

    def __init__(
        self,
        store: Store,
        session_id: str,
        *,
        compaction_threshold: int = 500,
        event_bus: Any = None,
    ) -> None:
        self._store = store
        self._session_id = session_id
        self._seq = 0
        self._lock = asyncio.Lock()
        self._compaction_threshold = compaction_threshold
        self.last_error: Optional[DurableError] = None
        self._event_bus = event_bus
        self._degraded_emitted = False

    @property
    def key(self) -> str:
        return f"{_EVENT_LOG_PREFIX}{self._session_id}"

    async def append(self, record_type: RecordType, data: dict[str, Any]) -> Record:
        """Append a record and persist immediately (write-through).

        Acquires an asyncio.Lock to serialize concurrent appends, ensuring
        sequence numbers remain strictly monotonic with no lost updates.

        On store write failure, wraps the exception in a DurableError, stores it
        in ``self.last_error``, and continues in degraded mode (record is still
        returned but may not be persisted).
        """
        async with self._lock:
            record = Record(type=record_type, seq=self._seq, data=data)
            self._seq += 1
            try:
                log = self._store.get(self.key) or []
                log.append(record_to_dict(record))
                self._store.set(self.key, log)
                # Recovery detection
                if self.last_error is not None:
                    self.last_error = None
                    self._degraded_emitted = False
                    self._emit_recovered_event()
                # Compact if threshold exceeded
                if self._compaction_threshold > 0 and len(log) > self._compaction_threshold:
                    self._compact(log)
            except Exception as exc:
                self.last_error = DurableError(
                    str(exc),
                    session_id=self._session_id,
                    operation="append",
                )
                # Emit exactly once per None→Error transition
                if not self._degraded_emitted:
                    self._degraded_emitted = True
                    self._emit_degraded_event()
            return record

    def _compact(self, log: list[dict[str, Any]]) -> None:
        """Reduce log to a single snapshot record. Called inside the lock.

        Defers compaction if the log ends mid-run (between run_start and run_end)
        to preserve run boundary safety (Req 13.1, 13.2).
        """
        # ponytail: early return if mid-run — next append after run_end retriggers compaction
        if self._is_mid_run(log):
            return

        from ..durable import message_to_dict
        from .reducer import reduce

        try:
            messages = reduce(log)
            snapshot = [message_to_dict(m) for m in messages]
            compacted_log = [
                {
                    "type": "session_start",
                    "seq": 0,
                    "timestamp": time.time(),
                    "data": {"snapshot": snapshot},
                }
            ]
            self._store.set(self.key, compacted_log)
            self._seq = 1
        except Exception as exc:
            # ponytail: compaction failure is non-fatal — preserve original log
            self.last_error = DurableError(
                f"compaction failed: {exc}",
                session_id=self._session_id,
                operation="compact",
            )

    def _is_mid_run(self, log: list[dict[str, Any]]) -> bool:
        """Check if the log ends in the middle of a run (run_start without run_end)."""
        run_starts = 0
        run_ends = 0
        for record in log:
            if not isinstance(record, dict):
                continue
            rtype = record.get("type")
            if rtype == "run_start":
                run_starts += 1
            elif rtype == "run_end":
                run_ends += 1
        return run_starts > run_ends

    def _emit_degraded_event(self) -> None:
        """Emit session.degraded event. Falls back to stderr."""
        import json
        import sys
        import time as _time

        payload = {
            "session_id": self._session_id,
            "error_message": str(self.last_error),
            "operation": "append",
            "timestamp": _time.time(),
        }
        if self._event_bus is not None:
            try:
                self._event_bus.publish("session.degraded", payload)
                return
            except Exception:
                pass
        print(json.dumps({"event": "session.degraded", **payload}), file=sys.stderr)

    def _emit_recovered_event(self) -> None:
        """Emit session.recovered event. Falls back to stderr."""
        import json
        import sys
        import time as _time

        payload = {
            "session_id": self._session_id,
            "error_message": None,
            "operation": "append",
            "timestamp": _time.time(),
        }
        if self._event_bus is not None:
            try:
                self._event_bus.publish("session.recovered", payload)
                return
            except Exception:
                pass
        print(json.dumps({"event": "session.recovered", **payload}), file=sys.stderr)

    def load_seq(self) -> int:
        """Recover sequence counter from existing log length."""
        log = self._store.get(self.key) or []
        self._seq = len(log)
        return self._seq
