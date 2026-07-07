"""EU AI Act high-risk system obligation checker.

Checks Articles 9, 12, 13, 14 against a Loop's configuration and produces
a machine-readable ComplianceReport.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ArticleCheck:
    article: str  # e.g. "Article 12"
    feature: str  # e.g. "TrustLog"
    passed: bool
    remediation: str = ""  # populated on failure


@dataclass
class ComplianceReport:
    status: str  # "COMPLIANT" | "NON_COMPLIANT"
    articles: list[ArticleCheck] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)

    def to_json(self) -> str:
        """Machine-readable JSON with article mappings, status, timestamp."""
        return json.dumps(
            {
                "status": self.status,
                "timestamp": self.timestamp,
                "articles": {
                    a.article: {
                        "feature": a.feature,
                        "passed": a.passed,
                        "remediation": a.remediation,
                    }
                    for a in self.articles
                },
            }
        )


class ComplianceVerifier:
    """Programmatic EU AI Act high-risk system obligation checker.

    Checks Articles 9, 12, 13, 14 against a Loop's configuration.
    """

    def verify(self, loop: Any) -> ComplianceReport:
        """Run all four article checks and return a ComplianceReport.

        Raises:
            TypeError: if loop is None or not a Loop instance.
        """
        from .loop import Loop

        if loop is None or not isinstance(loop, Loop):
            raise TypeError(
                f"Expected Loop instance, got {type(loop).__name__ if loop else 'None'}"
            )

        checks: list[ArticleCheck] = []
        checks.append(self._check_article_12(loop))
        checks.append(self._check_article_14(loop))
        checks.append(self._check_article_13(loop))
        checks.append(self._check_article_9(loop))

        all_pass = all(c.passed for c in checks)
        return ComplianceReport(
            status="COMPLIANT" if all_pass else "NON_COMPLIANT",
            articles=checks,
        )

    def _check_article_12(self, loop: Any) -> ArticleCheck:
        """Record-keeping: AssurancePolicy with TrustLog configured."""
        spec = loop._base_spec
        policy = getattr(spec, "assurance", None)
        has_trust_log = policy is not None and getattr(policy, "log", None) is not None
        return ArticleCheck(
            article="Article 12",
            feature="TrustLog",
            passed=has_trust_log,
            remediation=""
            if has_trust_log
            else (
                "Set AgentSpec.assurance to an AssurancePolicy with a TrustLog instance "
                "in the 'log' field to satisfy Article 12 record-keeping requirements."
            ),
        )

    def _check_article_14(self, loop: Any) -> ArticleCheck:
        """Human oversight: ApprovalGate or handoff policy configured."""
        spec = loop._base_spec
        has_approval_gate = getattr(spec, "approval_gate", None) is not None
        has_handoff = getattr(loop.config, "handoff", None) is not None
        passed = has_approval_gate or has_handoff
        return ArticleCheck(
            article="Article 14",
            feature="HumanOversight",
            passed=passed,
            remediation=""
            if passed
            else (
                "Set AgentSpec.approval_gate to an ApprovalGate instance or "
                "set LoopConfig.handoff to a HandoffPolicy to satisfy Article 14 "
                "human oversight requirements."
            ),
        )

    def _check_article_13(self, loop: Any) -> ArticleCheck:
        """Transparency: non-empty signing key on AssurancePolicy."""
        spec = loop._base_spec
        policy = getattr(spec, "assurance", None)
        key = getattr(policy, "key", "") if policy is not None else ""
        passed = bool(key)
        return ArticleCheck(
            article="Article 13",
            feature="SigningKey",
            passed=passed,
            remediation=""
            if passed
            else (
                "Set AssurancePolicy.key to a non-empty HMAC-SHA256 or PQC/ML-DSA-65 "
                "signing key (or set TVASTAR_RECEIPT_KEY env var) to satisfy Article 13 "
                "transparency requirements."
            ),
        )

    def _check_article_9(self, loop: Any) -> ArticleCheck:
        """Risk management: at least one detector in AgentSpec."""
        spec = loop._base_spec
        detectors = getattr(spec, "detectors", [])
        passed = len(detectors) > 0
        return ArticleCheck(
            article="Article 9",
            feature="Detectors",
            passed=passed,
            remediation=""
            if passed
            else (
                "Add at least one silent-failure detector to AgentSpec.detectors "
                "to satisfy Article 9 risk management requirements."
            ),
        )
