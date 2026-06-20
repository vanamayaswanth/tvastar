"""AssurancePolicy — configure verifiable execution for an agent.

Attach an ``AssurancePolicy`` to any agent to get:

- A signed :class:`~tvastar.assurance.receipt.ExecutionReceipt` on every run.
- Automatic appending to a :class:`~tvastar.assurance.log.TrustLog`.
- SLA enforcement: escalate, raise, or ignore when quality drops below threshold.

Usage::

    from tvastar.assurance import AssurancePolicy, TrustLog

    policy = AssurancePolicy(
        log=TrustLog(".tvastar-trust.jsonl"),
        min_score=80,         # PASS required (score ≥ 80)
        on_fail="escalate",
        on_escalate=lambda r: alert_team(r),
    )

    agent = create_agent("billing-bot", model=model, assurance=policy)
    result = await harness.run("Charge customer $50")

    print(result.receipt.content_hash)   # sha256:...
    print(result.receipt.verify())       # True
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Literal, Optional

if TYPE_CHECKING:  # pragma: no cover
    from .log import TrustLog
    from .receipt import ExecutionReceipt

__all__ = ["AssurancePolicy", "SLABreached"]

_ENV_KEY = "TVASTAR_RECEIPT_KEY"


class SLABreached(Exception):
    """Raised when ``on_fail='raise'`` and quality score drops below min_score."""

    def __init__(self, score: int, min_score: int, receipt: "ExecutionReceipt"):
        self.score = score
        self.min_score = min_score
        self.receipt = receipt
        super().__init__(
            f"SLA breached: quality score {score} < required {min_score} (run_id={receipt.run_id})"
        )


@dataclass
class AssurancePolicy:
    """Verifiable-execution configuration attached to an AgentSpec.

    Args:
        key: HMAC-SHA256 signing key. Falls back to the ``TVASTAR_RECEIPT_KEY``
             environment variable. If neither is set, receipts are unsigned but
             still content-hashed (tamper-detectable, not tamper-proof).
        log: A :class:`~tvastar.assurance.log.TrustLog` instance. When set,
             every receipt is appended to the log immediately after the run.
        min_score: Minimum acceptable Loop Quality score (0–100). 0 disables
                   SLA enforcement. When the run's quality score is below this
                   value, ``on_fail`` determines what happens.
        on_fail: Action taken when quality score < ``min_score``:
                 - ``"ignore"``  — do nothing (default)
                 - ``"raise"``   — raise :class:`SLABreached`
                 - ``"escalate"``— call ``on_escalate(receipt)``
        on_escalate: Callable invoked with the receipt when ``on_fail="escalate"``
                     and the SLA is breached. Can be sync or async.
    """

    key: str = field(default_factory=lambda: os.environ.get(_ENV_KEY, ""))
    log: Optional["TrustLog"] = None
    min_score: int = 0
    on_fail: Literal["ignore", "raise", "escalate"] = "ignore"
    on_escalate: Optional[Callable[["ExecutionReceipt"], None]] = None
    sanitize: Optional[Any] = None  # SanitizationPolicy | None

    def enforce_sla(self, receipt: "ExecutionReceipt") -> None:
        """Check SLA and take action if breached. Called synchronously."""
        if self.min_score <= 0:
            return
        if receipt.quality_score >= self.min_score:
            return
        if self.on_fail == "raise":
            raise SLABreached(receipt.quality_score, self.min_score, receipt)
        if self.on_fail == "escalate" and self.on_escalate is not None:
            import asyncio
            import inspect

            if inspect.iscoroutinefunction(self.on_escalate):
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        loop.create_task(self.on_escalate(receipt))
                    else:
                        loop.run_until_complete(self.on_escalate(receipt))
                except RuntimeError:
                    pass
            else:
                self.on_escalate(receipt)
