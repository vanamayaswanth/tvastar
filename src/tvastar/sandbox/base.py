"""Sandbox layer — where agents run bash and touch files.

Design goal: **pluggable**. Tvastar ships lightweight built-ins (virtual,
local-subprocess) but the interface is deliberately small so high-performance
external providers (Docker, E2B, Daytona, Firecracker, ...) plug in as adapters
without the rest of the harness caring which one is active.

A Sandbox exposes:
* ``exec(cmd)``    — run a shell command, return ExecResult
* ``fs``           — a FileSystem rooted in the sandbox
* lifecycle: ``start`` / ``stop`` (async, idempotent)

Security is policy-driven via :class:`SecurityPolicy`, enforced by built-ins
and recommended for adapters.
"""

from __future__ import annotations

import abc
import fnmatch
import shlex
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Optional

from ..errors import SecurityViolation
from ..filesystem.base import FileSystem


@dataclass
class ExecResult:
    exit_code: int
    stdout: str
    stderr: str = ""
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def render(self) -> str:
        """Compact representation for feeding back to the model."""
        parts = []
        if self.stdout:
            parts.append(self.stdout.rstrip())
        if self.stderr:
            parts.append("[stderr]\n" + self.stderr.rstrip())
        if self.timed_out:
            parts.append("[timed out]")
        if self.exit_code != 0:
            parts.append(f"[exit {self.exit_code}]")
        return "\n".join(parts) if parts else "[no output]"


@dataclass
class CredentialFilter:
    """Strips secret-looking env vars from the subprocess environment before exec.

    Any env var whose name matches one of the glob patterns (case-insensitive)
    is removed so the agent cannot read or leak it.

    Default patterns cover the most common secret naming conventions.
    Pass ``patterns=[]`` to disable all filtering.
    """

    patterns: list[str] = field(
        default_factory=lambda: [
            "*_KEY",
            "*_TOKEN",
            "*_SECRET",
            "*_PASSWORD",
            "*_PASS",
            "*_CREDENTIAL",
            "*_CREDENTIALS",
        ]
    )

    def filter_env(self, env: dict[str, str]) -> dict[str, str]:
        """Return a copy of *env* with matching keys removed."""
        upper_pats = [p.upper() for p in self.patterns]
        return {
            k: v
            for k, v in env.items()
            if not any(fnmatch.fnmatch(k.upper(), pat) for pat in upper_pats)
        }


@dataclass
class ResourcePolicy:
    """Hard resource limits applied per command execution.

    Works cross-platform for ``max_cpu_seconds`` and ``max_output_chars``.
    ``max_memory_mb`` is enforced via ``ulimit -v`` on Linux/macOS and
    silently ignored on Windows.
    ``allowed_domains`` documents intent; wire a proxy or firewall rule
    for real network enforcement.
    """

    max_cpu_seconds: float = 30.0
    max_memory_mb: int | None = None
    max_output_chars: int = 50_000
    allowed_domains: list[str] = field(default_factory=list)


@dataclass
class AuditEntry:
    """One record in a sandbox audit log."""

    command: str
    timestamp: float
    allowed: bool
    violation: str | None = None
    exit_code: int | None = None
    duration_ms: float | None = None

    @classmethod
    def blocked(cls, command: str, reason: str) -> "AuditEntry":
        return cls(command=command, timestamp=time.time(), allowed=False, violation=reason)

    @classmethod
    def executed(cls, command: str, exit_code: int, duration_ms: float) -> "AuditEntry":
        return cls(
            command=command,
            timestamp=time.time(),
            allowed=True,
            exit_code=exit_code,
            duration_ms=duration_ms,
        )


@dataclass
class SecurityPolicy:
    """Allow/deny rules applied before a command runs.

    Defaults are conservative-but-usable. Set ``network=False`` and supply a
    ``denied_commands`` set to tighten further, or ``allowed_commands`` to flip
    to an allowlist model.
    """

    network: bool = True
    max_output_bytes: int = 256_000
    timeout_seconds: float = 60.0
    denied_commands: set[str] = field(default_factory=lambda: {"shutdown", "reboot", "mkfs", "dd"})
    #: if non-empty, ONLY these top-level commands are permitted (allowlist)
    allowed_commands: set[str] = field(default_factory=set)
    #: substrings that, if present anywhere in the command, block it
    denied_substrings: set[str] = field(default_factory=lambda: {"rm -rf /", ":(){:|:&};:"})

    def check(self, cmd: str) -> None:
        """Raise SecurityViolation if the command is disallowed."""
        for bad in self.denied_substrings:
            if bad in cmd:
                raise SecurityViolation(f"Command blocked by policy: {bad!r}")
        head = _first_command(cmd)
        if self.allowed_commands and head not in self.allowed_commands:
            raise SecurityViolation(
                f"Command '{head}' not in allowlist {sorted(self.allowed_commands)}"
            )
        if head in self.denied_commands:
            raise SecurityViolation(f"Command '{head}' is denied by policy")


def _first_command(cmd: str) -> str:
    try:
        tokens = shlex.split(cmd, posix=True)
    except ValueError:
        tokens = cmd.split()
    return tokens[0] if tokens else ""


class Sandbox(abc.ABC):
    """Abstract execution environment."""

    fs: FileSystem
    policy: SecurityPolicy

    async def start(self) -> None:  # idempotent; override if needed
        return None

    async def stop(self) -> None:  # idempotent; override if needed
        return None

    @abc.abstractmethod
    async def exec(
        self,
        cmd: str,
        *,
        env: Optional[dict[str, str]] = None,
        cwd: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> ExecResult: ...

    async def __aenter__(self) -> "Sandbox":
        await self.start()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.stop()


def _truncate(data: str, limit: int) -> str:
    if len(data) <= limit:
        return data
    return data[:limit] + f"\n…[truncated {len(data) - limit} bytes]"


def clamp_output(parts: Iterable[str], limit: int) -> list[str]:
    return [_truncate(p, limit) for p in parts]
