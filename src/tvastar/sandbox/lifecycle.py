"""Durable compute lifecycle primitives.

Data models and mixin for sandbox lifecycle management — hibernate, wake,
scale, checkpoint, and fork. The LifecycleMixin is opt-in; existing sandboxes
remain unchanged.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .base import Sandbox

from ..errors import SandboxError, SecurityViolation


class LifecycleState(Enum):
    created = "created"
    running = "running"
    hibernated = "hibernated"
    stopped = "stopped"


@dataclass
class ScalingBounds:
    """Min/max ranges for runtime scaling. Separate from ResourcePolicy."""

    min_memory_mb: int
    max_memory_mb: int
    min_cpu_count: int
    max_cpu_count: int

    def __post_init__(self) -> None:
        if self.min_memory_mb > self.max_memory_mb:
            raise ValueError("min_memory_mb must be <= max_memory_mb")
        if self.min_cpu_count > self.max_cpu_count:
            raise ValueError("min_cpu_count must be <= max_cpu_count")
        if self.min_memory_mb < 0 or self.min_cpu_count < 0:
            raise ValueError("bounds must be non-negative")


@dataclass
class CheckpointInfo:
    """Metadata for a named checkpoint."""

    checkpoint_id: str
    name: str
    container_id: str
    timestamp: float = field(default_factory=time.time)


class LifecycleMixin:
    """Opt-in mixin adding lifecycle state tracking to Sandbox implementations."""

    _OPERATION_TIMEOUT: float = 60.0

    def __init__(self, *args, scaling_bounds: Optional[ScalingBounds] = None, event_bus=None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._lifecycle_state = LifecycleState.created
        self._state_lock = asyncio.Lock()
        self._inflight_count: int = 0
        self._draining: bool = False
        self._scaling_bounds = scaling_bounds
        self._event_bus = event_bus
        self._audit_log: list = []

    @property
    def state(self) -> str:
        return self._lifecycle_state.value

    def _require_state(self, required: LifecycleState, operation: str) -> None:
        if self._lifecycle_state != required:
            self._audit(f"lifecycle:{operation}", allowed=False, violation=f"state is {self._lifecycle_state.value}, requires {required.value}")
            raise SandboxError(f"Cannot {operation}: state is '{self._lifecycle_state.value}', requires '{required.value}'")

    def _validate_scaling(self, memory_mb: int, cpu_count: int) -> None:
        if self._scaling_bounds is None:
            raise SandboxError("Scaling not configured — ScalingBounds is None")
        b = self._scaling_bounds
        if not (b.min_memory_mb <= memory_mb <= b.max_memory_mb):
            raise SecurityViolation(f"memory_mb={memory_mb} outside bounds [{b.min_memory_mb}, {b.max_memory_mb}]")
        if not (b.min_cpu_count <= cpu_count <= b.max_cpu_count):
            raise SecurityViolation(f"cpu_count={cpu_count} outside bounds [{b.min_cpu_count}, {b.max_cpu_count}]")

    def _audit(self, command: str, allowed: bool, violation: str | None = None) -> None:
        from .base import AuditEntry
        entry = AuditEntry(command=command, timestamp=time.time(), allowed=allowed, violation=violation)
        self._audit_log.append(entry)

    def _emit_transition(self, prev: LifecycleState, new: LifecycleState) -> None:
        if self._event_bus:
            self._event_bus.publish(
                topic="sandbox.lifecycle",
                payload={"sandbox_id": id(self), "prev_state": prev.value, "new_state": new.value},
                source_agent="lifecycle_mixin",
            )

    # --- Public lifecycle methods ---

    async def hibernate(self) -> None:
        """Suspend the sandbox, preserving full process state."""
        async with self._state_lock:
            self._require_state(LifecycleState.running, "hibernate")
            self._draining = True
        await self._drain_inflight()
        async with self._state_lock:
            try:
                await asyncio.wait_for(self._do_hibernate(), timeout=self._OPERATION_TIMEOUT)
                prev = self._lifecycle_state
                self._lifecycle_state = LifecycleState.hibernated
                self._emit_transition(prev, self._lifecycle_state)
                self._audit("lifecycle:hibernate", allowed=True)
            except asyncio.TimeoutError:
                self._draining = False
                self._audit("lifecycle:hibernate", allowed=False, violation="operation timed out")
                raise SandboxError("hibernate operation timed out")
            finally:
                self._draining = False

    async def wake(self) -> None:
        """Resume a hibernated sandbox."""
        async with self._state_lock:
            self._require_state(LifecycleState.hibernated, "wake")
            try:
                await asyncio.wait_for(self._do_wake(), timeout=self._OPERATION_TIMEOUT)
                prev = self._lifecycle_state
                self._lifecycle_state = LifecycleState.running
                self._emit_transition(prev, self._lifecycle_state)
                self._audit("lifecycle:wake", allowed=True)
            except asyncio.TimeoutError:
                self._audit("lifecycle:wake", allowed=False, violation="operation timed out")
                raise SandboxError("wake operation timed out")

    async def scale(self, memory_mb: int, cpu_count: int) -> None:
        """Resize sandbox resources at runtime."""
        async with self._state_lock:
            self._require_state(LifecycleState.running, "scale")
            self._validate_scaling(memory_mb, cpu_count)
            try:
                await asyncio.wait_for(self._do_scale(memory_mb, cpu_count), timeout=self._OPERATION_TIMEOUT)
                self._audit("lifecycle:scale", allowed=True)
            except asyncio.TimeoutError:
                self._audit("lifecycle:scale", allowed=False, violation="operation timed out")
                raise SandboxError("scale operation timed out")

    async def checkpoint(self, name: str) -> str:
        """Create a named persistent snapshot. Returns checkpoint_id."""
        async with self._state_lock:
            self._require_state(LifecycleState.running, "checkpoint")
            try:
                cid = await asyncio.wait_for(self._do_checkpoint(name), timeout=self._OPERATION_TIMEOUT)
                self._audit("lifecycle:checkpoint", allowed=True)
                return cid
            except asyncio.TimeoutError:
                self._audit("lifecycle:checkpoint", allowed=False, violation="operation timed out")
                raise SandboxError("checkpoint operation timed out")

    async def fork(self, name: str) -> "Sandbox":
        """Create a new sandbox from current state."""
        async with self._state_lock:
            self._require_state(LifecycleState.running, "fork")
            try:
                result = await asyncio.wait_for(self._do_fork(name), timeout=self._OPERATION_TIMEOUT)
                self._audit("lifecycle:fork", allowed=True)
                return result
            except asyncio.TimeoutError:
                self._audit("lifecycle:fork", allowed=False, violation="operation timed out")
                raise SandboxError("fork operation timed out")

    async def delete_checkpoint(self, checkpoint_id: str) -> None:
        """Remove a previously created checkpoint."""
        try:
            await asyncio.wait_for(self._do_delete_checkpoint(checkpoint_id), timeout=self._OPERATION_TIMEOUT)
            self._audit("lifecycle:delete_checkpoint", allowed=True)
        except asyncio.TimeoutError:
            self._audit("lifecycle:delete_checkpoint", allowed=False, violation="operation timed out")
            raise SandboxError("delete_checkpoint operation timed out")

    async def list_checkpoints(self) -> list[CheckpointInfo]:
        """Return metadata for all checkpoints belonging to this sandbox."""
        return await self._do_list_checkpoints()

    async def exec(self, cmd: str, *, env=None, cwd=None, timeout=None):
        """Guarded exec — rejects calls in non-running states and tracks in-flight."""
        if self._lifecycle_state in (LifecycleState.hibernated, LifecycleState.stopped):
            raise SandboxError(
                f"Cannot exec: sandbox is '{self._lifecycle_state.value}', requires 'running'"
            )
        if self._draining:
            raise SandboxError("Cannot exec: sandbox is entering hibernation")
        self._inflight_count += 1
        try:
            return await super().exec(cmd, env=env, cwd=cwd, timeout=timeout)
        finally:
            self._inflight_count -= 1

    # --- Internal helpers ---

    async def _drain_inflight(self) -> None:
        """Wait for in-flight exec calls to finish, up to timeout."""
        deadline = asyncio.get_event_loop().time() + self._OPERATION_TIMEOUT
        while self._inflight_count > 0:
            if asyncio.get_event_loop().time() >= deadline:
                self._draining = False
                self._audit("lifecycle:hibernate", allowed=False, violation="aborted due to in-flight operations")
                raise SandboxError("Hibernation aborted: in-flight exec calls did not complete within timeout")
            await asyncio.sleep(0.05)

    # --- Backend hooks (override in concrete classes) ---

    async def _do_hibernate(self) -> None:
        raise NotImplementedError(f"{type(self).__name__} does not support hibernate")

    async def _do_wake(self) -> None:
        raise NotImplementedError(f"{type(self).__name__} does not support wake")

    async def _do_scale(self, memory_mb: int, cpu_count: int) -> None:
        raise NotImplementedError(f"{type(self).__name__} does not support scale")

    async def _do_checkpoint(self, name: str) -> str:
        raise NotImplementedError(f"{type(self).__name__} does not support checkpoint")

    async def _do_fork(self, name: str) -> "Sandbox":
        raise NotImplementedError(f"{type(self).__name__} does not support fork")

    async def _do_delete_checkpoint(self, checkpoint_id: str) -> None:
        raise NotImplementedError(f"{type(self).__name__} does not support delete_checkpoint")

    async def _do_list_checkpoints(self) -> list[CheckpointInfo]:
        raise NotImplementedError(f"{type(self).__name__} does not support list_checkpoints")


# --- Factory function (module-level) ---


async def sandbox_from_checkpoint(
    checkpoint_id: str,
    backend: str,
    **kwargs,
) -> "Sandbox":
    """Create a sandbox from a previously saved checkpoint.

    Parameters
    ----------
    checkpoint_id:
        For docker: "{container_id}:{checkpoint_name}"
        For cube: opaque identifier from the server
    backend:
        "docker" or "cube"
    **kwargs:
        Passed through to the sandbox constructor (policy, fs, scaling_bounds, etc.)

    Raises
    ------
    ValueError: unsupported backend
    SandboxError: checkpoint restoration failed
    """
    supported = ("docker", "cube")
    if backend not in supported:
        raise ValueError(f"Unsupported backend '{backend}'. Supported: {sorted(supported)}")
    if backend == "docker":
        from .durable_docker import DurableDockerSandbox

        container_id, checkpoint_name = checkpoint_id.split(":", 1)
        sandbox = DurableDockerSandbox(container_id_path=None, **kwargs)
        sandbox._cid = container_id
        # Start from checkpoint
        proc = await asyncio.create_subprocess_exec(
            "docker", "start", "--checkpoint", checkpoint_name, container_id,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, err = await proc.communicate()
        if proc.returncode != 0:
            raise SandboxError(f"docker start --checkpoint failed: {err.decode().strip()}")
        sandbox._lifecycle_state = LifecycleState.running
        return sandbox
    else:  # cube
        from .providers import CubeSandboxAdapter

        adapter = CubeSandboxAdapter(**kwargs)
        resp = await asyncio.to_thread(adapter._http_post, "/sessions/from-checkpoint", {"checkpoint_id": checkpoint_id})
        adapter._session_id = resp["session_id"]
        adapter._lifecycle_state = LifecycleState.running
        return adapter
