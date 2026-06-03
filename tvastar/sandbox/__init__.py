"""Sandbox layer: pluggable execution environments.

Built-ins are imported eagerly (zero deps). External provider adapters
(Docker/Remote) are lazy so importing this package never requires docker etc.
"""

from __future__ import annotations

from .base import ExecResult, Sandbox, SecurityPolicy
from .local import LocalSandbox, default_local_shell
from .virtual import VirtualSandbox

__all__ = [
    "Sandbox",
    "ExecResult",
    "SecurityPolicy",
    "VirtualSandbox",
    "LocalSandbox",
    "default_local_shell",
    "DockerSandbox",
    "RemoteSandbox",
]


def __getattr__(name: str):
    if name in ("DockerSandbox", "RemoteSandbox"):
        from . import providers

        return getattr(providers, name)
    raise AttributeError(f"module 'tvastar.sandbox' has no attribute {name!r}")
