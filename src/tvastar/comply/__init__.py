"""tvastar.comply — Continuous compliance layer for Tvastar AI agents.

Provides fleet monitoring, multi-framework compliance auditing, alerting,
retention management, cost tracking, and CLI tooling on top of the existing
ComplianceVerifier → TrustLog → ExecutionReceipt chain.

    from tvastar.comply import AuditResult, ComplianceAlert, FleetSummary

All compliance operations are fault-isolated — exceptions are caught and
reported, never propagated into agent loops.
"""

from .alert import AlertEngine, AlertSink, CallbackSink, FileSink, StderrSink
from .audit import audit_compliance
from .cli import main
from .config import ComplianceConfig, load_config
from .cost import CostTracker
from .dashboard import ComplianceDashboard
from .exceptions import ComplianceError, LoopNotFoundError, RunNotFoundError
from .frameworks import FrameworkRegistry, RegulatoryFramework
from .models import (
    AuditResult,
    ComplianceAlert,
    ComplianceCostReport,
    FleetSummary,
    LoopStatus,
    PIIVerificationRecord,
    RetentionAction,
)
from .report import ReportGenerator
from .retention import FRAMEWORK_RETENTION, RetentionManager
from .vault_verify import verify_pii_protection
from .watch import WatchDaemon

__all__ = [
    # Alert engine
    "AlertEngine",
    # CLI entry point
    "main",
    # Config
    "ComplianceConfig",
    "load_config",
    # Cost tracking
    "CostTracker",
    # Dashboard
    "ComplianceDashboard",
    "AlertSink",
    "CallbackSink",
    "FileSink",
    "StderrSink",
    # Data models
    "AuditResult",
    "ComplianceAlert",
    "ComplianceCostReport",
    "FleetSummary",
    "LoopStatus",
    "PIIVerificationRecord",
    "RetentionAction",
    # Functions
    "audit_compliance",
    "verify_pii_protection",
    # Frameworks
    "FrameworkRegistry",
    "RegulatoryFramework",
    # Retention
    "FRAMEWORK_RETENTION",
    "RetentionManager",
    # Report
    "ReportGenerator",
    # Watch
    "WatchDaemon",
    # Exceptions
    "ComplianceError",
    "LoopNotFoundError",
    "RunNotFoundError",
]
