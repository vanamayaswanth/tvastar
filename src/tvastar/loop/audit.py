"""Loop readiness audit — L0→L3 scoring.

Werner principle: a loop you cannot reason about is a loop you cannot trust.
Score before deploying. Never discover failure modes at 2am.

Levels:
  L0  MANUAL      No schedule. Manual trigger only. Fine for experiments.
  L1  OBSERVE     Scheduled + handoff. Auto-fires and escalates failures.
  L2  GATED       L1 + cancel_after timeout. Safe for loops that mutate state.
  L3  AUTONOMOUS  L2 + detectors + circuit breaker. True production autonomy.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from . import Loop

_LEVEL_COLORS: dict[int, str] = {0: "red", 1: "orange", 2: "yellow", 3: "green"}


@dataclass
class ReadinessLevel:
    level: int  # 0–3
    name: str  # MANUAL | OBSERVE | GATED | AUTONOMOUS
    description: str
    passes: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)  # blocking — fix these
    warnings: list[str] = field(default_factory=list)  # advisories

    @property
    def is_production_ready(self) -> bool:
        return self.level >= 3

    def to_badge(self) -> dict:
        """Return badge metadata dict for the readiness level.

        Raises:
            ValueError: if level is outside 0-3.
        """
        if self.level not in _LEVEL_COLORS:
            raise ValueError(f"Level {self.level} outside valid range 0-3")
        return {
            "label": self.name,
            "level": self.level,
            "color": _LEVEL_COLORS[self.level],
            "description": self.description[:128],
            "passes_count": len(self.passes),
            "gaps_count": len(self.gaps),
            "warnings_count": len(self.warnings),
        }

    def to_json(self) -> str:
        """Return valid JSON string with all ReadinessLevel fields."""
        return json.dumps({
            "level": self.level,
            "name": self.name,
            "description": self.description,
            "passes": self.passes,
            "gaps": self.gaps,
            "warnings": self.warnings,
            "is_production_ready": self.is_production_ready,
        })

    def to_shields_endpoint(self) -> dict:
        """Return shields.io endpoint badge schema dict.

        Raises:
            ValueError: if level is outside 0-3.
        """
        if self.level not in _LEVEL_COLORS:
            raise ValueError(f"Level {self.level} outside valid range 0-3")
        return {
            "schemaVersion": 1,
            "label": "loop readiness",
            "message": self.name,
            "color": _LEVEL_COLORS[self.level],
        }


def audit_loop(loop: "Loop") -> ReadinessLevel:  # type: ignore[name-defined]
    """Score a Loop object and return its L0–L3 ReadinessLevel.

    Pure function — does not start or run the loop.

    Args:
        loop: A Loop instance to audit.

    Returns:
        ReadinessLevel with level, name, description, passes, gaps, warnings.

    Raises:
        TypeError: if *loop* is not a Loop instance.
    """
    from . import Loop, LoopConfig

    if not isinstance(loop, Loop):
        raise TypeError(f"Expected Loop instance, got {type(loop).__name__}")

    cfg: LoopConfig = loop.config
    spec = loop._harness.spec

    passes: list[str] = []
    gaps: list[str] = []
    warnings: list[str] = []

    # ── L0 baseline ──────────────────────────────────────────────────────────
    passes.append("Loop object exists")

    # ── L1 gate: schedule ────────────────────────────────────────────────────
    has_schedule = cfg.schedule != "@manual"
    if has_schedule:
        passes.append(f"Schedule configured: {cfg.schedule!r}")
    else:
        gaps.append(
            "No schedule (@manual) — loop only fires when you call trigger() yourself. "
            "Set LoopConfig(schedule='*/15 * * * *') or similar to reach L1."
        )

    # ── L1 gate: handoff ─────────────────────────────────────────────────────
    has_handoff = cfg.handoff is not None
    if has_handoff:
        passes.append(f"Handoff configured: {type(cfg.handoff).__name__}")
    else:
        gaps.append(
            "No handoff policy — failures stop silently with no alert. "
            "Set LoopConfig(handoff=LogHandoff()) or a Slack/PagerDuty handler to reach L1."
        )

    # ── L2 gate: cancel_after timeout ────────────────────────────────────────
    has_timeout = cfg.cancel_after is not None
    if has_timeout:
        passes.append(f"Timeout: {cfg.cancel_after}s per run (cancel_after)")
    else:
        gaps.append(
            "No cancel_after timeout — a runaway agent call blocks indefinitely. "
            "Set LoopConfig(cancel_after=300.0) to reach L2."
        )

    # ── L3 gate: silent-failure detectors ────────────────────────────────────
    has_detectors = bool(getattr(spec, "detectors", None))
    if has_detectors:
        n = len(spec.detectors)
        passes.append(f"Detectors: {n} silent-failure detector{'s' if n != 1 else ''} active")
    else:
        gaps.append(
            "No silent-failure detectors — the agent can claim SUCCESS while failing. "
            "Pass detect=default_detectors() to create_agent() to reach L3."
        )

    # ── L3 gate: circuit breaker ─────────────────────────────────────────────
    has_circuit_breaker = cfg.circuit_breaker_limit > 0
    if has_circuit_breaker:
        passes.append(
            f"Circuit breaker: {cfg.circuit_breaker_limit} consecutive failures → SUSPENDED"
        )
    else:
        gaps.append(
            "Circuit breaker disabled (circuit_breaker_limit=0) — a permanently-broken "
            "loop will escalate forever. Set circuit_breaker_limit >= 3 to reach L3."
        )

    # ── Non-blocking advisories ───────────────────────────────────────────────
    try:
        from ..model.mock import MockModel

        if isinstance(spec.model, MockModel):
            warnings.append("Model is MockModel — replace with a real model for production.")
    except ImportError:
        pass

    if cfg.max_iterations > 5:
        warnings.append(
            f"max_iterations={cfg.max_iterations} is high (>5). "
            "This delays human escalation when the loop is permanently broken."
        )

    if cfg.retry_backoff_base < 10.0:
        warnings.append(
            f"retry_backoff_base={cfg.retry_backoff_base}s is low. "
            "Recommend >= 30s to avoid hammering the model on transient errors."
        )

    # ── Compute level ─────────────────────────────────────────────────────────
    if not has_schedule or not has_handoff:
        level, name = 0, "MANUAL"
        description = (
            "Manual-only — suitable for experiments and one-shot runs, not unattended production."
        )
    elif not has_timeout:
        level, name = 1, "OBSERVE"
        description = (
            "Scheduled with escalation, but no timeout. "
            "Safe for read-only reporting loops; add cancel_after before mutating state."
        )
    elif not has_detectors or not has_circuit_breaker:
        level, name = 2, "GATED"
        description = (
            "Scheduled, timeout-protected, and escalating. "
            "Missing silent-failure detection or circuit breaker. "
            "Add them to reach full autonomous operation."
        )
    else:
        level, name = 3, "AUTONOMOUS"
        description = (
            "Full production config. Runs unattended with timeout, "
            "silent-failure detection, escalating handoff, and automatic circuit-breaker "
            "suspension on persistent failures."
        )

    return ReadinessLevel(
        level=level,
        name=name,
        description=description,
        passes=passes,
        gaps=gaps,
        warnings=warnings,
    )


__all__ = ["ReadinessLevel", "audit_loop"]
