"""WatchDaemon — continuous compliance monitoring for registered Loops.

Runs as an asyncio task. Re-audits each loop at the configured interval.
Detects drift, chain breaches, PII leaks. Fault isolation: exceptions in
individual loop checks are logged and reported as alerts — they never crash
the daemon or affect other loops.
"""

from __future__ import annotations

import asyncio
import sys
import time
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from .audit import audit_compliance
from .models import AuditResult, ComplianceAlert
from .vault_verify import verify_pii_protection

if TYPE_CHECKING:
    from .alert import AlertEngine
    from .dashboard import ComplianceDashboard
    from .retention import RetentionManager

__all__ = ["WatchDaemon"]


def _get_loop_name(loop: Any) -> str:
    """Extract a loop name safely."""
    name = getattr(loop, "name", None)
    if name:
        return str(name)
    config = getattr(loop, "_config", None)
    if config:
        cfg_name = getattr(config, "name", None)
        if cfg_name:
            return str(cfg_name)
    return f"<{type(loop).__name__}>"


def _get_trust_log(loop: Any) -> Any:
    """Extract TrustLog from loop._base_spec.assurance.log, or None."""
    spec = getattr(loop, "_base_spec", None)
    policy = getattr(spec, "assurance", None) if spec else None
    return getattr(policy, "log", None) if policy else None


def _get_vault_configured(loop: Any) -> bool:
    """Check if TokenVault is configured on a loop."""
    spec = getattr(loop, "_base_spec", None)
    policy = getattr(spec, "assurance", None) if spec else None
    return getattr(policy, "vault", None) is not None


class WatchDaemon:
    """Continuous compliance monitoring for registered Loops.

    Runs as an asyncio task. Re-audits each loop at the configured
    interval. Detects drift, chain breaches, PII leaks.

    Fault isolation: exceptions in individual loop checks are logged
    and reported as alerts — they never crash the daemon or affect
    other loops.
    """

    def __init__(
        self,
        loops: List[Any],
        *,
        interval: float = 60.0,
        alert_engine: "AlertEngine | None" = None,
        dashboard: "ComplianceDashboard | None" = None,
        framework: str | None = None,
        retention_manager: "RetentionManager | None" = None,
    ) -> None:
        if not loops:
            raise ValueError("WatchDaemon requires at least one loop to monitor")
        self._loops = loops
        self._interval = interval
        self._framework = framework
        self._dashboard = dashboard
        self._retention_manager = retention_manager

        # Lazy import to avoid circular; use provided or create default
        if alert_engine is None:
            from .alert import AlertEngine
            self._alert_engine: AlertEngine = AlertEngine()
        else:
            self._alert_engine = alert_engine

        # State: previous audit results keyed by loop name
        self._prev_results: Dict[str, AuditResult] = {}
        self._running = False
        self._task: Optional[asyncio.Task[None]] = None

    async def start(self) -> None:
        """Start the monitoring loop. Logs config to stderr on start."""
        self._log_config()
        self._running = True
        self._task = asyncio.current_task()
        try:
            while self._running:
                await self._check_cycle()
                await asyncio.sleep(self._interval)
        except asyncio.CancelledError:
            pass

    async def stop(self) -> None:
        """Graceful shutdown."""
        self._running = False
        if self._task is not None and not self._task.done():
            self._task.cancel()

    # ------------------------------------------------------------------ internal

    def _log_config(self) -> None:
        """Log configuration to stderr on start."""
        loop_names = [_get_loop_name(lp) for lp in self._loops]
        sink_count = len(self._alert_engine._sinks)
        sys.stderr.write(
            f"[WatchDaemon] interval={self._interval}s "
            f"loops={loop_names} "
            f"sinks={sink_count}\n"
        )
        sys.stderr.flush()

    async def _check_cycle(self) -> None:
        """Run one full check cycle across all registered loops."""
        for loop in self._loops:
            # ponytail: fault isolation per loop — one failure doesn't affect others
            try:
                await self._check_one_loop(loop)
            except Exception as exc:
                loop_name = _get_loop_name(loop)
                self._alert_engine.emit(ComplianceAlert(
                    severity="WARNING",
                    alert_type="INTERNAL_ERROR",
                    loop_name=loop_name,
                    run_id="",
                    timestamp=time.time(),
                    description=f"Check cycle error: {exc}",
                ))

        # Retention expiry check (once per cycle, not per loop)
        self._check_retention_expiry()

    async def _check_one_loop(self, loop: Any) -> None:
        """Audit one loop and emit alerts for drift/chain/PII issues."""
        loop_name = _get_loop_name(loop)
        result = audit_compliance(loop, framework=self._framework)

        # Drift detection
        prev = self._prev_results.get(loop_name)
        drift_alert = self._detect_drift(loop_name, prev, result)
        if drift_alert:
            self._alert_engine.emit(drift_alert)

        # Chain integrity check
        chain_alert = self._check_chain_integrity(loop)
        if chain_alert:
            self._alert_engine.emit(chain_alert)

        # PII leak detection
        pii_alerts = self._check_pii_leaks(loop)
        for alert in pii_alerts:
            self._alert_engine.emit(alert)

        # Update stored state
        self._prev_results[loop_name] = result

        # Update dashboard
        if self._dashboard is not None:
            self._dashboard.update(loop_name, result)

    def _detect_drift(
        self, loop_name: str, prev: "AuditResult | None", curr: "AuditResult"
    ) -> Optional["ComplianceAlert"]:
        """Emit DRIFT alert only on COMPLIANT → NON_COMPLIANT transition."""
        if prev is None:
            return None
        if prev.status == "COMPLIANT" and curr.status == "NON_COMPLIANT":
            # Identify newly failed articles
            failed_articles = [
                getattr(c, "article", "unknown")
                for c in curr.checks
                if not getattr(c, "passed", True)
            ]
            return ComplianceAlert(
                severity="WARNING",
                alert_type="DRIFT",
                loop_name=loop_name,
                run_id="",
                timestamp=time.time(),
                description=(
                    f"Compliance drift: {loop_name} transitioned from COMPLIANT "
                    f"to NON_COMPLIANT. Failed articles: {failed_articles}"
                ),
            )
        return None

    def _check_chain_integrity(self, loop: Any) -> Optional["ComplianceAlert"]:
        """Verify TrustLog chain; emit CRITICAL alert with first corrupted run_id."""
        trust_log = _get_trust_log(loop)
        if trust_log is None:
            return None

        if trust_log.verify_chain():
            return None

        # Chain is broken — find first corrupted entry
        first_corrupted_run_id = ""
        prev_hash = ""
        for receipt in trust_log:
            if not receipt.verify() or receipt.prev_hash != prev_hash:
                first_corrupted_run_id = receipt.run_id
                break
            prev_hash = receipt.content_hash

        return ComplianceAlert(
            severity="CRITICAL",
            alert_type="CHAIN_BREACH",
            loop_name=_get_loop_name(loop),
            run_id=first_corrupted_run_id,
            timestamp=time.time(),
            description=(
                f"TrustLog chain integrity broken at {first_corrupted_run_id}"
            ),
        )

    def _check_pii_leaks(self, loop: Any) -> List["ComplianceAlert"]:
        """Scan receipts for bypassed TokenVault; emit CRITICAL alerts."""
        trust_log = _get_trust_log(loop)
        if trust_log is None:
            return []

        vault_configured = _get_vault_configured(loop)
        if not vault_configured:
            return []

        alerts: List[ComplianceAlert] = []
        loop_name = _get_loop_name(loop)

        for receipt in trust_log:
            verification = verify_pii_protection(receipt, vault_configured=True)
            if verification.leak_count > 0:
                alerts.append(ComplianceAlert(
                    severity="CRITICAL",
                    alert_type="PII_LEAK",
                    loop_name=loop_name,
                    run_id=receipt.run_id,
                    timestamp=time.time(),
                    description=(
                        f"PII leak detected in run {receipt.run_id}: "
                        f"leaked types {verification.leaked_types}"
                    ),
                ))
        return alerts

    def _check_retention_expiry(self) -> None:
        """Check retention expiry and emit WARNING if approaching."""
        if self._retention_manager is None:
            return
        count = self._retention_manager.check_approaching_expiry()
        if count > 0:
            self._alert_engine.emit(ComplianceAlert(
                severity="WARNING",
                alert_type="RETENTION_EXPIRY",
                loop_name="fleet",
                run_id="",
                timestamp=time.time(),
                description=(
                    f"{count} receipt(s) approaching retention expiry "
                    f"(within 30 days of max_age_days)"
                ),
            ))
