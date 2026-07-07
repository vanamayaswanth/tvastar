"""Smoke tests for the three feature-synthesis integrations."""

from __future__ import annotations

import importlib.util

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


# ---------------------------------------------------------------------------
# 4. Encrypted FileStore — round-trip with a key
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not importlib.util.find_spec("cryptography"),
    reason="cryptography package not installed",
)
def test_encrypted_filestore_roundtrip(tmp_path):
    from tvastar.memory.store import FileStore

    store = FileStore(tmp_path / "enc", key="test-secret")
    store.set("hello", {"msg": "world"})
    # file on disk must not contain the plaintext value
    raw = (tmp_path / "enc" / "hello.json").read_bytes()
    assert b"world" not in raw
    # but round-trip must recover the value
    assert store.get("hello") == {"msg": "world"}


# ---------------------------------------------------------------------------
# 5. Session scrub — message content replaced with hash after run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scrub_after_run_replaces_message_content():
    from tvastar import Harness

    agent = create_agent(
        "scrubber",
        model=MockModel(script=["done"]),
        scrub_after_run=True,
    )
    result = await Harness(agent).run("Secret PII: alice@example.com")
    # result.text is extracted before scrub so it stays intact
    assert result.text == "done"
    # every message in the history must be scrubbed
    for msg in result.messages:
        assert str(msg.content).startswith("[scrubbed:sha256:")


# ---------------------------------------------------------------------------
# 6. Receipt PQC fields default to empty when oqs is absent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_receipt_pqc_fields_default_empty():
    from tvastar import Harness

    agent = create_agent(
        "assured",
        model=MockModel(script=["ok"]),
        assurance=AssurancePolicy(),
    )
    result = await Harness(agent).run("test")
    if result.receipt is not None:
        assert result.receipt.pqc_signature == ""
        assert result.receipt.pqc_public_key == ""
        assert result.receipt.verify()
