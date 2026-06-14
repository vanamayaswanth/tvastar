"""Tests for 0.4.0 wiring: cost/budget, approval gate, eval, RunResult.cost."""

import pytest

from tvastar import (
    ApprovalDenied,
    ApprovalGate,
    BudgetExceeded,
    BudgetPolicy,
    Case,
    EvalSuite,
    Harness,
    ToolContext,
    assert_contains,
    assert_ok,
    cost_for_model,
    create_agent,
    default_toolset,
    require_approval,
    tool,
)
from tvastar.model import MockModel
from tvastar.types import ToolUseBlock


def _priced_mock(script, model="claude-opus-4-6"):
    m = MockModel(script)
    m.name = model  # so cost lookups hit the COST_TABLE
    return m


# ── cost on RunResult ──────────────────────────────────────────────────────


async def test_run_result_has_cost():
    agent = create_agent("t", model=_priced_mock(["hello"], model="gpt-4o"))
    result = await Harness(agent).run("hi")
    assert result.cost is not None
    assert result.cost.model == "gpt-4o"
    assert result.cost.usd >= 0.0


def test_cost_for_model_math():
    c = cost_for_model("gpt-4o", input_tokens=1_000_000, output_tokens=0)
    assert round(c.usd, 2) == 2.50  # $2.50 / 1M input tokens


# ── budget enforcement ──────────────────────────────────────────────────────


async def test_budget_raise_stops_with_exception():
    agent = create_agent(
        "t",
        model=_priced_mock(["a costly answer"]),
        budget=BudgetPolicy(max_usd=0.0000001, on_exceed="raise"),
    )
    with pytest.raises(BudgetExceeded):
        await Harness(agent).run("go")


async def test_budget_stop_ends_cleanly():
    agent = create_agent(
        "t",
        model=_priced_mock(
            [ToolUseBlock(name="list_files", input={}) for _ in range(5)] + ["done"]
        ),
        tools=default_toolset(),
        budget=BudgetPolicy(max_usd=0.0000001, on_exceed="stop"),
    )
    result = await Harness(agent).run("go")
    assert result.stopped == "budget"


# ── approval gate ────────────────────────────────────────────────────────────


async def test_require_approval_event_approve():
    gate = ApprovalGate(backend="event", on_request=lambda r: r.approve())
    await require_approval("Proceed?", gate=gate, timeout=5)  # should not raise


async def test_require_approval_event_deny():
    gate = ApprovalGate(backend="event", on_request=lambda r: r.deny())
    with pytest.raises(ApprovalDenied):
        await require_approval("Proceed?", gate=gate, timeout=5)


async def test_approval_gate_wired_through_agent():
    seen: list[str] = []
    gate = ApprovalGate(backend="event", on_request=lambda r: r.approve())

    @tool
    async def deploy(env: str, ctx: ToolContext) -> str:
        "Deploy to an environment."
        await require_approval(f"Deploy to {env}?", ctx=ctx)
        seen.append(env)
        return "deployed"

    agent = create_agent(
        "t",
        model=MockModel([ToolUseBlock(name="deploy", input={"env": "prod"}), "done"]),
        tools=[deploy],
        approval_gate=gate,  # exposed to the tool via ctx
    )
    await Harness(agent).run("deploy it")
    assert seen == ["prod"]  # the gate approved, so the tool ran


# ── eval harness ─────────────────────────────────────────────────────────────


async def test_eval_suite_scores_and_handles_cancel_after():
    # MockModel with no script echoes the prompt — deterministic under concurrency.
    agent = create_agent("t", model=MockModel(), tools=default_toolset())
    suite = EvalSuite(agent, concurrency=2)
    suite.add(Case(prompt="hello there", checks=[assert_contains("hello there"), assert_ok()]))
    suite.add(Case(prompt="abc", checks=[assert_contains("zzz")]))  # will fail
    # cancel_after used to TypeError before the fix; now it just works.
    suite.add(Case(prompt="quick", checks=[assert_ok()], cancel_after=10.0))

    report = await suite.run()
    assert report.total == 3
    assert report.passed == 2
    assert report.failed == 1
    assert 0.0 < report.score < 1.0
