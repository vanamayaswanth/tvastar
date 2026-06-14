"""Email sending — StdoutSender (dev/demo) as the default; subclass for SMTP/SendGrid."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from .email import EmailDraft

__all__ = ["SendResult", "EmailSender", "StdoutSender"]


@dataclass
class SendResult:
    draft: EmailDraft
    sent: bool
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.sent and not self.error


class EmailSender:
    """Base class for email sending. Subclass and override send_one()."""

    async def send_one(self, draft: EmailDraft) -> SendResult:
        raise NotImplementedError

    async def send_all(self, drafts: list[EmailDraft]) -> list[SendResult]:
        return list(await asyncio.gather(*[self.send_one(d) for d in drafts]))


class StdoutSender(EmailSender):
    """Prints emails to stdout — for development, demos, and dry-run mode."""

    async def send_one(self, draft: EmailDraft) -> SendResult:
        print(f"\n{'═' * 64}")
        print(f"  TO:      {draft.lead_name} <{draft.lead_email}>")
        print(f"  COMPANY: {draft.company}  (score: {draft.score:.0%})")
        print(f"  SUBJECT: {draft.subject}")
        print(f"  {'─' * 58}")
        for line in draft.body.splitlines():
            print(f"  {line}")
        print(f"{'═' * 64}")
        return SendResult(draft=draft, sent=True)
