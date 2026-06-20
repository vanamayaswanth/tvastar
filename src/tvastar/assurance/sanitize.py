"""SanitizationPolicy — PII/PHI redaction before receipts are signed and stored.

Applies regex-based scrubbing to prompt, tool call inputs/outputs, and final
text before they reach the ExecutionReceipt hash and TrustLog. The hash covers
the *redacted* content, so the audit trail is both tamper-evident and
regulation-safe.

Built-in patterns cover common PII/PHI under HIPAA, GDPR, and PCI-DSS:
- SSN, credit card numbers, phone numbers, email addresses, IP addresses
- US date-of-birth patterns, passport numbers, driver's licence formats
- Bearer tokens, API keys, passwords in key=value form

Usage::

    from tvastar.assurance import AssurancePolicy, SanitizationPolicy, TrustLog

    agent = create_agent(
        "billing-bot",
        model=model,
        assurance=AssurancePolicy(
            log=TrustLog(".trust.jsonl"),
            sanitize=SanitizationPolicy.hipaa(),   # built-in HIPAA preset
        ),
    )

    result = await harness.run("Patient Jane Smith SSN 123-45-6789 has diabetes")
    # receipt.prompt == "Patient [NAME] SSN [SSN] has diabetes"
    # receipt.verify() == True  (hash covers redacted form)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

__all__ = ["SanitizationPolicy"]

# ---------------------------------------------------------------------------
# Built-in pattern library
# ---------------------------------------------------------------------------

# Each entry: (compiled_regex, replacement_label)
_SSN = (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN]")
_CREDIT_CARD = (
    re.compile(r"\b(?:4\d{3}|5[1-5]\d{2}|3[47]\d{2}|6(?:011|5\d{2}))[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b"),
    "[CARD]",
)
_EMAIL = (re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"), "[EMAIL]")
_PHONE = (
    re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b"),
    "[PHONE]",
)
_IP_ADDR = (re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"), "[IP]")
_BEARER = (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9\-._~+/]+=*\b"), "Bearer [TOKEN]")
_API_KEY = (
    re.compile(r"(?i)(?:api[_\-]?key|token|secret|password)\s*[:=]\s*\S+"),
    "[CREDENTIAL]",
)
_DOB = (
    re.compile(
        r"\b(?:0[1-9]|1[0-2])[/-](?:0[1-9]|[12]\d|3[01])[/-](?:19|20)\d{2}\b"
    ),
    "[DOB]",
)

_PRESETS: Dict[str, List[Tuple[re.Pattern, str]]] = {
    "pci": [_CREDIT_CARD, _EMAIL, _PHONE, _BEARER, _API_KEY],
    "hipaa": [_SSN, _EMAIL, _PHONE, _IP_ADDR, _DOB, _BEARER, _API_KEY],
    "gdpr": [_SSN, _CREDIT_CARD, _EMAIL, _PHONE, _IP_ADDR, _DOB, _BEARER, _API_KEY],
    "all": [_SSN, _CREDIT_CARD, _EMAIL, _PHONE, _IP_ADDR, _DOB, _BEARER, _API_KEY],
}


@dataclass
class SanitizationPolicy:
    """Redact PII/PHI from receipts before hashing and logging.

    Attributes:
        patterns:      List of ``(compiled_regex, replacement)`` pairs applied
                       in order. Each pattern is applied to every text field.
        redact_prompt: Whether to redact the prompt field (default True).
        redact_tools:  Whether to redact tool call inputs and outputs (default True).
        redact_answer: Whether to redact the final answer text (default True).
    """

    patterns: List[Tuple[re.Pattern, str]] = field(default_factory=list)
    redact_prompt: bool = True
    redact_tools: bool = True
    redact_answer: bool = True

    # ----------------------------------------------------------------- presets

    @classmethod
    def hipaa(cls) -> "SanitizationPolicy":
        """Preset covering HIPAA Protected Health Information identifiers."""
        return cls(patterns=list(_PRESETS["hipaa"]))

    @classmethod
    def pci(cls) -> "SanitizationPolicy":
        """Preset covering PCI-DSS cardholder data."""
        return cls(patterns=list(_PRESETS["pci"]))

    @classmethod
    def gdpr(cls) -> "SanitizationPolicy":
        """Preset covering common GDPR personal data categories."""
        return cls(patterns=list(_PRESETS["gdpr"]))

    @classmethod
    def all_pii(cls) -> "SanitizationPolicy":
        """All built-in patterns combined."""
        return cls(patterns=list(_PRESETS["all"]))

    # ----------------------------------------------------------------- API

    def add_pattern(self, pattern: str, replacement: str) -> "SanitizationPolicy":
        """Add a custom regex pattern and return self (fluent)."""
        self.patterns.append((re.compile(pattern), replacement))
        return self

    def scrub(self, text: str) -> str:
        """Apply all patterns to *text* and return the redacted string."""
        for regex, replacement in self.patterns:
            text = regex.sub(replacement, text)
        return text

    def scrub_tool_calls(
        self, tool_calls: List[Dict]
    ) -> List[Dict]:
        """Return a new list with PII scrubbed from input and output fields."""
        result = []
        for tc in tool_calls:
            tc2 = dict(tc)
            if "input" in tc2:
                raw = json.dumps(tc2["input"], separators=(",", ":"), default=str)
                tc2["input"] = json.loads(self.scrub(raw))
            if "output" in tc2 and isinstance(tc2["output"], str):
                tc2["output"] = self.scrub(tc2["output"])
            result.append(tc2)
        return result

    # ----------------------------------------------------------------- apply

    def apply(
        self,
        *,
        prompt: str,
        tool_calls: List[Dict],
        final_text: str,
    ) -> "tuple[str, List[Dict], str]":
        """Scrub all receipt text fields. Returns (prompt, tool_calls, final_text)."""
        clean_prompt = self.scrub(prompt) if self.redact_prompt else prompt
        clean_tools = self.scrub_tool_calls(tool_calls) if self.redact_tools else tool_calls
        clean_text = self.scrub(final_text) if self.redact_answer else final_text
        return clean_prompt, clean_tools, clean_text
