"""ReportGenerator — regulator-ready reports from ExecutionReceipts.

Generates text, HTML, and JSON reports by retrieving an ExecutionReceipt
from a TrustLog and formatting it for regulatory submission.

Reuses the existing ``ExecutionReceipt.to_audit_report()`` renderers for
text and HTML formats. JSON format uses ``receipt.to_dict()`` with optional
PII proof and compliance metadata.

Usage::

    from tvastar.assurance import TrustLog
    from tvastar.comply.report import ReportGenerator

    log = TrustLog(".tvastar-trust.jsonl")
    gen = ReportGenerator(log)

    # To stdout
    print(gen.generate("run_abc123", fmt="text"))

    # To file
    gen.generate("run_abc123", fmt="html", output="report.html")
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:  # pragma: no cover
    from ..assurance.log import TrustLog

__all__ = ["ReportGenerator"]


class ReportGenerator:
    """Generates regulator-ready reports from ExecutionReceipts.

    Formats: JSON, text, HTML (self-contained).
    Includes cryptographic proof section when TokenVault is active.
    """

    def __init__(self, trust_log: "TrustLog") -> None:
        self._log = trust_log

    def generate(
        self,
        run_id: str,
        *,
        fmt: str = "text",
        include_pii_proof: bool = True,
        output: Optional[str] = None,
    ) -> str:
        """Retrieve receipt by run_id and render formatted report.

        Args:
            run_id:           The run identifier to look up in the TrustLog.
            fmt:              Output format — ``"text"``, ``"html"``, or ``"json"``.
            include_pii_proof: Include cryptographic PII proof section (default True).
            output:           File path to write to. If None, returns string only.

        Returns:
            The formatted report as a string.

        Raises:
            KeyError: if run_id not found in TrustLog.
        """
        receipt = self._log.get(run_id)
        if receipt is None:
            raise KeyError(
                f"run_id {run_id!r} not found in TrustLog. "
                f"The log contains {len(self._log)} entries."
            )

        if fmt == "json":
            result = self._render_json(receipt, include_pii_proof=include_pii_proof)
        elif fmt == "html":
            result = self._render_html(receipt, include_pii_proof=include_pii_proof)
        else:
            result = self._render_text(receipt, include_pii_proof=include_pii_proof)

        if output is not None:
            Path(output).write_text(result, encoding="utf-8")
        return result

    # ------------------------------------------------------------------ renderers

    def _render_text(self, receipt, *, include_pii_proof: bool) -> str:
        """Text report — delegates to receipt.to_audit_report("text") + PII proof."""
        report = receipt.to_audit_report("text")
        if include_pii_proof:
            report += "\n" + self._pii_proof_text(receipt)
        return report

    def _render_html(self, receipt, *, include_pii_proof: bool) -> str:
        """HTML report — delegates to receipt.to_audit_report("html") + PII proof."""
        html = receipt.to_audit_report("html")
        if include_pii_proof:
            # Insert PII proof section before closing </body>
            pii_section = self._pii_proof_html(receipt)
            html = html.replace("</body>", pii_section + "\n</body>")
        return html

    def _render_json(self, receipt, *, include_pii_proof: bool) -> str:
        """JSON report — receipt.to_dict() plus optional PII proof."""
        data = receipt.to_dict()
        if include_pii_proof:
            data["pii_proof"] = self._pii_proof_dict(receipt)
        return json.dumps(data, indent=2, sort_keys=False)

    # ------------------------------------------------------------------ PII proof

    def _pii_proof_dict(self, receipt) -> dict:
        """Structured PII proof data."""
        from .vault_verify import verify_pii_protection

        # ponytail: vault_configured=True is the optimistic default for report
        # generation — the caller can set include_pii_proof=False to skip entirely
        record = verify_pii_protection(receipt, vault_configured=True)
        return {
            "vault_active": record.vault_active,
            "token_count": record.token_count,
            "content_hash": receipt.content_hash,
            "leak_count": record.leak_count,
            "leaked_types": record.leaked_types,
        }

    def _pii_proof_text(self, receipt) -> str:
        """Plain-text PII proof section."""
        proof = self._pii_proof_dict(receipt)
        lines = [
            "PII PROTECTION PROOF:",
            f"  Vault Active:   {proof['vault_active']}",
            f"  Token Count:    {proof['token_count']}",
            f"  Content Hash:   {proof['content_hash']}",
            f"  Leak Count:     {proof['leak_count']}",
        ]
        if proof["leaked_types"]:
            lines.append(f"  Leaked Types:   {', '.join(proof['leaked_types'])}")
        return "\n".join(lines)

    def _pii_proof_html(self, receipt) -> str:
        """HTML PII proof section."""
        proof = self._pii_proof_dict(receipt)

        def esc(s: str) -> str:
            return (
                s.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;")
            )

        leaked = esc(", ".join(proof["leaked_types"])) if proof["leaked_types"] else "<em>none</em>"
        return f"""
<h2>PII Protection Proof</h2>
<table class="proof">
  <tr><td>Vault Active</td><td><strong>{proof["vault_active"]}</strong></td></tr>
  <tr><td>Token Count</td><td>{proof["token_count"]}</td></tr>
  <tr><td>Content Hash</td><td><code>{esc(proof["content_hash"])}</code></td></tr>
  <tr><td>Leak Count</td><td>{proof["leak_count"]}</td></tr>
  <tr><td>Leaked Types</td><td>{leaked}</td></tr>
</table>"""
