"""Tvastar exception hierarchy."""

from __future__ import annotations


class TvastarError(Exception):
    """Base class for all Tvastar errors."""


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


class SecurityViolation(SandboxError):
    """An action was blocked by the sandbox security policy."""


class DurableError(TvastarError):
    """Checkpointing / resume failed."""
