"""SanitizationPolicy — PII/PHI redaction before receipts are signed and stored.

Two tiers:

1. **Regex tier** (zero dependencies, always available):
   Built-in patterns for SSN, credit cards, email, phone, IP, DOB, tokens.
   Instantiate with presets: ``SanitizationPolicy.hipaa()`` / ``.pci()`` /
   ``.gdpr()`` / ``.all_pii()``.

2. **ML / NER tier** (optional, requires ``pip install tvastar[presidio]``):
   Microsoft Presidio — 50+ entity types (PERSON, LOCATION, MEDICAL_LICENSE,
   US_PASSPORT, …) across 15+ languages. Catches "Jane Smith" that regex
   misses. Activate with ``SanitizationPolicy.presidio()``.

   Both tiers compose: ``SanitizationPolicy.presidio().add_pattern(...)``
   applies Presidio first, then any custom regex patterns on top.

Usage::

    from tvastar.assurance import AssurancePolicy, SanitizationPolicy, TrustLog

    # Zero-dep regex preset
    agent = create_agent("billing-bot", model=model, assurance=AssurancePolicy(
        sanitize=SanitizationPolicy.hipaa(),
    ))

    # ML-powered (requires pip install tvastar[presidio])
    agent = create_agent("healthcare-bot", model=model, assurance=AssurancePolicy(
        sanitize=SanitizationPolicy.presidio(languages=["en"]),
    ))

    result = await harness.run("Patient Jane Smith SSN 123-45-6789 has diabetes")
    # receipt.prompt == "Patient [PERSON] SSN [US_SSN] has diabetes"
    # receipt.verify() == True
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

__all__ = ["SanitizationPolicy", "TokenVault"]

# ---------------------------------------------------------------------------
# Built-in pattern library
# ---------------------------------------------------------------------------

# Each entry: (compiled_regex, replacement_label)
_SSN = (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN]")
_CREDIT_CARD = (
    re.compile(
        r"\b(?:4\d{3}|5[1-5]\d{2}|3[47]\d{2}|6(?:011|5\d{2}))[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b"
    ),
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
    re.compile(r"\b(?:0[1-9]|1[0-2])[/-](?:0[1-9]|[12]\d|3[01])[/-](?:19|20)\d{2}\b"),
    "[DOB]",
)

_PRESETS: Dict[str, List[Tuple[re.Pattern, str]]] = {
    "pci": [_CREDIT_CARD, _EMAIL, _PHONE, _BEARER, _API_KEY],
    "hipaa": [_SSN, _EMAIL, _PHONE, _IP_ADDR, _DOB, _BEARER, _API_KEY],
    "gdpr": [_SSN, _CREDIT_CARD, _EMAIL, _PHONE, _IP_ADDR, _DOB, _BEARER, _API_KEY],
    "all": [_SSN, _CREDIT_CARD, _EMAIL, _PHONE, _IP_ADDR, _DOB, _BEARER, _API_KEY],
}


class TokenVault:
    """Reversible PII tokenization for zero-PII agent prompts.

    Replaces sensitive values with opaque tokens (``<<EMAIL_1>>``, ``<<SSN_1>>``, …)
    so the model never sees real PII. After the model returns, ``rehydrate()``
    swaps tokens back to the original values in the response.

    Usage::

        vault = TokenVault()
        clean = vault.tokenize(prompt, SanitizationPolicy.hipaa())
        result = await sess.prompt(clean)          # model sees only tokens
        final  = vault.rehydrate(result.text)      # tokens → originals restored

    The vault accumulates all tokenized values for the lifetime of the object —
    create a new vault per session or per request as appropriate.
    """

    def __init__(self) -> None:
        self._map: dict[str, str] = {}  # token  → original
        self._counters: dict[str, int] = {}  # label → count

    def _next_token(self, label: str) -> str:
        n = self._counters.get(label, 0) + 1
        self._counters[label] = n
        tag = re.sub(r"[^A-Z0-9]", "_", label.upper())
        return f"<<{tag}_{n}>>"

    def tokenize(self, text: str, policy: "SanitizationPolicy") -> str:
        """Apply *policy* patterns to *text*, storing originals. Returns tokenized text."""
        for regex, label in policy.patterns:

            def _replacer(m: re.Match, _lbl: str = label) -> str:
                tok = self._next_token(_lbl)
                self._map[tok] = m.group(0)
                return tok

            text = regex.sub(_replacer, text)
        return text

    def rehydrate(self, text: str) -> str:
        """Replace all tokens in *text* with the originals captured during tokenize()."""
        for tok, original in self._map.items():
            text = text.replace(tok, original)
        return text

    def __len__(self) -> int:
        return len(self._map)

    def __repr__(self) -> str:
        return f"TokenVault({len(self._map)} tokens)"


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

    @classmethod
    def presidio(
        cls,
        languages: Optional[List[str]] = None,
        entities: Optional[List[str]] = None,
        score_threshold: float = 0.5,
    ) -> "SanitizationPolicy":
        """ML-powered PII detection via Microsoft Presidio (optional dependency).

        Presidio uses NLP + rule-based recognisers to detect 50+ entity types
        across 15+ languages — catching names, locations, medical terms, and
        custom entities that regex alone cannot find.

        Requires ``pip install tvastar[presidio]`` (presidio-analyzer +
        presidio-anonymizer). Raises ``ImportError`` with install instructions
        at first ``scrub()`` call if the packages are absent.

        Args:
            languages:       List of BCP-47 language codes to analyse.
                             Default: ``["en"]``.
            entities:        Restrict to these Presidio entity types
                             (e.g. ``["PERSON", "US_SSN", "EMAIL_ADDRESS"]``).
                             ``None`` = all supported entities.
            score_threshold: Minimum confidence score (0–1) for a match to be
                             redacted. Default: ``0.5``.

        Returns:
            A ``_PresidioSanitizationPolicy`` instance. You can chain
            ``.add_pattern()`` on it to layer additional regex patterns
            after Presidio runs.

        Example::

            policy = SanitizationPolicy.presidio(languages=["en", "de"])
            policy.add_pattern(r"ACCT-\\d+", "[ACCOUNT]")

            agent = create_agent("hipaa-bot", model=model,
                                 assurance=AssurancePolicy(sanitize=policy))
        """
        return _PresidioSanitizationPolicy(
            languages=languages or ["en"],
            entities=entities,
            score_threshold=score_threshold,
        )

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

    def scrub_tool_calls(self, tool_calls: List[Dict]) -> List[Dict]:
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


# ---------------------------------------------------------------------------
# Presidio-backed implementation (optional dependency)
# ---------------------------------------------------------------------------

_PRESIDIO_INSTALL_HINT = (
    "Microsoft Presidio is not installed. "
    "Enable ML-powered PII detection with:\n\n"
    "    pip install tvastar[presidio]\n\n"
    "or directly:\n\n"
    "    pip install presidio-analyzer presidio-anonymizer spacy\n"
    "    python -m spacy download en_core_web_lg"
)


class _PresidioSanitizationPolicy(SanitizationPolicy):
    """SanitizationPolicy backed by Microsoft Presidio NLP recognisers.

    Lazy-imports presidio at first ``scrub()`` call so the class can be
    instantiated freely without the optional dependency installed.
    """

    def __init__(
        self,
        languages: List[str],
        entities: Optional[List[str]],
        score_threshold: float,
    ) -> None:
        super().__init__()
        self._languages = languages
        self._entities = entities
        self._score_threshold = score_threshold
        self._analyzer = None
        self._anonymizer = None

    def _init_engines(self) -> None:
        if self._analyzer is not None:
            return
        try:
            from presidio_analyzer import AnalyzerEngine  # type: ignore[import]
            from presidio_anonymizer import AnonymizerEngine  # type: ignore[import]
        except ImportError as exc:
            raise ImportError(_PRESIDIO_INSTALL_HINT) from exc
        self._analyzer = AnalyzerEngine()
        self._anonymizer = AnonymizerEngine()

    def scrub(self, text: str) -> str:
        """Redact PII using Presidio NLP, then apply any extra regex patterns."""
        if not text:
            return text
        self._init_engines()

        # Presidio imports are available after _init_engines()
        from presidio_anonymizer.entities import OperatorConfig  # type: ignore[import]

        for lang in self._languages:
            results = self._analyzer.analyze(
                text=text,
                language=lang,
                entities=self._entities,
                score_threshold=self._score_threshold,
            )
            if results:
                operators = {
                    r.entity_type: OperatorConfig("replace", {"new_value": f"[{r.entity_type}]"})
                    for r in results
                }
                text = self._anonymizer.anonymize(
                    text=text,
                    analyzer_results=results,
                    operators=operators,
                ).text

        # Layer any additional regex patterns from the parent class
        for regex, replacement in self.patterns:
            text = regex.sub(replacement, text)

        return text

    def __repr__(self) -> str:
        langs = ",".join(self._languages)
        installed = self._analyzer is not None
        return (
            f"_PresidioSanitizationPolicy(languages={langs!r}, "
            f"entities={self._entities!r}, initialized={installed})"
        )
