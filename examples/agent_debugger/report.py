"""Report assembly for the Agent Debugger pipeline.

Provides factory functions for building a DebuggingReport from pipeline outputs,
determining status based on quality score thresholds and retry exhaustion, and
constructing execution receipts with timing, cost, and outcome data.

Requirements: 5.1, 5.2, 5.3, 6.4
"""

from __future__ import annotations

import time
from typing import Any

from .schemas import (
    DebuggingReport,
    DiagnosisReport,
    FixProposal,
    VerificationResult,
)


def _determine_status(
    quality_score: float,
    max_retries: int,
    attempts: int,
) -> str:
    """Determine report status from quality score and retry state.

    Status logic:
        - quality_score >= 0.8 → "resolved"
        - 0.5 <= quality_score < 0.8 → "improved"
        - quality_score < 0.5 → "unresolved"

    When all retries are exhausted and score remains below 0.5, status is
    always "unresolved" regardless of other factors.

    Args:
        quality_score: Final verification quality score (0.0–1.0).
        max_retries: Maximum number of fix-verify attempts allowed.
        attempts: Actual number of attempts made.

    Returns:
        One of "resolved", "improved", or "unresolved".
    """
    if quality_score >= 0.8:
        return "resolved"
    elif quality_score >= 0.5:
        return "improved"
    else:
        return "unresolved"


def build_execution_receipt(
    cost_breakdown: dict[str, float],
    timing: dict[str, Any],
    status: str,
) -> dict[str, Any]:
    """Build an execution receipt with timing, cost, and outcome data.

    The receipt provides a verifiable summary of the debugging session for
    auditing and observability purposes.

    Args:
        cost_breakdown: Per-phase cost in USD (e.g. {"ANALYZE": 0.01, ...}).
        timing: Timing data with keys:
            - "start": float (epoch timestamp when pipeline started)
            - "end": float (epoch timestamp when pipeline finished)
            - "phases": dict mapping phase name to duration in seconds
        status: Final report status ("resolved", "improved", or "unresolved").

    Returns:
        Dict containing the full execution receipt.
    """
    total_cost = sum(cost_breakdown.values())
    duration = timing.get("end", 0.0) - timing.get("start", 0.0)

    return {
        "timing": {
            "start": timing.get("start"),
            "end": timing.get("end"),
            "duration_seconds": round(duration, 3),
            "phases": timing.get("phases", {}),
        },
        "cost": {
            "breakdown": cost_breakdown,
            "total_usd": round(total_cost, 6),
        },
        "outcome": {
            "status": status,
        },
    }


def assemble_report(
    diagnosis: DiagnosisReport,
    fix: FixProposal,
    verification: VerificationResult,
    attempts: int,
    all_fixes: list[FixProposal],
    cost: dict[str, float],
    *,
    max_retries: int = 3,
    timing: dict[str, Any] | None = None,
) -> DebuggingReport:
    """Assemble a complete DebuggingReport from pipeline outputs.

    This is the primary factory function for constructing the final report.
    It determines the status based on the quality score and retry exhaustion,
    and attaches an execution receipt with timing and cost data.

    Args:
        diagnosis: The DiagnosisReport from the DIAGNOSE phase.
        fix: The final FixProposal (last accepted fix).
        verification: The final VerificationResult from the VERIFY phase.
        attempts: Number of fix-verify attempts executed.
        all_fixes: History of all FixProposal instances across retries.
        cost: Per-phase cost breakdown in USD.
        max_retries: Maximum retry budget (default 3).
        timing: Optional timing dict with "start", "end", and "phases" keys.
            If not provided, uses current time for both start and end.

    Returns:
        A fully populated DebuggingReport with execution receipt.
    """
    status = _determine_status(
        quality_score=verification.quality_score,
        max_retries=max_retries,
        attempts=attempts,
    )

    # Build timing data — use provided or default to now
    if timing is None:
        now = time.time()
        timing = {"start": now, "end": now, "phases": {}}

    execution_receipt = build_execution_receipt(
        cost_breakdown=cost,
        timing=timing,
        status=status,
    )

    return DebuggingReport(
        status=status,
        original_diagnosis=diagnosis,
        final_fix=fix,
        verification=verification,
        attempts=attempts,
        all_fixes=all_fixes,
        cost_breakdown=cost,
        execution_receipt=execution_receipt,
    )
