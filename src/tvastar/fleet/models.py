"""Shared data models for the Swarm coordination architecture.

All models use stdlib-only types (zero runtime dependencies).
Python 3.10+ union syntax (X | Y).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "CheckpointerConfig",
    "Directive",
    "Entry",
    "Escalation",
    "EscalationRule",
    "Goal",
    "SwarmResult",
]


@dataclass(frozen=True)
class Entry:
    """Immutable entry stored in SignalBus — one (namespace, key, value) with monotonic timestamp."""

    namespace: str
    key: str
    value: Any
    timestamp: float


@dataclass
class Escalation:
    """Structured escalation written by a Worker to SignalBus when retries are exhausted."""

    reason: str
    error_type: str
    attempts: int
    last_error: str | None = None


@dataclass
class Directive:
    """Advisory response written by Coordinator to an escalating Worker's namespace."""

    action: str
    wait_seconds: float | None = None
    fallback: str | None = None


@dataclass
class Goal:
    """Goal entry written by Coordinator to SignalBus for Workers to read."""

    goal: str
    priority: int = 5
    timestamp: float = 0.0


@dataclass
class EscalationRule:
    """Rule matching escalation (reason, error_type) to a directive — same pattern as AgentRouter."""

    match_reason: str | None = None
    match_error_type: str | None = None
    directive: dict = field(default_factory=lambda: {"action": "proceed_autonomously"})


@dataclass
class SwarmResult:
    """Aggregated result returned by Swarm.run() after all Workers complete."""

    goal: str
    worker_results: dict[str, Any]
    worker_states: dict[str, str]
    duration: float


@dataclass
class CheckpointerConfig:
    """Configuration for periodic SignalBus checkpointing to Store."""

    interval: float = 30.0
    checkpoint_key: str = "signal_bus_checkpoint"
