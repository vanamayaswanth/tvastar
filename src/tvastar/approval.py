"""
tvastar.approval — human-in-the-loop approval gate for agents.

Pause an agent run and wait for a human to approve or reject before
proceeding with a dangerous or irreversible action.

Usage (as a tool)::

    from tvastar.approval import require_approval

    @tool
    async def deploy(env: str, ctx: ToolContext) -> str:
        await require_approval(
            f"Deploy to {env}?",
            ctx=ctx,
            timeout=300,
        )
        return do_deploy(env)

Usage (standalone, outside a tool)::

    from tvastar.approval import ApprovalGate

    gate = ApprovalGate()
    approved = await gate.request("Reformat the entire repo?", timeout=60)
    if not approved:
        raise RuntimeError("User declined")
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

__all__ = [
    "ApprovalGate",
    "ApprovalRequest",
    "ApprovalDenied",
    "ApprovalTimeout",
    "require_approval",
]


class ApprovalDenied(RuntimeError):
    """Raised when a human explicitly denies an approval request."""

    pass


class ApprovalTimeout(RuntimeError):
    """Raised when an approval request times out."""

    pass


@dataclass
class ApprovalRequest:
    """Represents a pending approval request."""

    message: str
    timeout: float = 300.0
    metadata: dict = field(default_factory=dict)

    # Resolved by the backend
    _future: asyncio.Future | None = field(default=None, init=False, repr=False)
    # Audit trail — who approved and when (populated on approval)
    approved_by: str = field(default="", init=False)
    approved_at: float = field(default=0.0, init=False)

    def approve(self, approver: str = "") -> None:
        """Call this to approve the request (from outside the agent loop).

        Args:
            approver: Identity of the human approver (email, username, employee ID).
                      Captured in the ExecutionReceipt for regulatory audit trails.
        """
        import time as _time

        self.approved_by = approver
        self.approved_at = _time.time()
        if self._future and not self._future.done():
            self._future.get_loop().call_soon_threadsafe(self._future.set_result, True)

    def deny(self) -> None:
        """Call this to deny the request (from outside the agent loop)."""
        if self._future and not self._future.done():
            self._future.get_loop().call_soon_threadsafe(self._future.set_result, False)


class ApprovalGate:
    """
    Controls how approval requests are presented to a human.

    Two backends:
        ``"cli"``   — prints to stderr, reads from stdin (default).
        ``"event"`` — calls ``on_request(req)`` immediately; caller resolves
                      via ``req.approve()`` / ``req.deny()``. Useful for tests
                      and programmatic control.

    Args:
        backend:             ``"cli"`` | ``"event"``
        on_request:          Called with the ApprovalRequest when backend="event".
        approved_by_default: Auto-approve on timeout. Default False.

    Example::

        gate = ApprovalGate(backend="cli")
        agent = create_agent(..., approval_gate=gate)
    """

    def __init__(
        self,
        backend: Literal["cli", "event"] = "cli",
        *,
        on_request: Callable[[ApprovalRequest], None] | None = None,
        approved_by_default: bool = False,
    ) -> None:
        self.backend = backend
        self.on_request = on_request
        self.approved_by_default = approved_by_default

    async def request(
        self,
        message: str,
        *,
        timeout: float = 300.0,
        metadata: dict | None = None,
    ) -> bool:
        """
        Pause and wait for human approval.

        Returns True if approved, raises ApprovalDenied or ApprovalTimeout otherwise.
        """
        req = ApprovalRequest(message=message, timeout=timeout, metadata=metadata or {})
        if self.backend == "event":
            return await self._event_request(req)
        return await self._cli_request(req)

    # ------------------------------------------------------------------
    # CLI backend
    # ------------------------------------------------------------------

    async def _cli_request(self, req: ApprovalRequest) -> bool:
        loop = asyncio.get_running_loop()

        def _prompt() -> str:
            print(f"\n{'─' * 60}", file=sys.stderr)
            print("⚠  APPROVAL REQUIRED", file=sys.stderr)
            print(f"   {req.message}", file=sys.stderr)
            print(f"   Timeout: {req.timeout}s", file=sys.stderr)
            print(f"{'─' * 60}", file=sys.stderr)
            return input("   Approve? [y/N]: ").strip().lower()

        try:
            answer = await asyncio.wait_for(
                loop.run_in_executor(None, _prompt),
                timeout=req.timeout,
            )
        except asyncio.TimeoutError:
            if self.approved_by_default:
                return True
            raise ApprovalTimeout(
                f"Approval request timed out after {req.timeout}s: {req.message!r}"
            )

        if answer in ("y", "yes"):
            return True
        raise ApprovalDenied(f"User denied: {req.message!r}")

    # ------------------------------------------------------------------
    # Event backend — programmatic control via req.approve() / req.deny()
    # ------------------------------------------------------------------

    async def _event_request(self, req: ApprovalRequest) -> bool:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[bool] = loop.create_future()
        req._future = future

        if self.on_request:
            self.on_request(req)

        try:
            approved = await asyncio.wait_for(future, timeout=req.timeout)
        except asyncio.TimeoutError:
            if self.approved_by_default:
                return True
            raise ApprovalTimeout(
                f"Approval request timed out after {req.timeout}s: {req.message!r}"
            )

        if not approved:
            raise ApprovalDenied(f"Approval denied: {req.message!r}")
        return True


# ---------------------------------------------------------------------------
# Convenience function — used inside @tool functions
# ---------------------------------------------------------------------------

# Module-level default gate — swap with set_default_gate()
_default_gate: ApprovalGate = ApprovalGate()


def set_default_gate(gate: ApprovalGate) -> None:
    """Replace the module-level default approval gate."""
    global _default_gate
    _default_gate = gate


async def require_approval(
    message: str,
    *,
    ctx: Any = None,
    timeout: float = 300.0,
    gate: ApprovalGate | None = None,
    metadata: dict | None = None,
) -> None:
    """
    Pause execution and wait for human approval.

    Raises ApprovalDenied or ApprovalTimeout if not approved.
    Call this inside any @tool function before taking a dangerous action.

    Args:
        message: Human-readable description of the action requiring approval.
        ctx:     ToolContext (optional — reserved for future session-aware routing).
        timeout: Seconds to wait for a response (default 300).
        gate:    ApprovalGate to use. Defaults to the module-level default (CLI).
        metadata: Extra data passed to the gate backend.

    Example::

        @tool
        async def delete_database(name: str, ctx: ToolContext) -> str:
            await require_approval(
                f"Permanently delete database {name!r}? This cannot be undone.",
                ctx=ctx,
                timeout=120,
            )
            return do_delete(name)
    """
    # Precedence: explicit gate > the agent's configured gate (via ctx) > default.
    ctx_gate = getattr(ctx, "approval_gate", None) if ctx is not None else None
    active_gate = gate or ctx_gate or _default_gate
    await active_gate.request(message, timeout=timeout, metadata=metadata or {})
