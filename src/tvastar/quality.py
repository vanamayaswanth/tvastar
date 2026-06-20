"""Loop Quality — score a completed run's behavioral correctness.

Usage::

    result = await harness.run("fix the tests")
    print(result.quality.score)    # 70
    print(result.quality.grade)    # "WARN"
    print(result.quality.summary)  # "1 warning — tool 'bash' called 3x with identical arguments"
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .detect import Finding, Severity

if TYPE_CHECKING:  # pragma: no cover
    from .session import RunResult

__all__ = ["LoopQualityReport", "score_run"]

_ERROR_PENALTY = 30
_WARNING_PENALTY = 10
_MAX_STEPS_PENALTY = 20
_ERROR_STOP_PENALTY = 50


@dataclass
class LoopQualityReport:
    """Behavioral quality score for one completed agent run."""

    score: int
    grade: str              # "PASS" | "WARN" | "FAIL"
    errors: list[Finding]
    warnings: list[Finding]
    findings: list[Finding]
    summary: str

    @property
    def passed(self) -> bool:
        return self.grade == "PASS"


def score_run(result: "RunResult") -> LoopQualityReport:
    """Compute a LoopQualityReport from a RunResult's findings and stop state."""
    errors = [f for f in result.findings if f.severity == Severity.ERROR]
    warnings = [f for f in result.findings if f.severity == Severity.WARNING]

    score = 100
    score -= len(errors) * _ERROR_PENALTY
    score -= len(warnings) * _WARNING_PENALTY
    if result.stopped == "max_steps":
        score -= _MAX_STEPS_PENALTY
    elif result.stopped == "budget":
        score -= _MAX_STEPS_PENALTY  # same weight as step limit
    elif result.stopped == "error":
        score -= _ERROR_STOP_PENALTY
    score = max(0, score)

    if score >= 80:
        grade = "PASS"
    elif score >= 60:
        grade = "WARN"
    else:
        grade = "FAIL"

    parts = []
    if errors:
        parts.append(f"{len(errors)} error{'s' if len(errors) != 1 else ''}")
    if warnings:
        parts.append(f"{len(warnings)} warning{'s' if len(warnings) != 1 else ''}")
    if result.stopped == "max_steps":
        parts.append("hit step limit")
    elif result.stopped == "budget":
        parts.append("hit token budget")
    elif result.stopped == "error":
        parts.append("stopped on error")

    if not parts:
        summary = "No issues detected."
    elif errors:
        summary = ", ".join(parts) + f" — {errors[0].message}"
    elif warnings:
        summary = ", ".join(parts) + f" — {warnings[0].message}"
    else:
        summary = ", ".join(parts)

    return LoopQualityReport(
        score=score,
        grade=grade,
        errors=errors,
        warnings=warnings,
        findings=result.findings,
        summary=summary,
    )
