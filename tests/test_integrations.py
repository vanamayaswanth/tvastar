"""Smoke tests for the three feature-synthesis integrations."""
from __future__ import annotations

import pytest

from tvastar import (
    AgentPruner,
    AssurancePolicy,
    SanitizationPolicy,
    TokenVault,
    create_agent,
)
from tvastar.loop import Loop, LoopConfig
from tvastar.model.mock import MockModel
from tvastar.profiles import AgentProfile


# ---------------------------------------------------------------------------
# 1. AssurancePolicy(vault=...) — auto-tokenize prompt, rehydrate output
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_vault_tokenizes_prompt_and_rehydrates_output():
    vault = TokenVault()
    policy = AssurancePolicy(vault=vault, sanitize=SanitizationPolicy.hipaa())
    agent = create_agent(
        "pii-safe",
        model=MockModel(script=["All done."]),
        assurance=policy,
    )
    from tvastar import Harness

    result = await Harness(agent).run("Email alice@example.com for info.")
    # Prompt was tokenized: vault should hold at least one entry
    assert len(vault) >= 1
    # The message stored in the session must NOT contain real PII
    user_msg = result.messages[0]
    assert "alice@example.com" not in str(user_msg.content)


# ---------------------------------------------------------------------------
# 2. AgentSpec(pruner=...) — auto-update pruner after sess.task()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pruner_auto_updated_after_task():
    pruner = AgentPruner(threshold=0.0, min_runs=1)
    profiles = [
        AgentProfile(name="coder", description="Write Python code"),
    ]
    agent = create_agent(
        "orchestrator",
        model=MockModel(script=["delegating"]),
        subagents=profiles,
        pruner=pruner,
    )
    from tvastar import Harness

    harness = Harness(agent)
    sess = harness.session()
    async with sess:
        await sess.task("Write a hello function", agent="coder")

    # pruner should have been updated for "coder"
    assert "coder" in pruner._scores


# ---------------------------------------------------------------------------
# 3. LoopConfig(budget=...) — cumulative cost cap suspends the loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_loop_budget_suspends_when_exceeded():
    from tvastar.cost import BudgetPolicy
    from tvastar.loop import LoopState

    config = LoopConfig(
        name="budget-test",
        goal="do something",
        budget=BudgetPolicy(max_usd=0.0),  # zero cap → suspend after first run
    )
    agent = create_agent("looper", model=MockModel(script=["done", "done", "done"]))
    loop = Loop(agent, config)
    run = await loop.trigger()
    assert run.state == LoopState.SUSPENDED
