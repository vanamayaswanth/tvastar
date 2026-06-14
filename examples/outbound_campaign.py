"""tvastar-outbound — full campaign example.

Demonstrates the complete outbound pipeline:
  1. Parse leads from a CSV (or inline list)
  2. Research each lead in parallel via TaskGraph (web browse + search)
  3. Score each lead against the Ideal Customer Profile (0.0–1.0)
  4. Write a personalised cold email for each qualified lead
  5. Preview all drafts, then wait for approval
  6. Send

Run with a real model (needs ANTHROPIC_API_KEY):
    python examples/outbound_campaign.py

Dry-run (no API key needed — uses MockModel):
    python examples/outbound_campaign.py --mock
"""

from __future__ import annotations

import asyncio
import argparse
import sys


# ── Sample leads (swap for parse_csv("leads.csv")) ────────────────────────
SAMPLE_LEADS = [
    {
        "company": "LinearB",
        "name": "Ori Keren",
        "email": "ori@linearb.io",
        "title": "CEO",
        "website": "https://linearb.io",
    },
    {
        "company": "Trunk",
        "name": "Eli Schleifer",
        "email": "eli@trunk.io",
        "title": "CEO",
        "website": "https://trunk.io",
    },
]

ICP = (
    "B2B SaaS companies with 20–500 engineers that care deeply about developer "
    "productivity, CI/CD speed, and reducing toil for engineering teams."
)

SENDER = dict(
    sender_name="Jane Smith",
    sender_company="Acme Dev Tools",
    sender_email="jane@acmedevtools.com",
)


async def run(mock: bool = False) -> None:
    if mock:
        # Zero-dependency demo — MockModel, no API key, no web calls
        from tvastar.model import MockModel
        from tvastar.approval import ApprovalGate
        from tvastar.outbound import run_campaign, StdoutSender

        model = MockModel()
        gate = ApprovalGate(backend="event", on_request=lambda req: req.approve())

        result = await run_campaign(
            SAMPLE_LEADS,
            model=model,
            icp=ICP,
            min_score=0.0,  # MockModel returns 0.0; qualify everything
            approval_gate=gate,
            sender=StdoutSender(),
            **SENDER,
        )
    else:
        # Real run — uses Anthropic Claude + Jina AI web tools (no extra API key)
        from tvastar.model.anthropic import AnthropicModel
        from tvastar.outbound import run_campaign

        result = await run_campaign(
            SAMPLE_LEADS,
            model=AnthropicModel("claude-sonnet-4-5"),
            icp=ICP,
            min_score=0.5,
            **SENDER,
            # approval_gate defaults to CLI (prints drafts, reads stdin)
        )

    print(f"\n{'═' * 50}")
    print("Campaign result:")
    print(f"  Leads:     {result.leads_total}")
    print(f"  Qualified: {result.leads_qualified}")
    print(f"  Sent:      {result.sent}")
    print(f"  OK:        {result.ok}")


def main() -> None:
    parser = argparse.ArgumentParser(description="tvastar-outbound demo")
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Use MockModel — no API key or internet required",
    )
    args = parser.parse_args()
    asyncio.run(run(mock=args.mock))


if __name__ == "__main__":
    main()
