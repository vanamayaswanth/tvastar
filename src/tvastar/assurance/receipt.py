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

_RECEIPT_VERSION = "1"
_ENV_KEY = "TVASTAR_RECEIPT_KEY"


@dataclass
class ExecutionReceipt:
    """Cryptographically signed, chain-linked record of one agent run.

    Attributes:
        run_id:        Unique identifier for this run (auto-generated).
        agent:         Name of the AgentSpec that ran.
        prompt:        The user prompt that triggered the run.
        tool_calls:    Every tool invocation: [{name, input, output, at}].
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
        version:       Receipt schema version ("1").
    """

    run_id: str
    agent: str
    prompt: str
    tool_calls: List[Dict[str, Any]]
    final_text: str
    quality_score: int
    quality_grade: str
    findings: List[Dict[str, Any]]
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
        prompt: str,
        started_at: float,
        completed_at: float,
        key: str = "",
        prev_hash: str = "",
    ) -> "ExecutionReceipt":
        """Build and sign a receipt from a completed RunResult.

        Args:
            result:       The RunResult returned by ``session.prompt()``.
            agent:        Agent name (``spec.name``).
            prompt:       The original user prompt.
            started_at:   Unix timestamp when ``session.prompt()`` was called.
            completed_at: Unix timestamp when the run returned.
            key:          HMAC signing key. Falls back to the
                          ``TVASTAR_RECEIPT_KEY`` environment variable, then
                          to an unsigned receipt (``signature=""``) if neither
                          is set.
            prev_hash:    content_hash of the preceding receipt in the log.

        Returns:
            A fully populated, signed :class:`ExecutionReceipt`.
        """
        signing_key = key or os.environ.get(_ENV_KEY, "")
        quality = result.quality
        run_id = f"run_{uuid.uuid4().hex[:16]}"
        tool_calls = _extract_tool_calls(result.messages)
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
            prompt=prompt,
            tool_calls=tool_calls,
            final_text=result.text,
            quality_score=quality.score,
            quality_grade=quality.grade,
            findings=findings_data,
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
            prompt=prompt,
            tool_calls=tool_calls,
            final_text=result.text,
            quality_score=quality.score,
            quality_grade=quality.grade,
            findings=findings_data,
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
            prompt=self.prompt,
            tool_calls=self.tool_calls,
            final_text=self.final_text,
            quality_score=self.quality_score,
            quality_grade=self.quality_grade,
            findings=self.findings,
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
            "prompt": self.prompt,
            "tool_calls": self.tool_calls,
            "final_text": self.final_text,
            "quality_score": self.quality_score,
            "quality_grade": self.quality_grade,
            "findings": self.findings,
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
            prompt=str(data["prompt"]),
            tool_calls=list(data.get("tool_calls") or []),
            final_text=str(data.get("final_text", "")),
            quality_score=int(data.get("quality_score", 0)),
            quality_grade=str(data.get("quality_grade", "FAIL")),
            findings=list(data.get("findings") or []),
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
    """Pull every ToolUseBlock out of a message history."""
    from ..types import ToolUseBlock

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
                })
    return calls
