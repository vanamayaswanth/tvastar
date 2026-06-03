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
from pathlib import Path
from typing import Optional

from ..filesystem.local import LocalFileSystem
from .base import ExecResult, Sandbox, SecurityPolicy, _truncate


class LocalSandbox(Sandbox):
    def __init__(
        self,
        root: str | Path = ".tvastar-workspace",
        *,
        policy: Optional[SecurityPolicy] = None,
        shell: Optional[str] = None,
    ):
        self.fs = LocalFileSystem(root)
        self.root = self.fs.root
        self.policy = policy or SecurityPolicy()
        # Use bash where available, else the platform default shell.
        self._shell = shell

    async def exec(
        self,
        cmd: str,
        *,
        env: Optional[dict[str, str]] = None,
        cwd: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> ExecResult:
        self.policy.check(cmd)
        timeout = timeout or self.policy.timeout_seconds
        workdir = self.root if not cwd else (self.root / cwd).resolve()

        run_env = dict(os.environ)
        if not self.policy.network:
            # Best-effort: many tools honor these to avoid egress.
            run_env.update({"http_proxy": "", "https_proxy": "", "no_proxy": "*"})
        if env:
            run_env.update(env)

        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(workdir),
            env=run_env,
            executable=self._shell,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:  # pragma: no cover
                pass
            await proc.wait()
            return ExecResult(124, "", f"timed out after {timeout}s", timed_out=True)

        limit = self.policy.max_output_bytes
        return ExecResult(
            exit_code=proc.returncode or 0,
            stdout=_truncate(out.decode("utf-8", "replace"), limit),
            stderr=_truncate(err.decode("utf-8", "replace"), limit),
        )


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
