"""tvastar-outbound CLI — research, score, write, and send cold email campaigns."""

from __future__ import annotations

import argparse
import asyncio
import sys


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="tvastar-outbound",
        description=(
            "AI outbound sales agent — research leads in parallel, score against your ICP, "
            "write personalised emails, wait for approval, then send."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  tvastar-outbound --csv leads.csv --icp "B2B SaaS, 50+ employees" \\
      --sender-name "Jane" --sender-company "Acme" --sender-email jane@acme.com

  tvastar-outbound --csv leads.csv --icp "..." --sender-name "Jane" \\
      --sender-company "Acme" --sender-email jane@acme.com --dry-run

CSV columns (case-insensitive): company, name, email, title, website, linkedin_url
""",
    )
    parser.add_argument("--csv", metavar="PATH", required=True, help="CSV file with leads")
    parser.add_argument("--icp", required=True, help="Ideal Customer Profile description")
    parser.add_argument("--sender-name", required=True, dest="sender_name")
    parser.add_argument("--sender-company", required=True, dest="sender_company")
    parser.add_argument("--sender-email", required=True, dest="sender_email")
    parser.add_argument(
        "--min-score",
        type=float,
        default=0.5,
        dest="min_score",
        metavar="FLOAT",
        help="Min ICP fit score to qualify a lead (0.0–1.0). Default: 0.5",
    )
    parser.add_argument(
        "--max-leads",
        type=int,
        dest="max_leads",
        metavar="N",
        help="Cap on number of leads to process",
    )
    parser.add_argument(
        "--model",
        default="claude-sonnet-4-5",
        help="Model ID. Default: claude-sonnet-4-5",
    )
    parser.add_argument(
        "--provider",
        choices=["anthropic", "openai"],
        default="anthropic",
        help="Model provider. Default: anthropic",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=3,
        metavar="N",
        help="Max parallel research tasks. Default: 3",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="Preview emails without sending (skips approval gate)",
    )
    parser.add_argument(
        "--context",
        default="",
        metavar="TEXT",
        help="Extra context injected into the email-writing prompt",
    )

    args = parser.parse_args()
    asyncio.run(_run(args))


async def _run(args: argparse.Namespace) -> None:
    from .campaign import run_campaign

    model = _load_model(args.provider, args.model)

    approval_gate = None
    if args.dry_run:
        from ..approval import ApprovalGate

        approval_gate = ApprovalGate(backend="event", on_request=lambda req: req.deny())
        print("[outbound] Dry-run mode — emails will be previewed but not sent.\n")

    result = await run_campaign(
        args.csv,
        model=model,
        icp=args.icp,
        sender_name=args.sender_name,
        sender_company=args.sender_company,
        sender_email=args.sender_email,
        min_score=args.min_score,
        max_leads=args.max_leads,
        approval_gate=approval_gate,
        email_context=args.context,
        concurrency=args.concurrency,
    )

    print("\nCampaign summary:")
    print(f"  Leads total:   {result.leads_total}")
    print(f"  Researched:    {result.leads_researched}")
    print(f"  Qualified:     {result.leads_qualified}")
    print(f"  Drafted:       {result.leads_drafted}")
    print(f"  Approved:      {result.approved}")
    print(f"  Sent:          {result.sent}")
    if result.errors:
        print(f"  Errors:        {len(result.errors)}")
        for err in result.errors:
            print(f"    - {err}")

    sys.exit(0 if (result.ok or args.dry_run) else 1)


def _load_model(provider: str, model_id: str):
    if provider == "anthropic":
        from ..model.anthropic import AnthropicModel

        return AnthropicModel(model_id)
    from ..model.openai import OpenAIModel

    return OpenAIModel(model_id)
