"""Tests for tvastar.compliance — ComplianceVerifier."""

import json

import pytest

from tvastar.agent import AgentSpec
from tvastar.assurance.log import TrustLog
from tvastar.assurance.policy import AssurancePolicy
from tvastar.compliance import ArticleCheck, ComplianceReport, ComplianceVerifier
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


def _make_loop(
    assurance=None, detectors=None, approval_gate=None, handoff=None
):
    spec = AgentSpec(
        name="test-agent",
        model=FakeModel(),
        assurance=assurance,
        detectors=detectors or [],
        approval_gate=approval_gate,
    )
    config = LoopConfig(name="compliance-test", goal="test", handoff=handoff)
    return Loop(spec, config)


class TestComplianceVerifierTypeError:
    def test_none_raises(self):
        with pytest.raises(TypeError, match="None"):
            ComplianceVerifier().verify(None)

    def test_invalid_type_raises(self):
        with pytest.raises(TypeError, match="str"):
            ComplianceVerifier().verify("not a loop")

    def test_int_raises(self):
        with pytest.raises(TypeError, match="int"):
            ComplianceVerifier().verify(42)


class TestComplianceVerifierChecks:
    def test_all_fail_non_compliant(self):
        loop = _make_loop()
        report = ComplianceVerifier().verify(loop)
        assert report.status == "NON_COMPLIANT"
        assert len(report.articles) == 4
        assert all(not a.passed for a in report.articles)
        assert all(a.remediation for a in report.articles)

    def test_all_pass_compliant(self):
        trust_log = TrustLog()
        policy = AssurancePolicy(key="secret-hmac-key", log=trust_log)
        loop = _make_loop(
            assurance=policy,
            detectors=[FakeDetector()],
            approval_gate=lambda: True,  # any truthy object
        )
        report = ComplianceVerifier().verify(loop)
        assert report.status == "COMPLIANT"
        assert all(a.passed for a in report.articles)
        assert all(a.remediation == "" for a in report.articles)

    def test_article_12_trust_log(self):
        # No TrustLog -> fail
        policy = AssurancePolicy(key="k")
        loop = _make_loop(assurance=policy)
        report = ComplianceVerifier().verify(loop)
        art12 = next(a for a in report.articles if a.article == "Article 12")
        assert not art12.passed
        assert "TrustLog" in art12.remediation

    def test_article_14_handoff_satisfies(self):
        from tvastar.loop.handoff import HandoffPolicy

        class FakeHandoff(HandoffPolicy):
            async def escalate(self, loop_name, run, history):
                pass

        loop = _make_loop(handoff=FakeHandoff())
        report = ComplianceVerifier().verify(loop)
        art14 = next(a for a in report.articles if a.article == "Article 14")
        assert art14.passed

    def test_article_13_empty_key_fails(self):
        policy = AssurancePolicy(key="", log=TrustLog())
        loop = _make_loop(assurance=policy)
        report = ComplianceVerifier().verify(loop)
        art13 = next(a for a in report.articles if a.article == "Article 13")
        assert not art13.passed

    def test_article_9_no_detectors_fails(self):
        loop = _make_loop(detectors=[])
        report = ComplianceVerifier().verify(loop)
        art9 = next(a for a in report.articles if a.article == "Article 9")
        assert not art9.passed


class TestComplianceReportJSON:
    def test_to_json_structure(self):
        report = ComplianceReport(
            status="COMPLIANT",
            articles=[
                ArticleCheck("Article 12", "TrustLog", True),
                ArticleCheck("Article 9", "Detectors", False, "add detectors"),
            ],
            timestamp=1700000000.0,
        )
        data = json.loads(report.to_json())
        assert data["status"] == "COMPLIANT"
        assert data["timestamp"] == 1700000000.0
        assert "Article 12" in data["articles"]
        assert data["articles"]["Article 12"]["feature"] == "TrustLog"
        assert data["articles"]["Article 12"]["passed"] is True
        assert data["articles"]["Article 9"]["remediation"] == "add detectors"

    def test_to_json_is_valid_json(self):
        report = ComplianceReport(status="NON_COMPLIANT", articles=[])
        parsed = json.loads(report.to_json())
        assert parsed["status"] == "NON_COMPLIANT"
        assert parsed["articles"] == {}
