"""tvastar.assurance — Verifiable Execution for AI agents.

Make AI agent runs as trustworthy as compiled code. Every run produces a
cryptographically signed, chain-linked execution receipt — a tamper-evident
record of exactly what the agent was asked, every tool it called, what it
answered, and its Loop Quality score.

    Agent = AgentSpec + Harness
    Trust = Agent + AssurancePolicy + TrustLog

Quick start::

    from tvastar import create_agent
    from tvastar.assurance import AssurancePolicy, TrustLog

    policy = AssurancePolicy(
        log=TrustLog(".tvastar-trust.jsonl"),
        min_score=80,        # PASS required
        on_fail="escalate",
        on_escalate=lambda r: alert_slack(r),
    )

    agent = create_agent("billing-bot", model=model, assurance=policy)
    result = await harness.run("Charge customer $50")

    # Signed proof of what happened
    print(result.receipt.content_hash)   # sha256:abc123...
    print(result.receipt.verify())       # True

    # Audit the full log — detect tampering
    assert policy.log.verify_chain()
    for receipt in policy.log:
        print(receipt.run_id, receipt.quality_grade)
"""

from .log import TrustLog
from .policy import AssurancePolicy, SLABreached
from .receipt import ExecutionReceipt

__all__ = [
    "AssurancePolicy",
    "ExecutionReceipt",
    "TrustLog",
    "SLABreached",
]
