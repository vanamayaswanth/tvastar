"""Write personalised cold email drafts for qualified leads."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..harness import Harness

from .score import ScoredLead

__all__ = ["EmailDraft", "write_draft"]


@dataclass
class EmailDraft:
    lead_name: str
    lead_email: str
    company: str
    subject: str
    body: str
    score: float
    research_summary: str


async def write_draft(
    scored: ScoredLead,
    *,
    sender_name: str,
    sender_company: str,
    sender_email: str,
    harness: "Harness",
    context: str = "",
) -> EmailDraft:
    """Write a personalised cold email for a qualified lead.

    The email leads with a specific insight about the recipient (not about the
    sender), uses peer-to-peer tone, and ends with a single clear CTA.
    """
    try:
        from pydantic import BaseModel

        class _Email(BaseModel):
            subject: str
            body: str

        use_pydantic = True
    except ImportError:
        use_pydantic = False

    lead = scored.research.lead
    prompt = (
        f"Write a concise, personalised cold email from {sender_name} at {sender_company}.\n\n"
        f"## Sender\n"
        f"Name: {sender_name}\n"
        f"Company: {sender_company}\n"
        f"Email: {sender_email}\n\n"
        f"## Recipient\n{lead.display()}\n\n"
        f"## Research Brief\n{scored.research.summary}\n\n"
        f"## Why they scored {scored.score:.0%}\n{scored.rationale}\n\n"
        + (f"## Extra context\n{context}\n\n" if context else "")
        + "## Rules\n"
        "- Subject: short, specific, no clickbait or ALL CAPS\n"
        "- Body: 3-4 sentences max\n"
        "- Lead with a specific insight about THEM — not about us\n"
        "- One clear CTA (15-min call, reply with feedback, etc.)\n"
        "- No generic filler (no 'I hope this finds you well')\n"
        "- Tone: peer-to-peer, not salesy\n"
        "- Do NOT use their first name in the opening line"
    )

    sess = harness.session(f"email-{lead.email or lead.name}")
    async with sess:
        if use_pydantic:
            result = await sess.prompt(prompt, result=_Email)
            if isinstance(result.data, _Email):
                return EmailDraft(
                    lead_name=lead.name,
                    lead_email=lead.email,
                    company=lead.company,
                    subject=result.data.subject,
                    body=result.data.body,
                    score=scored.score,
                    research_summary=scored.research.summary,
                )
        result = await sess.prompt(prompt)

    # Fallback: extract Subject line from raw text
    lines = result.text.splitlines()
    subject = next(
        (ln.replace("Subject:", "").strip() for ln in lines if ln.startswith("Subject:")),
        "Quick thought",
    )
    body = "\n".join(ln for ln in lines if not ln.startswith("Subject:")).strip()

    return EmailDraft(
        lead_name=lead.name,
        lead_email=lead.email,
        company=lead.company,
        subject=subject,
        body=body or result.text,
        score=scored.score,
        research_summary=scored.research.summary,
    )
