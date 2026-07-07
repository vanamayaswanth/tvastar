"""Compliance Audit Agent — verifiable AI audit trail for regulated industries.

Showcases:
- Verifiable execution (ExecutionReceipt + HMAC-SHA256 signatures)
- TrustLog (tamper-evident chain of receipts)
- PII/PHI sanitization (TokenVault + HIPAA policy)
- SLA enforcement (min quality score or breach)
- Audit report generation (text + HTML)
- GovernancePolicy (read-only phase — agent cannot modify data)

Usage:
    export ANTHROPIC_API_KEY=sk-...
    python examples/compliance_audit_agent.py
"""

import asyncio

from tvastar import Harness, create_agent, tool
from tvastar.assurance import (
    AssurancePolicy,
    SanitizationPolicy,
    TrustLog,
)
from tvastar.masking import GovernancePolicy
from tvastar.model import MockModel  # swap for AnthropicModel in production


# ---------------------------------------------------------------------------
# Tools — read-only audit capabilities
# ---------------------------------------------------------------------------


@tool
async def query_patient_records(patient_id: str) -> str:
    """Query patient records from the EHR system."""
    return (
        f"Patient {patient_id}: John Smith, DOB 03/15/1985, "
        f"SSN 123-45-6789, email john.smith@hospital.org, "
        f"Diagnosis: Type 2 Diabetes, Last visit: 2026-06-15"
    )


@tool
async def check_access_log(patient_id: str, days: int = 30) -> str:
    """Check who accessed a patient's records in the last N days."""
    return (
        f"Access log for {patient_id} (last {days} days):\n"
        f"  2026-06-20 09:14 — Dr. Adams (attending) — VIEW\n"
        f"  2026-06-18 14:30 — Nurse Chen (floor) — VIEW\n"
        f"  2026-06-15 11:00 — Dr. Adams (attending) — MODIFY\n"
        f"  2026-06-10 03:22 — admin_bot (system) — EXPORT ⚠️ unusual hour\n"
        f"4 access events. 1 flagged as unusual."
    )


@tool
async def check_consent_status(patient_id: str) -> str:
    """Verify the patient's data sharing consent status."""
    return f"Patient {patient_id}: Consent ACTIVE for treatment. Research consent DECLINED. Marketing DECLINED."


@tool
async def generate_finding(category: str, severity: str, description: str) -> str:
    """Record an audit finding."""
    return f"FINDING [{severity}] {category}: {description}"


# ---------------------------------------------------------------------------
# Governance — agent can ONLY read, never modify
# ---------------------------------------------------------------------------

governance = GovernancePolicy(
    phases={
        "audit": {
            "query_patient_records",
            "check_access_log",
            "check_consent_status",
            "generate_finding",
        },
    },
    current_phase="audit",
)

# ---------------------------------------------------------------------------
# PII sanitization — redacts PHI before model and in audit logs
# ---------------------------------------------------------------------------

sanitize = SanitizationPolicy.hipaa()

# ---------------------------------------------------------------------------
# Trust log + receipts — tamper-evident audit trail
# ---------------------------------------------------------------------------

trust_log = TrustLog()
assurance = AssurancePolicy(
    key="audit-signing-key-2026",  # HMAC key for receipt signatures
    log=trust_log,
    sanitize=sanitize,  # PII redacted in receipts
    min_score=80,
    on_fail="raise",  # SLA breach = raise exception
)

# ---------------------------------------------------------------------------
# Agent definition
# ---------------------------------------------------------------------------

agent = create_agent(
    "hipaa-compliance-auditor",
    model=MockModel(
        [
            "I'll audit patient P-4821's records for HIPAA compliance. "
            "Let me check the access logs, consent status, and look for anomalies.\n\n"
            "**Findings:**\n"
            "1. [WARNING] Unusual access at 03:22 by admin_bot — outside business hours, "
            "needs investigation\n"
            "2. [OK] All clinical access by authorized personnel (Dr. Adams, Nurse Chen)\n"
            "3. [OK] Consent is active for treatment use\n"
            "4. [OK] No data exports to research (consent declined, no exports found)\n\n"
            "**Recommendation:** Investigate the admin_bot EXPORT at 03:22. "
            "This could be a legitimate backup job or unauthorized data exfiltration. "
            "Suggest reviewing the bot's access policy and comparing against scheduled jobs."
        ]
    ),
    instructions="""You are a HIPAA compliance auditor. Your job:
1. Review patient record access for unauthorized use
2. Verify consent status matches actual data usage
3. Flag anomalous access patterns (unusual hours, bulk exports, unauthorized roles)
4. Generate findings with severity levels

Rules:
- You can ONLY read data. You cannot modify, delete, or export anything.
- Always check consent before reviewing records.
- Flag any access outside business hours (6am-8pm) by non-clinical systems.
- Be specific in findings — include who, when, and what action.
- Never include raw PII in your final report (the sanitizer handles this).
""",
    tools=[query_patient_records, check_access_log, check_consent_status, generate_finding],
    governance=governance,
    assurance=assurance,
    max_steps=10,
)


async def main():
    print("🏥 HIPAA Compliance Audit Agent")
    print("=" * 50)

    harness = Harness(agent)
    result = await harness.run(
        "Audit patient P-4821's records for HIPAA compliance. "
        "Check access logs, verify consent, and flag any anomalies."
    )

    print(f"\n📊 Quality: {result.quality.grade} (score: {result.quality.score})")
    print(f"📝 Steps: {result.steps}")

    # Verify the receipt
    if result.receipt:
        print("\n🔏 Execution Receipt:")
        print(f"   Run ID: {result.receipt.run_id}")
        print(f"   Agent: {result.receipt.agent}")
        print(f"   Signed: {'✅ HMAC-SHA256' if result.receipt.signature else '❌ unsigned'}")
        print(f"   Verified: {result.receipt.verify(key='audit-signing-key-2026')}")
        print(f"   Content Hash: {result.receipt.content_hash[:30]}...")

        # Check PII was redacted in the receipt
        print("\n🛡️  PII Sanitization:")
        print(f"   Prompt in receipt: {result.receipt.prompt[:80]}...")
        has_pii = "123-45-6789" in result.receipt.prompt
        print(f"   Raw SSN in receipt: {'❌ LEAKED!' if has_pii else '✅ Redacted'}")

    # Verify the trust log chain
    print("\n🔗 Trust Log:")
    print(f"   Entries: {len(trust_log)}")
    print(f"   Chain valid: {trust_log.verify_chain()}")

    # Generate audit report
    if result.receipt:
        report = result.receipt.to_audit_report()
        print("\n📋 Audit Report (first 500 chars):")
        print(f"   {report[:500]}")

    print(f"\n💬 Agent Response:\n{result.text}")


if __name__ == "__main__":
    asyncio.run(main())
