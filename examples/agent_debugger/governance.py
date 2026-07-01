"""Governance phase controller for the Agent Debugger pipeline.

Defines the four-phase governance policy (ANALYZE, DIAGNOSE, FIX, VERIFY)
and provides transition helpers. Each phase exposes a strict subset of tools;
violations return an error ToolResultBlock without raising exceptions so the
agent loop stays alive and the model can self-correct.

Uses the core ``GovernancePolicy.enforce()`` method for tool-use checks instead
of a custom helper.

Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 2.3, 3.2, 4.2
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from tvastar.masking import GovernancePolicy

if TYPE_CHECKING:
    from tvastar.approval import ApprovalGate

# ---------------------------------------------------------------------------
# Tool sets per phase
# ---------------------------------------------------------------------------

ANALYZE_TOOLS: set[str] = {"load_trajectory", "redact_pii", "validate_trajectory"}
"""Tools available during the ANALYZE phase (trajectory loading & sanitization)."""

DIAGNOSE_TOOLS: set[str] = {"run_detector", "run_all_detectors", "generate_diagnosis_report"}
"""Tools available during the DIAGNOSE phase (failure detection & reporting)."""

FIX_TOOLS: set[str] = {"rewrite_instructions", "request_approval"}
"""Tools available during the FIX phase (instruction rewriting & approval)."""

VERIFY_TOOLS: set[str] = {"run_in_sandbox", "compare_findings", "compute_quality_score"}
"""Tools available during the VERIFY phase (sandbox re-run & comparison)."""


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_debugger_governance(
    *, approval_gate: Optional["ApprovalGate"] = None
) -> GovernancePolicy:
    """Create the four-phase governance policy for the debugger pipeline.

    The returned ``GovernancePolicy`` starts in the ``ANALYZE`` phase.
    Transition between phases with ``set_phase("DIAGNOSE")`` etc.

    Tool-use checks are performed via ``governance.enforce(tool_name, tool_use_id)``
    which returns ``None`` when allowed or a ``ToolResultBlock(is_error=True)``
    when blocked.

    Parameters
    ----------
    approval_gate:
        Optional approval gate for human-in-the-loop escalation on
        governance violations. When ``None``, violations are hard-blocked.
    """
    return GovernancePolicy(
        phases={
            "ANALYZE": ANALYZE_TOOLS,
            "DIAGNOSE": DIAGNOSE_TOOLS,
            "FIX": FIX_TOOLS,
            "VERIFY": VERIFY_TOOLS,
        },
        current_phase="ANALYZE",
        approval_gate=approval_gate,
    )
