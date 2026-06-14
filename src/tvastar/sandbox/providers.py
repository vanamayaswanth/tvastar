"""Adapters for external, high-performance sandbox providers.

The whole point of the :class:`Sandbox` interface is that you can swap in a
faster / more isolated backend without touching agent code. This module ships:

* :class:`DockerSandbox` — runs commands in a container via the ``docker`` CLI
  (no Python SDK dependency). Good isolation, ubiquitous.
* :class:`RemoteSandbox` — a generic adapter over any provider exposing an
  async ``exec``/``upload``/``download`` client. Use this to wire E2B, Daytona,
  Modal, Firecracker, etc. with a ~20-line client shim.

These are optional; nothing imports them unless you do.
"""

from __future__ import annotations

import asyncio
import shlex
from typing import Optional, Protocol

from ..errors import SandboxError
from ..filesystem.base import FileSystem
from ..filesystem.virtual import VirtualFileSystem
from .base import ExecResult, Sandbox, SecurityPolicy, _truncate


class DockerSandbox(Sandbox):
    """Run commands inside a Docker container via the docker CLI.

    A long-lived container is started on ``start()`` and removed on ``stop()``.
    """

    def __init__(
        self,
        image: str = "python:3.12-slim",
        *,
        policy: Optional[SecurityPolicy] = None,
        workdir: str = "/workspace",
        fs: Optional[FileSystem] = None,
    ):
        self.image = image
        self.workdir = workdir
        self.policy = policy or SecurityPolicy()
        self.fs = fs or VirtualFileSystem()  # host-side staging fs
        self._cid: Optional[str] = None

    async def start(self) -> None:
        if self._cid:
            return
        net = "bridge" if self.policy.network else "none"
        args = [
            "docker",
            "run",
            "-d",
            "--rm",
            "--network",
            net,
            "-w",
            self.workdir,
            self.image,
            "sleep",
            "infinity",
        ]
        rc, out, err = await _run(args)
        if rc != 0:
            raise SandboxError(f"docker run failed: {err.strip()}")
        self._cid = out.strip()

    async def stop(self) -> None:
        if self._cid:
            await _run(["docker", "rm", "-f", self._cid])
            self._cid = None

    async def exec(
        self,
        cmd: str,
        *,
        env: Optional[dict[str, str]] = None,
        cwd: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> ExecResult:
        self.policy.check(cmd)
        if not self._cid:
            await self.start()
        timeout = timeout or self.policy.timeout_seconds
        env_flags: list[str] = []
        for k, v in (env or {}).items():
            env_flags += ["-e", f"{k}={v}"]
        args = [
            "docker",
            "exec",
            *env_flags,
            "-w",
            cwd or self.workdir,
            self._cid,
            "sh",
            "-c",
            cmd,
        ]
        rc, out, err = await _run(args, timeout=timeout)
        limit = self.policy.max_output_bytes
        if rc == 124:
            return ExecResult(124, "", "timed out", timed_out=True)
        return ExecResult(rc, _truncate(out, limit), _truncate(err, limit))


class RemoteClient(Protocol):
    """Minimal contract an external provider client must satisfy."""

    async def exec(self, cmd: str, timeout: float) -> tuple[int, str, str]: ...


class RemoteSandbox(Sandbox):
    """Generic adapter over any provider exposing the RemoteClient protocol.

    Example (pseudo)::

        class E2BClient:
            async def exec(self, cmd, timeout):
                r = await self._sbx.commands.run(cmd, timeout=timeout)
                return r.exit_code, r.stdout, r.stderr

        sandbox = RemoteSandbox(E2BClient())
    """

    def __init__(
        self,
        client: RemoteClient,
        *,
        policy: Optional[SecurityPolicy] = None,
        fs: Optional[FileSystem] = None,
    ):
        self.client = client
        self.policy = policy or SecurityPolicy()
        self.fs = fs or VirtualFileSystem()

    async def start(self) -> None:
        start = getattr(self.client, "start", None)
        if start:
            await start()

    async def stop(self) -> None:
        stop = getattr(self.client, "stop", None)
        if stop:
            await stop()

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
        if env or cwd:
            prefix = ""
            if cwd:
                prefix += f"cd {shlex.quote(cwd)} && "
            for k, v in (env or {}).items():
                prefix += f"export {k}={shlex.quote(v)}; "
            cmd = prefix + cmd
        try:
            rc, out, err = await self.client.exec(cmd, timeout)
        except Exception as e:  # pragma: no cover - provider specific
            raise SandboxError(f"remote exec failed: {e}") from e
        limit = self.policy.max_output_bytes
        return ExecResult(rc, _truncate(out, limit), _truncate(err, limit))


async def _run(args: list[str], timeout: Optional[float] = None) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return 124, "", "timed out"
    return (
        proc.returncode or 0,
        out.decode("utf-8", "replace"),
        err.decode("utf-8", "replace"),
    )
