"""Tests for tvastar.contrib.ltm.vectors — TF-IDF vector search."""

from __future__ import annotations

import pytest

from tvastar.contrib.ltm.store import LTMStore
from tvastar.contrib.ltm.vectors import SearchResult, VectorIndex


@pytest.fixture
def store(tmp_path):
    """Create a temporary LTMStore with some knowledge entries."""
    db_path = str(tmp_path / "test_vectors.db")
    with LTMStore(db_path) as s:
        yield s


@pytest.fixture
def populated_store(store: LTMStore) -> LTMStore:
    """Store with several knowledge entries for search testing."""
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


# --- Build ---


class TestBuild:
    def test_build_indexes_documents(self, populated_store: LTMStore):
        index = VectorIndex(populated_store)
        count = index.build()
        assert count == 5

    def test_build_empty_store(self, store: LTMStore):
        index = VectorIndex(store)
        count = index.build()
        assert count == 0

    def test_build_sets_built_flag(self, populated_store: LTMStore):
        index = VectorIndex(populated_store)
        assert index._built is False
        index.build()
        assert index._built is True


# --- Search ---


class TestSearch:
    def test_search_finds_relevant_documents(self, populated_store: LTMStore):
        index = VectorIndex(populated_store)
        index.build()
        results = index.search("attention mechanism transformer")
        assert len(results) >= 1
        # The transformers document should be most relevant
        assert "attention" in results[0].knowledge.text.lower()

    def test_search_returns_search_result_objects(self, populated_store: LTMStore):
        index = VectorIndex(populated_store)
        index.build()
        results = index.search("python programming")
        assert len(results) >= 1
        assert isinstance(results[0], SearchResult)
        assert isinstance(results[0].score, float)
        assert 0.0 < results[0].score <= 1.0

    def test_search_respects_limit(self, populated_store: LTMStore):
        index = VectorIndex(populated_store)
        index.build()
        results = index.search("learning", limit=2)
        assert len(results) <= 2

    def test_search_results_sorted_by_score_descending(self, populated_store: LTMStore):
        index = VectorIndex(populated_store)
        index.build()
        results = index.search("machine learning neural networks")
        if len(results) > 1:
            for i in range(len(results) - 1):
                assert results[i].score >= results[i + 1].score

    def test_search_no_results_for_unrelated_query(self, store: LTMStore):
        """A query with no overlapping tokens returns empty."""
        store.store_knowledge("alpha beta gamma", source="a", agent="x")
        index = VectorIndex(store)
        index.build()
        # Query terms that don't appear in any document after stop word removal
        results = index.search("zzzzz qqqqq")
        assert results == []

    def test_search_before_build_returns_empty(self, populated_store: LTMStore):
        index = VectorIndex(populated_store)
        results = index.search("anything")
        assert results == []

    def test_search_on_empty_index_returns_empty(self, store: LTMStore):
        index = VectorIndex(store)
        index.build()
        results = index.search("something")
        assert results == []


# --- Cosine similarity ---


class TestCosineSimilarity:
    def test_identical_vectors_return_one(self):
        vec = [1.0, 2.0, 3.0]
        sim = VectorIndex._cosine_similarity(vec, vec)
        assert abs(sim - 1.0) < 1e-9

    def test_orthogonal_vectors_return_zero(self):
        a = [1.0, 0.0, 0.0]
        b = [0.0, 1.0, 0.0]
        sim = VectorIndex._cosine_similarity(a, b)
        assert abs(sim) < 1e-9

    def test_zero_vector_returns_zero(self):
        a = [0.0, 0.0, 0.0]
        b = [1.0, 2.0, 3.0]
        assert VectorIndex._cosine_similarity(a, b) == 0.0
        assert VectorIndex._cosine_similarity(b, a) == 0.0

    def test_different_length_vectors_return_zero(self):
        a = [1.0, 2.0]
        b = [1.0, 2.0, 3.0]
        assert VectorIndex._cosine_similarity(a, b) == 0.0

    def test_antiparallel_vectors(self):
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        sim = VectorIndex._cosine_similarity(a, b)
        assert abs(sim - (-1.0)) < 1e-9


# --- Tokenizer ---


class TestTokenizer:
    def test_removes_stop_words(self):
        tokens = VectorIndex._tokenize("the cat is on the mat")
        assert "the" not in tokens
        assert "is" not in tokens
        assert "on" not in tokens
        assert "cat" in tokens
        assert "mat" in tokens

    def test_lowercases_text(self):
        tokens = VectorIndex._tokenize("Python Machine Learning")
        assert "python" in tokens
        assert "machine" in tokens
        assert "learning" in tokens

    def test_removes_single_char_tokens(self):
        tokens = VectorIndex._tokenize("I a x go by")
        # "I", "a", "x" are single char → removed
        # "go" is 2 chars → kept
        # "by" is a stop word → removed
        assert "go" in tokens
        assert "i" not in tokens
        assert "a" not in tokens

    def test_splits_on_punctuation(self):
        tokens = VectorIndex._tokenize("hello-world, foo.bar")
        assert "hello" in tokens
        assert "world" in tokens
        assert "foo" in tokens
        assert "bar" in tokens

    def test_empty_string(self):
        tokens = VectorIndex._tokenize("")
        assert tokens == []


# --- Custom embed_fn ---


class TestCustomEmbedFn:
    def test_custom_embed_fn_is_called(self, populated_store: LTMStore):
        """Verify that a custom embed_fn is used instead of TF-IDF."""
        calls: list[str] = []

        def mock_embed(text: str) -> list[float]:
            calls.append(text)
            # Simple hash-based embedding for deterministic testing
            return [float(hash(text) % 100) / 100.0, float(hash(text[::-1]) % 100) / 100.0]

        index = VectorIndex(populated_store, embed_fn=mock_embed)
        count = index.build()

        # embed_fn should have been called for each document
        assert len(calls) == count

        # Search should also call embed_fn
        calls.clear()
        index.search("test query")
        assert len(calls) == 1
        assert calls[0] == "test query"

    def test_custom_embed_fn_search_results(self, store: LTMStore):
        """Custom embeddings produce valid search results."""
        store.store_knowledge("hello world", source="a", agent="x")
        store.store_knowledge("goodbye world", source="b", agent="x")

        # Embed function that makes "hello world" closer to "hello" query
        def embed(text: str) -> list[float]:
            if "hello" in text:
                return [1.0, 0.0]
            return [0.0, 1.0]

        index = VectorIndex(store, embed_fn=embed)
        index.build()

        results = index.search("hello")
        assert len(results) >= 1
        assert "hello" in results[0].knowledge.text
