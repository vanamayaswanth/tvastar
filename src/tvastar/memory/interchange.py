"""Memory interchange format: export, import, and transfer between backends.

Defines MemoryFact schema, validation, and round-trippable serialization.
Zero runtime dependencies beyond stdlib (json, dataclasses).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from .contradiction import ContradictionDetector
    from .store import Store

FORMAT_VERSION = "1.0"
VALID_TIERS = ("semantic", "episodic", "procedural")


@dataclass
class MemoryFact:
    """A single semantic unit in the interchange format."""

    key: str  # 1-512 characters
    value: Any  # JSON-serializable
    tier: Literal["semantic", "episodic", "procedural"]
    source_ref: str
    confidence: float  # 0.0–1.0
    created_at: str  # ISO 8601 UTC
    updated_at: str  # ISO 8601 UTC
    contradiction_history: list[dict] = field(default_factory=list)


@dataclass
class ImportResult:
    """Result of an import operation."""

    imported_count: int
    rejected: list[dict] = field(default_factory=list)  # each: {"fact": ..., "reason": str}
    error: str | None = None


def validate_fact(fact: MemoryFact) -> str | None:
    """Return reason string if invalid, None if valid."""
    if not fact.key or len(fact.key) > 512:
        return f"key length must be 1-512, got {len(fact.key) if fact.key else 0}"
    if fact.tier not in VALID_TIERS:
        return f"tier must be one of {VALID_TIERS}, got {fact.tier!r}"
    if not isinstance(fact.confidence, (int, float)) or not (0.0 <= fact.confidence <= 1.0):
        return f"confidence must be 0.0-1.0, got {fact.confidence}"
    # ponytail: required fields presence check
    if not isinstance(fact.source_ref, str):
        return "source_ref must be a string"
    if not isinstance(fact.created_at, str) or not fact.created_at:
        return "created_at must be a non-empty ISO 8601 string"
    if not isinstance(fact.updated_at, str) or not fact.updated_at:
        return "updated_at must be a non-empty ISO 8601 string"
    return None


def export_memories(store: "Store", prefix: str = "") -> list[dict]:
    """Serialize matching keys from Store into interchange format.

    Skips keys that cannot be mapped to valid MemoryFacts.
    Returns list of dicts sorted by key, with format_version included.
    """
    results: list[dict] = []
    for key in store.keys(prefix):
        raw = store.get(key)
        if raw is None:
            continue
        # Stored values must be dicts with MemoryFact fields
        if not isinstance(raw, dict):
            continue
        try:
            fact = MemoryFact(
                key=raw.get("key", key),
                value=raw.get("value"),
                tier=raw.get("tier", ""),
                source_ref=raw.get("source_ref", "unknown"),
                confidence=raw.get("confidence", 0.0),
                created_at=raw.get("created_at", ""),
                updated_at=raw.get("updated_at", ""),
                contradiction_history=raw.get("contradiction_history", []),
            )
        except (TypeError, KeyError):
            continue
        if validate_fact(fact) is not None:
            continue
        results.append(asdict(fact))
    results.sort(key=lambda f: f["key"])
    return results


def import_memories(
    store: "Store",
    facts: list[dict],
    *,
    contradiction_detector: "ContradictionDetector | None" = None,
    format_version: str = FORMAT_VERSION,
) -> ImportResult:
    """Import MemoryFact objects into Store.

    - Reject entire import if format_version > supported.
    - Each fact processed independently (one failure doesn't abort others).
    - Existing keys invoke ContradictionDetector if provided.
    - Returns ImportResult with imported count and rejected list.
    """
    if _version_gt(format_version, FORMAT_VERSION):
        return ImportResult(
            imported_count=0,
            rejected=[],
            error=f"Unsupported format_version: {format_version} (supported: {FORMAT_VERSION})",
        )

    imported = 0
    rejected: list[dict] = []

    for fact_dict in facts:
        try:
            fact = MemoryFact(
                key=fact_dict.get("key", ""),
                value=fact_dict.get("value"),
                tier=fact_dict.get("tier", ""),
                source_ref=fact_dict.get("source_ref", "unknown"),
                confidence=fact_dict.get("confidence", 0.0),
                created_at=fact_dict.get("created_at", ""),
                updated_at=fact_dict.get("updated_at", ""),
                contradiction_history=fact_dict.get("contradiction_history", []),
            )
        except (TypeError, KeyError) as exc:
            rejected.append({"fact": fact_dict, "reason": f"construction failed: {exc}"})
            continue

        reason = validate_fact(fact)
        if reason is not None:
            rejected.append({"fact": fact_dict, "reason": reason})
            continue

        # If key exists and we have a contradiction detector, use it
        existing = store.get(fact.key)
        if existing is not None and contradiction_detector is not None:
            contradiction_detector.write(
                fact.key,
                asdict(fact),
                source_ref=fact.source_ref,
            )
        else:
            store.set(fact.key, asdict(fact))

        imported += 1

    return ImportResult(imported_count=imported, rejected=rejected)


def _version_gt(a: str, b: str) -> bool:
    """True if version string a > b (simple major.minor comparison)."""
    try:
        a_parts = tuple(int(x) for x in a.split("."))
        b_parts = tuple(int(x) for x in b.split("."))
        return a_parts > b_parts
    except (ValueError, AttributeError):
        # Non-parseable version is treated as unsupported
        return True
