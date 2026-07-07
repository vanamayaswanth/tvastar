"""Security Remediation Agent — auto-fixes CVEs in your dependencies.

Showcases:
- Loop engineering (retry + backoff + circuit breaker + handoff)
- Sandbox execution (SecurityPolicy enforces what the agent can run)
- Silent-failure detection (catches "fixed" claims over failing builds)
- Quality scoring (PASS/WARN/FAIL gates the merge)
- Budget enforcement (caps token spend per run)
- Durable execution (crash at step 47 resumes from step 47)
- Verifiable execution (ExecutionReceipt proves what happened)

Usage:
    export ANTHROPIC_API_KEY=sk-...
    python examples/security_remediation_agent.py
"""

import asyncio

from tvastar import (
    Harness,
    create_agent,
    tool,
)
from tvastar.assurance import AssurancePolicy, TrustLog
from tvastar.compaction import CompactionPolicy
from tvastar.cost import BudgetPolicy
from tvastar.loop import LoopConfig
from tvastar.masking import GovernancePolicy
from tvastar.model import MockModel  # swap for AnthropicModel in production


# ---------------------------------------------------------------------------
# Tools — what the agent can do
# ---------------------------------------------------------------------------


@tool
async def scan_vulnerabilities(target: str = ".") -> str:
    """Run a vulnerability scanner on the project."""
    # In production: calls `pip-audit`, `trivy`, or `grype`
    return (
        "VULN-001: requests==2.28.0 has CVE-2023-32681 (HIGH) — upgrade to >=2.31.0\n"
        "VULN-002: cryptography==39.0.0 has CVE-2023-49083 (CRITICAL) — upgrade to >=41.0.6\n"
        "2 vulnerabilities found."
    )


@tool
async def apply_patch(package: str, target_version: str) -> str:
    """Upgrade a package to a fixed version."""
    # In production: runs `pip install --upgrade` or edits requirements.txt
    return f"Upgraded {package} to {target_version} in requirements.txt"


@tool
async def run_tests(test_cmd: str = "pytest tests/ -q") -> str:
    """Run the test suite to verify the fix doesn't break anything."""
    # In production: actually runs the command in sandbox
    return "42 passed, 0 failed in 3.2s"


@tool
async def create_pr(title: str, body: str) -> str:
    """Create a pull request with the security fix."""
    return f"PR #142 created: '{title}'"


# ---------------------------------------------------------------------------
# Governance — phase-based tool control
# ---------------------------------------------------------------------------

governance = GovernancePolicy(
    phases={
        # Phase 1: can only scan (read-only)
        "scan": {"scan_vulnerabilities"},
        # Phase 2: can apply patches (write)
        "remediate": {"scan_vulnerabilities", "apply_patch", "run_tests"},
        # Phase 3: can create PR (deploy)
        "deploy": {"create_pr"},
    },
    current_phase="scan",
)

# ---------------------------------------------------------------------------
# Safety constraints
# ---------------------------------------------------------------------------

budget = BudgetPolicy(max_usd=2.00, on_exceed="stop")

compaction = CompactionPolicy(max_messages=40, keep_last=10, min_messages=8)

# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------

trust_log = TrustLog()
assurance = AssurancePolicy(
    log=trust_log,
    min_score=70,
    on_fail="escalate",
    on_escalate=lambda receipt: print(f"⚠️  SLA BREACH: score={receipt.quality_score}"),
)

# ---------------------------------------------------------------------------
# Agent definition
# ---------------------------------------------------------------------------

agent = create_agent(
    "security-remediation",
    model=MockModel(
        [
            "Let me scan for vulnerabilities first.",
            "Found 2 CVEs. I'll upgrade both packages and run tests to verify.",
            "Both packages upgraded successfully. Tests pass (42 passed, 0 failed). "
            "Creating a PR with the security fix.\n\n"
            "Summary:\n"
            "- Fixed CVE-2023-32681 in requests (upgraded to 2.31.0)\n"
            "- Fixed CVE-2023-49083 in cryptography (upgraded to 41.0.6)\n"
            "- All 42 tests pass after the upgrade\n"
            "- PR #142 created for review",
        ]
    ),
    instructions="""You are a security remediation agent. Your job:
1. SCAN: Run vulnerability scan to find CVEs
2. REMEDIATE: For each CVE, upgrade the package to the fixed version
3. VERIFY: Run tests to confirm nothing broke
4. DEPLOY: Create a PR with the fix

Rules:
- Never skip the test step. If tests fail after a patch, revert and try an alternative.
- Report honestly. If you cannot fix a CVE, say so.
- Do not modify any file outside requirements.txt and pyproject.toml.
""",
    tools=[scan_vulnerabilities, apply_patch, run_tests, create_pr],
    governance=governance,
    budget=budget,
    compaction=compaction,
    assurance=assurance,
    max_steps=20,
)

# ---------------------------------------------------------------------------
# Loop — runs on schedule with retry and handoff
# ---------------------------------------------------------------------------

loop_config = LoopConfig(
    name="security-sweep",
    goal="Scan for vulnerabilities, patch them, verify, and open a PR",
    schedule="@daily",
    max_iterations=3,
    retry_backoff_base=30.0,
    circuit_breaker_limit=5,
)


async def main():
    print("🔒 Security Remediation Agent")
    print("=" * 50)

    # Single run (for demo — in production, use loop.start())
    harness = Harness(agent, durable=True)
    result = await harness.run(
        "Scan the project for security vulnerabilities, fix them, "
        "run tests to verify, and create a PR with the fix."
    )

    print(f"\n📊 Quality: {result.quality.grade} (score: {result.quality.score})")
    print(f"💰 Cost: ${result.cost.usd:.4f}")
    print(f"📝 Steps: {result.steps}")
    print(f"🛑 Stopped: {result.stopped}")

    if result.findings:
        print("\n⚠️  Findings:")
        for f in result.findings:
            print(f"  [{f.severity.value}] {f.detector}: {f.message}")

    if result.receipt:
        print(f"\n🔏 Receipt: {result.receipt.run_id}")
        print(f"   Verified: {result.receipt.verify()}")

    print(f"\n🔗 Trust Log: {len(trust_log)} entries, chain valid: {trust_log.verify_chain()}")
    print(f"\n💬 Response:\n{result.text}")


if __name__ == "__main__":
    asyncio.run(main())
