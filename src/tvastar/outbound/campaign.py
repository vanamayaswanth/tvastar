"""run_campaign — full outbound pipeline: parse → research → score → write → approve → send."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..approval import ApprovalGate
    from ..model.base import Model

from .email import EmailDraft, write_draft
from .leads import Lead, parse_csv, parse_leads
from .research import ResearchResult, research_lead
from .score import ScoredLead, score_lead
from .send import EmailSender, SendResult, StdoutSender

__all__ = ["CampaignResult", "run_campaign"]


@dataclass
class CampaignResult:
    leads_total: int
    leads_researched: int
    leads_qualified: int
    leads_drafted: int
    approved: bool
    sent: int
    drafts: list[EmailDraft] = field(default_factory=list)
    send_results: list[SendResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.approved and self.sent > 0


async def run_campaign(
    leads,
    *,
    model: "Model",
    icp: str,
    sender_name: str,
    sender_company: str,
    sender_email: str,
    min_score: float = 0.5,
    max_leads: int | None = None,
    approval_gate: "ApprovalGate | None" = None,
    sender: EmailSender | None = None,
    email_context: str = "",
    max_research_steps: int = 10,
    concurrency: int = 5,
) -> CampaignResult:
    """Run a full outbound email campaign end-to-end.

    Steps:
    1. Parse leads from CSV path, list[dict], or list[Lead].
    2. Research each lead in parallel via TaskGraph (site + news + contact).
    3. Score each research result against the ICP.
    4. Filter qualified leads (score >= min_score).
    5. Write a personalised cold email for each qualified lead.
    6. Display all drafts, then wait for approval via ApprovalGate.
    7. Send approved emails.

    Args:
        leads:              CSV path, list of dicts, or list of Lead objects.
        model:              LLM to use (needs web_browse/web_search for research).
        icp:                Ideal Customer Profile — describe your target buyer in plain text.
        sender_name:        Your full name (appears in emails).
        sender_company:     Your company name.
        sender_email:       Your email address.
        min_score:          ICP fit threshold (0.0–1.0). Default 0.5.
        max_leads:          Cap on number of leads to process.
        approval_gate:      Gate to wait for before sending. Defaults to CLI prompt.
        sender:             EmailSender implementation. Defaults to StdoutSender.
        email_context:      Optional extra context injected into email-writing prompt.
        max_research_steps: Max agent steps per research sub-task. Default 10.
        concurrency:        Max parallel leads. Default 5.
    """
    from ..agent import create_agent
    from ..approval import ApprovalDenied, ApprovalGate, ApprovalTimeout
    from ..harness import Harness
    from ..tools import web_toolset

    # ── 1. Parse leads ────────────────────────────────────────────────────
    if isinstance(leads, str):
        lead_list = parse_csv(leads)
    elif leads and isinstance(leads[0], dict):
        lead_list = parse_leads(list(leads))
    else:
        lead_list = list(leads)

    if max_leads:
        lead_list = lead_list[:max_leads]

    errors: list[str] = []

    # ── 2. Research harness (web-enabled) ─────────────────────────────────
    research_agent = create_agent(
        "outbound-researcher",
        model=model,
        instructions=(
            "You are a B2B research specialist. Use web_browse to visit company "
            "websites and web_search to find news and professional information. "
            "Be concise, factual, and prioritise signals from the last 6 months."
        ),
        tools=web_toolset(),
        max_steps=max_research_steps,
    )
    research_harness = Harness(research_agent)

    # ── 3. Research all leads in parallel ─────────────────────────────────
    sem = asyncio.Semaphore(concurrency)

    async def _research(lead: Lead) -> ResearchResult:
        async with sem:
            return await research_lead(lead, research_harness)

    print(f"[outbound] Researching {len(lead_list)} lead(s)...")
    research_results: list[ResearchResult] = list(
        await asyncio.gather(*[_research(lead) for lead in lead_list])
    )
    for r in research_results:
        if not r.ok:
            errors.append(f"Research failed for {r.lead.display()}: {r.summary}")
    good_research = [r for r in research_results if r.ok]

    # ── Injection guard: research data is untrusted (scraped from the web) ──
    # Quarantine any lead whose research summary contains prompt-injection patterns.
    # Wrap clean summaries with wrap_untrusted() so the scorer/writer treats them
    # as opaque data, not instructions.
    from ..boundary import scan_for_injection, wrap_untrusted

    safe_research: list[ResearchResult] = []
    for r in good_research:
        hits = scan_for_injection(r.summary)
        if hits:
            errors.append(
                f"[QUARANTINED] {r.lead.display()}: prompt-injection pattern "
                f"detected in research data ({', '.join(hits)}). Lead skipped."
            )
            print(f"[outbound] WARNING: quarantined {r.lead.display()} (injection detected)")
        else:
            safe_research.append(
                ResearchResult(lead=r.lead, summary=wrap_untrusted(r.summary), ok=True)
            )
    good_research = safe_research

    # ── 4. Score all researched leads ─────────────────────────────────────
    score_agent = create_agent(
        "outbound-scorer",
        model=model,
        instructions=(
            "You are a precise B2B sales qualification specialist. "
            "Score leads against the Ideal Customer Profile with rigour — "
            "a score above 0.7 should mean genuine buying intent signals, not just category fit."
        ),
        max_steps=3,
    )
    score_harness = Harness(score_agent)

    async def _score(res: ResearchResult) -> ScoredLead:
        async with sem:
            return await score_lead(res, icp=icp, harness=score_harness, min_score=min_score)

    print(f"[outbound] Scoring {len(good_research)} lead(s)...")
    scored: list[ScoredLead] = list(await asyncio.gather(*[_score(r) for r in good_research]))
    scored.sort(key=lambda s: s.score, reverse=True)
    qualified = [s for s in scored if s.qualified]

    print(f"[outbound] {len(qualified)}/{len(scored)} lead(s) qualified (score >= {min_score:.0%})")

    if not qualified:
        return CampaignResult(
            leads_total=len(lead_list),
            leads_researched=len(good_research),
            leads_qualified=0,
            leads_drafted=0,
            approved=False,
            sent=0,
            errors=errors,
        )

    # ── 5. Write personalised emails ──────────────────────────────────────
    email_agent = create_agent(
        "outbound-writer",
        model=model,
        instructions=(
            "You write concise, personalised B2B cold emails that don't sound like cold emails. "
            "Lead with a specific insight about the recipient. Be brief. One CTA."
        ),
        max_steps=3,
    )
    email_harness = Harness(email_agent)

    async def _write(s: ScoredLead) -> EmailDraft:
        async with sem:
            return await write_draft(
                s,
                sender_name=sender_name,
                sender_company=sender_company,
                sender_email=sender_email,
                harness=email_harness,
                context=email_context,
            )

    print(f"[outbound] Writing {len(qualified)} email(s)...")
    drafts: list[EmailDraft] = list(await asyncio.gather(*[_write(q) for q in qualified]))

    # ── 6. Preview all drafts ─────────────────────────────────────────────
    _print_preview(drafts)

    # ── 7. Approval gate ──────────────────────────────────────────────────
    gate = approval_gate or ApprovalGate(backend="cli")
    try:
        await gate.request(
            f"Send {len(drafts)} email(s) to the lead(s) shown above?",
            timeout=600.0,
        )
    except (ApprovalDenied, ApprovalTimeout):
        print("[outbound] Sending cancelled.")
        return CampaignResult(
            leads_total=len(lead_list),
            leads_researched=len(good_research),
            leads_qualified=len(qualified),
            leads_drafted=len(drafts),
            approved=False,
            sent=0,
            drafts=drafts,
            errors=errors,
        )

    # ── 8. Send ───────────────────────────────────────────────────────────
    _sender = sender or StdoutSender()
    send_results = await _sender.send_all(drafts)
    sent = sum(1 for r in send_results if r.ok)
    print(f"[outbound] Sent {sent}/{len(drafts)} email(s).")

    return CampaignResult(
        leads_total=len(lead_list),
        leads_researched=len(good_research),
        leads_qualified=len(qualified),
        leads_drafted=len(drafts),
        approved=True,
        sent=sent,
        drafts=drafts,
        send_results=send_results,
        errors=errors,
    )


def _print_preview(drafts: list[EmailDraft]) -> None:
    print(f"\n{'═' * 70}")
    print(f"  OUTBOUND CAMPAIGN PREVIEW — {len(drafts)} email(s)")
    print(f"{'═' * 70}")
    for i, draft in enumerate(drafts, 1):
        print(
            f"\n[{i}/{len(drafts)}] {draft.lead_name} @ {draft.company}  (score: {draft.score:.0%})"
        )
        print(f"  TO:      {draft.lead_email}")
        print(f"  SUBJECT: {draft.subject}")
        print(f"  {'─' * 62}")
        for line in draft.body.splitlines():
            print(f"  {line}")
    print(f"\n{'═' * 70}\n")
