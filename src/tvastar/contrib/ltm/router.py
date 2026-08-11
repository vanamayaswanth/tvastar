"""Retrieval routing — classifies queries and dispatches to the optimal retrieval strategy.

RetrievalRouter accepts a query, classifies its type via a pluggable classifier_fn
(default: keyword heuristic), and dispatches to the appropriate backend:

- "lookup"    → LTMStore.recall() or FTS5 search_knowledge()
- "semantic"  → VectorIndex.search()
- "multi_hop" → LTMStore.traverse()
- "temporal"  → extract time reference, LTMStore.recall(key, at=T)
- "hybrid"    → combine multiple strategies + re-rank by score

Default classification on ambiguity: "lookup" (cheapest path).
Fallback chain on strategy failure: lookup → semantic → graph.

Usage:
    from tvastar.contrib.ltm.store import LTMStore
    from tvastar.contrib.ltm.vectors import VectorIndex
    from tvastar.contrib.ltm.router import RetrievalRouter

    store = LTMStore("memory.db")
    index = VectorIndex(store)
    index.build()

    router = RetrievalRouter(store=store, vector_index=index)
    results = router.retrieve("What is the user's preferred language?")
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Callable, Optional

from .store import LTMStore
from .vectors import VectorIndex

logger = logging.getLogger(__name__)


@dataclass
class RetrievalResult:
    """Common result schema for all retrieval strategies."""

    text: str
    score: float  # 0.0 to 1.0
    source: str  # where this came from (e.g. "facts", "knowledge", "graph")
    method: str  # which strategy produced it (e.g. "lookup", "semantic", "multi_hop", "temporal")


# Type alias for classifier functions
ClassifierFn = Callable[[str], str]


def _default_classifier(query: str) -> str:
    """Keyword heuristic classifier. Returns one of: lookup, semantic, multi_hop, temporal, hybrid.

    ponytail: simple regex heuristic — ceiling is accuracy on ambiguous queries.
    Upgrade path: swap in an LLM-based classifier_fn.
    """
    lower = query.lower().strip()

    # Temporal: contains time references like "yesterday", "last week", dates, "at time T"
    temporal_patterns = (
        r"\b(yesterday|last\s+(week|month|year)|ago|before|at\s+time|"
        r"in\s+\d{4}|on\s+\d{4}|\d{4}[-/]\d{2}|\bwhen\b.*\bwas\b)\b"
    )
    if re.search(temporal_patterns, lower):
        return "temporal"

    # Multi-hop: relationship-heavy queries
    multi_hop_patterns = (
        r"\b(related\s+to|depends\s+on|caused\s+by|leads?\s+to|connected|"
        r"chain|path|through|via|between.*and)\b"
    )
    if re.search(multi_hop_patterns, lower):
        return "multi_hop"

    # Semantic: abstract/conceptual queries
    semantic_patterns = (
        r"\b(how|why|explain|describe|what\s+is|concept|meaning|similar|"
        r"like|understand|overview)\b"
    )
    if re.search(semantic_patterns, lower):
        return "semantic"

    # ponytail: default to lookup — cheapest path per spec invariant
    return "lookup"


class RetrievalRouter:
    """Routes queries to the optimal retrieval strategy.

    Parameters
    ----------
    store: LTMStore instance for fact recall and FTS5.
    vector_index: Optional VectorIndex for semantic search.
    classifier_fn: Optional custom classifier. Default uses keyword heuristic.
    """

    def __init__(
        self,
        store: LTMStore,
        *,
        vector_index: Optional[VectorIndex] = None,
        classifier_fn: Optional[ClassifierFn] = None,
    ) -> None:
        self._store = store
        self._vector_index = vector_index
        self._classifier_fn = classifier_fn or _default_classifier

    def retrieve(
        self,
        query: str,
        *,
        limit: int = 5,
        hybrid: bool = False,
    ) -> list[RetrievalResult]:
        """Classify query and dispatch to appropriate retrieval strategy.

        Parameters
        ----------
        query: The search query.
        limit: Max results to return.
        hybrid: If True, combine multiple strategies and re-rank.

        Returns list of RetrievalResult sorted by score descending.
        """
        if hybrid:
            return self._hybrid_retrieve(query, limit=limit)

        strategy = self._classifier_fn(query)
        return self._dispatch_with_fallback(query, strategy, limit=limit)

    def _dispatch_with_fallback(
        self, query: str, strategy: str, *, limit: int
    ) -> list[RetrievalResult]:
        """Execute strategy with fallback chain: lookup → semantic → graph."""
        # ponytail: fallback chain is ordered cheapest-first per spec
        fallback_order = ["lookup", "semantic", "multi_hop"]

        # Start from the requested strategy's position in fallback order
        try:
            start_idx = fallback_order.index(strategy)
        except ValueError:
            # temporal or unknown — try it, then fall through from lookup
            try:
                results = self._dispatch(query, strategy, limit=limit)
                if results:
                    return results
            except Exception:
                logger.warning("Strategy %r failed, falling back to lookup", strategy)
            start_idx = 0

        # Try strategies in fallback order starting from requested
        strategies_to_try = fallback_order[start_idx:] + fallback_order[:start_idx]
        for strat in strategies_to_try:
            try:
                results = self._dispatch(query, strat, limit=limit)
                if results:
                    return results
            except Exception:
                logger.warning("Strategy %r failed, trying next fallback", strat)
                continue

        return []

    def _dispatch(self, query: str, strategy: str, *, limit: int) -> list[RetrievalResult]:
        """Dispatch to a single retrieval strategy."""
        if strategy == "lookup":
            return self._retrieve_lookup(query, limit=limit)
        elif strategy == "semantic":
            return self._retrieve_semantic(query, limit=limit)
        elif strategy == "multi_hop":
            return self._retrieve_multi_hop(query, limit=limit)
        elif strategy == "temporal":
            return self._retrieve_temporal(query, limit=limit)
        else:
            # Unknown strategy — treat as lookup
            return self._retrieve_lookup(query, limit=limit)

    def _retrieve_lookup(self, query: str, *, limit: int) -> list[RetrievalResult]:
        """Lookup via recall() first, then FTS5 search_knowledge()."""
        results: list[RetrievalResult] = []

        # Try exact recall by key (query might be a fact key)
        value = self._store.recall(query)
        if value is not None:
            text = str(value) if not isinstance(value, str) else value
            results.append(RetrievalResult(text=text, score=1.0, source="facts", method="lookup"))

        # FTS5 keyword search — may fail on malformed queries, so guard
        try:
            knowledge_results = self._store.search_knowledge(query, limit=limit)
            for kr in knowledge_results:
                # Normalize BM25 rank to 0-1 range (higher rank = more relevant)
                # ponytail: rank from FTS5 is already positive (abs'd in store.py).
                # Normalize with a simple 1/(1+rank) heuristic — ceiling: proper BM25 normalization.
                score = 1.0 / (1.0 + kr.rank) if kr.rank > 0 else 0.5
                results.append(
                    RetrievalResult(text=kr.text, score=score, source="knowledge", method="lookup")
                )
        except Exception:
            logger.debug("FTS5 search failed for query %r", query)

        # Sort and limit
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:limit]

    def _retrieve_semantic(self, query: str, *, limit: int) -> list[RetrievalResult]:
        """Semantic search via VectorIndex."""
        if self._vector_index is None:
            raise RuntimeError("No VectorIndex configured for semantic retrieval")

        search_results = self._vector_index.search(query, limit=limit)
        return [
            RetrievalResult(
                text=sr.knowledge.text,
                score=sr.score,
                source="knowledge",
                method="semantic",
            )
            for sr in search_results
        ]

    def _retrieve_multi_hop(self, query: str, *, limit: int) -> list[RetrievalResult]:
        """Graph traversal via LTMStore.traverse()."""
        # Extract a key from the query — use first quoted string or first word
        key = self._extract_key(query)
        if not key:
            raise RuntimeError("Cannot extract traversal start key from query")

        traversal = self._store.traverse(key, max_hops=3)
        results: list[RetrievalResult] = []
        for source, edge_type, target, depth in traversal:
            # Score decreases with depth
            score = 1.0 / (1.0 + depth)
            text = f"{source} --[{edge_type}]--> {target}"
            results.append(
                RetrievalResult(text=text, score=score, source="graph", method="multi_hop")
            )

        results.sort(key=lambda r: r.score, reverse=True)
        return results[:limit]

    def _retrieve_temporal(self, query: str, *, limit: int) -> list[RetrievalResult]:
        """Temporal recall — extract time reference and key, call recall(key, at=T)."""
        key, timestamp = self._extract_temporal(query)
        if not key:
            raise RuntimeError("Cannot extract key/time from temporal query")

        value = self._store.recall(key, at=timestamp)
        if value is None:
            return []

        text = str(value) if not isinstance(value, str) else value
        return [RetrievalResult(text=text, score=1.0, source="facts", method="temporal")]

    def _hybrid_retrieve(self, query: str, *, limit: int) -> list[RetrievalResult]:
        """Combine multiple strategies and re-rank by score."""
        all_results: list[RetrievalResult] = []

        # Run lookup (always available)
        try:
            all_results.extend(self._retrieve_lookup(query, limit=limit))
        except Exception:
            pass

        # Run semantic if available
        if self._vector_index is not None:
            try:
                all_results.extend(self._retrieve_semantic(query, limit=limit))
            except Exception:
                pass

        # Run multi_hop — may fail if no key extractable
        try:
            all_results.extend(self._retrieve_multi_hop(query, limit=limit))
        except Exception:
            pass

        # De-duplicate by text, keeping highest score
        seen: dict[str, RetrievalResult] = {}
        for r in all_results:
            if r.text not in seen or r.score > seen[r.text].score:
                seen[r.text] = r

        # Re-rank by score
        ranked = sorted(seen.values(), key=lambda r: r.score, reverse=True)
        return ranked[:limit]

    @staticmethod
    def _extract_quoted(query: str) -> str | None:
        # ponytail: extracted for DRY
        """Extract first quoted string (double or single quotes) from query."""
        match = re.search(r'"([^"]+)"', query)
        if match:
            return match.group(1)
        match = re.search(r"'([^']+)'", query)
        if match:
            return match.group(1)
        return None

    @staticmethod
    def _extract_key(query: str) -> str:
        """Extract a fact key from the query. Tries quoted strings, then first noun-like word."""
        # Try quoted string first
        quoted = RetrievalRouter._extract_quoted(query)
        if quoted:
            return quoted
        # Fall back to longest word that looks like a key (alphanumeric + underscores)
        words = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", query)
        # Filter out common stop words
        stop = {
            "what",
            "how",
            "why",
            "the",
            "is",
            "are",
            "related",
            "to",
            "depends",
            "on",
            "caused",
            "by",
            "leads",
            "connected",
            "chain",
            "path",
            "through",
            "via",
            "between",
            "and",
        }
        candidates = [w for w in words if w.lower() not in stop]
        return candidates[0] if candidates else ""

    @staticmethod
    def _extract_temporal(query: str) -> tuple[str, float | None]:
        """Extract key and timestamp from a temporal query.

        Returns (key, timestamp) where timestamp may be None if not parseable.
        """
        # Look for explicit numeric timestamp
        ts_match = re.search(r"at\s+(?:time\s+)?(\d+(?:\.\d+)?)", query, re.IGNORECASE)
        timestamp: float | None = None
        if ts_match:
            timestamp = float(ts_match.group(1))

        # Extract key — try quoted, then first identifier-like word excluding temporal words
        quoted = RetrievalRouter._extract_quoted(query)
        if quoted:
            key = quoted
        else:
            words = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", query)
            stop = {
                "what",
                "was",
                "the",
                "at",
                "time",
                "when",
                "before",
                "after",
                "yesterday",
                "last",
                "week",
                "month",
                "year",
                "ago",
                "in",
                "on",
                "recall",
            }
            candidates = [w for w in words if w.lower() not in stop]
            key = candidates[0] if candidates else ""

        return key, timestamp
