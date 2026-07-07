"""Structured JSON-line logger for loop state transitions.

Zero dependencies beyond stdlib (json, sys, datetime).
Installable via loop.on_event(StructuredLogger()) or registry.on_event(StructuredLogger()).
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import IO

from . import LoopEvent

# Keys in event.data that are internal plumbing, not user metadata
_INTERNAL_KEYS = frozenset({"from_state", "iteration"})


class StructuredLogger:
    """Emit one JSON line per LoopEvent to stderr or a file.

    Args:
        output: None → stderr (default), or a file path (append mode, flush per write).
    """

    def __init__(self, output: str | None = None) -> None:
        self._path = output
        self._file: IO[str] | None = None
        if output is not None:
            self._file = open(output, "a", encoding="utf-8")  # noqa: SIM115

    def __call__(self, event: LoopEvent) -> None:
        metadata = {k: v for k, v in event.data.items() if k not in _INTERNAL_KEYS}
        entry = {
            "timestamp": datetime.fromtimestamp(event.at, tz=timezone.utc).isoformat(),
            "loop_name": event.loop_name,
            "run_id": event.run_id,
            "from_state": event.data.get("from_state", ""),
            "to_state": event.state.value,
            "iteration": event.data.get("iteration", 0),
            "metadata": metadata,
        }
        line = json.dumps(entry) + "\n"
        if self._file is not None:
            self._file.write(line)
            self._file.flush()
        else:
            sys.stderr.write(line)
