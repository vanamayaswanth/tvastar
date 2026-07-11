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
from .lifecycle import CheckpointInfo, LifecycleMixin, ScalingBounds


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

    async def fork(self, name: str):
        raise NotImplementedError("DockerSandbox uses --rm and cannot fork — use DurableDockerSandbox")


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


class CubeSandboxAdapter(LifecycleMixin, Sandbox):
    """Drop-in Sandbox implementation backed by self-hosted CubeSandbox.

    Uses stdlib urllib.request — zero additional dependencies.
    Reads endpoint from CUBESANDBOX_URL environment variable.
    """

    _TIMEOUT = 5.0  # connection timeout in seconds

    def __init__(
        self,
        *,
        policy: Optional[SecurityPolicy] = None,
        fs: Optional[FileSystem] = None,
        scaling_bounds: Optional[ScalingBounds] = None,
        event_bus=None,
    ):
        import os

        url = os.environ.get("CUBESANDBOX_URL")
        if not url:
            raise SandboxError("CUBESANDBOX_URL environment variable is not set")
        self._url = url.rstrip("/")
        self.policy = policy or SecurityPolicy()
        self.fs = fs or VirtualFileSystem()
        super().__init__(scaling_bounds=scaling_bounds, event_bus=event_bus)
        self._session_id: Optional[str] = None

    async def start(self) -> None:
        """Create a CubeSandbox session."""
        try:
            resp = self._http_post("/sessions", {})
            self._session_id = resp.get("id")
        except SandboxError:
            raise
        except Exception as exc:
            if "timed out" in str(exc).lower() or "timeout" in str(exc).lower():
                raise SandboxError("connection timeout") from exc
            raise SandboxError(f"CubeSandbox start failed: {exc}") from exc

    async def stop(self) -> None:
        """Terminate the CubeSandbox session. Silent if unreachable."""
        if self._session_id:
            try:
                self._http_post(f"/sessions/{self._session_id}/stop", {})
            except Exception:
                pass  # SHALL NOT raise if endpoint unreachable
            self._session_id = None

    async def exec(
        self,
        cmd: str,
        *,
        env: Optional[dict[str, str]] = None,
        cwd: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> ExecResult:
        """Execute command via CubeSandbox HTTP API."""
        self.policy.check(cmd)
        if not self._session_id:
            await self.start()
        try:
            resp = self._http_post(
                f"/sessions/{self._session_id}/exec",
                {
                    "cmd": cmd,
                    "env": env,
                    "cwd": cwd,
                    "timeout": timeout or self.policy.timeout_seconds,
                },
            )
            limit = self.policy.max_output_bytes
            return ExecResult(
                resp.get("exit_code", 1),
                _truncate(resp.get("stdout", ""), limit),
                _truncate(resp.get("stderr", ""), limit),
            )
        except SandboxError:
            raise
        except Exception as exc:
            if "timed out" in str(exc).lower() or "timeout" in str(exc).lower():
                raise SandboxError("connection timeout") from exc
            raise SandboxError(f"CubeSandbox exec failed: {exc}") from exc

    def _http_post(self, path: str, body: dict) -> dict:
        """Synchronous HTTP POST using stdlib urllib.request."""
        import json
        from urllib.error import URLError
        from urllib.request import Request, urlopen

        url = self._url + path
        data = json.dumps(body).encode("utf-8")
        req = Request(url, data=data, headers={"Content-Type": "application/json"})
        try:
            with urlopen(req, timeout=self._TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except URLError as exc:
            if "timed out" in str(exc).lower() or "timeout" in str(exc).lower():
                raise SandboxError("connection timeout") from exc
            raise SandboxError(f"CubeSandbox HTTP error: {exc}") from exc
        except TimeoutError as exc:
            raise SandboxError("connection timeout") from exc

    def _http_get(self, path: str) -> list | dict:
        """Synchronous HTTP GET using stdlib urllib.request."""
        import json
        from urllib.error import URLError
        from urllib.request import Request, urlopen

        url = self._url + path
        req = Request(url, headers={"Accept": "application/json"})
        try:
            with urlopen(req, timeout=self._TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except URLError as exc:
            if "timed out" in str(exc).lower() or "timeout" in str(exc).lower():
                raise SandboxError("connection timeout") from exc
            raise SandboxError(f"CubeSandbox HTTP error: {exc}") from exc
        except TimeoutError as exc:
            raise SandboxError("connection timeout") from exc

    def _http_request(self, method: str, path: str) -> dict:
        """Synchronous HTTP request (GET/DELETE/etc.) using stdlib urllib.request."""
        import json
        from urllib.error import URLError
        from urllib.request import Request, urlopen

        url = self._url + path
        req = Request(url, method=method, headers={"Accept": "application/json"})
        try:
            with urlopen(req, timeout=self._TIMEOUT) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body) if body else {}
        except URLError as exc:
            if "timed out" in str(exc).lower() or "timeout" in str(exc).lower():
                raise SandboxError("connection timeout") from exc
            raise SandboxError(f"CubeSandbox HTTP error: {exc}") from exc
        except TimeoutError as exc:
            raise SandboxError("connection timeout") from exc

    # --- Lifecycle backend hooks ---

    async def _do_hibernate(self) -> None:
        await asyncio.to_thread(self._http_post, f"/sessions/{self._session_id}/hibernate", {})

    async def _do_wake(self) -> None:
        await asyncio.to_thread(self._http_post, f"/sessions/{self._session_id}/wake", {})

    async def _do_scale(self, memory_mb: int, cpu_count: int) -> None:
        await asyncio.to_thread(self._http_post, f"/sessions/{self._session_id}/scale", {"memory_mb": memory_mb, "cpu_count": cpu_count})

    async def _do_checkpoint(self, name: str) -> str:
        resp = await asyncio.to_thread(self._http_post, f"/sessions/{self._session_id}/checkpoint", {"name": name})
        return resp["checkpoint_id"]

    async def _do_fork(self, name: str) -> "Sandbox":
        resp = await asyncio.to_thread(self._http_post, f"/sessions/{self._session_id}/fork", {"name": name})
        new_adapter = CubeSandboxAdapter(policy=self.policy, fs=self.fs)
        new_adapter._session_id = resp["session_id"]
        return new_adapter

    async def _do_delete_checkpoint(self, checkpoint_id: str) -> None:
        await asyncio.to_thread(self._http_request, "DELETE", f"/sessions/{self._session_id}/checkpoints/{checkpoint_id}")

    async def _do_list_checkpoints(self) -> list[CheckpointInfo]:
        resp = await asyncio.to_thread(self._http_get, f"/sessions/{self._session_id}/checkpoints")
        return [
            CheckpointInfo(
                checkpoint_id=c["checkpoint_id"],
                name=c["name"],
                container_id=c.get("container_id", self._session_id),
                timestamp=c["timestamp"],
            )
            for c in resp
        ]


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
