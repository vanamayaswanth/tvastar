"""Incident Auto-Responder — triages alerts, runs runbooks, escalates if stuck.

Showcases:
- Loop engineering (schedule + retry + exponential backoff + handoff)
- Human-in-the-loop (ApprovalGate for dangerous actions)
- GovernancePolicy (phase transitions: triage → investigate → mitigate → escalate)
- Task delegation (sub-agents for specialist work)
- Silent-failure detection (catches "resolved" claims over active incidents)
- Context compaction (long incident threads don't overflow)
- Cost tracking (budget per incident)

Usage:
    export ANTHROPIC_API_KEY=sk-...
    python examples/incident_responder.py
"""

import asyncio

from tvastar import (
    Harness,
    create_agent,
    define_agent_profile,
    tool,
)
from tvastar.approval import ApprovalGate
from tvastar.compaction import CompactionPolicy
from tvastar.cost import BudgetPolicy
from tvastar.loop import LoopConfig
from tvastar.masking import GovernancePolicy
from tvastar.model import MockModel  # swap for AnthropicModel in production


# ---------------------------------------------------------------------------
# Tools — incident response capabilities
# ---------------------------------------------------------------------------


@tool
async def check_service_health(service: str) -> str:
    """Check if a service is healthy (HTTP health endpoint)."""
    # In production: actual HTTP call to service health endpoint
    return f"Service '{service}' is DOWN — last healthy: 3 minutes ago, error: connection refused on port 8080"


@tool
async def query_logs(service: str, timeframe: str = "15m") -> str:
    """Query recent logs for a service."""
    return (
        f"Logs for {service} (last {timeframe}):\n"
        "ERROR 2026-06-28T10:42:01 OOMKilled: container exceeded 512Mi memory limit\n"
        "ERROR 2026-06-28T10:42:00 Java heap space: java.lang.OutOfMemoryError\n"
        "WARN  2026-06-28T10:41:55 GC pause 4.2s — old gen 98% full\n"
        "3 relevant log entries found."
    )


@tool
async def restart_service(service: str) -> str:
    """Restart a service (requires approval for production)."""
    return f"Service '{service}' restarted successfully. New pod running, health check passing."


@tool
async def scale_service(service: str, replicas: int) -> str:
    """Scale a service to N replicas."""
    return f"Scaled '{service}' to {replicas} replicas. All pods healthy."


@tool
async def notify_oncall(message: str, severity: str = "P2") -> str:
    """Page the on-call engineer via PagerDuty."""
    return f"[{severity}] Paged on-call: {message}"


@tool
async def update_status_page(service: str, status: str, message: str) -> str:
    """Update the public status page."""
    return f"Status page updated: {service} → {status}: {message}"


# ---------------------------------------------------------------------------
# Governance — phased response
# ---------------------------------------------------------------------------

governance = GovernancePolicy(
    phases={
        # Phase 1: read-only investigation
        "triage": {"check_service_health", "query_logs"},
        # Phase 2: can take action
        "mitigate": {"check_service_health", "query_logs", "restart_service",
                     "scale_service", "update_status_page"},
        # Phase 3: can escalate to humans
        "escalate": {"notify_oncall", "update_status_page"},
    },
    current_phase="triage",
)

# ---------------------------------------------------------------------------
# Human approval for dangerous actions
# ---------------------------------------------------------------------------


def on_approval_request(req):
    """Auto-approve for demo. In production: Slack/PagerDuty integration."""
    print(f"  🙋 APPROVAL REQUESTED: {req.message}")
    req.approve(approver="demo-auto-approve")


approval_gate = ApprovalGate(backend="event", on_request=on_approval_request)

# ---------------------------------------------------------------------------
# Sub-agent profiles for delegation
# ---------------------------------------------------------------------------

log_analyst = define_agent_profile(
    "log-analyst",
    description="Analyzes logs and identifies root causes from error patterns",
    instructions="You are a log analysis specialist. Identify the root cause from log entries. Be specific about the error type and recommend a fix.",
    max_steps=5,
)

mitigation_specialist = define_agent_profile(
    "mitigation-specialist",
    description="Executes remediation actions like restarts and scaling",
    instructions="You execute remediation. Be careful — verify health after every action. If the fix doesn't work, say so clearly.",
    max_steps=8,
)

# ---------------------------------------------------------------------------
# Agent definition
# ---------------------------------------------------------------------------

agent = create_agent(
    "incident-responder",
    model=MockModel([
        # Simulated conversation for demo
        "Let me check the service health and investigate the logs.",
        "Based on the logs, the service is experiencing an OOM (Out of Memory) error. "
        "The container exceeded its 512Mi memory limit. I recommend:\n"
        "1. Restart the service to recover immediately\n"
        "2. Scale to 3 replicas to distribute memory pressure\n"
        "3. Update the status page\n\n"
        "The root cause is a memory leak — the Java heap filled up (98% old gen). "
        "This needs a code fix long-term, but restart + scale handles the immediate incident."
    ]),
    instructions="""You are an incident response agent. When an alert fires:

1. TRIAGE: Check service health and query logs. Identify the root cause.
2. MITIGATE: Take the minimum action to restore service. Prefer restart before scale.
3. VERIFY: Confirm the service is healthy after mitigation.
4. COMMUNICATE: Update the status page. Escalate to on-call only if you cannot resolve.

Rules:
- Never restart production services without confirming the issue first.
- If you cannot identify the root cause in 3 steps, escalate immediately.
- Always update the status page — even if the fix works.
- Be honest: if the mitigation is temporary, say so.
""",
    tools=[check_service_health, query_logs, restart_service,
           scale_service, notify_oncall, update_status_page],
    governance=governance,
    approval_gate=approval_gate,
    subagents=[log_analyst, mitigation_specialist],
    budget=BudgetPolicy(max_usd=1.00, on_exceed="stop"),
    compaction=CompactionPolicy(max_messages=30, keep_last=8, min_messages=6),
    max_steps=15,
)

# ---------------------------------------------------------------------------
# Loop config — runs on alert trigger
# ---------------------------------------------------------------------------

loop_config = LoopConfig(
    name="incident-responder",
    goal="Triage, mitigate, and resolve production incidents automatically",
    schedule="@manual",  # triggered by alert webhook
    max_iterations=3,
    retry_backoff_base=15.0,
    circuit_breaker_limit=5,
)


async def main():
    print("🚨 Incident Auto-Responder")
    print("=" * 50)
    print("Alert: service-payments is DOWN\n")

    harness = Harness(agent)
    result = await harness.run(
        "ALERT: service-payments is DOWN. "
        "Last healthy 3 minutes ago. "
        "Investigate and restore service."
    )

    print(f"\n📊 Quality: {result.quality.grade} (score: {result.quality.score})")
    print(f"📝 Steps: {result.steps}")

    if result.findings:
        print("\n⚠️  Findings:")
        for f in result.findings:
            print(f"  [{f.severity.value}] {f.detector}: {f.message}")

    if result.ok:
        print("\n✅ Incident handled successfully")
    else:
        print("\n❌ Incident requires human intervention")

    print(f"\n💬 Response:\n{result.text}")


if __name__ == "__main__":
    asyncio.run(main())
