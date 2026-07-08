"""Core data models for the tvastar.comply package."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class PIIVerificationRecord:
    """Result of PII leak verification for a single ExecutionReceipt."""

    vault_active: bool
    token_count: int
    leak_count: int
    content_hash: str
    leaked_types: List[str]  # e.g. ["SSN", "EMAIL"]


@dataclass
class AuditResult:
    """Result of a single compliance audit for one Loop."""

    loop_name: str
    status: str  # "COMPLIANT" | "NON_COMPLIANT"
    framework: str
    checks: List[Any]  # List[ArticleCheck]
    pii_verification: Optional[PIIVerificationRecord]
    timestamp: float = field(default_factory=time.time)
    remediation: List[str] = field(default_factory=list)


@dataclass
class ComplianceAlert:
    """Alert emitted when compliance posture degrades."""

    severity: str  # "INFO" | "WARNING" | "CRITICAL"
    alert_type: str  # "DRIFT" | "CHAIN_BREACH" | "PII_LEAK"
    loop_name: str
    run_id: str  # "" if not applicable
    timestamp: float
    description: str
    suppression_count: int = 0


@dataclass
class LoopStatus:
    """Compliance status snapshot for a single Loop."""

    loop_name: str
    last_check: float
    status: str  # "COMPLIANT" | "NON_COMPLIANT" | "STALE"
    articles: List[Any]  # List[ArticleCheck]
    consecutive_compliant: int


@dataclass
class FleetSummary:
    """Aggregated compliance posture across all registered Loops."""

    total: int
    compliant: int
    non_compliant: int
    stale: int
    fleet_compliance_pct: float
    per_loop: List[LoopStatus]
    compliance_overhead: Optional[Dict[str, float]] = None  # loop_name → ratio


@dataclass
class ComplianceCostReport:
    """Token cost breakdown for compliance vs. business logic."""

    loop_name: str
    compliance_tokens: int
    total_tokens: int
    overhead_ratio: float  # compliance_tokens / total_tokens
    window_start: float
    window_end: float


@dataclass
class RetentionAction:
    """Record of a retention management action."""

    action: str  # "archive" | "hold_activated" | "hold_released"
    timestamp: float
    affected_count: int
    framework: str
