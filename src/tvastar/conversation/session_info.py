"""SessionInfo — metadata about a session for listing and discovery."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SessionInfo:
    """Metadata returned by ``Harness.list_sessions()``."""

    id: str
    last_activity: float
