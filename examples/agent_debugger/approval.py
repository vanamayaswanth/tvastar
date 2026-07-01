"""Human-in-the-loop approval gate for the Agent Debugger pipeline.

When hitl=True, the FIX phase presents each FixProposal for user approval
before proceeding to verification. On rejection, the rejection reason is
passed back to the Rewriter_Agent for an alternative proposal.

Requirements: 3.3, 3.4
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from .schemas import FixProposal


@dataclass
class ApprovalResult:
    """Result of a human-in-the-loop approval decision."""

    approved: bool
    rejection_reason: str | None = None


class ApprovalGate(Protocol):
    """Protocol for approval gate implementations."""

    def review(self, proposal: FixProposal) -> ApprovalResult:
        """Present a FixProposal for review and return the decision."""
        ...


class AutoApprovalGate:
    """Default approval gate that auto-approves all proposals.

    Used in non-interactive / test mode.
    """

    def review(self, proposal: FixProposal) -> ApprovalResult:
        return ApprovalResult(approved=True)


class InteractiveApprovalGate:
    """Interactive approval gate that uses stdin/stdout for user input."""

    def review(self, proposal: FixProposal) -> ApprovalResult:
        print("\n" + "=" * 60)
        print("FIX PROPOSAL — Awaiting Approval")
        print("=" * 60)
        print(f"\nRewritten instructions:\n{proposal.rewritten_instructions}\n")
        if proposal.changes:
            print("Changes:")
            for change in proposal.changes:
                print(f"  [{change.section}] {change.rationale}")
        print(f"\nAddresses: {', '.join(proposal.addresses_modes)}")
        print("=" * 60)

        response = input("\nApprove this fix? [Y/n]: ").strip().lower()
        if response in ("", "y", "yes"):
            return ApprovalResult(approved=True)

        reason = input("Rejection reason: ").strip()
        return ApprovalResult(approved=False, rejection_reason=reason or None)


class CallbackApprovalGate:
    """Approval gate that delegates to a callback function.

    Useful for testing or programmatic approval flows.
    """

    def __init__(self, callback: Callable[[FixProposal], ApprovalResult]) -> None:
        self._callback = callback

    def review(self, proposal: FixProposal) -> ApprovalResult:
        return self._callback(proposal)
