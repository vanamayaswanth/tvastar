"""Unit tests for Cost dataclass and BudgetPolicy enforcement.

Validates Requirements: 12.1, 12.2, 12.3, 12.4, 12.5, 12.6
"""

import pytest

from tvastar import (
    ApprovalGate,
    BudgetExceeded,
    BudgetPolicy,
    Cost,
    COST_TABLE,
    Harness,
    cost_for_model,
    create_agent,
    default_toolset,
)
from tvastar.model import MockModel
from tvastar.types import ToolUseBlock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _priced_mock(script, model="claude-opus-4-6"):
    """Create a MockModel whose .name hits the COST_TABLE."""
    m = MockModel(script)
    m.name = model
    return m


# ---------------------------------------------------------------------------
# Requirement 12.1: Cost computation from input_tokens, output_tokens, model
# ---------------------------------------------------------------------------


class TestCostComputation:
    """Test Cost.usd is computed correctly from tokens and model name."""

    def test_known_model_cost_calculation(self):
        """Cost for a known model uses COST_TABLE rates."""
        # claude-opus-4-6: input=$15/M, output=$75/M
        cost = Cost(input_tokens=1_000_000, output_tokens=1_000_000, model="claude-opus-4-6")
        assert cost.usd == pytest.approx(15.0 + 75.0)

    def test_gpt4o_cost_calculation(self):
        """GPT-4o: input=$2.50/M, output=$10/M."""
        cost = Cost(input_tokens=1_000_000, output_tokens=0, model="gpt-4o")
        assert cost.usd == pytest.approx(2.50)

    def test_gpt4o_output_tokens(self):
        """GPT-4o output tokens cost $10/M."""
        cost = Cost(input_tokens=0, output_tokens=1_000_000, model="gpt-4o")
        assert cost.usd == pytest.approx(10.0)

    def test_small_token_count(self):
        """Small token counts produce fractional USD."""
        # gpt-4o: input=$2.50/M → 1000 tokens = $0.0025
        cost = Cost(input_tokens=1000, output_tokens=0, model="gpt-4o")
        assert cost.usd == pytest.approx(0.0025)

    def test_unknown_model_returns_zero(self):
        """Unknown model names yield zero cost (no entry in COST_TABLE)."""
        cost = Cost(input_tokens=10_000, output_tokens=5_000, model="unknown-model-xyz")
        assert cost.usd == 0.0

    def test_zero_tokens_zero_cost(self):
        """Zero tokens always yields zero cost regardless of model."""
        cost = Cost(input_tokens=0, output_tokens=0, model="claude-opus-4-6")
        assert cost.usd == 0.0

    def test_cost_for_model_helper(self):
        """cost_for_model() creates a Cost with correct fields."""
        c = cost_for_model("gpt-4o", input_tokens=500, output_tokens=200)
        assert c.input_tokens == 500
        assert c.output_tokens == 200
        assert c.model == "gpt-4o"
        # $2.50/M * 500 + $10/M * 200 = $0.00125 + $0.002 = $0.00325
        assert c.usd == pytest.approx(0.00325)

    def test_cost_addition(self):
        """Cost.__add__ sums tokens and preserves model."""
        c1 = Cost(input_tokens=100, output_tokens=50, model="gpt-4o")
        c2 = Cost(input_tokens=200, output_tokens=100, model="gpt-4o")
        total = c1 + c2
        assert total.input_tokens == 300
        assert total.output_tokens == 150
        assert total.model == "gpt-4o"

    def test_cost_addition_preserves_first_model(self):
        """When adding costs, model is taken from the first non-empty model."""
        c1 = Cost(input_tokens=100, output_tokens=50, model="gpt-4o")
        c2 = Cost(input_tokens=200, output_tokens=100, model="claude-opus-4-6")
        total = c1 + c2
        assert total.model == "gpt-4o"

    def test_all_cost_table_entries_have_input_and_output(self):
        """Every COST_TABLE entry has both input and output rates."""
        for model_name, rates in COST_TABLE.items():
            assert "input" in rates, f"{model_name} missing 'input' rate"
            assert "output" in rates, f"{model_name} missing 'output' rate"
            assert rates["input"] >= 0
            assert rates["output"] >= 0


# ---------------------------------------------------------------------------
# Requirement 12.2: BudgetExceeded raised when on_exceed="raise"
# ---------------------------------------------------------------------------


class TestBudgetExceededRaise:
    """Test that BudgetPolicy raises BudgetExceeded when on_exceed='raise'."""

    def test_budget_check_raises_when_exceeded(self):
        """BudgetPolicy.check() raises when cost >= max_usd."""
        policy = BudgetPolicy(max_usd=0.01, on_exceed="raise")
        cost = Cost(input_tokens=1_000_000, output_tokens=0, model="gpt-4o")
        # $2.50 > $0.01
        with pytest.raises(BudgetExceeded) as exc_info:
            policy.check(cost)
        assert exc_info.value.spent == pytest.approx(2.50)
        assert exc_info.value.limit == 0.01

    def test_budget_check_does_not_raise_under_limit(self):
        """BudgetPolicy.check() does not raise when cost < max_usd."""
        policy = BudgetPolicy(max_usd=10.0, on_exceed="raise")
        cost = Cost(input_tokens=1000, output_tokens=500, model="gpt-4o")
        # $0.0025 + $0.005 = $0.0075 < $10
        policy.check(cost)  # should not raise

    def test_budget_exceeded_in_session(self):
        """Session raises BudgetExceeded during run when budget is exceeded."""

        async def _run():
            agent = create_agent(
                "budget_test",
                model=_priced_mock(["expensive answer"]),
                budget=BudgetPolicy(max_usd=0.0000001, on_exceed="raise"),
            )
            with pytest.raises(BudgetExceeded):
                await Harness(agent).run("compute something expensive")

        import asyncio

        asyncio.run(_run())

    def test_budget_exceeded_has_spent_and_limit(self):
        """BudgetExceeded stores spent and limit for error reporting."""
        exc = BudgetExceeded(spent=1.50, limit=1.00)
        assert exc.spent == 1.50
        assert exc.limit == 1.00
        assert "1.5000" in str(exc)
        assert "1.0000" in str(exc)


# ---------------------------------------------------------------------------
# Requirement 12.3: RunResult with stopped="budget" when on_exceed="stop"
# ---------------------------------------------------------------------------


class TestBudgetStop:
    """Test that on_exceed='stop' produces RunResult with stopped='budget'."""

    async def test_budget_stop_ends_with_budget_stopped(self):
        """Session returns stopped='budget' when cost exceeds limit with on_exceed='stop'."""
        agent = create_agent(
            "budget_stop_test",
            model=_priced_mock(
                [ToolUseBlock(name="list_files", input={}) for _ in range(3)] + ["done"]
            ),
            tools=default_toolset(),
            budget=BudgetPolicy(max_usd=0.0000001, on_exceed="stop"),
        )
        result = await Harness(agent).run("go")
        assert result.stopped == "budget"

    async def test_budget_stop_does_not_raise(self):
        """on_exceed='stop' never raises BudgetExceeded — it stops gracefully."""
        agent = create_agent(
            "no_raise_test",
            model=_priced_mock(["hello"]),
            budget=BudgetPolicy(max_usd=0.0000001, on_exceed="stop"),
        )
        # Should NOT raise — returns a RunResult instead
        result = await Harness(agent).run("go")
        assert result.stopped == "budget"

    async def test_budget_stop_returns_partial_result(self):
        """Stopped run still has valid RunResult fields."""
        agent = create_agent(
            "partial_test",
            model=_priced_mock([ToolUseBlock(name="list_files", input={}), "final answer"]),
            tools=default_toolset(),
            budget=BudgetPolicy(max_usd=0.0000001, on_exceed="stop"),
        )
        result = await Harness(agent).run("go")
        assert result.stopped == "budget"
        assert result.steps >= 1
        assert result.usage is not None


# ---------------------------------------------------------------------------
# Requirement 12.4: Approval request when on_exceed="approve"
# ---------------------------------------------------------------------------


class TestBudgetApprove:
    """Test that on_exceed='approve' requests approval via ApprovalGate."""

    async def test_budget_approve_requests_approval(self):
        """When budget exceeded and on_exceed='approve', gate.request is called."""
        approval_called = []
        gate = ApprovalGate(
            backend="event",
            on_request=lambda r: (approval_called.append(r.message), r.approve()),
        )

        agent = create_agent(
            "approve_test",
            model=_priced_mock([ToolUseBlock(name="list_files", input={}), "done"]),
            tools=default_toolset(),
            budget=BudgetPolicy(max_usd=0.0000001, on_exceed="approve"),
            approval_gate=gate,
        )
        result = await Harness(agent).run("go")
        # Approval was requested
        assert len(approval_called) >= 1
        # Since approved, run should complete normally
        assert result.stopped == "end_turn"

    async def test_budget_approve_denied_stops_run(self):
        """When approval is denied, run stops with stopped='budget'."""
        gate = ApprovalGate(backend="event", on_request=lambda r: r.deny())

        agent = create_agent(
            "deny_test",
            model=_priced_mock([ToolUseBlock(name="list_files", input={}), "done"]),
            tools=default_toolset(),
            budget=BudgetPolicy(max_usd=0.0000001, on_exceed="approve"),
            approval_gate=gate,
        )
        result = await Harness(agent).run("go")
        assert result.stopped == "budget"

    async def test_budget_approve_no_gate_raises(self):
        """When on_exceed='approve' but no gate configured, raises BudgetExceeded."""
        agent = create_agent(
            "no_gate_test",
            model=_priced_mock(["answer"]),
            budget=BudgetPolicy(max_usd=0.0000001, on_exceed="approve"),
            # no approval_gate set
        )
        with pytest.raises(BudgetExceeded):
            await Harness(agent).run("go")


# ---------------------------------------------------------------------------
# Requirement 12.5 / 12.6: RunResult.cost reflects total token cost
# ---------------------------------------------------------------------------


class TestRunResultCost:
    """Test that RunResult.cost reflects the total token cost for the run."""

    async def test_run_result_has_cost_field(self):
        """RunResult.cost is populated after a run."""
        agent = create_agent(
            "cost_field_test",
            model=_priced_mock(["hello"], model="gpt-4o"),
        )
        result = await Harness(agent).run("hi")
        assert result.cost is not None
        assert isinstance(result.cost, Cost)

    async def test_run_result_cost_model_matches(self):
        """RunResult.cost.model matches the model used for the run."""
        agent = create_agent(
            "model_match_test",
            model=_priced_mock(["response"], model="gpt-4o-mini"),
        )
        result = await Harness(agent).run("test")
        assert result.cost.model == "gpt-4o-mini"

    async def test_run_result_cost_has_tokens(self):
        """RunResult.cost has non-negative token counts."""
        agent = create_agent(
            "tokens_test",
            model=_priced_mock(["response"], model="gpt-4o"),
        )
        result = await Harness(agent).run("test")
        assert result.cost.input_tokens >= 0
        assert result.cost.output_tokens >= 0

    async def test_run_result_cost_usd_non_negative(self):
        """RunResult.cost.usd is always non-negative."""
        agent = create_agent(
            "usd_test",
            model=_priced_mock(["hello world"], model="claude-sonnet-4-5"),
        )
        result = await Harness(agent).run("hi")
        assert result.cost.usd >= 0.0

    async def test_multi_step_run_accumulates_cost(self):
        """Multi-step runs accumulate token counts from all model calls."""
        agent = create_agent(
            "multi_step_test",
            model=_priced_mock([ToolUseBlock(name="list_files", input={}), "final"]),
            tools=default_toolset(),
            budget=BudgetPolicy(max_usd=100.0),  # high limit so it doesn't stop
        )
        result = await Harness(agent).run("do something")
        # Two model calls happened (tool use + final), output tokens accumulate
        # MockModel's scripted path returns Usage(output_tokens=12) per call
        assert result.cost.output_tokens > 0
        assert result.steps == 2
        assert result.cost.usd > 0.0

    async def test_cost_reflects_usage_tokens(self):
        """RunResult.cost token counts match RunResult.usage tokens."""
        agent = create_agent(
            "usage_match_test",
            model=_priced_mock(["simple answer"], model="gpt-4o"),
        )
        result = await Harness(agent).run("question")
        assert result.cost.input_tokens == result.usage.input_tokens
        assert result.cost.output_tokens == result.usage.output_tokens


# ---------------------------------------------------------------------------
# BudgetPolicy.should_warn
# ---------------------------------------------------------------------------


class TestBudgetWarning:
    """Test the budget warning threshold (warn_at)."""

    def test_should_warn_at_threshold(self):
        """should_warn returns True when cost >= warn_at fraction of max_usd."""
        policy = BudgetPolicy(max_usd=1.00, warn_at=0.8)
        # 80% of $1.00 = $0.80
        cost = Cost(input_tokens=320_000, output_tokens=0, model="gpt-4o")
        # 320,000 * $2.50/M = $0.80
        assert policy.should_warn(cost) is True

    def test_should_not_warn_under_threshold(self):
        """should_warn returns False when cost < warn_at fraction."""
        policy = BudgetPolicy(max_usd=1.00, warn_at=0.8)
        cost = Cost(input_tokens=100_000, output_tokens=0, model="gpt-4o")
        # 100,000 * $2.50/M = $0.25 < $0.80
        assert policy.should_warn(cost) is False

    def test_warn_at_none_disables_warning(self):
        """When warn_at=None, should_warn always returns False."""
        policy = BudgetPolicy(max_usd=1.00, warn_at=None)
        cost = Cost(input_tokens=10_000_000, output_tokens=0, model="gpt-4o")
        assert policy.should_warn(cost) is False


# ---------------------------------------------------------------------------
# BudgetPolicy.check only raises for "raise" mode
# ---------------------------------------------------------------------------


class TestBudgetCheckModes:
    """Test that BudgetPolicy.check only raises for on_exceed='raise'."""

    def test_check_does_not_raise_for_stop_mode(self):
        """on_exceed='stop' — check() does nothing (Session handles stop logic)."""
        policy = BudgetPolicy(max_usd=0.01, on_exceed="stop")
        cost = Cost(input_tokens=1_000_000, output_tokens=0, model="gpt-4o")
        # Should not raise — 'stop' mode is handled by the session, not check()
        policy.check(cost)

    def test_check_does_not_raise_for_approve_mode(self):
        """on_exceed='approve' — check() does nothing (Session handles approval)."""
        policy = BudgetPolicy(max_usd=0.01, on_exceed="approve")
        cost = Cost(input_tokens=1_000_000, output_tokens=0, model="gpt-4o")
        policy.check(cost)
