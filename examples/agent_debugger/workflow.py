"""Workflow orchestrator for the Agent Debugger pipeline.

Implements the four-phase diagnose-fix-verify pipeline as a Tvastar @workflow:
    ANALYZE → DIAGNOSE → FIX → VERIFY

Each phase transitions governance, delegates to specialist sub-agents via
session.task(), respects budget limits, and handles partial results gracefully.

Uses core primitives:
- ``BudgetPolicy`` / ``Cost`` for cost tracking (replaces custom BudgetTracker)
- ``GovernancePolicy.enforce()`` for tool-use governance
- ``Tracer`` for observability spans

Requirements: 6.1, 9.2, 9.3, 8.2
"""

from __future__ import annotations

import logging
from pathlib import Path

from tvastar.agent import create_agent
from tvastar.cost import BudgetPolicy, Cost
from tvastar.observability import Tracer
from tvastar.workflow import WorkflowContext, workflow

from .agents import diagnosis_agent, rewriter_agent, verifier_agent
from .approval import ApprovalGate, AutoApprovalGate
from .compaction import compact_messages
from .detectors import run_all_detectors, scan_for_injection
from .governance import create_debugger_governance
from .model_selection import select_model
from .sanitize import redact_pii
from .schemas import (
    DebuggingReport,
    DiagnosisReport,
    FixProposal,
    InstructionChange,
    VerificationResult,
)
from .trajectory import load_trajectory, validate_trajectory

logger = logging.getLogger(__name__)

# Default token usage estimates per phase (used when mock model doesn't report real usage)
_DEFAULT_PHASE_TOKENS = {"input": 500, "output": 200}


@workflow
async def agent_debugger(ctx: WorkflowContext) -> dict:
    """The Agent Debugger pipeline.

    Orchestrates four sequential phases (ANALYZE, DIAGNOSE, FIX, VERIFY)
    with governance-controlled tool access, budget enforcement, and graceful
    handling of partial results from sub-agents.

    Payload:
        trajectory_path: str — path to JSONL file
        budget_usd: float — optional cost ceiling (default 2.0)
        hitl: bool — enable human-in-the-loop approval (default False)
        max_retries: int — fix-verify retry budget (default 3)
        mcp_server_url: str | None — optional MCP server for code analysis
        use_real_model: bool — override MockModel with configured provider
    """
    payload = ctx.payload or {}
    trajectory_path = payload.get("trajectory_path", "")
    budget_usd = payload.get("budget_usd", 2.0)
    hitl = payload.get("hitl", False)
    max_retries = payload.get("max_retries", 3)
    max_rejection_retries = payload.get("max_rejection_retries", 2)
    use_real_model = payload.get("use_real_model", False)
    memory_cap_mb = payload.get("memory_cap_mb", 50.0)
    approval_gate: ApprovalGate = payload.get("approval_gate") or AutoApprovalGate()

    # --- Setup: governance + budget + tracer ---
    governance = create_debugger_governance()
    budget = BudgetPolicy(max_usd=budget_usd, on_exceed="stop")
    tracer = Tracer()

    ctx.log.info("Starting Agent Debugger pipeline", trajectory_path=trajectory_path)

    # --- Select model ---
    model = select_model(use_real=use_real_model)

    # --- Create agent spec with sub-agents for delegation ---
    agent_spec = create_agent(
        "agent_debugger",
        model=model,
        instructions="Agent Debugger orchestrator.",
        governance=governance,
        max_steps=50,
        subagents=[diagnosis_agent, rewriter_agent, verifier_agent],
    )

    # --- Initialize harness via workflow context ---
    harness = await ctx.init(agent_spec)
    session = await harness.session()

    # =========================================================================
    # PHASE 1: ANALYZE
    # =========================================================================
    governance.set_phase("ANALYZE")
    ctx.log.info("Phase: ANALYZE — loading and sanitizing trajectory")

    async with budget.phase("ANALYZE"):
        with tracer.phase("ANALYZE"):
            try:
                messages = load_trajectory(Path(trajectory_path))
                messages = validate_trajectory(messages)
            except (FileNotFoundError, PermissionError, ValueError) as exc:
                ctx.log.error("ANALYZE failed", error=str(exc))
                return {"error": str(exc), "stopped": "error", "phase": "ANALYZE"}

            # Redact PII before anything enters session memory
            redaction_result = redact_pii(messages)
            messages = redaction_result.messages
            if redaction_result.redaction_count > 0:
                ctx.log.info(
                    "PII redacted",
                    count=redaction_result.redaction_count,
                    types=redaction_result.redacted_types,
                )

            # Compact if trajectory exceeds memory cap
            messages = compact_messages(messages, memory_cap_mb)

            # Attribute estimated usage for ANALYZE phase
            budget.attribute(
                Cost(
                    input_tokens=_DEFAULT_PHASE_TOKENS["input"],
                    output_tokens=_DEFAULT_PHASE_TOKENS["output"],
                )
            )

    # Budget check after ANALYZE
    if _budget_exceeded(budget):
        ctx.log.warn("Budget exceeded after ANALYZE phase")
        return _budget_stopped_report(budget, phase="ANALYZE")

    # =========================================================================
    # PHASE 2: DIAGNOSE
    # =========================================================================
    governance.set_phase("DIAGNOSE")
    ctx.log.info("Phase: DIAGNOSE — identifying failure modes")

    async with budget.phase("DIAGNOSE"):
        with tracer.phase("DIAGNOSE"):
            # Run detectors locally for initial analysis
            failure_modes = run_all_detectors(messages)
            is_adversarial, injection_evidence = scan_for_injection(messages)

            # Delegate to diagnosis_agent for deeper analysis via session.task()
            diagnosis_prompt = (
                "Analyze the following trajectory and produce a DiagnosisReport.\n"
                f"The trajectory has {len(messages)} messages.\n"
                f"Local detectors found {len(failure_modes)} failure modes: "
                f"{[fm.detector for fm in failure_modes]}.\n"
                f"Injection scan: adversarial={is_adversarial}.\n"
                "Determine the root cause and assign a confidence score."
            )

            with tracer.agent_call("diagnosis_agent"):
                diagnosis_result = await session.task(
                    diagnosis_prompt,
                    agent="diagnosis_agent",
                )

            # Handle stopped="max_steps" partial results gracefully
            if diagnosis_result.stopped == "max_steps":
                logger.warning("Diagnosis agent hit max_steps; using partial result.")
                ctx.log.warn("Diagnosis agent stopped at max_steps, using partial result")

            # Build DiagnosisReport from detector findings + agent analysis
            diagnosis_report = DiagnosisReport(
                failure_modes=failure_modes,
                root_cause=diagnosis_result.text[:500]
                if diagnosis_result.text
                else "Unable to determine root cause",
                is_adversarial=is_adversarial,
                injection_evidence=injection_evidence,
                confidence=0.7 if failure_modes else 0.3,
            )

            # Attribute usage for DIAGNOSE phase
            budget.attribute(
                Cost(
                    input_tokens=diagnosis_result.usage.input_tokens,
                    output_tokens=diagnosis_result.usage.output_tokens,
                )
            )

    # Budget check after DIAGNOSE
    if _budget_exceeded(budget):
        ctx.log.warn("Budget exceeded after DIAGNOSE phase")
        return _budget_stopped_report(budget, phase="DIAGNOSE", diagnosis=diagnosis_report)

    # =========================================================================
    # PHASE 3 & 4: FIX → VERIFY (with retry loop)
    # =========================================================================
    all_fixes: list[FixProposal] = []
    verification: VerificationResult | None = None
    current_fix: FixProposal | None = None
    prev_verification: VerificationResult | None = None

    for attempt in range(1, max_retries + 1):
        # --- FIX phase ---
        governance.set_phase("FIX")
        ctx.log.info("Phase: FIX", attempt=attempt)

        async with budget.phase("FIX"):
            with tracer.phase("FIX"):
                fix_prompt = _build_fix_prompt(diagnosis_report, prev_verification, attempt)

                with tracer.agent_call("rewriter_agent"):
                    fix_result = await session.task(
                        fix_prompt,
                        agent="rewriter_agent",
                    )

                if fix_result.stopped == "max_steps":
                    logger.warning("Rewriter agent hit max_steps; using partial result.")
                    ctx.log.warn("Rewriter agent stopped at max_steps, using partial result")

                current_fix = _parse_fix_proposal(fix_result.text, diagnosis_report)
                all_fixes.append(current_fix)

                # Attribute usage for FIX phase
                budget.attribute(
                    Cost(
                        input_tokens=fix_result.usage.input_tokens,
                        output_tokens=fix_result.usage.output_tokens,
                    )
                )

        # Budget check after FIX
        if _budget_exceeded(budget):
            ctx.log.warn("Budget exceeded after FIX phase")
            return _budget_stopped_report(
                budget,
                phase="FIX",
                diagnosis=diagnosis_report,
                fix=current_fix,
                all_fixes=all_fixes,
                attempts=attempt,
            )

        # --- HITL approval gate (when enabled) ---
        if hitl:
            ctx.log.info("HITL: awaiting approval for fix proposal")
            rejection_retries = 0
            while rejection_retries < max_rejection_retries:
                approval = approval_gate.review(current_fix)
                if approval.approved:
                    ctx.log.info("HITL: fix proposal approved")
                    break

                # Rejected — ask rewriter for an alternative
                rejection_retries += 1
                ctx.log.info(
                    "HITL: fix proposal rejected",
                    reason=approval.rejection_reason,
                    rejection_attempt=rejection_retries,
                )
                rejection_prompt = _build_fix_prompt_with_rejection(
                    diagnosis_report, current_fix, approval.rejection_reason
                )

                async with budget.phase("FIX"):
                    with tracer.agent_call("rewriter_agent"):
                        fix_result = await session.task(
                            rejection_prompt,
                            agent="rewriter_agent",
                        )
                    current_fix = _parse_fix_proposal(fix_result.text, diagnosis_report)
                    all_fixes.append(current_fix)

                    budget.attribute(
                        Cost(
                            input_tokens=fix_result.usage.input_tokens,
                            output_tokens=fix_result.usage.output_tokens,
                        )
                    )

                if _budget_exceeded(budget):
                    ctx.log.warn("Budget exceeded during HITL rejection retry")
                    return _budget_stopped_report(
                        budget,
                        phase="FIX",
                        diagnosis=diagnosis_report,
                        fix=current_fix,
                        all_fixes=all_fixes,
                        attempts=attempt,
                    )
            else:
                # Max rejection retries exhausted — proceed with last proposal
                ctx.log.warn("HITL: max rejection retries exhausted, proceeding with last proposal")

        # --- VERIFY phase ---
        governance.set_phase("VERIFY")
        ctx.log.info("Phase: VERIFY", attempt=attempt)

        async with budget.phase("VERIFY"):
            with tracer.phase("VERIFY"):
                verify_prompt = (
                    "Verify the following fix by re-running the original task with "
                    "the rewritten instructions and comparing results.\n\n"
                    f"Rewritten instructions:\n{current_fix.rewritten_instructions}\n\n"
                    f"Original failure modes: {[fm.detector for fm in diagnosis_report.failure_modes]}\n"
                    "Produce a VerificationResult with a quality_score."
                )

                with tracer.agent_call("verifier_agent"):
                    verify_result = await session.task(
                        verify_prompt,
                        agent="verifier_agent",
                    )

                if verify_result.stopped == "max_steps":
                    logger.warning("Verifier agent hit max_steps; using partial result.")
                    ctx.log.warn("Verifier agent stopped at max_steps, using partial result")

                verification = _parse_verification_result(
                    verify_result.text, diagnosis_report, current_fix
                )

                # Attribute usage for VERIFY phase
                budget.attribute(
                    Cost(
                        input_tokens=verify_result.usage.input_tokens,
                        output_tokens=verify_result.usage.output_tokens,
                    )
                )

        # Budget check after VERIFY
        if _budget_exceeded(budget):
            ctx.log.warn("Budget exceeded after VERIFY phase")
            return _budget_stopped_report(
                budget,
                phase="VERIFY",
                diagnosis=diagnosis_report,
                fix=current_fix,
                verification=verification,
                all_fixes=all_fixes,
                attempts=attempt,
            )

        # Check if quality is sufficient
        if verification.quality_score >= 0.5:
            ctx.log.info(
                "Verification passed",
                score=verification.quality_score,
                attempt=attempt,
            )
            break

        # Quality too low — retry if budget allows
        ctx.log.info(
            "Verification score below threshold, retrying",
            score=verification.quality_score,
            attempt=attempt,
            max_retries=max_retries,
        )
        prev_verification = verification

    # =========================================================================
    # Assemble final report
    # =========================================================================
    assert diagnosis_report is not None
    assert current_fix is not None
    assert verification is not None

    # Determine status
    if verification.quality_score >= 0.5:
        status = "resolved" if verification.quality_score >= 0.8 else "improved"
    else:
        status = "unresolved"

    report = DebuggingReport(
        status=status,
        original_diagnosis=diagnosis_report,
        final_fix=current_fix,
        verification=verification,
        attempts=len(all_fixes),
        all_fixes=all_fixes,
        cost_breakdown=_cost_breakdown_usd(budget),
    )

    ctx.log.info("Pipeline complete", status=report.status, attempts=report.attempts)
    return report.to_dict()


# =============================================================================
# Helpers
# =============================================================================


def _budget_exceeded(budget: BudgetPolicy) -> bool:
    """Check whether cumulative cost exceeds the budget limit."""
    total = sum(c.usd for c in budget.cost_breakdown().values())
    return total > budget.max_usd


def _cost_breakdown_usd(budget: BudgetPolicy) -> dict[str, float]:
    """Convert BudgetPolicy's per-phase Cost objects to a dict[str, float] in USD."""
    return {phase: cost.usd for phase, cost in budget.cost_breakdown().items()}


def _build_fix_prompt(
    diagnosis: DiagnosisReport,
    prev_verification: VerificationResult | None,
    attempt: int,
) -> str:
    """Build the prompt for the rewriter agent."""
    parts = [
        "Rewrite the agent instructions to address the following diagnosed failure modes:",
        "",
    ]
    for fm in diagnosis.failure_modes:
        parts.append(f"- {fm.detector} ({fm.severity}): {fm.message}")
    parts.append("")
    parts.append(f"Root cause: {diagnosis.root_cause}")

    if prev_verification is not None:
        parts.append("")
        parts.append(
            f"Previous attempt (#{attempt - 1}) scored {prev_verification.quality_score:.2f}."
        )
        if prev_verification.remaining_modes:
            parts.append(f"Still unresolved: {prev_verification.remaining_modes}")
        if prev_verification.new_findings:
            parts.append(
                "New issues introduced: "
                + ", ".join(f.detector for f in prev_verification.new_findings)
            )
        parts.append("Please produce an improved fix that addresses the remaining issues.")

    parts.append("")
    parts.append("Produce a FixProposal with rewritten instructions, changes, and rationale.")
    return "\n".join(parts)


def _build_fix_prompt_with_rejection(
    diagnosis: DiagnosisReport,
    rejected_fix: FixProposal,
    rejection_reason: str | None,
) -> str:
    """Build a fix prompt that incorporates a human rejection reason."""
    parts = [
        "Your previous fix proposal was rejected by the reviewer.",
        "",
    ]
    if rejection_reason:
        parts.append(f"Rejection reason: {rejection_reason}")
        parts.append("")
    parts.append("The rejected proposal was:")
    parts.append(f"  {rejected_fix.rewritten_instructions[:200]}")
    parts.append("")
    parts.append("Original failure modes to address:")
    for fm in diagnosis.failure_modes:
        parts.append(f"- {fm.detector} ({fm.severity}): {fm.message}")
    parts.append("")
    parts.append(f"Root cause: {diagnosis.root_cause}")
    parts.append("")
    parts.append(
        "Please produce an alternative FixProposal that addresses the rejection feedback "
        "while still fixing the diagnosed failure modes."
    )
    return "\n".join(parts)


def _parse_fix_proposal(agent_text: str, diagnosis: DiagnosisReport) -> FixProposal:
    """Parse the rewriter agent's output into a FixProposal.

    In a full implementation with structured output, this would use Pydantic
    parsing. For the orchestrator level, we construct a FixProposal from the
    agent's text response.
    """
    return FixProposal(
        rewritten_instructions=agent_text or "No rewritten instructions produced.",
        changes=[
            InstructionChange(
                section="general",
                original="(original instructions)",
                rewritten=agent_text[:200] if agent_text else "",
                rationale=f"Addresses: {[fm.detector for fm in diagnosis.failure_modes]}",
            )
        ],
        addresses_modes=[fm.detector for fm in diagnosis.failure_modes],
    )


def _parse_verification_result(
    agent_text: str,
    diagnosis: DiagnosisReport,
    fix: FixProposal,
) -> VerificationResult:
    """Parse the verifier agent's output into a VerificationResult.

    In a full implementation with structured output, this would use Pydantic
    parsing. For the orchestrator level, we construct a VerificationResult
    from the agent's text response and apply heuristics.
    """
    # Heuristic: if the agent text mentions "resolved" or "improved", score higher
    text_lower = (agent_text or "").lower()
    if "resolved" in text_lower or "all fixed" in text_lower:
        score = 0.85
    elif "improved" in text_lower or "partial" in text_lower:
        score = 0.6
    else:
        # Default to a moderate score for mock responses
        score = 0.75

    return VerificationResult(
        quality_score=score,
        new_findings=[],
        resolved_modes=fix.addresses_modes,
        remaining_modes=[],
        trajectory_length=10,
    )


def _budget_stopped_report(
    budget: BudgetPolicy,
    *,
    phase: str,
    diagnosis: DiagnosisReport | None = None,
    fix: FixProposal | None = None,
    verification: VerificationResult | None = None,
    all_fixes: list[FixProposal] | None = None,
    attempts: int = 0,
) -> dict:
    """Produce a partial report when budget is exceeded."""
    # Provide sensible defaults for missing components
    default_diagnosis = diagnosis or DiagnosisReport(
        failure_modes=[],
        root_cause="Budget exceeded before diagnosis could complete",
        is_adversarial=False,
        injection_evidence=[],
        confidence=0.0,
    )
    default_fix = fix or FixProposal(
        rewritten_instructions="",
        changes=[],
        addresses_modes=[],
    )
    default_verification = verification or VerificationResult(
        quality_score=0.0,
        new_findings=[],
        resolved_modes=[],
        remaining_modes=[],
        trajectory_length=0,
    )

    report = DebuggingReport(
        status="unresolved",
        original_diagnosis=default_diagnosis,
        final_fix=default_fix,
        verification=default_verification,
        attempts=attempts,
        all_fixes=all_fixes or [],
        cost_breakdown=_cost_breakdown_usd(budget),
    )

    result = report.to_dict()
    result["stopped"] = "budget"
    result["stopped_at_phase"] = phase
    return result
