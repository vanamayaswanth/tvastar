"""PII protection verification for ExecutionReceipts.

Scans receipt prompts for leaked PII patterns and confirms TokenVault
was active by counting opaque tokens. Pure functions, no side effects.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, List, Tuple

from .models import PIIVerificationRecord

if TYPE_CHECKING:
    from ..assurance.receipt import ExecutionReceipt

__all__ = ["verify_pii_protection"]

# Pattern matching opaque tokens from TokenVault: <<TYPE_NNNNN>>
_TOKEN_PATTERN = re.compile(r"<<[A-Z_]+_\d+>>")

# PII patterns that should NOT appear in tokenized prompts
_PII_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "SSN"),
    (re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"), "EMAIL"),
    (re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b"), "PHONE"),
    (re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"), "IP"),
    (re.compile(r"\b(?:0[1-9]|1[0-2])[/-](?:0[1-9]|[12]\d|3[01])[/-](?:19|20)\d{2}\b"), "DOB"),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9\-._~+/]+=*\b"), "BEARER_TOKEN"),
    (re.compile(r"(?i)(?:api[_\-]?key|token|secret|password)\s*[:=]\s*\S+"), "API_KEY"),
]


def verify_pii_protection(
    receipt: "ExecutionReceipt", vault_configured: bool
) -> PIIVerificationRecord:
    """Verify that a receipt's prompt contains only opaque tokens, no raw PII.

    Pure function. Scans the prompt field for known PII patterns.
    Counts opaque tokens to confirm TokenVault was active.

    Args:
        receipt: The ExecutionReceipt whose prompt field to scan.
        vault_configured: Whether TokenVault is configured on the Loop.

    Returns:
        PIIVerificationRecord with vault_active, token_count, leak_count,
        content_hash, and leaked_types.
    """
    prompt = receipt.prompt

    # Count opaque tokens to confirm TokenVault was active
    token_count = len(_TOKEN_PATTERN.findall(prompt))

    # Scan for leaked PII patterns
    leaked_types: List[str] = []
    for pattern, pii_type in _PII_PATTERNS:
        if pattern.search(prompt):
            if pii_type not in leaked_types:
                leaked_types.append(pii_type)

    leak_count = len(leaked_types)
    vault_active = vault_configured and token_count > 0

    return PIIVerificationRecord(
        vault_active=vault_active,
        token_count=token_count,
        leak_count=leak_count,
        content_hash=receipt.content_hash,
        leaked_types=leaked_types,
    )
