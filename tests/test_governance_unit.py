"""Unit tests for ToolPolicy and GovernancePolicy (Task 7.1).

Tests cover:
- ToolPolicy receives MaskContext and returns subset of available tools
- GovernancePolicy.is_allowed blocks tool calls when False
- GovernancePolicy with ApprovalGate requests approval before blocking
- Error ToolResultBlock returned for blocked tools

Requirements: 5.1, 5.3, 5.4
"""

from unittest.mock import AsyncMock


from tvastar import Harness, create_agent
from tvastar.approval import ApprovalGate, ApprovalTimeout
from tvastar.masking import GovernancePolicy, MaskContext, apply_policy
from tvastar.model import MockModel
from tvastar.tools.base import tool as tool_decorator
from tvastar.types import ToolResultBlock, ToolUseBlock


# ---------------------------------------------------------------------------
# ToolPolicy receives MaskContext and returns subset (REQ 5.1)
# ---------------------------------------------------------------------------


def test_tool_policy_receives_mask_context_and_returns_subset():
    """A ToolPolicy is a callable that receives MaskContext and returns tool names."""
    received_contexts = []

    def tracking_policy(ctx: MaskContext) -> list[str]:
        received_contexts.append(ctx)
        return ["tool_a", "tool_b"]

    ctx = MaskContext(step=2, available=["tool_a", "tool_b", "tool_c"])
    result = apply_policy(tracking_policy, ctx)

    assert len(received_contexts) == 1
    assert received_contexts[0] is ctx
    assert received_contexts[0].step == 2
    assert received_contexts[0].available == ["tool_a", "tool_b", "tool_c"]
    # Result is a set of allowed tool names
    assert result == {"tool_a", "tool_b"}


def test_tool_policy_returns_subset_of_available():
    """ToolPolicy output is intersected with available — can only hide, never grant."""

    def restrictive_policy(ctx: MaskContext) -> list[str]:
        return ["tool_a"]

    ctx = MaskContext(step=1, available=["tool_a", "tool_b", "tool_c"])
    result = apply_policy(restrictive_policy, ctx)
    assert result == {"tool_a"}
    # "tool_a" is a subset of available
    assert result.issubset(set(ctx.available))


def test_tool_policy_cannot_grant_tools_not_available():
    """If a policy returns a tool name not in available, apply_policy returns it
    but the harness intersects with available (tested at integration level).
    apply_policy itself returns whatever the policy gives."""

    def greedy_policy(ctx: MaskContext) -> list[str]:
        return ["tool_a", "tool_x_not_available"]

    ctx = MaskContext(step=1, available=["tool_a", "tool_b"])
    result = apply_policy(greedy_policy, ctx)
    # apply_policy returns the raw set; the harness intersects with available
    assert "tool_x_not_available" in result
    assert "tool_a" in result


def test_tool_policy_with_empty_return_hides_all():
    """A policy returning empty list hides all tools."""

    def hide_all(ctx: MaskContext) -> list[str]:
        return []

    ctx = MaskContext(step=1, available=["tool_a", "tool_b"])
    result = apply_policy(hide_all, ctx)
    assert result == set()


def test_tool_policy_none_means_expose_all():
    """When no policy is configured (None), apply_policy returns None meaning all exposed."""
    ctx = MaskContext(step=1, available=["tool_a", "tool_b"])
    result = apply_policy(None, ctx)
    assert result is None


def test_mask_context_provides_run_state():
    """MaskContext exposes step, available, messages, and active_skill."""
    from tvastar.types import Message

    msgs = [Message(role="user", content="hello")]
    ctx = MaskContext(step=3, available=["grep", "bash"], messages=msgs, active_skill="coder")
    assert ctx.step == 3
    assert ctx.available == ["grep", "bash"]
    assert ctx.messages == msgs
    assert ctx.active_skill == "coder"


def test_mask_context_last_tool_used():
    """MaskContext.last_tool_used returns the most recent tool invocation name."""
    from tvastar.types import Message, ToolUseBlock as TUB

    msgs = [
        Message(role="assistant", content=[TUB(name="grep", input={}, id="tu1")]),
        Message(role="assistant", content=[TUB(name="bash", input={}, id="tu2")]),
    ]
    ctx = MaskContext(step=3, available=["grep", "bash"], messages=msgs)
    assert ctx.last_tool_used == "bash"


def test_mask_context_last_tool_used_none_when_no_tools():
    """MaskContext.last_tool_used returns None when no tools used yet."""
    ctx = MaskContext(step=1, available=["grep"])
    assert ctx.last_tool_used is None


# ---------------------------------------------------------------------------
# GovernancePolicy.is_allowed blocks tool calls when False (REQ 5.3)
# ---------------------------------------------------------------------------


def test_governance_is_allowed_permits_tool_in_current_phase():
    """is_allowed returns True when the tool is in the current phase's allow set."""
    gov = GovernancePolicy(
        phases={"read": {"grep", "read_file"}, "write": {"grep", "read_file", "bash"}},
        current_phase="read",
    )
    assert gov.is_allowed("grep") is True
    assert gov.is_allowed("read_file") is True


def test_governance_is_allowed_blocks_tool_not_in_phase():
    """is_allowed returns False when tool is not in the current phase's allow set."""
    gov = GovernancePolicy(
        phases={"read": {"grep", "read_file"}, "write": {"grep", "read_file", "bash"}},
        current_phase="read",
    )
    assert gov.is_allowed("bash") is False
    assert gov.is_allowed("write_file") is False


def test_governance_star_allows_all_tools():
    """The '*' sentinel in a phase's tool set allows any tool."""
    gov = GovernancePolicy(phases={"admin": {"*"}}, current_phase="admin")
    assert gov.is_allowed("bash") is True
    assert gov.is_allowed("delete_everything") is True
    assert gov.is_allowed("") is True


def test_governance_unknown_phase_denies_all():
    """When current_phase is not in phases dict, all tools are denied (fail closed)."""
    gov = GovernancePolicy(phases={"read": {"grep"}}, current_phase="read")
    # Bypass set_phase validation to simulate misconfigured state
    gov.current_phase = "nonexistent_phase"
    assert gov.is_allowed("grep") is False
    assert gov.is_allowed("anything") is False


def test_governance_empty_phase_set_denies_all():
    """A phase with an empty tool set denies all tools."""
    gov = GovernancePolicy(phases={"locked": set()}, current_phase="locked")
    assert gov.is_allowed("grep") is False
    assert gov.is_allowed("bash") is False


# ---------------------------------------------------------------------------
# GovernancePolicy with ApprovalGate (REQ 5.4)
# ---------------------------------------------------------------------------


async def test_governance_approval_gate_approves_allows_tool():
    """When gate approves, the blocked tool call is allowed to proceed."""

    @tool_decorator
    async def restricted_tool() -> str:
        return "executed"

    gate = ApprovalGate(backend="event", on_request=lambda req: req.approve())

    gov = GovernancePolicy(
        phases={"locked": set()},  # no tools allowed
        current_phase="locked",
        approval_gate=gate,
    )
    agent = create_agent(
        "gated-approve",
        model=MockModel(
            [
                ToolUseBlock(name="restricted_tool", input={}, id="tu_1"),
                "done",
            ]
        ),
        tools=[restricted_tool],
        governance=gov,
        detect=False,
    )
    result = await Harness(agent).run("try it")
    assert result.text == "done"
    # The tool was actually executed (approval granted)
    tool_results = [b for m in result.messages for b in m.blocks if isinstance(b, ToolResultBlock)]
    # Should have a successful result (not an error)
    successful = [b for b in tool_results if not b.is_error]
    assert len(successful) >= 1
    assert "executed" in successful[0].content


async def test_governance_approval_gate_denied_returns_error():
    """When gate denies, the tool call returns an error ToolResultBlock."""

    @tool_decorator
    async def restricted_tool() -> str:
        return "should not run"

    gate = ApprovalGate(backend="event", on_request=lambda req: req.deny())

    gov = GovernancePolicy(
        phases={"locked": set()},
        current_phase="locked",
        approval_gate=gate,
    )
    agent = create_agent(
        "gated-deny",
        model=MockModel(
            [
                ToolUseBlock(name="restricted_tool", input={}, id="tu_2"),
                "gave up",
            ]
        ),
        tools=[restricted_tool],
        governance=gov,
        detect=False,
    )
    result = await Harness(agent).run("try it")
    assert result.text == "gave up"
    # Should have an error ToolResultBlock with governance message
    error_blocks = [
        b
        for m in result.messages
        for b in m.blocks
        if isinstance(b, ToolResultBlock) and b.is_error
    ]
    assert len(error_blocks) >= 1
    assert "governance" in error_blocks[0].content.lower()


async def test_governance_approval_gate_timeout_returns_error():
    """When gate times out, the tool call returns an error ToolResultBlock."""

    @tool_decorator
    async def restricted_tool() -> str:
        return "should not run"

    gate = ApprovalGate(backend="event")
    # Mock request to raise ApprovalTimeout
    gate.request = AsyncMock(side_effect=ApprovalTimeout("timed out"))

    gov = GovernancePolicy(
        phases={"locked": set()},
        current_phase="locked",
        approval_gate=gate,
    )
    agent = create_agent(
        "gated-timeout",
        model=MockModel(
            [
                ToolUseBlock(name="restricted_tool", input={}, id="tu_3"),
                "timed out",
            ]
        ),
        tools=[restricted_tool],
        governance=gov,
        detect=False,
    )
    result = await Harness(agent).run("try it")
    assert result.text == "timed out"
    error_blocks = [
        b
        for m in result.messages
        for b in m.blocks
        if isinstance(b, ToolResultBlock) and b.is_error
    ]
    assert len(error_blocks) >= 1
    assert "governance" in error_blocks[0].content.lower()


async def test_governance_no_gate_returns_hard_block_error():
    """Without an ApprovalGate, blocked tools return an error ToolResultBlock immediately."""

    @tool_decorator
    async def restricted_tool() -> str:
        return "should not run"

    gov = GovernancePolicy(
        phases={"locked": set()},
        current_phase="locked",
        # No approval_gate
    )
    agent = create_agent(
        "hard-block",
        model=MockModel(
            [
                ToolUseBlock(name="restricted_tool", input={}, id="tu_4"),
                "blocked",
            ]
        ),
        tools=[restricted_tool],
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
    assert "not permitted" in error_blocks[0].content.lower()


# ---------------------------------------------------------------------------
# Error ToolResultBlock for blocked tools (REQ 5.3, 5.4)
# ---------------------------------------------------------------------------


async def test_error_tool_result_block_has_correct_fields():
    """Blocked tool calls produce a ToolResultBlock with is_error=True and governance info."""

    @tool_decorator
    async def forbidden_tool() -> str:
        return "never"

    gov = GovernancePolicy(
        phases={"read_only": {"grep"}},
        current_phase="read_only",
    )
    agent = create_agent(
        "error-block-check",
        model=MockModel(
            [
                ToolUseBlock(name="forbidden_tool", input={}, id="tu_5"),
                "ok",
            ]
        ),
        tools=[forbidden_tool],
        governance=gov,
        detect=False,
    )
    result = await Harness(agent).run("do it")

    error_blocks = [
        b
        for m in result.messages
        for b in m.blocks
        if isinstance(b, ToolResultBlock) and b.is_error
    ]
    assert len(error_blocks) == 1
    block = error_blocks[0]
    assert block.is_error is True
    assert block.tool_use_id == "tu_5"
    assert "forbidden_tool" in block.content
    assert "read_only" in block.content  # mentions the phase


async def test_error_tool_result_block_loop_continues():
    """After a governance error ToolResultBlock, the agent loop continues (doesn't crash)."""

    @tool_decorator
    async def blocked_tool() -> str:
        return "never"

    @tool_decorator
    async def allowed_tool() -> str:
        return "allowed result"

    gov = GovernancePolicy(
        phases={"limited": {"allowed_tool"}},
        current_phase="limited",
    )
    agent = create_agent(
        "loop-continues",
        model=MockModel(
            [
                # First: try blocked tool
                ToolUseBlock(name="blocked_tool", input={}, id="tu_6"),
                # Then: try allowed tool
                ToolUseBlock(name="allowed_tool", input={}, id="tu_7"),
                "finished",
            ]
        ),
        tools=[blocked_tool, allowed_tool],
        governance=gov,
        detect=False,
    )
    result = await Harness(agent).run("go")
    assert result.text == "finished"
    # Both tool results should be present
    all_results = [b for m in result.messages for b in m.blocks if isinstance(b, ToolResultBlock)]
    errors = [b for b in all_results if b.is_error]
    successes = [b for b in all_results if not b.is_error]
    assert len(errors) >= 1  # blocked_tool got an error
    assert len(successes) >= 1  # allowed_tool succeeded
    assert "allowed result" in successes[0].content
