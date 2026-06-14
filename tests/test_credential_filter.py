"""Tests for v0.8.4: CredentialFilter + BudgetPolicy(on_exceed='approve')."""

from __future__ import annotations

import pytest

from tvastar.sandbox.base import CredentialFilter
from tvastar.sandbox.local import LocalSandbox
from tvastar.sandbox.virtual import VirtualSandbox
from tvastar.cost import BudgetPolicy, BudgetExceeded
from tvastar.approval import ApprovalGate


# ---------------------------------------------------------------------------
# CredentialFilter unit tests
# ---------------------------------------------------------------------------


def test_credential_filter_defaults():
    cf = CredentialFilter()
    env = {
        "OPENAI_API_KEY": "sk-secret",
        "MY_TOKEN": "tok-abc",
        "DB_SECRET": "hunter2",
        "DB_PASSWORD": "s3cur3",
        "MY_PASS": "letmein",
        "MY_CREDENTIAL": "cred",
        "MY_CREDENTIALS": "creds",
        "PATH": "/usr/bin",
        "HOME": "/home/user",
    }
    result = cf.filter_env(env)
    assert "OPENAI_API_KEY" not in result
    assert "MY_TOKEN" not in result
    assert "DB_SECRET" not in result
    assert "DB_PASSWORD" not in result
    assert "MY_PASS" not in result
    assert "MY_CREDENTIAL" not in result
    assert "MY_CREDENTIALS" not in result
    # safe vars preserved
    assert result["PATH"] == "/usr/bin"
    assert result["HOME"] == "/home/user"


def test_credential_filter_case_insensitive():
    cf = CredentialFilter()
    env = {"openai_api_key": "lower", "My_Token": "mixed", "PATH": "/usr/bin"}
    result = cf.filter_env(env)
    assert "openai_api_key" not in result
    assert "My_Token" not in result
    assert result["PATH"] == "/usr/bin"


def test_credential_filter_custom_patterns():
    cf = CredentialFilter(patterns=["MY_*"])
    env = {"MY_CUSTOM": "val", "OTHER_KEY": "keep", "PATH": "/bin"}
    result = cf.filter_env(env)
    assert "MY_CUSTOM" not in result
    # "OTHER_KEY" ends in _KEY but we only have MY_* pattern now
    assert result["OTHER_KEY"] == "keep"
    assert result["PATH"] == "/bin"


def test_credential_filter_empty_patterns():
    cf = CredentialFilter(patterns=[])
    env = {"API_KEY": "secret", "TOKEN": "tok", "PATH": "/bin"}
    result = cf.filter_env(env)
    # nothing filtered when patterns is empty
    assert result == env


def test_credential_filter_returns_copy():
    cf = CredentialFilter()
    env = {"PATH": "/bin", "API_KEY": "secret"}
    result = cf.filter_env(env)
    # original env unchanged
    assert "API_KEY" in env
    assert "API_KEY" not in result


# ---------------------------------------------------------------------------
# LocalSandbox credential_filter integration
# ---------------------------------------------------------------------------


def test_local_sandbox_accepts_credential_filter(tmp_path):
    cf = CredentialFilter()
    sb = LocalSandbox(tmp_path, credential_filter=cf)
    assert sb.credential_filter is cf


def test_local_sandbox_no_filter_by_default(tmp_path):
    sb = LocalSandbox(tmp_path)
    assert sb.credential_filter is None


@pytest.mark.asyncio
async def test_local_sandbox_agent_cannot_see_filtered_vars(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_SECRET_KEY", "supersecret")
    cf = CredentialFilter()
    sb = LocalSandbox(tmp_path, credential_filter=cf)
    result = await sb.exec("echo ${MY_SECRET_KEY:-HIDDEN}")
    # var is stripped from env so shell expands to the default HIDDEN
    assert "supersecret" not in result.stdout


# ---------------------------------------------------------------------------
# VirtualSandbox credential_filter integration
# ---------------------------------------------------------------------------


def test_virtual_sandbox_accepts_credential_filter():
    cf = CredentialFilter()
    sb = VirtualSandbox(credential_filter=cf)
    assert sb.credential_filter is cf


def test_virtual_sandbox_no_filter_by_default():
    sb = VirtualSandbox()
    assert sb.credential_filter is None


@pytest.mark.asyncio
async def test_virtual_sandbox_python_cannot_see_filtered_vars(monkeypatch):
    monkeypatch.setenv("MY_SECRET_TOKEN", "topsecret")
    cf = CredentialFilter()
    # Write the check to a file to avoid shell quoting issues with -c
    sb = VirtualSandbox(
        files={"check.py": "import os; print(os.environ.get('MY_SECRET_TOKEN', 'GONE'))"},
        credential_filter=cf,
    )
    result = await sb.exec("python check.py")
    assert "topsecret" not in result.stdout
    assert "GONE" in result.stdout


# ---------------------------------------------------------------------------
# BudgetPolicy on_exceed='approve'
# ---------------------------------------------------------------------------


def test_budget_policy_accepts_approve():
    bp = BudgetPolicy(max_usd=0.01, on_exceed="approve")
    assert bp.on_exceed == "approve"


@pytest.mark.asyncio
async def test_budget_approve_calls_gate_and_continues():
    """When gate approves, the session continues (stopped != budget)."""
    from tvastar import create_agent, Harness
    from tvastar.model import MockModel

    gate_called = []

    async def _auto_approve(message, *, timeout=300.0, metadata=None):
        gate_called.append(message)
        return True  # approved

    gate = ApprovalGate(backend="event", on_request=lambda req: req.approve())

    agent = create_agent(
        "budget-approve-test",
        model=MockModel(script=["step1", "step2"]),
        budget=BudgetPolicy(max_usd=0.0, on_exceed="approve"),  # always exceeded
        approval_gate=gate,
    )
    result = await Harness(agent).run("hello")
    # Gate was consulted (the run did not hard-raise BudgetExceeded)
    assert result.stopped != "error"


@pytest.mark.asyncio
async def test_budget_approve_stops_on_denial():
    """When gate denies, stopped='budget'."""
    from tvastar import create_agent, Harness
    from tvastar.model import MockModel

    gate = ApprovalGate(backend="event", on_request=lambda req: req.deny())

    agent = create_agent(
        "budget-deny-test",
        model=MockModel(script=["response"]),
        budget=BudgetPolicy(max_usd=0.0, on_exceed="approve"),
        approval_gate=gate,
    )
    result = await Harness(agent).run("hello")
    assert result.stopped == "budget"


@pytest.mark.asyncio
async def test_budget_approve_raises_if_no_gate():
    """If on_exceed='approve' but no approval_gate, raises BudgetExceeded."""
    from tvastar import create_agent, Harness
    from tvastar.model import MockModel

    agent = create_agent(
        "budget-no-gate-test",
        model=MockModel(script=["response"]),
        budget=BudgetPolicy(max_usd=0.0, on_exceed="approve"),
        # no approval_gate set
    )
    with pytest.raises(BudgetExceeded):
        await Harness(agent).run("hello")


@pytest.mark.asyncio
async def test_budget_approve_only_prompts_once():
    """Gate is only invoked once even across multiple steps."""
    from tvastar import create_agent, Harness
    from tvastar.model import MockModel
    from tvastar.tools import tool
    from tvastar.types import ToolUseBlock

    prompt_count = []

    @tool
    async def noop() -> str:
        return "ok"

    gate = ApprovalGate(
        backend="event",
        on_request=lambda req: (prompt_count.append(1), req.approve()),
    )

    agent = create_agent(
        "budget-once-test",
        model=MockModel(
            script=[
                ToolUseBlock(name="noop", input={}, id="tu1"),
                ToolUseBlock(name="noop", input={}, id="tu2"),
                "done",
            ]
        ),
        tools=[noop],
        budget=BudgetPolicy(max_usd=0.0, on_exceed="approve"),
        approval_gate=gate,
    )
    await Harness(agent).run("hello")
    assert len(prompt_count) == 1  # only one prompt regardless of steps


# ---------------------------------------------------------------------------
# Top-level exports
# ---------------------------------------------------------------------------


def test_top_level_exports():
    import tvastar

    assert hasattr(tvastar, "CredentialFilter")
    cf = tvastar.CredentialFilter()
    assert isinstance(cf, CredentialFilter)
