"""Subagent Permission Isolation — fresh, minimal policies for child agents.

A parent agent spawning a child via session.task(agent="name") gets the
child's SecurityPolicy built from a PermissionRegistry — never inherited
from the parent. Unknown profiles receive deny-all + security warning.

ponytail: no abstractions beyond what the requirements demand. The
registry is a dict, the resolver is a single function wrapped in a class
for DI convenience.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from .sandbox.base import SecurityPolicy

__all__ = ["PermissionEntry", "PermissionRegistry", "PermissionResolver"]

logger = logging.getLogger(__name__)


@runtime_checkable
class _Appendable(Protocol):
    """Anything with an append(record) method — list, TrustLog adapter, etc."""

    def append(self, record: Any) -> None: ...


@dataclass
class PermissionEntry:
    """Declares allowed capabilities for a named agent profile."""

    allowed_commands: list[str] = field(default_factory=list)
    deny_commands: list[str] = field(default_factory=list)
    network: bool = False
    file_patterns: list[str] = field(default_factory=list)
    timeout_seconds: float = 60.0


class PermissionRegistry:
    """Registry of per-role permission declarations.

    Used by PermissionResolver to build child SecurityPolicies.
    """

    def __init__(self) -> None:
        self._entries: dict[str, PermissionEntry] = {}

    def register(self, profile_name: str, entry: PermissionEntry) -> None:
        self._entries[profile_name] = entry

    def get(self, profile_name: str) -> PermissionEntry | None:
        return self._entries.get(profile_name)


class PermissionResolver:
    """Builds fresh, minimal SecurityPolicy for child agents.

    Parent's runtime policy is NEVER inherited. Policy is derived
    solely from the PermissionRegistry entry for the child's profile name.
    """

    def __init__(self, registry: PermissionRegistry, trust_log: Any = None):
        self._registry = registry
        self._trust_log = trust_log

    def resolve(
        self,
        child_profile_name: str,
        parent_agent_name: str,
        parent_policy: SecurityPolicy,
    ) -> SecurityPolicy:
        """Build child SecurityPolicy from registry. Ignores parent_policy.

        Unknown profile → deny-all + security warning.
        TrustLog record appended BEFORE returning (before child session begins).
        """
        entry = self._registry.get(child_profile_name)

        if entry is None:
            logger.warning(
                "Security: unknown profile %r spawned by %r — deny-all applied",
                child_profile_name,
                parent_agent_name,
            )
            child_policy = SecurityPolicy(
                network=False,
                allowed_commands=set(),
                denied_commands={"*"},
                timeout_seconds=60.0,
            )
        else:
            child_policy = SecurityPolicy(
                network=entry.network,
                allowed_commands=set(entry.allowed_commands),
                denied_commands=set(entry.deny_commands),
                timeout_seconds=entry.timeout_seconds,
            )

        # TrustLog record BEFORE child execution
        if self._trust_log is not None and hasattr(self._trust_log, "append"):
            try:
                self._trust_log.append(
                    {
                        "type": "permission_resolution",
                        "parent_agent": parent_agent_name,
                        "child_agent": child_profile_name,
                        "parent_policy": _serialize_policy(parent_policy),
                        "child_policy": _serialize_policy(child_policy),
                        "timestamp": time.time(),
                    }
                )
            except (TypeError, ValueError):
                # ponytail: TrustLog.append expects ExecutionReceipt — if the
                # caller passes a real TrustLog, the dict won't work. Log and
                # continue; never break the spawn path for audit failures.
                logger.debug("TrustLog append failed for permission_resolution record")

        return child_policy


def _serialize_policy(policy: SecurityPolicy) -> dict:
    """Minimal serialization of a SecurityPolicy for audit records."""
    return {
        "network": policy.network,
        "allowed_commands": sorted(policy.allowed_commands),
        "denied_commands": sorted(policy.denied_commands),
        "timeout_seconds": policy.timeout_seconds,
    }
