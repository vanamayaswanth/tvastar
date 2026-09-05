"""Synchronous success-verification contracts for loop runs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from .session import RunResult


@dataclass
class VerificationVerdict:
    passed: bool
    summary: str = ""
    evidence: dict = field(default_factory=dict)


@dataclass
class VerificationContract:
    required: bool = True
    verifier: Callable[["RunResult"], bool | VerificationVerdict] | None = None


def evaluate_contract(
    contract: VerificationContract, result: "RunResult"
) -> VerificationVerdict | None:
    """Evaluate a contract, returning a normalized verdict or no verdict."""
    if contract.verifier is None:
        return (
            VerificationVerdict(False, "verification required but no verifier configured")
            if contract.required
            else None
        )
    try:
        verdict = contract.verifier(result)
    except Exception:
        return VerificationVerdict(False, "verifier raised an exception")
    if isinstance(verdict, VerificationVerdict):
        return verdict
    if isinstance(verdict, bool):
        return VerificationVerdict(
            verdict, "verification passed" if verdict else "verification failed"
        )
    return VerificationVerdict(False, "verification verifier returned an invalid verdict")


__all__ = ["VerificationContract", "VerificationVerdict", "evaluate_contract"]
