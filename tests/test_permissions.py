"""Tests for subagent permission isolation (PermissionResolver)."""

from __future__ import annotations

import time

from tvastar.permissions import PermissionEntry, PermissionRegistry, PermissionResolver
from tvastar.sandbox.base import SecurityPolicy


class TestPermissionRegistry:
    def test_register_and_get(self):
        reg = PermissionRegistry()
        entry = PermissionEntry(allowed_commands=["ls", "cat"], network=True)
        reg.register("reader", entry)
        assert reg.get("reader") is entry

    def test_get_unknown_returns_none(self):
        reg = PermissionRegistry()
        assert reg.get("ghost") is None


class TestPermissionResolver:
    def _make_resolver(self, entries=None, trust_log=None):
        reg = PermissionRegistry()
        for name, entry in (entries or {}).items():
            reg.register(name, entry)
        return PermissionResolver(reg, trust_log=trust_log)

    def test_known_profile_builds_from_registry(self):
        entry = PermissionEntry(
            allowed_commands=["git", "npm"],
            deny_commands=["rm"],
            network=True,
            timeout_seconds=30.0,
        )
        resolver = self._make_resolver({"dev": entry})
        parent_policy = SecurityPolicy(network=True, timeout_seconds=120.0)

        child = resolver.resolve("dev", "parent-agent", parent_policy)

        # Derived from registry, NOT parent
        assert child.network is True
        assert "git" in child.allowed_commands
        assert "npm" in child.allowed_commands
        assert "rm" in child.denied_commands
        assert child.timeout_seconds == 30.0
        # Parent policy is ignored
        assert child.timeout_seconds != parent_policy.timeout_seconds

    def test_unknown_profile_deny_all(self):
        resolver = self._make_resolver({})
        parent_policy = SecurityPolicy(network=True)

        child = resolver.resolve("unknown-agent", "parent", parent_policy)

        assert child.network is False
        assert child.allowed_commands == set()
        assert "*" in child.denied_commands

    def test_parent_policy_never_inherited(self):
        """Even with a known profile, parent policy fields are ignored."""
        entry = PermissionEntry(network=False, timeout_seconds=45.0)
        resolver = self._make_resolver({"child": entry})
        parent_policy = SecurityPolicy(
            network=True,
            allowed_commands={"dangerous-cmd"},
            timeout_seconds=999.0,
        )

        child = resolver.resolve("child", "parent", parent_policy)

        assert child.network is False
        assert "dangerous-cmd" not in child.allowed_commands
        assert child.timeout_seconds == 45.0

    def test_trust_log_record_appended(self):
        log: list = []
        entry = PermissionEntry(allowed_commands=["echo"])
        resolver = self._make_resolver({"worker": entry}, trust_log=log)
        parent_policy = SecurityPolicy()

        resolver.resolve("worker", "boss", parent_policy)

        assert len(log) == 1
        record = log[0]
        assert record["type"] == "permission_resolution"
        assert record["parent_agent"] == "boss"
        assert record["child_agent"] == "worker"
        assert "parent_policy" in record
        assert "child_policy" in record
        assert "timestamp" in record

    def test_trust_log_appended_before_return(self):
        """TrustLog record must exist before child_policy is returned."""
        log: list = []
        entry = PermissionEntry()
        resolver = self._make_resolver({"x": entry}, trust_log=log)

        # The record is appended during resolve(), before the return
        child = resolver.resolve("x", "parent", SecurityPolicy())
        # By the time we have child, the log already has the record
        assert len(log) == 1
        assert log[0]["timestamp"] <= time.time()

    def test_no_trust_log_still_works(self):
        """PermissionResolver works without a TrustLog."""
        entry = PermissionEntry(network=True)
        resolver = self._make_resolver({"safe": entry}, trust_log=None)
        child = resolver.resolve("safe", "parent", SecurityPolicy())
        assert child.network is True

    def test_unknown_profile_logs_warning(self, caplog):
        import logging

        resolver = self._make_resolver({})
        with caplog.at_level(logging.WARNING, logger="tvastar.permissions"):
            resolver.resolve("mystery", "parent", SecurityPolicy())

        assert "unknown profile" in caplog.text.lower()
        assert "mystery" in caplog.text
        assert "parent" in caplog.text
