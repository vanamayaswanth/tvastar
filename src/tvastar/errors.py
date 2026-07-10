"""Tvastar exception hierarchy."""

from __future__ import annotations

import warnings
from enum import Enum
from typing import Any


class DegradedState(Enum):
    """Operational modes the system enters during partial failure."""

    model_unavailable = "model_unavailable"
    mcp_disconnected = "mcp_disconnected"
    state_backend_down = "state_backend_down"
    budget_exhausted = "budget_exhausted"
    sandbox_overloaded = "sandbox_overloaded"


class TvastarError(Exception):
    """Base class for all Tvastar errors."""

    def __init__(self, *args, details: dict[str, Any] | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self._details = details or {}

    @property
    def details(self) -> dict[str, Any]:
        return self._details


class ModelError(TvastarError):
    """A model provider failed or returned something unusable."""


class ToolError(TvastarError):
    """A tool failed to execute. Message is fed back to the model."""


class ToolNotFound(ToolError):
    """The model requested a tool that isn't registered."""


class SkillError(TvastarError):
    """A skill could not be loaded or resolved."""


class SandboxError(TvastarError):
    """A sandbox operation failed or was blocked by policy."""


class PolicyError(TvastarError):
    """Base for all policy-related violations."""


class SecurityViolation(PolicyError, SandboxError):
    """Action blocked by security policy. Migrating from SandboxError to PolicyError.

    Dual-inheritance shim — caught by both PolicyError and SandboxError.
    SandboxError inheritance will be removed in the next major version.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        warnings.warn(
            "SecurityViolation is migrating from SandboxError to PolicyError. "
            "Use `except PolicyError` or `except SecurityViolation` instead of "
            "`except SandboxError`. This will stop working in v1.0. "
            "See docs/migration/exception-hierarchy.md",
            DeprecationWarning,
            stacklevel=2,
        )


class GovernanceError(PolicyError):
    """Fleet-level governance violations."""


class DurableError(TvastarError):
    """Checkpointing / resume failed."""

    def __init__(
        self,
        *args,
        session_id: str | None = None,
        operation: str | None = None,
        details: dict[str, Any] | None = None,
        **kwargs,
    ):
        merged: dict[str, Any] = {}
        if details:
            merged.update(details)
        if session_id is not None:
            merged["session_id"] = session_id
        if operation is not None:
            merged["operation"] = operation
        super().__init__(*args, details=merged or None, **kwargs)


def _get_budget_exhausted_error():
    """Lazy import of BudgetExhaustedError from tvastar.fleet for re-export."""
    from tvastar.fleet import BudgetExhaustedError

    return BudgetExhaustedError


def __getattr__(name: str):
    if name == "BudgetExhaustedError":
        return _get_budget_exhausted_error()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
