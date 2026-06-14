"""tvastar-outbound — AI-powered outbound email campaign agent.

Give it a CSV of leads. It researches each one in parallel (company site, news,
LinkedIn via web_browse + web_search), scores and prioritises them against your
Ideal Customer Profile, writes a personalised cold email for each, waits for
your approval via ApprovalGate, then sends.

Quick start::

    import asyncio
    from tvastar.model import AnthropicModel
    from tvastar.outbound import run_campaign

    result = asyncio.run(run_campaign(
        "leads.csv",
        model=AnthropicModel("claude-sonnet-4-5"),
        icp="B2B SaaS companies with 50+ employees struggling with developer productivity",
        sender_name="Jane Smith",
        sender_company="Acme",
        sender_email="jane@acme.com",
        min_score=0.6,
    ))
    print(f"Sent {result.sent}/{result.leads_qualified} emails.")

CLI::

    tvastar-outbound --csv leads.csv --icp "..." \\
        --sender-name Jane --sender-company Acme --sender-email jane@acme.com
"""

from .campaign import CampaignResult, run_campaign
from .email import EmailDraft, write_draft
from .leads import Lead, parse_csv, parse_leads
from .research import ResearchResult, research_lead
from .score import ScoredLead, score_lead
from .send import EmailSender, SendResult, StdoutSender

__all__ = [
    "run_campaign",
    "CampaignResult",
    "Lead",
    "parse_csv",
    "parse_leads",
    "ResearchResult",
    "research_lead",
    "ScoredLead",
    "score_lead",
    "EmailDraft",
    "write_draft",
    "EmailSender",
    "SendResult",
    "StdoutSender",
]
