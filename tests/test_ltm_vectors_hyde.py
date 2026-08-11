"""Tests for VectorIndex HyDE + hybrid_search — Requirement 11.

Covers:
- No optional deps = current behavior (TF-IDF works unchanged)
- HyDE failure degrades gracefully (falls back to raw query embedding)
- hybrid_search combines BM25 + vector scores
- hyde_model generates hypothetical answer before embedding

**Validates: Requirements 11**
"""

from __future__ import annotations

import pytest

from tvastar.contrib.ltm.store import LTMStore
from tvastar.contrib.ltm.vectors import SearchResult, VectorIndex


@pytest.fixture
def store(tmp_path):
    db_path = str(tmp_path / "test_hyde.db")
    with LTMStore(db_path) as s:
        yield s


@pytest.fixture
def populated_store(store: LTMStore) -> LTMStore:
    """Store with knowledge entries for hybrid/HyDE testing."""
    store.store_knowledge(
        "Transformers use self-attention mechanisms to process sequences in parallel",
        source="paper.pdf",
        agent="researcher",
    )
    store.store_knowledge(
        "Python is a dynamically typed programming language with garbage collection",
        source="docs",
        agent="researcher",
    )
    store.store_knowledge(
        "Machine learning models require training data and compute resources",
        source="textbook",
        agent="researcher",
    )
    store.store_knowledge(
        "Neural networks consist of layers of interconnected neurons",
        source="lecture",
        agent="researcher",
    )
    store.store_knowledge(
        "Database indexing improves query performance significantly",
        source="manual",
        agent="dba",
    )
    return store


# ---------------------------------------------------------------------------
# No optional deps = current behavior
# ---------------------------------------------------------------------------


class TestNoOptionalDeps:
    """VectorIndex without hyde_model behaves identically to pre-upgrade."""

    def test_search_works_without_hyde_model(self, populated_store: LTMStore):
        """TF-IDF search works without any optional parameters."""
        index = VectorIndex(populated_store)
        index.build()
        results = index.search("attention mechanism transformer")
        assert len(results) >= 1
        assert "attention" in results[0].knowledge.text.lower()

    def test_hybrid_search_without_hyde_produces_results(self, populated_store: LTMStore):
        """hybrid_search with no hyde_model uses TF-IDF + FTS5."""
        index = VectorIndex(populated_store)
        index.build()
        results = index.hybrid_search("machine learning", limit=3)
        assert len(results) >= 1
        # Results should be SearchResult objects with scores
        assert isinstance(results[0], SearchResult)
        assert 0.0 < results[0].score <= 1.0

    def test_constructor_defaults_unchanged(self, populated_store: LTMStore):
        """VectorIndex() with no kwargs behaves the same as before."""
        index = VectorIndex(populated_store)
        assert index._hyde_model is None
        assert index._embed_fn is None
        index.build()
        # Original search still works
        results = index.search("python programming")
        assert len(results) >= 1


# ---------------------------------------------------------------------------
# HyDE failure degrades gracefully
# ---------------------------------------------------------------------------


class TestHyDEGracefulDegradation:
    """HyDE failures never propagate — always fall back to raw query embedding."""

    def test_hyde_exception_falls_back_to_raw_query(self, populated_store: LTMStore):
        """When hyde_model raises, search still works using raw query."""

        def failing_hyde(query: str) -> str:
            raise RuntimeError("LLM connection failed")

        index = VectorIndex(populated_store, hyde_model=failing_hyde)
        index.build()

        # Should NOT raise — should fall back to raw query
        results = index.search("attention mechanism")
        assert len(results) >= 1
        assert "attention" in results[0].knowledge.text.lower()

    def test_hyde_returns_empty_uses_raw_query(self, populated_store: LTMStore):
        """When hyde_model returns empty string, falls back to raw query."""

        def empty_hyde(query: str) -> str:
            return ""

        index = VectorIndex(populated_store, hyde_model=empty_hyde)
        index.build()

        results = index.search("python programming")
        assert len(results) >= 1

    def test_hyde_failure_in_hybrid_search(self, populated_store: LTMStore):
        """hybrid_search degrades gracefully when HyDE fails."""

        def failing_hyde(query: str) -> str:
            raise ValueError("Model not available")

        index = VectorIndex(populated_store, hyde_model=failing_hyde)
        index.build()

        results = index.hybrid_search("neural networks")
        assert len(results) >= 1


# ---------------------------------------------------------------------------
# hybrid_search combines BM25 + vector scores
# ---------------------------------------------------------------------------


class TestHybridSearch:
    """hybrid_search combines normalized FTS5 BM25 + vector cosine similarity."""

    def test_hybrid_search_returns_results(self, populated_store: LTMStore):
        index = VectorIndex(populated_store)
        index.build()
        results = index.hybrid_search("machine learning")
        assert len(results) >= 1

    def test_hybrid_search_respects_limit(self, populated_store: LTMStore):
        index = VectorIndex(populated_store)
        index.build()
        results = index.hybrid_search("learning", limit=2)
        assert len(results) <= 2

    def test_hybrid_search_results_sorted_by_score(self, populated_store: LTMStore):
        index = VectorIndex(populated_store)
        index.build()
        results = index.hybrid_search("machine learning neural")
        if len(results) > 1:
            for i in range(len(results) - 1):
                assert results[i].score >= results[i + 1].score

    def test_hybrid_search_bm25_weight_zero_is_pure_vector(self, populated_store: LTMStore):
        """bm25_weight=0.0 means only vector scores matter."""
        index = VectorIndex(populated_store)
        index.build()

        pure_vector = index.search("attention mechanism", limit=3)
        hybrid_zero_bm25 = index.hybrid_search("attention mechanism", limit=3, bm25_weight=0.0)

        # Should produce same top result since vector dominates
        if pure_vector and hybrid_zero_bm25:
            assert pure_vector[0].knowledge.id == hybrid_zero_bm25[0].knowledge.id

    def test_hybrid_search_bm25_weight_one_is_pure_bm25(self, populated_store: LTMStore):
        """bm25_weight=1.0 means only FTS5 scores matter."""
        index = VectorIndex(populated_store)
        index.build()
        results = index.hybrid_search("transformers", bm25_weight=1.0)
        # Should still return results from BM25
        assert len(results) >= 1

    def test_hybrid_search_empty_index(self, store: LTMStore):
        """hybrid_search on empty store returns empty."""
        index = VectorIndex(store)
        index.build()
        results = index.hybrid_search("anything")
        assert results == []

    def test_hybrid_search_before_build(self, populated_store: LTMStore):
        """hybrid_search before build still works (BM25 part works, vector part empty)."""
        index = VectorIndex(populated_store)
        # Don't build — vector index is empty but FTS5 should still work
        results = index.hybrid_search("python")
        # Should get results from BM25 alone
        assert len(results) >= 1


# ---------------------------------------------------------------------------
# hyde_model generates hypothetical answer before embedding
# ---------------------------------------------------------------------------


class TestHyDEModel:
    """When hyde_model is set, it generates a hypothetical answer before embedding."""

    def test_hyde_model_called_during_search(self, populated_store: LTMStore):
        """hyde_model is invoked with the query, result is embedded."""
        calls: list[str] = []

        def tracking_hyde(query: str) -> str:
            calls.append(query)
            # Return a hypothetical answer that overlaps with "transformers" doc
            return "Self-attention processes sequences in parallel using transformer architecture"

        index = VectorIndex(populated_store, hyde_model=tracking_hyde)
        index.build()
        index.search("How does attention work?")

        assert len(calls) == 1
        assert calls[0] == "How does attention work?"

    def test_hyde_model_called_during_hybrid_search(self, populated_store: LTMStore):
        """hyde_model is invoked during hybrid_search for the vector part."""
        calls: list[str] = []

        def tracking_hyde(query: str) -> str:
            calls.append(query)
            return "Machine learning requires large datasets for training models"

        index = VectorIndex(populated_store, hyde_model=tracking_hyde)
        index.build()
        index.hybrid_search("What is needed for ML?")

        assert len(calls) == 1
        assert calls[0] == "What is needed for ML?"

    def test_hyde_improves_retrieval_quality(self, populated_store: LTMStore):
        """A good hypothetical answer should help match the right document."""

        def good_hyde(query: str) -> str:
            # Return text that overlaps heavily with the database indexing doc
            return "Database indexing creates data structures that improve query lookup speed"

        index = VectorIndex(populated_store, hyde_model=good_hyde)
        index.build()

        # The query itself has no overlap, but the HyDE answer does
        results = index.search("How to make SQL faster?")
        assert len(results) >= 1
        # The database doc should rank high thanks to HyDE
        assert (
            "database" in results[0].knowledge.text.lower()
            or "index" in results[0].knowledge.text.lower()
        )

    def test_hyde_with_custom_embed_fn(self, populated_store: LTMStore):
        """hyde_model works alongside custom embed_fn."""
        hyde_calls: list[str] = []
        embed_calls: list[str] = []

        def my_hyde(query: str) -> str:
            hyde_calls.append(query)
            return "hypothetical answer about " + query

        def my_embed(text: str) -> list[float]:
            embed_calls.append(text)
            return [float(hash(text) % 100) / 100.0, float(hash(text[::-1]) % 100) / 100.0]

        index = VectorIndex(populated_store, embed_fn=my_embed, hyde_model=my_hyde)
        index.build()

        embed_calls.clear()
        hyde_calls.clear()

        index.search("test query")

        # hyde_model should be called first
        assert hyde_calls == ["test query"]
        # embed_fn should receive the HyDE output, not the raw query
        assert len(embed_calls) == 1
        assert embed_calls[0] == "hypothetical answer about test query"
