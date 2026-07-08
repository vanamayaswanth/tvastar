"""Tests for tvastar.comply.audit — audit_compliance function."""

from __future__ import annotations

import pytest

from tvastar.agent import AgentSpec
from tvastar.assurance.log import TrustLog
from tvastar.assurance.policy import AssurancePolicy
from tvastar.comply import audit_compliance, AuditResult, FrameworkRegistry
from tvastar.loop import Loop, LoopConfig
from tvastar.model import Model


class FakeModel(Model):
    name = "fake"

    async def generate(self, messages, **kw):
        pass


class FakeDetector:
    name = "silent-failure"

    async def detect(self, run):
        return []


def _make_loop(assurance=None, detectors=None, approval_gate=None, handoff=None):
    spec = AgentSpec(
        name="test-agent",
        model=FakeModel(),
        assurance=assurance,
        detectors=detectors or [],
        approval_gate=approval_gate,
    )
    config = LoopConfig(name="audit-test", goal="test")
    return Loop(spec, config)


class TestAuditComplianceInvalidLoop:
    def test_none_returns_non_compliant(self):
        result = audit_compliance(None)
        assert result.status == "NON_COMPLIANT"
        assert result.loop_name == "<None>"
        assert any("None" in r for r in result.remediation)

    def test_wrong_type_returns_non_compliant(self):
        result = audit_compliance("not a loop")
        assert result.status == "NON_COMPLIANT"
        assert any("str" in r for r in result.remediation)

    def test_int_returns_non_compliant(self):
        result = audit_compliance(42)
        assert result.status == "NON_COMPLIANT"
        assert any("int" in r for r in result.remediation)


class TestAuditComplianceStatus:
    def test_all_pass_compliant(self):
        trust_log = TrustLog()
        policy = AssurancePolicy(key="secret-hmac-key", log=trust_log)
        loop = _make_loop(
            assurance=policy,
            detectors=[FakeDetector()],
            approval_gate=lambda: True,
        )
        result = audit_compliance(loop)
        assert result.status == "COMPLIANT"
        assert result.framework == "EU_AI_Act"
        assert result.loop_name == "audit-test"
        assert len(result.checks) == 4
        assert all(c.passed for c in result.checks)
        assert result.remediation == []

    def test_some_fail_non_compliant(self):
        loop = _make_loop()  # No assurance, no detectors, no gate
        result = audit_compliance(loop)
        assert result.status == "NON_COMPLIANT"
        assert len(result.checks) == 4
        assert not all(c.passed for c in result.checks)
        assert len(result.remediation) > 0

    def test_remediation_text_for_failures(self):
        loop = _make_loop()
        result = audit_compliance(loop)
        for check in result.checks:
            if not check.passed:
                assert check.remediation != ""


class TestAuditComplianceFramework:
    def test_default_framework_is_eu_ai_act(self):
        loop = _make_loop()
        result = audit_compliance(loop)
        assert result.framework == "EU_AI_Act"

    def test_explicit_framework(self):
        loop = _make_loop()
        result = audit_compliance(loop, framework="EU_AI_Act")
        assert result.framework == "EU_AI_Act"

    def test_unknown_framework_returns_no_checks(self):
        loop = _make_loop()
        result = audit_compliance(loop, framework="UNKNOWN_FW")
        # No checks for unknown framework → all pass (vacuously true)
        assert result.status == "COMPLIANT"
        assert result.checks == []

    def test_custom_registry(self):
        from tvastar.comply.frameworks import RegulatoryFramework

        registry = FrameworkRegistry()
        # The default already has EU_AI_Act, verify it works through custom registry
        loop = _make_loop()
        result = audit_compliance(loop, registry=registry)
        assert result.framework == "EU_AI_Act"
        assert len(result.checks) == 4


class TestAuditComplianceFaultIsolation:
    def test_never_raises(self):
        """audit_compliance should never raise, even on bizarre inputs."""
        # These should all return AuditResult, never raise
        for bad_input in [None, 42, "string", [], {}, object()]:
            result = audit_compliance(bad_input)
            assert isinstance(result, AuditResult)
            assert result.status == "NON_COMPLIANT"


class TestAuditResultStructure:
    def test_has_all_required_fields(self):
        loop = _make_loop()
        result = audit_compliance(loop)
        assert hasattr(result, "loop_name")
        assert hasattr(result, "status")
        assert hasattr(result, "framework")
        assert hasattr(result, "checks")
        assert hasattr(result, "pii_verification")
        assert hasattr(result, "timestamp")
        assert hasattr(result, "remediation")
        assert result.timestamp > 0
