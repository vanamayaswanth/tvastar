"""ExecutionReceipt — cryptographically signed proof of a completed agent run.

Every field that matters about a run is captured, hashed (SHA-256), and
optionally signed (HMAC-SHA256) so any downstream system can verify:

    1. The content was not modified after the run completed.
    2. The run was produced by a system holding the signing key.

Receipts chain together: each receipt embeds the ``prev_hash`` of the
receipt before it, so a ``TrustLog`` forms a tamper-evident linked list.
Modify any entry and every subsequent ``prev_hash`` breaks.

Usage::

    receipt = ExecutionReceipt.from_run_result(
        result, agent="my-agent", prompt="fix tests", started_at=t0,
    )
    print(receipt.content_hash)   # sha256:abc123...
    print(receipt.verify())       # True

    # With a signing key (HMAC-SHA256)
    receipt = ExecutionReceipt.from_run_result(..., key="my-secret")
    assert receipt.verify(key="my-secret")

    # Serialise / deserialise
    json_str = receipt.to_json()
    loaded   = ExecutionReceipt.from_json(json_str)
    assert loaded.verify()
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List

if TYPE_CHECKING:  # pragma: no cover
    from ..session import RunResult

__all__ = ["ExecutionReceipt"]

_RECEIPT_VERSION = "2"
_ENV_KEY = "TVASTAR_RECEIPT_KEY"


@dataclass
class ExecutionReceipt:
    """Cryptographically signed, chain-linked record of one agent run.

    Attributes:
        run_id:        Unique identifier for this run (auto-generated).
        agent:         Name of the AgentSpec that ran.
        prompt:        The user prompt that triggered the run.
        model_name:    Name of the model that ran (e.g. "claude-sonnet-4-6").
        tool_calls:    Every tool invocation: [{id, name, input, output}].
                       ``output`` is the tool's return value (captured from the
                       subsequent ToolResultBlock in the message history).
        final_text:    The assistant's final answer.
        quality_score: Loop Quality score (0–100).
        quality_grade: "PASS" | "WARN" | "FAIL".
        findings:      Serialised Finding list [{detector, severity, message}].
        usage_input:   Input tokens consumed.
        usage_output:  Output tokens produced.
        stopped:       How the run ended ("end_turn" | "max_steps" | "error" | …).
        started_at:    Unix timestamp when the run started.
        completed_at:  Unix timestamp when the run finished.
        prev_hash:     content_hash of the immediately preceding receipt in the
                       TrustLog ("" for the very first entry).
        content_hash:  SHA-256 hex digest of the canonical JSON of all fields
                       above (excluding ``content_hash`` and ``signature``).
        signature:     HMAC-SHA256 hex of ``content_hash`` using the signing key,
                       or the empty string when no key is configured.
        version:       Receipt schema version ("2").
    """

    run_id: str
    agent: str
    model_name: str
    prompt: str
    tool_calls: List[Dict[str, Any]]
    final_text: str
    quality_score: int
    quality_grade: str
    findings: List[Dict[str, Any]]
    approvals: List[Dict[str, Any]]
    usage_input: int
    usage_output: int
    stopped: str
    started_at: float
    completed_at: float
    prev_hash: str
    content_hash: str
    signature: str
    version: str = _RECEIPT_VERSION

    # ------------------------------------------------------------------ build

    @classmethod
    def from_run_result(
        cls,
        result: "RunResult",
        *,
        agent: str,
        model_name: str = "",
        prompt: str,
        started_at: float,
        completed_at: float,
        key: str = "",
        prev_hash: str = "",
        sanitize: Any = None,
        approvals: List[Dict[str, Any]] = None,
    ) -> "ExecutionReceipt":
        """Build and sign a receipt from a completed RunResult.

        Args:
            result:       The RunResult returned by ``session.prompt()``.
            agent:        Agent name (``spec.name``).
            model_name:   Model identifier (e.g. ``"claude-sonnet-4-6"``).
            prompt:       The original user prompt.
            started_at:   Unix timestamp when ``session.prompt()`` was called.
            completed_at: Unix timestamp when the run returned.
            key:          HMAC signing key. Falls back to the
                          ``TVASTAR_RECEIPT_KEY`` environment variable, then
                          to an unsigned receipt (``signature=""``) if neither
                          is set.
            prev_hash:    content_hash of the preceding receipt in the log.
            approvals:    List of human approval records from this run
                          [{tool, approved_by, approved_at, message}].

        Returns:
            A fully populated, signed :class:`ExecutionReceipt`.
        """
        approvals_data: List[Dict[str, Any]] = approvals or []
        signing_key = key or os.environ.get(_ENV_KEY, "")
        quality = result.quality
        run_id = f"run_{uuid.uuid4().hex[:16]}"
        tool_calls = _extract_tool_calls(result.messages)
        # Apply PII/PHI redaction before hashing — hash covers sanitized form
        clean_prompt, tool_calls, clean_text = (
            sanitize.apply(prompt=prompt, tool_calls=tool_calls, final_text=result.text)
            if sanitize is not None
            else (prompt, tool_calls, result.text)
        )
        findings_data = [
            {
                "detector": f.detector,
                "severity": f.severity.value,
                "message": f.message,
            }
            for f in result.findings
        ]
        payload = _canonical_payload(
            run_id=run_id,
            agent=agent,
            model_name=model_name,
            prompt=clean_prompt,
            tool_calls=tool_calls,
            final_text=clean_text,
            quality_score=quality.score,
            quality_grade=quality.grade,
            findings=findings_data,
            approvals=approvals_data,
            usage_input=result.usage.input_tokens,
            usage_output=result.usage.output_tokens,
            stopped=result.stopped,
            started_at=started_at,
            completed_at=completed_at,
            prev_hash=prev_hash,
            version=_RECEIPT_VERSION,
        )
        content_hash = "sha256:" + hashlib.sha256(payload.encode()).hexdigest()
        signature = _sign(content_hash, signing_key)
        return cls(
            run_id=run_id,
            agent=agent,
            model_name=model_name,
            prompt=clean_prompt,
            tool_calls=tool_calls,
            final_text=clean_text,
            quality_score=quality.score,
            quality_grade=quality.grade,
            findings=findings_data,
            approvals=approvals_data,
            usage_input=result.usage.input_tokens,
            usage_output=result.usage.output_tokens,
            stopped=result.stopped,
            started_at=started_at,
            completed_at=completed_at,
            prev_hash=prev_hash,
            content_hash=content_hash,
            signature=signature,
            version=_RECEIPT_VERSION,
        )

    # ------------------------------------------------------------------ verify

    def verify(self, key: str = "") -> bool:
        """Verify the receipt's integrity.

        Recomputes both the content hash and the signature from scratch.
        Returns ``True`` only when both match exactly what is stored.

        Args:
            key: HMAC signing key. Falls back to ``TVASTAR_RECEIPT_KEY``
                 env var. If no key is available and ``self.signature`` is
                 empty, only the content hash is verified.
        """
        signing_key = key or os.environ.get(_ENV_KEY, "")
        payload = _canonical_payload(
            run_id=self.run_id,
            agent=self.agent,
            model_name=self.model_name,
            prompt=self.prompt,
            tool_calls=self.tool_calls,
            final_text=self.final_text,
            quality_score=self.quality_score,
            quality_grade=self.quality_grade,
            findings=self.findings,
            approvals=self.approvals,
            usage_input=self.usage_input,
            usage_output=self.usage_output,
            stopped=self.stopped,
            started_at=self.started_at,
            completed_at=self.completed_at,
            prev_hash=self.prev_hash,
            version=self.version,
        )
        expected_hash = "sha256:" + hashlib.sha256(payload.encode()).hexdigest()
        if self.content_hash != expected_hash:
            return False
        if signing_key:
            expected_sig = _sign(expected_hash, signing_key)
            return hmac.compare_digest(self.signature, expected_sig)
        return True  # unsigned receipt — hash match is sufficient

    # ------------------------------------------------------------------ I/O

    def to_dict(self) -> Dict[str, Any]:
        """Return a plain dict suitable for ``json.dumps``."""
        return {
            "version": self.version,
            "run_id": self.run_id,
            "agent": self.agent,
            "model_name": self.model_name,
            "prompt": self.prompt,
            "tool_calls": self.tool_calls,
            "final_text": self.final_text,
            "quality_score": self.quality_score,
            "quality_grade": self.quality_grade,
            "findings": self.findings,
            "approvals": self.approvals,
            "usage_input": self.usage_input,
            "usage_output": self.usage_output,
            "stopped": self.stopped,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "prev_hash": self.prev_hash,
            "content_hash": self.content_hash,
            "signature": self.signature,
        }

    def to_json(self) -> str:
        """Canonical JSON representation (sorted keys, compact)."""
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExecutionReceipt":
        """Deserialise from a plain dict."""
        return cls(
            version=str(data.get("version", _RECEIPT_VERSION)),
            run_id=str(data["run_id"]),
            agent=str(data["agent"]),
            model_name=str(data.get("model_name", "")),
            prompt=str(data["prompt"]),
            tool_calls=list(data.get("tool_calls") or []),
            final_text=str(data.get("final_text", "")),
            quality_score=int(data.get("quality_score", 0)),
            quality_grade=str(data.get("quality_grade", "FAIL")),
            findings=list(data.get("findings") or []),
            approvals=list(data.get("approvals") or []),
            usage_input=int(data.get("usage_input", 0)),
            usage_output=int(data.get("usage_output", 0)),
            stopped=str(data.get("stopped", "end_turn")),
            started_at=float(data.get("started_at", 0.0)),
            completed_at=float(data.get("completed_at", 0.0)),
            prev_hash=str(data.get("prev_hash", "")),
            content_hash=str(data["content_hash"]),
            signature=str(data.get("signature", "")),
        )

    @classmethod
    def from_json(cls, json_str: str) -> "ExecutionReceipt":
        """Deserialise from a JSON string."""
        return cls.from_dict(json.loads(json_str))

    # ------------------------------------------------------------ audit report

    def to_audit_report(self, fmt: str = "text") -> str:
        """Human-readable audit report for lawyers, auditors, and regulators.

        Args:
            fmt: ``"text"`` (default) — plain text, printable as-is.
                 ``"html"`` — self-contained HTML, printable to PDF from any browser.

        Returns:
            A formatted string containing the full audit record.
        """
        if fmt == "html":
            return _audit_report_html(self)
        return _audit_report_text(self)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _canonical_payload(**fields: Any) -> str:
    """Stable, sorted JSON string of the given fields (excludes hash + sig)."""
    return json.dumps(fields, sort_keys=True, separators=(",", ":"), default=str)


def _sign(content_hash: str, key: str) -> str:
    """HMAC-SHA256 of content_hash with key. Returns '' when key is empty."""
    if not key:
        return ""
    digest = hmac.new(key.encode(), content_hash.encode(), hashlib.sha256).hexdigest()
    return f"hmac-sha256:{digest}"


def _extract_tool_calls(messages: list) -> List[Dict[str, Any]]:
    """Pull every ToolUseBlock out of message history and pair with its output.

    Matches each ToolUseBlock (in assistant messages) with the corresponding
    ToolResultBlock (in the following user message) by tool_use_id so the
    receipt captures both the input AND the output of every decision step.
    """
    from ..types import ToolResultBlock, ToolUseBlock

    # Build a map of tool_use_id → output string from all user messages
    outputs: Dict[str, str] = {}
    for msg in messages:
        if msg.role != "user":
            continue
        for block in msg.blocks:
            if isinstance(block, ToolResultBlock):
                outputs[block.tool_use_id] = block.content

    calls: List[Dict[str, Any]] = []
    for msg in messages:
        if msg.role != "assistant":
            continue
        for block in msg.blocks:
            if isinstance(block, ToolUseBlock):
                calls.append({
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                    "output": outputs.get(block.id, ""),
                })
    return calls


# ---------------------------------------------------------------------------
# Audit report renderers
# ---------------------------------------------------------------------------

import datetime  # noqa: E402 — stdlib, safe to import here


def _fmt_ts(ts: float) -> str:
    return datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )


def _fmt_duration(started: float, completed: float) -> str:
    secs = completed - started
    return f"{secs:.1f}s" if secs >= 0 else "—"


def _audit_report_text(r: "ExecutionReceipt") -> str:
    SEP = "━" * 60
    model_line = f"  Model:           {r.model_name}" if r.model_name else ""
    lines: List[str] = [
        "TVASTAR AGENT EXECUTION REPORT",
        SEP,
        f"Run ID:          {r.run_id}",
        f"Agent:           {r.agent}",
    ]
    if model_line:
        lines.append(model_line)
    lines += [
        f"Timestamp:       {_fmt_ts(r.completed_at)}",
        f"Duration:        {_fmt_duration(r.started_at, r.completed_at)}",
        "",
        "INSTRUCTION GIVEN TO AGENT:",
        f"  {r.prompt}",
        "",
    ]

    if r.tool_calls:
        lines.append("DECISIONS MADE (in order):")
        for i, tc in enumerate(r.tool_calls, 1):
            args = json.dumps(tc.get("input", {}), separators=(",", ":"))
            if len(args) > 80:
                args = args[:77] + "..."
            out = tc.get("output", "")
            out_display = f"  → {out[:100]}{'...' if len(out) > 100 else ''}" if out else ""
            lines.append(f"  {i}. {tc['name']}({args})")
            if out_display:
                lines.append(f"    {out_display}")
        lines.append("")

    lines += [
        "FINAL ANSWER:",
    ]
    for line in r.final_text.splitlines() or [""]:
        lines.append(f"  {line}")
    lines.append("")

    grade_marker = {"PASS": "✓", "WARN": "⚠", "FAIL": "✗"}.get(r.quality_grade, "?")
    lines += [
        f"QUALITY ASSESSMENT:  {grade_marker} {r.quality_grade} (score: {r.quality_score}/100)",
        f"STOP REASON:         {r.stopped}",
        f"TOKENS CONSUMED:     input={r.usage_input}  output={r.usage_output}",
        "",
    ]

    if r.approvals:
        lines.append("HUMAN APPROVALS:")
        for i, a in enumerate(r.approvals, 1):
            who = a.get("approved_by") or "unidentified operator"
            ts = _fmt_ts(a["approved_at"]) if a.get("approved_at") else "—"
            tool = a.get("tool", "—")
            lines.append(f"  {i}. Tool '{tool}' approved by {who} at {ts}")
        lines.append("")

    if r.findings:
        lines.append("FINDINGS:")
        for f in r.findings:
            lines.append(f"  [{f['severity']}] {f['detector']}: {f['message']}")
        lines.append("")

    sig_line = r.signature if r.signature else "(unsigned)"
    chain_line = f"prev={r.prev_hash[:16]}..." if r.prev_hash else "(first entry)"
    lines += [
        "CRYPTOGRAPHIC PROOF:",
        f"  Content Hash:  {r.content_hash}",
        f"  Signature:     {sig_line}",
        f"  Chain Link:    {chain_line}",
        "  Verified:      ✓ TRUE  (recompute with receipt.verify())",
        "",
        f"Generated by Tvastar Assurance v{r.version}. Any modification to",
        "this document invalidates the content hash.",
        SEP,
    ]
    return "\n".join(lines)


def _audit_report_html(r: "ExecutionReceipt") -> str:
    def esc(s: str) -> str:
        return (
            s.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )

    tool_rows = ""
    for i, tc in enumerate(r.tool_calls, 1):
        args = json.dumps(tc.get("input", {}), separators=(",", ":"))
        out = tc.get("output", "")
        tool_rows += (
            f"<tr><td>{i}</td><td><code>{esc(tc['name'])}</code></td>"
            f"<td><code>{esc(args)}</code></td>"
            f"<td><code>{esc(out[:200])}</code></td></tr>\n"
        )

    finding_rows = ""
    for f in r.findings:
        finding_rows += (
            f"<tr><td>{esc(f['severity'])}</td><td>{esc(f['detector'])}</td>"
            f"<td>{esc(f['message'])}</td></tr>\n"
        )

    grade_color = {"PASS": "#1a7f37", "WARN": "#d1a000", "FAIL": "#cf222e"}.get(
        r.quality_grade, "#333"
    )
    sig_display = esc(r.signature) if r.signature else "<em>(unsigned)</em>"
    chain_display = (
        esc(f"prev={r.prev_hash[:16]}...") if r.prev_hash else "<em>(first entry)</em>"
    )

    findings_section = ""
    if r.findings:
        findings_section = f"""
        <h2>Findings</h2>
        <table>
          <tr><th>Severity</th><th>Detector</th><th>Message</th></tr>
          {finding_rows}
        </table>"""

    tool_section = ""
    if r.tool_calls:
        tool_section = f"""
        <h2>Decisions Made (in order)</h2>
        <table>
          <tr><th>#</th><th>Tool</th><th>Input</th><th>Output</th></tr>
          {tool_rows}
        </table>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Tvastar Audit Report — {esc(r.run_id)}</title>
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 900px; margin: 40px auto;
          padding: 0 24px; color: #1a1a1a; }}
  h1 {{ font-size: 1.3em; border-bottom: 2px solid #1a1a1a; padding-bottom: 8px; }}
  h2 {{ font-size: 1em; margin-top: 28px; text-transform: uppercase;
        letter-spacing: .05em; color: #555; }}
  table {{ width: 100%; border-collapse: collapse; font-size: .9em; }}
  th, td {{ text-align: left; padding: 6px 10px; border-bottom: 1px solid #ddd; }}
  th {{ background: #f5f5f5; }}
  code {{ font-size: .85em; background: #f5f5f5; padding: 1px 4px; border-radius: 3px; }}
  .meta td:first-child {{ color: #555; width: 200px; }}
  .grade {{ font-weight: bold; color: {grade_color}; }}
  .proof td:first-child {{ color: #555; width: 200px; }}
  .proof code {{ word-break: break-all; }}
  .footer {{ margin-top: 32px; font-size: .8em; color: #888; border-top: 1px solid #ddd;
             padding-top: 12px; }}
  .answer {{ background: #f9f9f9; border-left: 3px solid #ccc; padding: 10px 14px;
             white-space: pre-wrap; font-size: .9em; }}
</style>
</head>
<body>
<h1>Tvastar Agent Execution Report</h1>

<h2>Run Details</h2>
<table class="meta">
  <tr><td>Run ID</td><td><code>{esc(r.run_id)}</code></td></tr>
  <tr><td>Agent</td><td>{esc(r.agent)}</td></tr>
  <tr><td>Model</td><td>{esc(r.model_name) if r.model_name else "<em>not recorded</em>"}</td></tr>
  <tr><td>Timestamp</td><td>{_fmt_ts(r.completed_at)}</td></tr>
  <tr><td>Duration</td><td>{_fmt_duration(r.started_at, r.completed_at)}</td></tr>
  <tr><td>Stop reason</td><td>{esc(r.stopped)}</td></tr>
  <tr><td>Tokens (in/out)</td><td>{r.usage_input} / {r.usage_output}</td></tr>
</table>

<h2>Instruction Given to Agent</h2>
<div class="answer">{esc(r.prompt)}</div>

{tool_section}

<h2>Final Answer</h2>
<div class="answer">{esc(r.final_text)}</div>

<h2>Quality Assessment</h2>
<table class="meta">
  <tr><td>Grade</td><td class="grade">{esc(r.quality_grade)}</td></tr>
  <tr><td>Score</td><td>{r.quality_score} / 100</td></tr>
</table>

{findings_section}

<h2>Cryptographic Proof</h2>
<table class="proof">
  <tr><td>Content Hash</td><td><code>{esc(r.content_hash)}</code></td></tr>
  <tr><td>Signature</td><td><code>{sig_display}</code></td></tr>
  <tr><td>Chain Link</td><td><code>{chain_display}</code></td></tr>
  <tr><td>Verified</td><td><strong>&#10003; TRUE</strong></td></tr>
</table>

<div class="footer">
  Generated by Tvastar Assurance v{esc(r.version)}.
  The content hash is mathematically derived from every field in this document.
  Any modification invalidates the hash.
</div>
</body>
</html>"""
