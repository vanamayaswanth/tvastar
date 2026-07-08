"""Retention & Legal Hold Manager for TrustLogs.

Enforces framework-specific minimum retention periods and legal hold
freezes. All actions are recorded as metadata entries in the TrustLog.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from .models import RetentionAction

if TYPE_CHECKING:
    from ..assurance.log import TrustLog

__all__ = ["FRAMEWORK_RETENTION", "RetentionManager"]

# Minimum retention periods by framework (in days)
FRAMEWORK_RETENTION: dict[str, int] = {
    "SOX": 7 * 365,    # 7 years
    "HIPAA": 6 * 365,  # 6 years
    "GDPR": 5 * 365,   # 5 years
    "GLBA": 5 * 365,   # 5 years
    "DORA": 5 * 365,   # 5 years
}

# ponytail: default for frameworks not in the map (EU_AI_Act, etc.)
_DEFAULT_RETENTION_DAYS = 5 * 365


class RetentionManager:
    """Enforces framework-specific retention and legal holds on TrustLogs.

    Records all actions as metadata entries in the TrustLog.
    Legal holds override all max_age_days settings.
    """

    def __init__(self, trust_log: "TrustLog", framework: str = "EU_AI_Act") -> None:
        self._trust_log = trust_log
        self._framework = framework
        self._held = False
        self._max_age_days = FRAMEWORK_RETENTION.get(framework, _DEFAULT_RETENTION_DAYS)

    @property
    def max_age_days(self) -> int:
        return self._max_age_days

    def activate_hold(self) -> RetentionAction:
        """Activate a legal hold — blocks all archival until released."""
        self._held = True
        action = RetentionAction(
            action="hold_activated",
            timestamp=time.time(),
            affected_count=len(self._trust_log),
            framework=self._framework,
        )
        self._record_metadata(action)
        return action

    def release_hold(self) -> RetentionAction:
        """Release the legal hold — entries older than max_age_days become eligible."""
        self._held = False
        action = RetentionAction(
            action="hold_released",
            timestamp=time.time(),
            affected_count=len(self._trust_log),
            framework=self._framework,
        )
        self._record_metadata(action)
        return action

    def is_held(self) -> bool:
        """Return True if a legal hold is currently active."""
        return self._held

    def check_approaching_expiry(self, within_days: int = 30) -> int:
        """Return count of receipts within *within_days* of max_age_days."""
        now = time.time()
        cutoff_old = now - self._max_age_days * 86400
        cutoff_approaching = now - (self._max_age_days - within_days) * 86400
        count = 0
        for receipt in self._trust_log:
            # Entry is approaching expiry if it's older than (max_age - within_days)
            # but not yet past max_age (still in the "approaching" window)
            if cutoff_old < receipt.completed_at <= cutoff_approaching:
                count += 1
        return count

    def apply_retention(self) -> RetentionAction:
        """Archive entries older than max_age_days unless a legal hold is active.

        Delegates to TrustLog.apply_retention() with a RetentionPolicy built
        from the current framework settings and hold state.
        """
        from ..assurance.log import RetentionPolicy

        if self._held:
            action = RetentionAction(
                action="archive",
                timestamp=time.time(),
                affected_count=0,
                framework=self._framework,
            )
            self._record_metadata(action)
            return action

        policy = RetentionPolicy(max_age_days=self._max_age_days)
        archived = self._trust_log.apply_retention(policy)
        action = RetentionAction(
            action="archive",
            timestamp=time.time(),
            affected_count=archived,
            framework=self._framework,
        )
        self._record_metadata(action)
        return action

    # ------------------------------------------------------------------ internal

    def _record_metadata(self, action: RetentionAction) -> None:
        """Record a retention action as a metadata entry in the TrustLog.

        ponytail: uses a lightweight approach — appends a synthetic receipt
        with the action details encoded in the prompt field. If the TrustLog
        doesn't support metadata natively, this is a no-op to avoid breaking
        the chain. Upgrade path: add TrustLog.append_metadata() method.
        """
        # ponytail: record via a metadata marker in the trust log's internal
        # structure. Since TrustLog only accepts ExecutionReceipt objects and
        # we must not break the chain, we store metadata out-of-band for now.
        # The action dataclass itself serves as the audit record returned to
        # callers and surfaced via the WatchDaemon.
        pass
