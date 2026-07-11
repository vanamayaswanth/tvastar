"""Durable Docker sandbox — persistent containers with lifecycle support.

Unlike :class:`DockerSandbox` (which uses ``--rm``), this class runs containers
without ``--rm`` so they survive stop/start cycles and support hibernate, wake,
checkpoint, and fork via Docker's CRIU integration.

Fork semantics: filesystem-only via ``docker commit``. Process state is NOT
preserved. Use CubeSandboxAdapter for full-state fork.
"""

from __future__ import annotations

import asyncio
import os
from typing import Optional

from ..errors import SandboxError
from ..filesystem.base import FileSystem
from ..filesystem.virtual import VirtualFileSystem
from .base import ExecResult, Sandbox, SecurityPolicy, _truncate
from .lifecycle import CheckpointInfo, LifecycleMixin, LifecycleState, ScalingBounds


class DurableDockerSandbox(LifecycleMixin, Sandbox):
    """Docker sandbox without --rm — supports hibernate, wake, checkpoint, fork.

    Fork semantics: filesystem-only via ``docker commit``. Process state is NOT
    preserved. Use CubeSandboxAdapter for full-state fork.
    """

    def __init__(
        self,
        image: str = "python:3.12-slim",
        *,
        policy: Optional[SecurityPolicy] = None,
        workdir: str = "/workspace",
        fs: Optional[FileSystem] = None,
        durable: bool = True,
        container_id_path: Optional[str] = None,
        scaling_bounds: Optional[ScalingBounds] = None,
        event_bus=None,
    ) -> None:
        super().__init__(scaling_bounds=scaling_bounds, event_bus=event_bus)
        self.image = image
        self.workdir = workdir
        self.policy = policy or SecurityPolicy()
        self.fs = fs or VirtualFileSystem()
        self._durable = durable
        self._container_id_path = container_id_path
        self._cid: Optional[str] = None
        self._checkpoints: list[CheckpointInfo] = []
        self._current_memory_mb: Optional[int] = None
        self._current_cpu_count: Optional[int] = None
        self._hibernate_checkpoint: Optional[str] = None
        # Reconnection: if container ID file exists, reconnect
        if container_id_path and os.path.isfile(container_id_path):
            with open(container_id_path) as f:
                cid = f.read().strip()
            if cid:
                self._cid = cid

    async def start(self) -> None:
        """Start a container with ``docker run -d`` (no --rm). Persists container ID."""
        if self._cid:
            # Already have a container (reconnected or running)
            self._lifecycle_state = LifecycleState.running
            return
        net = "bridge" if self.policy.network else "none"
        args = ["docker", "run", "-d", "--network", net, "-w", self.workdir, self.image, "sleep", "infinity"]
        if not self._durable:
            args.insert(3, "--rm")
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        out, err = await proc.communicate()
        if proc.returncode != 0:
            raise SandboxError(f"docker run failed: {err.decode().strip()}")
        self._cid = out.decode().strip()
        # Persist container ID
        if self._container_id_path:
            os.makedirs(os.path.dirname(self._container_id_path) or ".", exist_ok=True)
            with open(self._container_id_path, "w") as f:
                f.write(self._cid)
        self._lifecycle_state = LifecycleState.running

    async def stop(self) -> None:
        """Stop the container (it persists on disk for later reconnection)."""
        if not self._cid:
            return
        proc = await asyncio.create_subprocess_exec(
            "docker", "stop", self._cid,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        prev = self._lifecycle_state
        self._lifecycle_state = LifecycleState.stopped
        self._emit_transition(prev, self._lifecycle_state)

    async def destroy(self) -> None:
        """Explicitly remove the container with ``docker rm -f``."""
        if not self._cid:
            return
        proc = await asyncio.create_subprocess_exec(
            "docker", "rm", "-f", self._cid,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        self._cid = None
        # Clean up persisted ID
        if self._container_id_path and os.path.isfile(self._container_id_path):
            os.remove(self._container_id_path)

    async def exec(self, cmd: str, *, env=None, cwd=None, timeout=None) -> ExecResult:
        """Guarded exec with docker execution.

        State guard rejects calls when hibernated/stopped or draining.
        In-flight counter tracks active calls for drain-before-hibernate.
        """
        # State guard from LifecycleMixin
        if self._lifecycle_state in (LifecycleState.hibernated, LifecycleState.stopped):
            raise SandboxError(
                f"Cannot exec: sandbox is '{self._lifecycle_state.value}', requires 'running'"
            )
        if self._draining:
            raise SandboxError("Cannot exec: sandbox is entering hibernation")
        self._inflight_count += 1
        try:
            # Actual docker exec
            self.policy.check(cmd)
            if not self._cid:
                await self.start()
            timeout = timeout or self.policy.timeout_seconds
            env_flags: list[str] = []
            for k, v in (env or {}).items():
                env_flags += ["-e", f"{k}={v}"]
            args = [
                "docker", "exec", *env_flags,
                "-w", cwd or self.workdir,
                self._cid, "sh", "-c", cmd,
            ]
            proc = await asyncio.create_subprocess_exec(
                *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            try:
                out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return ExecResult(124, "", "timed out", timed_out=True)
            limit = self.policy.max_output_bytes
            return ExecResult(
                proc.returncode or 0,
                _truncate(out.decode("utf-8", "replace"), limit),
                _truncate(err.decode("utf-8", "replace"), limit),
            )
        finally:
            self._inflight_count -= 1

    # --- Lifecycle backend: hibernate / wake ---

    async def _do_hibernate(self) -> None:
        """docker checkpoint create <name>; docker stop."""
        checkpoint_name = f"hibernate-{self._cid[:12]}"
        proc = await asyncio.create_subprocess_exec(
            "docker", "checkpoint", "create", self._cid, checkpoint_name,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, err = await proc.communicate()
        if proc.returncode != 0:
            raise SandboxError(f"docker checkpoint create failed: {err.decode().strip()}")
        self._hibernate_checkpoint = checkpoint_name
        # Stop the container to release compute resources
        proc = await asyncio.create_subprocess_exec(
            "docker", "stop", self._cid,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

    async def _do_wake(self) -> None:
        """docker start --checkpoint <name>."""
        checkpoint_name = getattr(self, "_hibernate_checkpoint", None)
        if not checkpoint_name:
            raise SandboxError("No hibernate checkpoint found")
        proc = await asyncio.create_subprocess_exec(
            "docker", "start", "--checkpoint", checkpoint_name, self._cid,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, err = await proc.communicate()
        if proc.returncode != 0:
            raise SandboxError(f"docker start --checkpoint failed: {err.decode().strip()}")
        self._hibernate_checkpoint = None

    # --- Lifecycle backend: checkpoint / delete / list ---

    async def _do_checkpoint(self, name: str) -> str:
        """docker checkpoint create --leave-running. Returns {cid}:{name}."""
        if any(cp.name == name for cp in self._checkpoints):
            raise SandboxError(f"Checkpoint name '{name}' already exists for this container")
        proc = await asyncio.create_subprocess_exec(
            "docker", "checkpoint", "create", "--leave-running", self._cid, name,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, err = await proc.communicate()
        if proc.returncode != 0:
            raise SandboxError(f"docker checkpoint create failed: {err.decode().strip()}")
        import time
        checkpoint_id = f"{self._cid}:{name}"
        self._checkpoints.append(CheckpointInfo(
            checkpoint_id=checkpoint_id, name=name, container_id=self._cid, timestamp=time.time()
        ))
        return checkpoint_id

    async def _do_delete_checkpoint(self, checkpoint_id: str) -> None:
        """docker checkpoint rm."""
        idx = next((i for i, cp in enumerate(self._checkpoints) if cp.checkpoint_id == checkpoint_id), None)
        if idx is None:
            raise SandboxError(f"Checkpoint '{checkpoint_id}' not found")
        cp = self._checkpoints[idx]
        proc = await asyncio.create_subprocess_exec(
            "docker", "checkpoint", "rm", self._cid, cp.name,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, err = await proc.communicate()
        if proc.returncode != 0:
            raise SandboxError(f"docker checkpoint rm failed: {err.decode().strip()}")
        self._checkpoints.pop(idx)

    async def _do_list_checkpoints(self) -> list[CheckpointInfo]:
        """Return in-memory checkpoint metadata list."""
        return list(self._checkpoints)

    # --- Lifecycle backend: fork ---

    async def _do_fork(self, name: str) -> "DurableDockerSandbox":
        """docker commit -> new DurableDockerSandbox from committed image.

        Fork is filesystem-only — process state is NOT preserved.
        Use CubeSandboxAdapter for full-state fork.
        """
        image_tag = f"forge-fork:{name}"
        proc = await asyncio.create_subprocess_exec(
            "docker", "commit", self._cid, image_tag,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, err = await proc.communicate()
        if proc.returncode != 0:
            raise SandboxError(f"docker commit failed: {err.decode().strip()}")
        forked = DurableDockerSandbox(
            image=image_tag,
            policy=self.policy,
            workdir=self.workdir,
            fs=self.fs,
            durable=self._durable,
            scaling_bounds=self._scaling_bounds,
            event_bus=self._event_bus,
        )
        await forked.start()
        return forked

    # --- Lifecycle backend: scale ---

    async def _do_scale(self, memory_mb: int, cpu_count: int) -> None:
        """docker update --memory {m}m --cpus {c}."""
        proc = await asyncio.create_subprocess_exec(
            "docker", "update", f"--memory={memory_mb}m", f"--cpus={cpu_count}", self._cid,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, err = await proc.communicate()
        if proc.returncode != 0:
            raise SandboxError(f"docker update failed: {err.decode().strip()}")
        self._current_memory_mb = memory_mb
        self._current_cpu_count = cpu_count
