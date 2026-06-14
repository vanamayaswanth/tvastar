"""LocalSandbox — runs real shell commands in a subprocess, jailed to a root.

For when you need actual tooling (python, git, compilers) on the host. Commands
run with cwd pinned to the sandbox root, an enforced timeout, and output caps.
Use a :class:`SecurityPolicy` allowlist for untrusted models. For full
isolation prefer a container adapter (see ``providers/``).
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path
from typing import Optional

from ..filesystem.local import LocalFileSystem
from .base import (
    AuditEntry,
    CredentialFilter,
    ExecResult,
    ResourcePolicy,
    Sandbox,
    SecurityPolicy,
    _truncate,
)


def _supports_ulimit() -> bool:
    return sys.platform != "win32"


class LocalSandbox(Sandbox):
    def __init__(
        self,
        root: str | Path = ".tvastar-workspace",
        *,
        policy: Optional[SecurityPolicy] = None,
        resources: Optional[ResourcePolicy] = None,
        credential_filter: Optional[CredentialFilter] = None,
        shell: Optional[str] = None,
    ):
        self.fs = LocalFileSystem(root)
        self.root = self.fs.root
        self.policy = policy or SecurityPolicy()
        self.resources = resources or ResourcePolicy()
        self.credential_filter = credential_filter
        self._shell = shell
        self.audit: list[AuditEntry] = []

    async def exec(
        self,
        cmd: str,
        *,
        env: Optional[dict[str, str]] = None,
        cwd: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> ExecResult:
        # Security check first — log blocked commands
        try:
            self.policy.check(cmd)
        except Exception as exc:
            self.audit.append(AuditEntry.blocked(cmd, str(exc)))
            raise

        # Resource limits: caller timeout wins if tighter than resource policy
        cpu_limit = self.resources.max_cpu_seconds
        effective_timeout = min(t for t in [timeout, self.policy.timeout_seconds, cpu_limit] if t)
        workdir = self.root if not cwd else (self.root / cwd).resolve()

        run_env = dict(os.environ)
        if not self.policy.network:
            run_env.update({"http_proxy": "", "https_proxy": "", "no_proxy": "*"})
        if env:
            run_env.update(env)
        if self.credential_filter is not None:
            run_env = self.credential_filter.filter_env(run_env)

        # Memory limit via ulimit on Linux/macOS (silent no-op on Windows)
        actual_cmd = cmd
        if self.resources.max_memory_mb is not None and _supports_ulimit():
            kb = self.resources.max_memory_mb * 1024
            actual_cmd = f"ulimit -v {kb} 2>/dev/null; {cmd}"

        t0 = time.monotonic()
        proc = await asyncio.create_subprocess_shell(
            actual_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(workdir),
            env=run_env,
            executable=self._shell,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=effective_timeout)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:  # pragma: no cover
                pass
            await proc.wait()
            elapsed = round((time.monotonic() - t0) * 1000, 1)
            self.audit.append(AuditEntry.executed(cmd, exit_code=124, duration_ms=elapsed))
            return ExecResult(124, "", f"timed out after {effective_timeout}s", timed_out=True)

        elapsed = round((time.monotonic() - t0) * 1000, 1)
        exit_code = proc.returncode or 0
        self.audit.append(AuditEntry.executed(cmd, exit_code=exit_code, duration_ms=elapsed))

        limit = self.resources.max_output_chars
        return ExecResult(
            exit_code=exit_code,
            stdout=_truncate(out.decode("utf-8", "replace"), limit),
            stderr=_truncate(err.decode("utf-8", "replace"), limit),
        )


    # ------------------------------------------------------------------
    # Transactional snapshot / restore (< 500 ms NFR for local FS)
    # ------------------------------------------------------------------

    def snapshot(self) -> dict[str, bytes]:
        """Return all files under root as ``{relative_posix_path: raw_bytes}``."""
        snap: dict[str, bytes] = {}
        root = Path(self.root)
        if not root.exists():
            return snap
        for p in root.rglob("*"):
            if p.is_file():
                rel = p.relative_to(root).as_posix()
                snap[rel] = p.read_bytes()
        return snap

    def restore(self, snap: dict[str, bytes]) -> None:
        """Restore workspace to a previously snapshotted state.

        Files present in the snapshot are (re-)created; files *not* in the
        snapshot are deleted; directory structure is rebuilt as needed.
        """
        root = Path(self.root)
        # Remove all current files
        if root.exists():
            for p in root.rglob("*"):
                if p.is_file():
                    p.unlink()
            # Prune empty directories bottom-up (skip root itself)
            for p in sorted(root.rglob("*"), key=lambda x: len(x.parts), reverse=True):
                if p.is_dir() and p != root:
                    try:
                        p.rmdir()
                    except OSError:
                        pass
        # Recreate snapshotted files
        for rel, data in snap.items():
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)


def default_local_shell() -> Optional[str]:
    """Prefer bash if present (incl. Git Bash on Windows), else None."""
    if sys.platform == "win32":
        for candidate in (
            r"C:\Program Files\Git\bin\bash.exe",
            r"C:\Windows\System32\bash.exe",
        ):
            if Path(candidate).exists():
                return candidate
        return None
    return "/bin/bash"
