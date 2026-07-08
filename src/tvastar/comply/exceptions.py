"""Exceptions for the tvastar.comply package."""

from __future__ import annotations


class ComplianceError(Exception):
    """Base exception for compliance copilot operations."""


class LoopNotFoundError(ComplianceError):
    """Raised when a specified Loop does not exist or is invalid."""


class RunNotFoundError(ComplianceError):
    """Raised when a specified run_id is not found in the TrustLog."""
