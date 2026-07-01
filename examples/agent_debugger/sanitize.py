"""PII redaction for agent trajectories.

Thin wrapper around :func:`tvastar.boundary.redact_messages` for the agent
debugger pipeline. Detects and replaces PII (emails, phone numbers,
configurable name patterns) with indexed placeholders before trajectory enters
session memory.
"""

from __future__ import annotations

import re

from tvastar.boundary import (
    RedactionResult,
    redact_messages,
)
from tvastar.types import Message


def redact_pii(
    messages: list[Message],
    *,
    patterns: dict[str, re.Pattern] | None = None,
) -> RedactionResult:
    """Replace PII tokens with placeholders like [EMAIL_1], [PHONE_2].

    Thin wrapper around :func:`tvastar.boundary.redact_messages`.

    Default patterns: email, phone (US/intl). Additional or override patterns
    can be supplied via the ``patterns`` dict (key = type name, value = compiled
    regex).

    Placeholders use per-type counters that increment across the entire message
    list (e.g., first email found anywhere → [EMAIL_1], second → [EMAIL_2]).
    """
    return redact_messages(messages, patterns=patterns)
