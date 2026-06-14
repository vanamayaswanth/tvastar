"""Tests for v0.8.2 — ResourcePolicy + AuditEntry in LocalSandbox."""

from __future__ import annotations

import time

import pytest

import tvastar
from tvastar import AuditEntry, ResourcePolicy
from tvastar.errors import SecurityViolation
from tvastar.sandbox import LocalSandbox, SecurityPolicy


# ---------------------------------------------------------------------------
# ResourcePolicy dataclass
# ---------------------------------------------------------------------------


def test_resource_policy_defaults():
    rp = ResourcePolicy()
    assert rp.max_cpu_seconds == 30.0
    assert rp.max_memory_mb is None
    assert rp.max_output_chars == 50_000
    assert rp.allowed_domains == []


def test_resource_policy_custom():
    rp = ResourcePolicy(max_cpu_seconds=5.0, max_memory_mb=256, max_output_chars=1000)
    assert rp.max_cpu_seconds == 5.0
    assert rp.max_memory_mb == 256
    assert rp.max_output_chars == 1000


# ---------------------------------------------------------------------------
# AuditEntry factories
# ---------------------------------------------------------------------------


def test_audit_entry_blocked():
    entry = AuditEntry.blocked("rm -rf /", "denied by policy")
    assert not entry.allowed
    assert entry.violation == "denied by policy"
    assert entry.command == "rm -rf /"
    assert entry.exit_code is None
    assert entry.duration_ms is None
    assert entry.timestamp <= time.time()


def test_audit_entry_executed():
    entry = AuditEntry.executed("echo hi", exit_code=0, duration_ms=42.5)
    assert entry.allowed
    assert entry.violation is None
    assert entry.exit_code == 0
    assert entry.duration_ms == 42.5


# ---------------------------------------------------------------------------
# LocalSandbox with ResourcePolicy
# ---------------------------------------------------------------------------


def test_local_sandbox_has_audit_list(tmp_path):
    sb = LocalSandbox(tmp_path)
    assert sb.audit == []


def test_local_sandbox_accepts_resource_policy(tmp_path):
    rp = ResourcePolicy(max_cpu_seconds=10.0)
    sb = LocalSandbox(tmp_path, resources=rp)
    assert sb.resources is rp


def test_local_sandbox_default_resource_policy(tmp_path):
    sb = LocalSandbox(tmp_path)
    assert isinstance(sb.resources, ResourcePolicy)


@pytest.mark.asyncio
async def test_exec_records_success(tmp_path):
    sb = LocalSandbox(tmp_path)
    result = await sb.exec("echo hello")
    assert result.ok
    assert len(sb.audit) == 1
    entry = sb.audit[0]
    assert entry.allowed
    assert entry.command == "echo hello"
    assert entry.exit_code == 0
    assert entry.duration_ms is not None and entry.duration_ms >= 0


@pytest.mark.asyncio
async def test_exec_blocked_command_records_audit(tmp_path):
    policy = SecurityPolicy(denied_commands={"evil"})
    sb = LocalSandbox(tmp_path, policy=policy)
    with pytest.raises(SecurityViolation):
        await sb.exec("evil --run")
    assert len(sb.audit) == 1
    entry = sb.audit[0]
    assert not entry.allowed
    assert entry.violation is not None
    assert "evil" in entry.violation


@pytest.mark.asyncio
async def test_output_truncated_by_resource_policy(tmp_path):
    rp = ResourcePolicy(max_output_chars=10)
    sb = LocalSandbox(tmp_path, resources=rp)
    result = await sb.exec("echo 12345678901234567890")
    # stdout should be truncated to 10 chars + truncation notice
    assert len(result.stdout) > 10  # notice appended
    assert "truncated" in result.stdout


@pytest.mark.asyncio
async def test_cpu_timeout_via_resource_policy(tmp_path):
    rp = ResourcePolicy(max_cpu_seconds=0.1)
    sb = LocalSandbox(tmp_path, resources=rp)
    result = await sb.exec('python -c "import time; time.sleep(5)"')
    assert result.timed_out
    assert result.exit_code == 124
    # timeout is still audited
    assert len(sb.audit) == 1
    assert sb.audit[0].allowed  # command was allowed, just timed out


@pytest.mark.asyncio
async def test_caller_timeout_wins_when_tighter(tmp_path):
    rp = ResourcePolicy(max_cpu_seconds=10.0)
    sb = LocalSandbox(tmp_path, resources=rp)
    # caller passes 0.05s, resource policy allows 10s — caller wins
    result = await sb.exec('python -c "import time; time.sleep(5)"', timeout=0.05)
    assert result.timed_out


@pytest.mark.asyncio
async def test_resource_policy_timeout_wins_when_tighter(tmp_path):
    rp = ResourcePolicy(max_cpu_seconds=0.1)
    sb = LocalSandbox(tmp_path, resources=rp)
    # caller passes 10s, resource policy allows 0.1s — resource wins
    result = await sb.exec('python -c "import time; time.sleep(5)"', timeout=10.0)
    assert result.timed_out


@pytest.mark.asyncio
async def test_multiple_commands_accumulate_audit(tmp_path):
    sb = LocalSandbox(tmp_path)
    await sb.exec("echo a")
    await sb.exec("echo b")
    assert len(sb.audit) == 2
    assert sb.audit[0].command == "echo a"
    assert sb.audit[1].command == "echo b"


# ---------------------------------------------------------------------------
# Top-level exports
# ---------------------------------------------------------------------------


def test_top_level_exports():
    assert hasattr(tvastar, "ResourcePolicy")
    assert hasattr(tvastar, "AuditEntry")
    assert tvastar.ResourcePolicy is ResourcePolicy
    assert tvastar.AuditEntry is AuditEntry
