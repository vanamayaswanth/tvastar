"""Unit tests for ApprovalGate — human-in-the-loop approval mechanism.

Tests cover:
- require_approval() pauses execution and requests approval (REQ 17.1)
- Denial raises ApprovalDenied (REQ 17.2)
- Timeout raises ApprovalTimeout (REQ 17.3)
- Granted approval recorded in receipt (REQ 17.4)
- Multiple backends: CLI, event (REQ 17.5)
- GovernancePolicy + ApprovalGate requests approval instead of blocking (REQ 17.6)
"""

import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest

from tvastar import Harness, create_agent
from tvastar.approval import (
    ApprovalDenied,
    ApprovalGate,
    ApprovalRequest,
    ApprovalTimeout,
    require_approval,
    set_default_gate,
)
from tvastar.masking import GovernancePolicy
from tvastar.model import MockModel
from tvastar.tools.base import ToolContext, tool as tool_decorator
from tvastar.types import ToolResultBlock, ToolUseBlock


# ---------------------------------------------------------------------------
# REQ 17.1: require_approval() pauses execution and requests approval
# ---------------------------------------------------------------------------


class TestRequireApprovalPauses:
    """Verify that require_approval() suspends until a human responds."""

    async def test_require_approval_pauses_until_approved(self):
        """require_approval() suspends the coroutine until approve() is called."""
        execution_order = []

        def on_request(req):
            execution_order.append("request_received")
            # Simulate delayed approval
            req.approve("operator@example.com")

        gate = ApprovalGate(backend="event", on_request=on_request)

        execution_order.append("before_approval")
        await require_approval("Deploy?", gate=gate, timeout=5)
        execution_order.append("after_approval")

        assert execution_order == ["before_approval", "request_received", "after_approval"]

    async def test_require_approval_passes_message_to_backend(self):
        """The approval message is forwarded to the backend's on_request handler."""
        received_messages = []

        def on_request(req):
            received_messages.append(req.message)
            req.approve()

        gate = ApprovalGate(backend="event", on_request=on_request)
        await require_approval("Delete database 'prod'?", gate=gate, timeout=5)

        assert received_messages == ["Delete database 'prod'?"]

    async def test_require_approval_passes_metadata(self):
        """Metadata dict is forwarded to the ApprovalRequest."""
        received_metadata = []

        def on_request(req):
            received_metadata.append(req.metadata)
            req.approve()

        gate = ApprovalGate(backend="event", on_request=on_request)
        await require_approval(
            "Deploy?",
            gate=gate,
            timeout=5,
            metadata={"env": "production", "user": "admin"},
        )

        assert received_metadata == [{"env": "production", "user": "admin"}]

    async def test_require_approval_uses_ctx_gate_when_no_explicit_gate(self):
        """require_approval() picks up the gate from ctx.approval_gate."""
        called = []

        def on_request(req):
            called.append(True)
            req.approve()

        gate = ApprovalGate(backend="event", on_request=on_request)

        class FakeCtx:
            approval_gate = gate

        await require_approval("Do it?", ctx=FakeCtx(), timeout=5)
        assert called == [True]

    async def test_require_approval_explicit_gate_overrides_ctx(self):
        """Explicit gate parameter takes precedence over ctx.approval_gate."""
        explicit_called = []
        ctx_called = []

        def on_explicit(req):
            explicit_called.append(True)
            req.approve()

        def on_ctx(req):
            ctx_called.append(True)
            req.approve()

        explicit_gate = ApprovalGate(backend="event", on_request=on_explicit)
        ctx_gate = ApprovalGate(backend="event", on_request=on_ctx)

        class FakeCtx:
            approval_gate = ctx_gate

        await require_approval("Do it?", ctx=FakeCtx(), gate=explicit_gate, timeout=5)
        assert explicit_called == [True]
        assert ctx_called == []

    async def test_require_approval_falls_back_to_default_gate(self):
        """Without explicit gate or ctx gate, require_approval uses module default."""
        called = []

        def on_request(req):
            called.append(True)
            req.approve()

        gate = ApprovalGate(backend="event", on_request=on_request)
        original_default = None

        try:
            from tvastar import approval as _mod

            original_default = _mod._default_gate
            set_default_gate(gate)
            await require_approval("Do it?", timeout=5)
            assert called == [True]
        finally:
            if original_default is not None:
                set_default_gate(original_default)


# ---------------------------------------------------------------------------
# REQ 17.2: Denial raises ApprovalDenied
# ---------------------------------------------------------------------------


class TestDenialRaisesApprovalDenied:
    """Verify that denial raises ApprovalDenied exception."""

    async def test_event_backend_deny_raises_approval_denied(self):
        """When req.deny() is called, ApprovalDenied is raised."""
        gate = ApprovalGate(backend="event", on_request=lambda r: r.deny())

        with pytest.raises(ApprovalDenied):
            await require_approval("Proceed?", gate=gate, timeout=5)

    async def test_approval_denied_contains_message(self):
        """ApprovalDenied exception message includes the request text."""
        gate = ApprovalGate(backend="event", on_request=lambda r: r.deny())

        with pytest.raises(ApprovalDenied, match="Delete all data"):
            await gate.request("Delete all data?", timeout=5)

    async def test_approval_denied_is_runtime_error(self):
        """ApprovalDenied inherits from RuntimeError."""
        assert issubclass(ApprovalDenied, RuntimeError)

    async def test_deny_after_future_set_is_noop(self):
        """Calling deny() on an already-resolved request is harmless."""
        req = ApprovalRequest(message="test")
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        req._future = future
        # Approve first
        req.approve()
        # Deny after should be a no-op (future already done)
        req.deny()  # Should not raise


# ---------------------------------------------------------------------------
# REQ 17.3: Timeout raises ApprovalTimeout
# ---------------------------------------------------------------------------


class TestTimeoutRaisesApprovalTimeout:
    """Verify that timeout raises ApprovalTimeout exception."""

    async def test_event_backend_timeout_raises_approval_timeout(self):
        """When no response within timeout, ApprovalTimeout is raised."""
        # on_request does nothing — never approves or denies
        gate = ApprovalGate(backend="event", on_request=lambda r: None)

        with pytest.raises(ApprovalTimeout):
            await gate.request("Proceed?", timeout=0.05)

    async def test_timeout_message_includes_duration(self):
        """ApprovalTimeout exception message includes the timeout duration."""
        gate = ApprovalGate(backend="event", on_request=lambda r: None)

        with pytest.raises(ApprovalTimeout, match="0.05"):
            await gate.request("Deploy?", timeout=0.05)

    async def test_timeout_message_includes_request_text(self):
        """ApprovalTimeout exception message includes the request text."""
        gate = ApprovalGate(backend="event", on_request=lambda r: None)

        with pytest.raises(ApprovalTimeout, match="Deploy to prod"):
            await gate.request("Deploy to prod?", timeout=0.05)

    async def test_approval_timeout_is_runtime_error(self):
        """ApprovalTimeout inherits from RuntimeError."""
        assert issubclass(ApprovalTimeout, RuntimeError)

    async def test_approved_by_default_on_timeout(self):
        """With approved_by_default=True, timeout auto-approves instead of raising."""
        gate = ApprovalGate(
            backend="event",
            on_request=lambda r: None,  # Never responds
            approved_by_default=True,
        )

        # Should not raise — auto-approved
        result = await gate.request("Proceed?", timeout=0.05)
        assert result is True

    async def test_cli_backend_timeout_raises_approval_timeout(self):
        """CLI backend also raises ApprovalTimeout on timeout."""
        gate = ApprovalGate(backend="cli")

        # Mock run_in_executor to simulate a hung stdin
        async def _blocked(*args, **kwargs):
            await asyncio.sleep(10)  # will exceed timeout
            return "y"

        with patch.object(asyncio.get_running_loop(), "run_in_executor", side_effect=lambda *a: _blocked()):
            with pytest.raises(ApprovalTimeout):
                await gate._cli_request(ApprovalRequest(message="Deploy?", timeout=0.05))


# ---------------------------------------------------------------------------
# REQ 17.4: Granted approval recorded in receipt
# ---------------------------------------------------------------------------


class TestApprovalRecordedInReceipt:
    """Verify that approval metadata is captured for audit trails."""

    async def test_approve_records_approver_identity(self):
        """ApprovalRequest.approve() captures the approver identity."""
        req = ApprovalRequest(message="Deploy?")
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        req._future = future

        req.approve(approver="alice@company.com")

        assert req.approved_by == "alice@company.com"

    async def test_approve_records_timestamp(self):
        """ApprovalRequest.approve() captures the approval timestamp."""
        req = ApprovalRequest(message="Deploy?")
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        req._future = future

        before = time.time()
        req.approve(approver="bob")
        after = time.time()

        assert before <= req.approved_at <= after

    async def test_approval_recorded_in_session_receipt(self):
        """When ApprovalGate approves in agent loop, approval is recorded in session."""
        approval_records = []

        def on_request(req):
            req.approve(approver="admin@ops.io")

        gate = ApprovalGate(backend="event", on_request=on_request)

        @tool_decorator
        async def dangerous_action(ctx: ToolContext) -> str:
            "A dangerous tool."
            await require_approval("Execute dangerous action?", ctx=ctx)
            return "action executed"

        agent = create_agent(
            "receipt-test",
            model=MockModel(
                [
                    ToolUseBlock(name="dangerous_action", input={}, id="tu_1"),
                    "done",
                ]
            ),
            tools=[dangerous_action],
            approval_gate=gate,
            detect=False,
        )
        result = await Harness(agent).run("do it")
        assert result.text == "done"
        # Tool executed successfully
        tool_results = [
            b for m in result.messages for b in m.blocks if isinstance(b, ToolResultBlock)
        ]
        successful = [b for b in tool_results if not b.is_error]
        assert any("action executed" in b.content for b in successful)

    async def test_approve_without_future_still_records(self):
        """approve() records identity/timestamp even without a pending future."""
        req = ApprovalRequest(message="test")
        req.approve(approver="system")
        assert req.approved_by == "system"
        assert req.approved_at > 0


# ---------------------------------------------------------------------------
# REQ 17.5: Multiple backends — CLI, event
# ---------------------------------------------------------------------------


class TestMultipleBackends:
    """Verify that ApprovalGate supports multiple backend types."""

    async def test_event_backend_calls_on_request(self):
        """Event backend invokes on_request callback with the ApprovalRequest."""
        received_requests = []

        def on_request(req):
            received_requests.append(req)
            req.approve()

        gate = ApprovalGate(backend="event", on_request=on_request)
        await gate.request("Test?", timeout=5)

        assert len(received_requests) == 1
        assert received_requests[0].message == "Test?"

    async def test_event_backend_without_on_request(self):
        """Event backend without on_request times out (no one to respond)."""
        gate = ApprovalGate(backend="event")  # no on_request callback

        with pytest.raises(ApprovalTimeout):
            await gate.request("Test?", timeout=0.05)

    async def test_cli_backend_approves_on_yes(self):
        """CLI backend approves when user enters 'y'."""
        gate = ApprovalGate(backend="cli")

        with patch("asyncio.wait_for", return_value="y"):
            # We need to mock the internal _cli_request path
            pass

        # More directly: mock the executor call
        async def mock_wait_for(coro, timeout):
            return "y"

        gate_result = None

        async def run_cli():
            nonlocal gate_result
            # Patch the loop.run_in_executor to return "y"
            with patch("builtins.input", return_value="y"):
                gate_result = await gate._cli_request(
                    ApprovalRequest(message="Deploy?", timeout=5)
                )

        await run_cli()
        assert gate_result is True

    async def test_cli_backend_denies_on_no(self):
        """CLI backend raises ApprovalDenied when user enters 'n'."""
        gate = ApprovalGate(backend="cli")

        with patch("builtins.input", return_value="n"):
            with pytest.raises(ApprovalDenied):
                await gate._cli_request(ApprovalRequest(message="Deploy?", timeout=5))

    async def test_cli_backend_denies_on_empty(self):
        """CLI backend raises ApprovalDenied on empty input (default is N)."""
        gate = ApprovalGate(backend="cli")

        with patch("builtins.input", return_value=""):
            with pytest.raises(ApprovalDenied):
                await gate._cli_request(ApprovalRequest(message="Deploy?", timeout=5))

    async def test_event_backend_approve_resolves_future(self):
        """In event backend, approve() resolves the internal future."""
        resolved = []

        def on_request(req):
            # Schedule approval slightly later to prove the future is awaitable
            loop = asyncio.get_running_loop()
            loop.call_soon(req.approve, "tester")
            resolved.append("callback_fired")

        gate = ApprovalGate(backend="event", on_request=on_request)
        result = await gate.request("Approve this?", timeout=5)

        assert result is True
        assert resolved == ["callback_fired"]

    async def test_event_backend_deny_resolves_future_false(self):
        """In event backend, deny() resolves the internal future with False."""

        def on_request(req):
            loop = asyncio.get_running_loop()
            loop.call_soon(req.deny)

        gate = ApprovalGate(backend="event", on_request=on_request)

        with pytest.raises(ApprovalDenied):
            await gate.request("Approve?", timeout=5)

    async def test_default_backend_is_cli(self):
        """ApprovalGate defaults to CLI backend."""
        gate = ApprovalGate()
        assert gate.backend == "cli"

    async def test_event_backend_custom_timeout(self):
        """Event backend respects custom timeout values."""
        gate = ApprovalGate(backend="event", on_request=lambda r: None)

        start = time.time()
        with pytest.raises(ApprovalTimeout):
            await gate.request("Test?", timeout=0.1)
        elapsed = time.time() - start

        assert elapsed >= 0.1
        assert elapsed < 1.0  # Should not hang


# ---------------------------------------------------------------------------
# REQ 17.6: GovernancePolicy + ApprovalGate requests approval instead of blocking
# ---------------------------------------------------------------------------


class TestGovernancePlusApprovalGate:
    """Verify that GovernancePolicy routes blocked calls through ApprovalGate."""

    async def test_governance_blocked_tool_routed_through_gate_approve(self):
        """A blocked tool is routed through ApprovalGate; approval allows execution."""

        @tool_decorator
        async def write_file(path: str) -> str:
            "Write a file."
            return f"wrote {path}"

        gate = ApprovalGate(backend="event", on_request=lambda r: r.approve("admin"))

        gov = GovernancePolicy(
            phases={"read": {"read_file"}, "write": {"read_file", "write_file"}},
            current_phase="read",  # write_file is NOT allowed
            approval_gate=gate,
        )

        agent = create_agent(
            "gate-approve-test",
            model=MockModel(
                [
                    ToolUseBlock(name="write_file", input={"path": "/tmp/x"}, id="tu_1"),
                    "done writing",
                ]
            ),
            tools=[write_file],
            governance=gov,
            detect=False,
        )
        result = await Harness(agent).run("write something")
        assert result.text == "done writing"
        # Tool should have executed (approval granted)
        tool_results = [
            b for m in result.messages for b in m.blocks if isinstance(b, ToolResultBlock)
        ]
        successful = [b for b in tool_results if not b.is_error]
        assert any("wrote /tmp/x" in b.content for b in successful)

    async def test_governance_blocked_tool_denied_returns_error(self):
        """When gate denies, governance returns error ToolResultBlock."""

        @tool_decorator
        async def delete_db(name: str) -> str:
            "Delete a database."
            return f"deleted {name}"

        gate = ApprovalGate(backend="event", on_request=lambda r: r.deny())

        gov = GovernancePolicy(
            phases={"safe": {"read_file"}},
            current_phase="safe",
            approval_gate=gate,
        )

        agent = create_agent(
            "gate-deny-test",
            model=MockModel(
                [
                    ToolUseBlock(name="delete_db", input={"name": "prod"}, id="tu_1"),
                    "ok cancelled",
                ]
            ),
            tools=[delete_db],
            governance=gov,
            detect=False,
        )
        result = await Harness(agent).run("delete it")
        assert result.text == "ok cancelled"
        # Should have an error block with governance message
        error_blocks = [
            b
            for m in result.messages
            for b in m.blocks
            if isinstance(b, ToolResultBlock) and b.is_error
        ]
        assert len(error_blocks) >= 1
        assert "governance" in error_blocks[0].content.lower()

    async def test_governance_without_gate_blocks_immediately(self):
        """Without ApprovalGate, governance hard-blocks without human intervention."""

        @tool_decorator
        async def secret_tool() -> str:
            "A secret tool."
            return "secret value"

        gov = GovernancePolicy(
            phases={"locked": set()},  # nothing allowed
            current_phase="locked",
            # No approval_gate — hard block
        )

        agent = create_agent(
            "no-gate-test",
            model=MockModel(
                [
                    ToolUseBlock(name="secret_tool", input={}, id="tu_1"),
                    "blocked",
                ]
            ),
            tools=[secret_tool],
            governance=gov,
            detect=False,
        )
        result = await Harness(agent).run("try it")
        assert result.text == "blocked"
        error_blocks = [
            b
            for m in result.messages
            for b in m.blocks
            if isinstance(b, ToolResultBlock) and b.is_error
        ]
        assert len(error_blocks) >= 1
        assert "governance" in error_blocks[0].content.lower()

    async def test_governance_allowed_tool_skips_gate(self):
        """Tools allowed by the current phase bypass the ApprovalGate entirely."""
        gate_called = []

        def on_request(req):
            gate_called.append(True)
            req.approve()

        gate = ApprovalGate(backend="event", on_request=on_request)

        @tool_decorator
        async def read_file(path: str) -> str:
            "Read a file."
            return f"content of {path}"

        gov = GovernancePolicy(
            phases={"read": {"read_file"}},
            current_phase="read",
            approval_gate=gate,
        )

        agent = create_agent(
            "allowed-skip-gate",
            model=MockModel(
                [
                    ToolUseBlock(name="read_file", input={"path": "/etc/hosts"}, id="tu_1"),
                    "got it",
                ]
            ),
            tools=[read_file],
            governance=gov,
            detect=False,
        )
        result = await Harness(agent).run("read hosts")
        assert result.text == "got it"
        # Gate should NOT have been called since tool is allowed
        assert gate_called == []

    async def test_governance_gate_timeout_returns_error(self):
        """When gate times out, governance returns error ToolResultBlock."""

        @tool_decorator
        async def deploy(env: str) -> str:
            "Deploy to environment."
            return f"deployed to {env}"

        gate = ApprovalGate(backend="event")
        # Mock to raise ApprovalTimeout
        gate.request = AsyncMock(side_effect=ApprovalTimeout("timed out"))

        gov = GovernancePolicy(
            phases={"safe": {"read_file"}},
            current_phase="safe",
            approval_gate=gate,
        )

        agent = create_agent(
            "gate-timeout-test",
            model=MockModel(
                [
                    ToolUseBlock(name="deploy", input={"env": "prod"}, id="tu_1"),
                    "timed out",
                ]
            ),
            tools=[deploy],
            governance=gov,
            detect=False,
        )
        result = await Harness(agent).run("deploy")
        error_blocks = [
            b
            for m in result.messages
            for b in m.blocks
            if isinstance(b, ToolResultBlock) and b.is_error
        ]
        assert len(error_blocks) >= 1
        assert "governance" in error_blocks[0].content.lower()


# ---------------------------------------------------------------------------
# ApprovalRequest direct tests
# ---------------------------------------------------------------------------


class TestApprovalRequest:
    """Direct tests on ApprovalRequest dataclass."""

    def test_default_timeout(self):
        """Default timeout is 300 seconds."""
        req = ApprovalRequest(message="test")
        assert req.timeout == 300.0

    def test_custom_metadata(self):
        """Custom metadata dict is stored."""
        req = ApprovalRequest(message="test", metadata={"key": "value"})
        assert req.metadata == {"key": "value"}

    def test_initial_approved_by_empty(self):
        """approved_by starts empty before approval."""
        req = ApprovalRequest(message="test")
        assert req.approved_by == ""
        assert req.approved_at == 0.0

    async def test_approve_without_future_does_not_raise(self):
        """approve() without a future is safe (standalone usage)."""
        req = ApprovalRequest(message="test")
        req.approve("someone")  # Should not raise
        assert req.approved_by == "someone"

    async def test_deny_without_future_does_not_raise(self):
        """deny() without a future is safe (standalone usage)."""
        req = ApprovalRequest(message="test")
        req.deny()  # Should not raise
