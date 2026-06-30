"""Unit tests for SecurityPolicy enforcement (Task 6.1).

Tests cover:
- denied_substrings matching raises SecurityViolation
- allowed_commands enforcement for first token
- denied_commands matching for first token
- Timeout handling returns ExecResult with timed_out=True

Requirements: 4.1, 4.2, 4.3, 4.5
"""

from __future__ import annotations

import pytest

from tvastar.errors import SecurityViolation
from tvastar.sandbox import LocalSandbox, SecurityPolicy
from tvastar.sandbox.base import ExecResult


# ---------------------------------------------------------------------------
# SecurityPolicy.check() — denied_substrings (Requirement 4.1)
# ---------------------------------------------------------------------------


class TestDeniedSubstrings:
    """WHEN a command matches a denied_substrings entry,
    THE SecurityPolicy SHALL raise SecurityViolation before execution."""

    def test_default_denied_substring_rm_rf_root(self):
        policy = SecurityPolicy()
        with pytest.raises(SecurityViolation, match="rm -rf /"):
            policy.check("rm -rf /")

    def test_default_denied_substring_fork_bomb(self):
        policy = SecurityPolicy()
        with pytest.raises(SecurityViolation):
            policy.check(":(){:|:&};:")

    def test_custom_denied_substring_raises(self):
        policy = SecurityPolicy(denied_substrings={"DROP TABLE"})
        with pytest.raises(SecurityViolation):
            policy.check("psql -c 'DROP TABLE users'")

    def test_denied_substring_anywhere_in_command(self):
        policy = SecurityPolicy(denied_substrings={"secret"})
        with pytest.raises(SecurityViolation):
            policy.check("echo secret > file.txt")

    def test_denied_substring_partial_match(self):
        """Substring matching is literal — 'rm -rf /' matches inside longer commands."""
        policy = SecurityPolicy(denied_substrings={"rm -rf /"})
        with pytest.raises(SecurityViolation):
            policy.check("sudo rm -rf / --no-preserve-root")

    def test_no_denied_substring_passes(self):
        policy = SecurityPolicy(denied_substrings={"DROP TABLE"})
        # Should not raise
        policy.check("echo hello world")

    def test_multiple_denied_substrings_first_match_wins(self):
        policy = SecurityPolicy(denied_substrings={"badA", "badB"})
        with pytest.raises(SecurityViolation):
            policy.check("echo badA badB")

    def test_empty_denied_substrings_allows_all(self):
        policy = SecurityPolicy(denied_substrings=set())
        # Even dangerous-looking commands pass if denied_substrings is empty
        policy.check("rm -rf /")


# ---------------------------------------------------------------------------
# SecurityPolicy.check() — allowed_commands (Requirement 4.2)
# ---------------------------------------------------------------------------


class TestAllowedCommands:
    """WHEN allowed_commands is non-empty AND the command's first token is not
    in the set, THE SecurityPolicy SHALL raise SecurityViolation."""

    def test_command_not_in_allowlist_raises(self):
        policy = SecurityPolicy(allowed_commands={"cat", "ls"})
        with pytest.raises(SecurityViolation, match="not in allowlist"):
            policy.check("echo hello")

    def test_command_in_allowlist_passes(self):
        policy = SecurityPolicy(allowed_commands={"cat", "ls", "echo"})
        # Should not raise
        policy.check("echo hello world")

    def test_first_token_is_checked(self):
        """Only the first token (the command name) is checked against the allowlist."""
        policy = SecurityPolicy(allowed_commands={"python"})
        # python is the first token, args don't matter
        policy.check("python -c 'print(hello)'")

    def test_empty_allowlist_allows_all(self):
        """When allowed_commands is empty, it acts as a no-op (not an allowlist)."""
        policy = SecurityPolicy(allowed_commands=set())
        policy.check("arbitrary_command --flag")

    def test_allowlist_with_path_command(self):
        policy = SecurityPolicy(allowed_commands={"git"})
        with pytest.raises(SecurityViolation):
            policy.check("rm file.txt")

    def test_allowlist_single_entry(self):
        policy = SecurityPolicy(allowed_commands={"make"})
        policy.check("make build")
        with pytest.raises(SecurityViolation):
            policy.check("cmake .")


# ---------------------------------------------------------------------------
# SecurityPolicy.check() — denied_commands (Requirement 4.3)
# ---------------------------------------------------------------------------


class TestDeniedCommands:
    """WHEN a command's first token matches denied_commands,
    THE SecurityPolicy SHALL raise SecurityViolation."""

    def test_default_denied_commands(self):
        policy = SecurityPolicy()
        for cmd in ("shutdown", "reboot", "mkfs", "dd"):
            with pytest.raises(SecurityViolation, match="denied by policy"):
                policy.check(f"{cmd} --some-arg")

    def test_custom_denied_command_raises(self):
        policy = SecurityPolicy(denied_commands={"curl", "wget"})
        with pytest.raises(SecurityViolation):
            policy.check("curl https://evil.com")

    def test_command_not_denied_passes(self):
        policy = SecurityPolicy(denied_commands={"shutdown", "reboot"})
        policy.check("echo hello")

    def test_denied_commands_checks_first_token_only(self):
        """Arguments containing the denied command name don't trigger the block."""
        policy = SecurityPolicy(denied_commands={"reboot"})
        # "echo" is the first token, not "reboot"
        policy.check("echo reboot scheduled for tonight")

    def test_empty_denied_commands_allows_all(self):
        policy = SecurityPolicy(denied_commands=set())
        policy.check("shutdown now")


# ---------------------------------------------------------------------------
# SecurityPolicy.check() — interaction between rules
# ---------------------------------------------------------------------------


class TestPolicyRuleInteraction:
    """Verify denied_substrings is checked before allowed/denied commands."""

    def test_denied_substring_checked_before_allowlist(self):
        """Even if the command's first token is in allowed_commands,
        a denied_substrings match still blocks it."""
        policy = SecurityPolicy(
            allowed_commands={"bash"},
            denied_substrings={"rm -rf /"},
        )
        with pytest.raises(SecurityViolation, match="rm -rf /"):
            policy.check("bash -c 'rm -rf /'")

    def test_allowed_commands_checked_before_denied_commands(self):
        """If allowed_commands is non-empty and the command is not in it,
        the denied_commands check is never reached."""
        policy = SecurityPolicy(
            allowed_commands={"cat"},
            denied_commands={"shutdown"},
        )
        # "shutdown" would match denied_commands, but since it's not in
        # allowed_commands, the allowlist check fires first
        with pytest.raises(SecurityViolation, match="not in allowlist"):
            policy.check("shutdown now")


# ---------------------------------------------------------------------------
# Timeout handling — ExecResult with timed_out=True (Requirement 4.5)
# ---------------------------------------------------------------------------


class TestTimeoutHandling:
    """WHEN a command exceeds timeout_seconds, THE Sandbox SHALL terminate
    the process and return ExecResult with timed_out=True."""

    async def test_local_sandbox_policy_timeout(self, tmp_path):
        """LocalSandbox respects SecurityPolicy.timeout_seconds."""
        policy = SecurityPolicy(timeout_seconds=0.1)
        sb = LocalSandbox(tmp_path, policy=policy)
        result = await sb.exec('python -c "import time; time.sleep(5)"')
        assert result.timed_out is True
        assert result.exit_code == 124

    async def test_local_sandbox_timeout(self, tmp_path):
        """LocalSandbox returns timed_out=True when command exceeds timeout."""
        policy = SecurityPolicy(timeout_seconds=0.1)
        sb = LocalSandbox(tmp_path, policy=policy)
        result = await sb.exec('python -c "import time; time.sleep(5)"')
        assert result.timed_out is True
        assert result.exit_code == 124

    async def test_timeout_exec_result_not_ok(self, tmp_path):
        """A timed-out ExecResult has ok=False."""
        policy = SecurityPolicy(timeout_seconds=0.1)
        sb = LocalSandbox(tmp_path, policy=policy)
        result = await sb.exec('python -c "import time; time.sleep(5)"')
        assert result.ok is False

    async def test_fast_command_no_timeout(self, tmp_path):
        """Commands completing within timeout do not have timed_out=True."""
        policy = SecurityPolicy(timeout_seconds=10.0)
        sb = LocalSandbox(tmp_path, policy=policy)
        result = await sb.exec("echo fast")
        assert result.timed_out is False
        assert result.ok is True

    async def test_caller_timeout_override(self, tmp_path):
        """Caller-passed timeout overrides policy when tighter."""
        policy = SecurityPolicy(timeout_seconds=60.0)
        sb = LocalSandbox(tmp_path, policy=policy)
        result = await sb.exec(
            'python -c "import time; time.sleep(5)"',
            timeout=0.1,
        )
        assert result.timed_out is True

    def test_exec_result_timed_out_field(self):
        """ExecResult dataclass correctly carries timed_out flag."""
        result = ExecResult(exit_code=124, stdout="", stderr="timed out", timed_out=True)
        assert result.timed_out is True
        assert result.ok is False

    def test_exec_result_render_shows_timeout(self):
        """ExecResult.render() includes [timed out] indicator."""
        result = ExecResult(exit_code=124, stdout="", stderr="", timed_out=True)
        assert "[timed out]" in result.render()
