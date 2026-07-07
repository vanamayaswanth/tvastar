"""Email handoff channel — send handoff report via SMTP (stdlib only)."""

from __future__ import annotations

import asyncio
import os
import smtplib
from dataclasses import dataclass, field
from email.mime.text import MIMEText
from typing import TYPE_CHECKING

from ..handoff import HandoffPolicy

if TYPE_CHECKING:
    from .. import LoopRun


@dataclass
class EmailHandoff(HandoffPolicy):
    """Send a handoff report email via SMTP. Zero additional dependencies."""

    recipients: list[str] = field(default_factory=list)
    sender: str = ""
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_user: str | None = None
    smtp_pass: str | None = None
    use_tls: bool = False

    def __post_init__(self) -> None:
        # Resolve from env vars if not provided at construction
        if not self.smtp_host:
            self.smtp_host = os.environ.get("TVASTAR_SMTP_HOST")
        if not self.smtp_port:
            port_str = os.environ.get("TVASTAR_SMTP_PORT", "")
            self.smtp_port = int(port_str) if port_str else None
        if not self.smtp_user:
            self.smtp_user = os.environ.get("TVASTAR_SMTP_USER")
        if not self.smtp_pass:
            self.smtp_pass = os.environ.get("TVASTAR_SMTP_PASS")
        if not self.use_tls:
            self.use_tls = os.environ.get("TVASTAR_SMTP_TLS", "").lower() in ("1", "true", "yes")

    async def escalate(self, run: "LoopRun", history: list["LoopRun"]) -> None:
        subject = f"Loop Handoff: {run.loop_name} \u2014 {run.failure_kind.value if run.failure_kind else 'unknown'}"

        duration_str = f"{run.duration:.1f}s" if run.duration is not None else "unknown"
        body = (
            "LOOP HANDOFF REPORT\n"
            "====================\n"
            f"Loop: {run.loop_name}\n"
            f"Run ID: {run.run_id}\n"
            f"Iteration: {run.iteration}\n"
            f"Duration: {duration_str}\n"
            f"Error: {run.error or 'None'}\n"
            "\n"
            "ACTION REQUIRED\n"
        )

        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = self.sender
        msg["To"] = ", ".join(self.recipients)

        await asyncio.to_thread(self._send, msg)

    def _send(self, msg: MIMEText) -> None:
        """Blocking SMTP send — run via asyncio.to_thread."""
        port = self.smtp_port or (465 if self.use_tls else 587)
        if self.use_tls:
            conn = smtplib.SMTP_SSL(self.smtp_host or "localhost", port)
        else:
            conn = smtplib.SMTP(self.smtp_host or "localhost", port)
        try:
            if self.smtp_user and self.smtp_pass:
                conn.login(self.smtp_user, self.smtp_pass)
            conn.sendmail(self.sender, self.recipients, msg.as_string())
        finally:
            conn.quit()
