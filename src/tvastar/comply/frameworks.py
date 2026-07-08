"""Regulatory framework registry for multi-framework compliance checks."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Protocol, runtime_checkable


@runtime_checkable
class FrameworkCheck(Protocol):
    """A single compliance check within a framework."""

    article: str
    feature: str

    def __call__(self, loop: Any) -> Any: ...


@dataclass
class RegulatoryFramework:
    """A named set of compliance checks."""

    name: str  # "EU_AI_Act", "HIPAA", "CCPA", "GLBA", "DORA"
    checks: List[FrameworkCheck] = field(default_factory=list)


class _EUAIActCheck:
    """Lightweight callable wrapping a ComplianceVerifier method."""

    def __init__(self, article: str, feature: str, method_name: str) -> None:
        self.article = article
        self.feature = feature
        self._method_name = method_name

    def __call__(self, loop: Any) -> Any:
        from tvastar.compliance import ComplianceVerifier

        verifier = ComplianceVerifier()
        return getattr(verifier, self._method_name)(loop)


def _build_eu_ai_act() -> RegulatoryFramework:
    """Build the default EU_AI_Act framework with Articles 9, 12, 13, 14."""
    return RegulatoryFramework(
        name="EU_AI_Act",
        checks=[
            _EUAIActCheck("Article 9", "Detectors", "_check_article_9"),
            _EUAIActCheck("Article 12", "TrustLog", "_check_article_12"),
            _EUAIActCheck("Article 13", "SigningKey", "_check_article_13"),
            _EUAIActCheck("Article 14", "HumanOversight", "_check_article_14"),
        ],
    )


class FrameworkRegistry:
    """Registry of regulatory frameworks and their checks.

    Supports registering custom frameworks via Python API.
    Default: EU_AI_Act with Articles 9, 12, 13, 14.
    """

    def __init__(self) -> None:
        self._frameworks: dict[str, RegulatoryFramework] = {}
        # Register default EU_AI_Act
        self.register(_build_eu_ai_act())

    def register(self, framework: RegulatoryFramework) -> None:
        """Register a framework. Overwrites if name already exists."""
        self._frameworks[framework.name] = framework

    def get(self, name: str) -> RegulatoryFramework | None:
        """Get a framework by name, or None if not found."""
        return self._frameworks.get(name)

    def get_checks(self, name: str | None = None) -> List[FrameworkCheck]:
        """Get checks for a framework. Defaults to EU_AI_Act when name is None."""
        target = name if name is not None else "EU_AI_Act"
        fw = self._frameworks.get(target)
        if fw is None:
            return []
        return list(fw.checks)

    def list_frameworks(self) -> List[str]:
        """List all registered framework names."""
        return list(self._frameworks.keys())
