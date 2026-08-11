"""Tests for RetrievalRouter — query classification and dispatch.

Covers:
- Each strategy type routes correctly (lookup/semantic/multi_hop/temporal)
- Hybrid mode combines results
- Custom classifier_fn works
- Fallback chain on strategy failure

**Validates: Requirements 10**
"""

from __future__ import annotations

import pytest

from tvastar.contrib.ltm.router import RetrievalResult, RetrievalRouter, _default_classifier
from tvastar.contrib.ltm.store import LTMStore
from tvastar.contrib.ltm.vectors import VectorIndex


@pytest.fixture
def store(tmp_path):
    db_path = str(tmp_path / "test_router.db")
    with LTMStore(db_path) as s:
        yield s


@pytest.fixture
def populated_store(store: LTMStore):
    """Store with some facts, knowledge, and relationships for testing."""
    store.remember("language", "Python", agent="user")
    store.remember("framework", "FastAPI", agent="user")
    store.store_knowledge("Transformers use self-attention mechanisms", source="paper", agent="researcher")
    store.store_knowledge("Python is a programming language", source="docs", agent="system")
    store.relate("language", "RELATED_TO", "framework")
    return store


@pytest.fixture
def vector_index(populated_store: LTMStore):
    idx = VectorIndex(populated_store)
    idx.build()
    return idx


# ---------------------------------------------------------------------------
# RetrievalResult dataclass
# ---------------------------------------------------------------------------


class TestRetrievalResult:
    def test_fields(self):
        r = RetrievalResult(text="hello", score=0.9, source="facts", method="lookup")
        assert r.text == "hello"
        assert r.score == 0.9
        assert r.source == "facts"
        assert r.method == "lookup"


# ---------------------------------------------------------------------------
# Default classifier heuristic
# ---------------------------------------------------------------------------


class TestDefaultClassifier:
    def test_temporal_query(self):
        assert _default_classifier("What was the location yesterday?") == "temporal"
        assert _default_classifier("recall value at time 1700000000") == "temporal"
        assert _default_classifier("What happened last week?") == "temporal"

    def test_multi_hop_query(self):
        assert _default_classifier("What is related to Python?") == "multi_hop"
        assert _default_classifier("What depends on the auth module?") == "multi_hop"
        assert _default_classifier("path between A and B") == "multi_hop"

    def test_semantic_query(self):
        assert _default_classifier("How do transformers work?") == "semantic"
        assert _default_classifier("Explain attention mechanisms") == "semantic"
        assert _default_classifier("What is the concept of polymorphism?") == "semantic"

    def test_ambiguous_defaults_to_lookup(self):
        assert _default_classifier("Python") == "lookup"
        assert _default_classifier("user preference") == "lookup"
        assert _default_classifier("config value") == "lookup"


# ---------------------------------------------------------------------------
# Lookup routing
# ---------------------------------------------------------------------------


class TestLookupRouting:
    def test_recall_exact_key(self, populated_store: LTMStore):
        router = RetrievalRouter(populated_store)
        results = router.retrieve("language")

        assert len(results) > 0
        # Should find the fact via recall
        assert any(r.text == '"Python"' or r.text == "Python" for r in results)
        assert all(r.method == "lookup" for r in results)

    def test_fts5_search(self, populated_store: LTMStore):
        router = RetrievalRouter(populated_store)
        results = router.retrieve("programming")

        assert len(results) > 0
        assert any("Python" in r.text for r in results)
        assert all(r.method == "lookup" for r in results)


# ---------------------------------------------------------------------------
# Semantic routing
# ---------------------------------------------------------------------------


class TestSemanticRouting:
    def test_semantic_dispatch(self, populated_store: LTMStore, vector_index: VectorIndex):
        router = RetrievalRouter(populated_store, vector_index=vector_index)
        results = router.retrieve("How do attention mechanisms work?")

        assert len(results) > 0
        assert all(r.method == "semantic" for r in results)

    def test_semantic_without_index_falls_back(self, populated_store: LTMStore):
        """Without VectorIndex, semantic falls back through the chain."""
        router = RetrievalRouter(populated_store)
        # "Explain Python" classifies as semantic but no index → falls to lookup
        # FTS5 will match "Python is a programming language" in knowledge
        results = router.retrieve("Explain Python")

        # Should still return something via fallback to lookup (FTS5 matches "Python")
        assert len(results) > 0
        assert all(r.method == "lookup" for r in results)


# ---------------------------------------------------------------------------
# Multi-hop routing
# ---------------------------------------------------------------------------


class TestMultiHopRouting:
    def test_multi_hop_dispatch(self, populated_store: LTMStore):
        router = RetrievalRouter(populated_store)
        results = router.retrieve('What is related to "language"?')

        assert len(results) > 0
        assert any(r.method == "multi_hop" for r in results)

    def test_multi_hop_no_key_falls_back(self, populated_store: LTMStore):
        """If key extraction fails entirely, fallback chain kicks in."""
        router = RetrievalRouter(populated_store)
        # All words are stop words for the key extractor
        results = router.retrieve("related to the")
        # Falls back through chain — should still get results from lookup
        # or return empty if nothing matches
        assert isinstance(results, list)


# ---------------------------------------------------------------------------
# Temporal routing
# ---------------------------------------------------------------------------


class TestTemporalRouting:
    def test_temporal_dispatch(self, store: LTMStore):
        import time

        store.remember("location", "NYC", agent="user")
        t_after_nyc = time.time()
        time.sleep(0.01)
        store.remember("location", "SF", agent="user")

        router = RetrievalRouter(store)
        results = router.retrieve(f'recall "location" at time {t_after_nyc}')

        assert len(results) > 0
        assert results[0].method == "temporal"
        assert "NYC" in results[0].text


# ---------------------------------------------------------------------------
# Hybrid mode
# ---------------------------------------------------------------------------


class TestHybridMode:
    def test_hybrid_combines_results(self, populated_store: LTMStore, vector_index: VectorIndex):
        router = RetrievalRouter(populated_store, vector_index=vector_index)
        results = router.retrieve("Python programming", hybrid=True)

        assert len(results) > 0
        # Hybrid should include results from multiple methods
        methods = {r.method for r in results}
        # At minimum lookup should work since we have facts and knowledge
        assert "lookup" in methods

    def test_hybrid_deduplicates(self, populated_store: LTMStore, vector_index: VectorIndex):
        router = RetrievalRouter(populated_store, vector_index=vector_index)
        results = router.retrieve("Python programming", hybrid=True)

        # No duplicate texts
        texts = [r.text for r in results]
        assert len(texts) == len(set(texts))

    def test_hybrid_sorted_by_score(self, populated_store: LTMStore, vector_index: VectorIndex):
        router = RetrievalRouter(populated_store, vector_index=vector_index)
        results = router.retrieve("Python programming", hybrid=True)

        if len(results) > 1:
            for i in range(len(results) - 1):
                assert results[i].score >= results[i + 1].score


# ---------------------------------------------------------------------------
# Custom classifier_fn
# ---------------------------------------------------------------------------


class TestCustomClassifier:
    def test_custom_fn_used(self, populated_store: LTMStore, vector_index: VectorIndex):
        """Custom classifier_fn overrides default heuristic."""
        calls = []

        def always_semantic(query: str) -> str:
            calls.append(query)
            return "semantic"

        router = RetrievalRouter(
            populated_store, vector_index=vector_index, classifier_fn=always_semantic
        )
        results = router.retrieve("language")  # would normally be "lookup"

        assert len(calls) == 1
        assert calls[0] == "language"
        # Results should come from semantic method
        assert all(r.method == "semantic" for r in results)


# ---------------------------------------------------------------------------
# Fallback chain
# ---------------------------------------------------------------------------


class TestFallbackChain:
    def test_fallback_on_strategy_failure(self, populated_store: LTMStore):
        """When semantic fails (no index), falls back to lookup."""

        def always_semantic(query: str) -> str:
            return "semantic"

        router = RetrievalRouter(populated_store, classifier_fn=always_semantic)
        # No vector_index → semantic raises → fallback to lookup
        results = router.retrieve("Python")

        assert len(results) > 0
        # Should have fallen back to lookup
        assert all(r.method == "lookup" for r in results)

    def test_fallback_chain_order(self, populated_store: LTMStore):
        """Fallback goes lookup → semantic → graph."""
        attempt_log: list[str] = []

        class FailingRouter(RetrievalRouter):
            def _retrieve_lookup(self, query, *, limit):
                attempt_log.append("lookup")
                raise RuntimeError("lookup failed")

            def _retrieve_semantic(self, query, *, limit):
                attempt_log.append("semantic")
                raise RuntimeError("semantic failed")

            def _retrieve_multi_hop(self, query, *, limit):
                attempt_log.append("multi_hop")
                return [RetrievalResult(text="found", score=0.5, source="graph", method="multi_hop")]

        router = FailingRouter(populated_store)
        results = router.retrieve("something")

        assert attempt_log == ["lookup", "semantic", "multi_hop"]
        assert len(results) == 1
        assert results[0].method == "multi_hop"
