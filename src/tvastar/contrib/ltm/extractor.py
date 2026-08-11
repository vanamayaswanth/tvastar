"""MemoryExtractor — automatic fact extraction from session messages.

Modes:
  - "llm": prompts the model with structured schema to extract key-value facts.
  - "pattern": uses regex to find "X is Y" structured statements.
  - None / unset: no extraction (opt-out).

Extraction failure logs a warning and NEVER prevents session close.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tvastar.contrib.ltm.store import LTMStore
    from tvastar.types import Message

logger = logging.getLogger(__name__)


@dataclass
class ExtractedFact:
    """A single fact extracted from conversation."""

    key: str
    value: str
    confidence: float = 0.8


_LLM_SYSTEM = (
    "You are a memory extraction assistant. "
    "Return ONLY valid JSON — no markdown fences, no explanation."
)

_LLM_PROMPT = """\
Extract key-value facts from the conversation below.

<conversation>
{conversation}
</conversation>

Return JSON array with this structure:
[{{"key": "short_key", "value": "fact value", "confidence": 0.9}}]

Rules:
- Keys should be short snake_case identifiers.
- Values should be concise factual statements.
- Confidence between 0.0 and 1.0.
- Skip opinions, greetings, and failed attempts.
- Return empty array [] if nothing factual found.
"""

# ponytail: pattern mode — matches "X is Y", "X are Y", "X = Y" statements.
# Ceiling: simple regex, won't catch all natural language facts. Upgrade: NLP parser.
_PATTERN_RE = re.compile(
    r"(?:^|[.:]\s+)"  # sentence start or after role prefix colon
    r"(?:the\s+|my\s+|our\s+)?"
    r"([A-Za-z][A-Za-z0-9_ ]{1,40})"  # key (group 1)
    r"\s+(?:is|are|=)\s+"  # verb
    r"([^.!?\n]{2,100})",  # value (group 2)
    re.IGNORECASE | re.MULTILINE,
)


@dataclass
class MemoryExtractor:
    """Extracts facts from session messages and persists them to LTMStore.

    Args:
        mode: "llm" for model-based extraction, "pattern" for regex, None to disable.
        model: Required when mode="llm". The model to call for extraction.
    """

    mode: str | None = None
    model: Any = None

    async def extract(self, messages: list["Message"]) -> list[ExtractedFact]:
        """Extract facts from messages based on configured mode.

        Returns empty list if mode is None or messages are empty.
        Raises no exceptions — caller wraps in try/except anyway, but
        we handle gracefully internally too.
        """
        if self.mode is None or not messages:
            return []

        if self.mode == "llm":
            return await self._extract_llm(messages)
        elif self.mode == "pattern":
            return self._extract_pattern(messages)
        return []

    async def _extract_llm(self, messages: list["Message"]) -> list[ExtractedFact]:
        """Prompt model with messages + structured schema."""
        from tvastar.types import Message as Msg

        conversation = _format_messages(messages)
        if not conversation.strip():
            return []

        prompt_text = _LLM_PROMPT.format(conversation=conversation)
        resp = await self.model.generate(
            [Msg("user", prompt_text)],
            system=_LLM_SYSTEM,
            max_tokens=1024,
            temperature=0.0,
        )

        raw = resp.message.text.strip()
        # Strip markdown fences if present
        cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()
        try:
            data = json.loads(cleaned)
        except (json.JSONDecodeError, ValueError):
            return []

        if not isinstance(data, list):
            return []

        facts: list[ExtractedFact] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            key = item.get("key", "")
            value = item.get("value", "")
            confidence = item.get("confidence", 0.8)
            if key and value:
                facts.append(
                    ExtractedFact(
                        key=str(key).strip(),
                        value=str(value).strip(),
                        confidence=float(confidence),
                    )
                )
        return facts

    def _extract_pattern(self, messages: list["Message"]) -> list[ExtractedFact]:
        """Regex extraction for structured "X is Y" statements."""
        text = _format_messages(messages)
        facts: list[ExtractedFact] = []
        seen_keys: set[str] = set()

        for match in _PATTERN_RE.finditer(text):
            raw_key = match.group(1).strip().lower().replace(" ", "_")
            value = match.group(2).strip()
            if raw_key not in seen_keys and len(raw_key) > 1:
                seen_keys.add(raw_key)
                facts.append(ExtractedFact(key=raw_key, value=value, confidence=0.7))

        return facts

    async def extract_and_remember(
        self, messages: list["Message"], ltm_store: "LTMStore"
    ) -> list[ExtractedFact]:
        """Extract facts and feed them to LTMStore.remember().

        Returns the extracted facts. On any failure, logs warning and returns [].
        """
        facts = await self.extract(messages)
        for fact in facts:
            ltm_store.remember(
                fact.key,
                fact.value,
                agent="memory_extractor",
                confidence=fact.confidence,
            )
        return facts


def _format_messages(messages: list["Message"], char_limit: int = 6000) -> str:
    """Format messages into a simple text block for extraction."""
    parts: list[str] = []
    for m in messages:
        text = m.text if hasattr(m, "text") else ""
        if text:
            parts.append(f"{m.role}: {text[:400]}")
    return "\n".join(parts)[:char_limit]
