"""Sandbox layer: pluggable execution environments.

Built-ins are imported eagerly (zero deps). External provider adapters
(Docker/Remote) are lazy so importing this package never requires docker etc.
"""

from __future__ import annotations

from .base import AuditEntry, CredentialFilter, ExecResult, ResourcePolicy, Sandbox, SecurityPolicy
from .local import LocalSandbox, default_local_shell
from .virtual import VirtualSandbox

__all__ = [
    "Sandbox",
    "ExecResult",
    "SecurityPolicy",
    "ResourcePolicy",
    "AuditEntry",
    "CredentialFilter",
    "VirtualSandbox",
    "LocalSandbox",
    "default_local_shell",
    "DockerSandbox",
    "RemoteSandbox",
    "DurableDockerSandbox",
    "LifecycleMixin",
    "LifecycleState",
    "ScalingBounds",
    "CheckpointInfo",
    "sandbox_from_checkpoint",
    "CubeSandboxAdapter",
]


def __getattr__(name: str):
    if name in ("DockerSandbox", "RemoteSandbox", "CubeSandboxAdapter"):
        from . import providers

        return getattr(providers, name)
    if name in ("DurableDockerSandbox",):
        from .durable_docker import DurableDockerSandbox

        return DurableDockerSandbox
    if name in (
        "LifecycleMixin",
        "LifecycleState",
        "ScalingBounds",
        "CheckpointInfo",
        "sandbox_from_checkpoint",
    ):
        from . import lifecycle

        return getattr(lifecycle, name)
    raise AttributeError(f"module 'tvastar.sandbox' has no attribute {name!r}")
