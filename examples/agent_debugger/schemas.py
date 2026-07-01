"""Structured output schemas for the Agent Debugger pipeline.

Defines dataclasses for each pipeline boundary (DiagnosisReport, FixProposal,
VerificationResult, DebuggingReport) plus configuration and checkpoint schemas.
All schemas support round-trip serialization via to_dict() / from_dict().
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


# ---------------------------------------------------------------------------
# Pipeline boundary schemas
# ---------------------------------------------------------------------------


@dataclass
class FailureMode:
    """A single failure mode detected in a trajectory."""

    detector: str  # e.g. "thrash_loop", "unverified_completion"
    severity: str  # "info" | "warning" | "error"
    message: str
    evidence: list[str]  # relevant message excerpts
    line_range: tuple[int, int]  # trajectory line range

    def to_dict(self) -> dict:
        return {
            "detector": self.detector,
            "severity": self.severity,
            "message": self.message,
            "evidence": list(self.evidence),
            "line_range": list(self.line_range),
        }

    @classmethod
    def from_dict(cls, data: dict) -> FailureMode:
        return cls(
            detector=data["detector"],
            severity=data["severity"],
            message=data["message"],
            evidence=list(data["evidence"]),
            line_range=tuple(data["line_range"]),
        )


@dataclass
class DiagnosisReport:
    """Structured output from the Diagnosis_Agent."""

    failure_modes: list[FailureMode]
    root_cause: str  # free-text root cause analysis
    is_adversarial: bool  # True if prompt injection detected
    injection_evidence: list[str]
    confidence: float  # 0.0–1.0

    def to_dict(self) -> dict:
        return {
            "failure_modes": [fm.to_dict() for fm in self.failure_modes],
            "root_cause": self.root_cause,
            "is_adversarial": self.is_adversarial,
            "injection_evidence": list(self.injection_evidence),
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, data: dict) -> DiagnosisReport:
        return cls(
            failure_modes=[FailureMode.from_dict(fm) for fm in data["failure_modes"]],
            root_cause=data["root_cause"],
            is_adversarial=data["is_adversarial"],
            injection_evidence=list(data["injection_evidence"]),
            confidence=data["confidence"],
        )


@dataclass
class InstructionChange:
    """A single instruction change within a FixProposal."""

    section: str  # which part of instructions changed
    original: str
    rewritten: str
    rationale: str

    def to_dict(self) -> dict:
        return {
            "section": self.section,
            "original": self.original,
            "rewritten": self.rewritten,
            "rationale": self.rationale,
        }

    @classmethod
    def from_dict(cls, data: dict) -> InstructionChange:
        return cls(
            section=data["section"],
            original=data["original"],
            rewritten=data["rewritten"],
            rationale=data["rationale"],
        )


@dataclass
class FixProposal:
    """Structured output from the Rewriter_Agent."""

    rewritten_instructions: str
    changes: list[InstructionChange]
    addresses_modes: list[str]  # detector names this fix targets

    def to_dict(self) -> dict:
        return {
            "rewritten_instructions": self.rewritten_instructions,
            "changes": [c.to_dict() for c in self.changes],
            "addresses_modes": list(self.addresses_modes),
        }

    @classmethod
    def from_dict(cls, data: dict) -> FixProposal:
        return cls(
            rewritten_instructions=data["rewritten_instructions"],
            changes=[InstructionChange.from_dict(c) for c in data["changes"]],
            addresses_modes=list(data["addresses_modes"]),
        )


@dataclass
class VerificationResult:
    """Structured output from the Verifier_Agent."""

    quality_score: float  # 0.0–1.0
    new_findings: list[FailureMode]
    resolved_modes: list[str]
    remaining_modes: list[str]
    trajectory_length: int  # steps in re-run

    def to_dict(self) -> dict:
        return {
            "quality_score": self.quality_score,
            "new_findings": [f.to_dict() for f in self.new_findings],
            "resolved_modes": list(self.resolved_modes),
            "remaining_modes": list(self.remaining_modes),
            "trajectory_length": self.trajectory_length,
        }

    @classmethod
    def from_dict(cls, data: dict) -> VerificationResult:
        return cls(
            quality_score=data["quality_score"],
            new_findings=[FailureMode.from_dict(f) for f in data["new_findings"]],
            resolved_modes=list(data["resolved_modes"]),
            remaining_modes=list(data["remaining_modes"]),
            trajectory_length=data["trajectory_length"],
        )


@dataclass
class DebuggingReport:
    """Final structured report for a debugging session."""

    status: Literal["resolved", "improved", "unresolved"]
    original_diagnosis: DiagnosisReport
    final_fix: FixProposal
    verification: VerificationResult
    attempts: int
    all_fixes: list[FixProposal]  # history of all attempts
    cost_breakdown: dict[str, float]  # phase -> USD
    execution_receipt: dict | None = None

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "original_diagnosis": self.original_diagnosis.to_dict(),
            "final_fix": self.final_fix.to_dict(),
            "verification": self.verification.to_dict(),
            "attempts": self.attempts,
            "all_fixes": [f.to_dict() for f in self.all_fixes],
            "cost_breakdown": dict(self.cost_breakdown),
            "execution_receipt": self.execution_receipt,
        }

    @classmethod
    def from_dict(cls, data: dict) -> DebuggingReport:
        return cls(
            status=data["status"],
            original_diagnosis=DiagnosisReport.from_dict(data["original_diagnosis"]),
            final_fix=FixProposal.from_dict(data["final_fix"]),
            verification=VerificationResult.from_dict(data["verification"]),
            attempts=data["attempts"],
            all_fixes=[FixProposal.from_dict(f) for f in data["all_fixes"]],
            cost_breakdown=dict(data["cost_breakdown"]),
            execution_receipt=data.get("execution_receipt"),
        )

    def to_markdown(self) -> str:
        """Render the debugging report as a formatted Markdown string."""
        lines: list[str] = []
        lines.append("# Agent Debugger Report")
        lines.append("")
        lines.append(f"**Status:** {self.status}")
        lines.append(f"**Attempts:** {self.attempts}")
        lines.append("")

        # Diagnosis section
        lines.append("## Diagnosis")
        lines.append("")
        lines.append(f"**Root Cause:** {self.original_diagnosis.root_cause}")
        lines.append(f"**Confidence:** {self.original_diagnosis.confidence:.2f}")
        if self.original_diagnosis.is_adversarial:
            lines.append("**⚠️ Adversarial trajectory detected**")
        lines.append("")
        lines.append("### Failure Modes")
        lines.append("")
        for fm in self.original_diagnosis.failure_modes:
            lines.append(f"- **{fm.detector}** ({fm.severity}): {fm.message}")
        lines.append("")

        # Fix section
        lines.append("## Applied Fix")
        lines.append("")
        lines.append("### Rewritten Instructions")
        lines.append("")
        lines.append("```")
        lines.append(self.final_fix.rewritten_instructions)
        lines.append("```")
        lines.append("")
        lines.append("### Changes")
        lines.append("")
        for change in self.final_fix.changes:
            lines.append(f"#### {change.section}")
            lines.append(f"- **Rationale:** {change.rationale}")
            lines.append(f"- **Original:** {change.original}")
            lines.append(f"- **Rewritten:** {change.rewritten}")
            lines.append("")

        # Verification section
        lines.append("## Verification")
        lines.append("")
        lines.append(f"**Quality Score:** {self.verification.quality_score:.2f}")
        lines.append(f"**Trajectory Length:** {self.verification.trajectory_length} steps")
        if self.verification.resolved_modes:
            lines.append(f"**Resolved:** {', '.join(self.verification.resolved_modes)}")
        if self.verification.remaining_modes:
            lines.append(f"**Remaining:** {', '.join(self.verification.remaining_modes)}")
        lines.append("")

        # Cost section
        if self.cost_breakdown:
            lines.append("## Cost Breakdown")
            lines.append("")
            lines.append("| Phase | Cost (USD) |")
            lines.append("|-------|-----------|")
            total = 0.0
            for phase, cost in self.cost_breakdown.items():
                lines.append(f"| {phase} | ${cost:.4f} |")
                total += cost
            lines.append(f"| **Total** | **${total:.4f}** |")
            lines.append("")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Configuration schema
# ---------------------------------------------------------------------------


@dataclass
class DebuggerConfig:
    """Configuration for a debugging session."""

    trajectory_path: str = ""
    budget_usd: float = 2.0
    hitl: bool = False
    max_retries: int = 3
    mcp_server_url: str | None = None
    use_real_model: bool = False
    memory_cap_mb: float = 50.0
    pii_patterns: dict[str, str] | None = None  # name -> regex pattern

    def to_dict(self) -> dict:
        return {
            "trajectory_path": self.trajectory_path,
            "budget_usd": self.budget_usd,
            "hitl": self.hitl,
            "max_retries": self.max_retries,
            "mcp_server_url": self.mcp_server_url,
            "use_real_model": self.use_real_model,
            "memory_cap_mb": self.memory_cap_mb,
            "pii_patterns": dict(self.pii_patterns) if self.pii_patterns else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> DebuggerConfig:
        return cls(
            trajectory_path=data.get("trajectory_path", ""),
            budget_usd=data.get("budget_usd", 2.0),
            hitl=data.get("hitl", False),
            max_retries=data.get("max_retries", 3),
            mcp_server_url=data.get("mcp_server_url"),
            use_real_model=data.get("use_real_model", False),
            memory_cap_mb=data.get("memory_cap_mb", 50.0),
            pii_patterns=data.get("pii_patterns"),
        )


# ---------------------------------------------------------------------------
# Checkpoint schema (durable execution)
# ---------------------------------------------------------------------------


@dataclass
class DebuggerCheckpoint:
    """Durable execution checkpoint for resuming a debugging session."""

    run_id: str
    completed_phase: str  # "ANALYZE" | "DIAGNOSE" | "FIX" | "VERIFY"
    trajectory: list[dict]  # serialized messages
    diagnosis: dict | None = None  # serialized DiagnosisReport
    current_fix: dict | None = None  # serialized FixProposal
    verification: dict | None = None  # serialized VerificationResult
    attempt_number: int = 0
    all_fixes: list[dict] = field(default_factory=list)
    cumulative_usage: dict = field(default_factory=lambda: {"input_tokens": 0, "output_tokens": 0})

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "completed_phase": self.completed_phase,
            "trajectory": list(self.trajectory),
            "diagnosis": self.diagnosis,
            "current_fix": self.current_fix,
            "verification": self.verification,
            "attempt_number": self.attempt_number,
            "all_fixes": list(self.all_fixes),
            "cumulative_usage": dict(self.cumulative_usage),
        }

    @classmethod
    def from_dict(cls, data: dict) -> DebuggerCheckpoint:
        return cls(
            run_id=data["run_id"],
            completed_phase=data["completed_phase"],
            trajectory=list(data["trajectory"]),
            diagnosis=data.get("diagnosis"),
            current_fix=data.get("current_fix"),
            verification=data.get("verification"),
            attempt_number=data.get("attempt_number", 0),
            all_fixes=list(data.get("all_fixes", [])),
            cumulative_usage=dict(
                data.get("cumulative_usage", {"input_tokens": 0, "output_tokens": 0})
            ),
        )
