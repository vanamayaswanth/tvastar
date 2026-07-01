"""Loop Quality — score a completed run's behavioral correctness.

Usage::

    result = await harness.run("fix the tests")
    print(result.quality.score)    # 70
    print(result.quality.grade)    # "WARN"
    print(result.quality.summary)  # "1 warning — tool 'bash' called 3x with identical arguments"

Pipeline scoring::

    from tvastar.quality import score_pipeline
    report = score_pipeline([result1, result2], strategy="worst")
    print(report.score)  # min score across all results
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .detect import Finding, Severity

if TYPE_CHECKING:  # pragma: no cover
    from .session import RunResult

__all__ = ["LoopQualityReport", "score_run", "score_pipeline"]

_ERROR_PENALTY = 30
_WARNING_PENALTY = 10
_MAX_STEPS_PENALTY = 20
_ERROR_STOP_PENALTY = 50


@dataclass
class LoopQualityReport:
    """Behavioral quality score for one completed agent run."""

    score: int
    grade: str  # "PASS" | "WARN" | "FAIL"
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


def score_pipeline(
    results: "list[RunResult]",
    *,
    strategy: str = "worst",
) -> LoopQualityReport:
    """Score a pipeline of multiple RunResult objects.

    Strategies:
    - "worst": pipeline score = min score across all results (strictest)
    - "average": pipeline score = mean score across all results
    - "all_pass": score=100 if all results pass individually, else min score

    Aggregates findings from all results.
    """
    if not results:
        return LoopQualityReport(
            score=100,
            grade="PASS",
            errors=[],
            warnings=[],
            findings=[],
            summary="No issues detected.",
        )

    reports = [score_run(r) for r in results]

    # Aggregate all findings
    all_findings: list[Finding] = []
    all_errors: list[Finding] = []
    all_warnings: list[Finding] = []
    for rep in reports:
        all_findings.extend(rep.findings)
        all_errors.extend(rep.errors)
        all_warnings.extend(rep.warnings)

    scores = [rep.score for rep in reports]

    if strategy == "worst":
        score = min(scores)
    elif strategy == "average":
        score = int(sum(scores) / len(scores))
    elif strategy == "all_pass":
        if all(rep.passed for rep in reports):
            score = 100
        else:
            score = min(scores)
    else:
        raise ValueError(f"Unknown strategy: {strategy!r}")

    if score >= 80:
        grade = "PASS"
    elif score >= 60:
        grade = "WARN"
    else:
        grade = "FAIL"

    parts = []
    if all_errors:
        parts.append(f"{len(all_errors)} error{'s' if len(all_errors) != 1 else ''}")
    if all_warnings:
        parts.append(f"{len(all_warnings)} warning{'s' if len(all_warnings) != 1 else ''}")

    if not parts:
        summary = "No issues detected."
    elif all_errors:
        summary = ", ".join(parts) + f" — {all_errors[0].message}"
    elif all_warnings:
        summary = ", ".join(parts) + f" — {all_warnings[0].message}"
    else:
        summary = ", ".join(parts)

    return LoopQualityReport(
        score=score,
        grade=grade,
        errors=all_errors,
        warnings=all_warnings,
        findings=all_findings,
        summary=summary,
    )
