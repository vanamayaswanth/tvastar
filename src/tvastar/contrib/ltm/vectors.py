"""Vector search extension for LTMStore — semantic similarity retrieval.

Uses a simple TF-IDF + cosine similarity approach with stdlib only.
No external embedding models required — works offline with zero deps.

For higher quality, users can pass a custom ``embed_fn`` that calls an
external embedding API (OpenAI, Cohere, local model, etc.).

Optional HyDE (Hypothetical Document Embedding): pass ``hyde_model`` callable
to generate a hypothetical answer before embedding — improves retrieval quality
for question-style queries.

Optional hybrid_search: combines normalized FTS5 BM25 scores with vector cosine
similarity using a configurable weight. Falls back gracefully when optional deps
are unavailable.

Usage:
    from tvastar.contrib.ltm.store import LTMStore
    from tvastar.contrib.ltm.vectors import VectorIndex

    store = LTMStore("memory.db")
    index = VectorIndex(store)

    # Index existing knowledge
    index.build()

    # Semantic search (TF-IDF default)
    results = index.search("How do transformers work?", limit=5)

    # With custom embeddings:
    index = VectorIndex(store, embed_fn=my_openai_embed)
    index.build()
    results = index.search("attention mechanism", limit=3)

    # With HyDE:
    index = VectorIndex(store, hyde_model=my_llm_call)
    index.build()
    results = index.search("How does attention work?")

    # Hybrid search (BM25 + vector):
    results = index.hybrid_search("attention mechanism", bm25_weight=0.5)
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Callable, Optional

from .store import Knowledge, LTMStore

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    """A single vector search result with similarity score."""

    knowledge: Knowledge
    score: float  # 0.0 to 1.0 similarity


# Type for custom embedding functions
EmbedFn = Callable[[str], list[float]]

# Type for HyDE model: takes a query string, returns a hypothetical answer string
HydeModelFn = Callable[[str], str]


class VectorIndex:
    """TF-IDF vector index over LTM knowledge entries.

    Provides semantic search using cosine similarity on TF-IDF vectors.
    Zero external dependencies — uses stdlib math and collections.

    For production quality, pass a custom embed_fn (e.g., OpenAI embeddings).

    Parameters
    ----------
    store: The LTMStore to index.
    embed_fn: Optional custom embedding function. If None, uses built-in TF-IDF.
    hyde_model: Optional callable that generates a hypothetical answer from a query.
        When set, search() and hybrid_search() embed the hypothetical answer instead
        of the raw query. On failure, falls back to embedding the raw query.
    """

    def __init__(
        self,
        store: LTMStore,
        *,
        embed_fn: Optional[EmbedFn] = None,
        hyde_model: Optional[HydeModelFn] = None,
    ) -> None:
        self._store = store
        self._embed_fn = embed_fn
        self._hyde_model = hyde_model
        self._documents: list[Knowledge] = []
        self._vectors: list[list[float]] = []
        self._vocab: list[str] = []
        self._idf: dict[str, float] = {}
        self._built = False

    def build(self) -> int:
        """Build the index from all knowledge entries in the store.

        Returns the number of documents indexed.
        """
        rows = self._store._conn.execute(
            "SELECT id, text, source, agent, created_at FROM knowledge_content"
        ).fetchall()

        self._documents = [
            Knowledge(id=r[0], text=r[1], source=r[2], agent=r[3], created_at=r[4]) for r in rows
        ]

        if self._embed_fn is not None:
            # Custom embeddings
            self._vectors = [self._embed_fn(doc.text) for doc in self._documents]
        else:
            # Built-in TF-IDF
            self._build_tfidf()

        self._built = True
        return len(self._documents)

    def search(self, query: str, *, limit: int = 5) -> list[SearchResult]:
        """Search for knowledge semantically similar to the query.

        If hyde_model is configured, generates a hypothetical answer first and
        embeds that. On HyDE failure, falls back to embedding the raw query.

        Parameters
        ----------
        query: The search query text.
        limit: Maximum results to return.

        Returns
        -------
        List of SearchResult sorted by descending similarity score.
        """
        if not self._built or not self._documents:
            return []

        query_vec = self._embed_query(query)

        # Compute cosine similarity with all documents
        scores: list[tuple[int, float]] = []
        for i, doc_vec in enumerate(self._vectors):
            sim = self._cosine_similarity(query_vec, doc_vec)
            if sim > 0.0:
                scores.append((i, sim))

        # Sort by score descending
        scores.sort(key=lambda x: x[1], reverse=True)

        results = []
        for idx, score in scores[:limit]:
            results.append(SearchResult(knowledge=self._documents[idx], score=score))

        return results

    def hybrid_search(
        self, query: str, *, limit: int = 5, bm25_weight: float = 0.5
    ) -> list[SearchResult]:
        """Combine normalized FTS5 BM25 scores with vector cosine similarity.

        final_score = bm25_weight * bm25_norm + (1 - bm25_weight) * vector_score

        Falls back gracefully:
        - HyDE failure → embed raw query
        - Missing sentence-transformers → TF-IDF (current behavior)

        Parameters
        ----------
        query: The search query text.
        limit: Maximum results to return.
        bm25_weight: Weight for BM25 scores (0.0-1.0). Vector weight = 1 - bm25_weight.

        Returns
        -------
        List of SearchResult sorted by descending combined score.
        """
        # ponytail: clamp weight to [0, 1] — no validation error, just safe math
        bm25_weight = max(0.0, min(1.0, bm25_weight))
        vec_weight = 1.0 - bm25_weight

        # --- BM25 scores from FTS5 ---
        bm25_scores: dict[int, float] = {}  # knowledge id → normalized score
        try:
            fts_results = self._store.search_knowledge(query, limit=limit * 2)
            if fts_results:
                # Normalize: FTS5 rank is already positive (abs'd in store.py).
                # Scale to [0, 1] using max normalization.
                max_rank = max(r.rank for r in fts_results) or 1.0
                for r in fts_results:
                    bm25_scores[r.id] = r.rank / max_rank if max_rank > 0 else 0.0
        except Exception:
            logger.debug("FTS5 search failed in hybrid_search for query %r", query)

        # --- Vector scores ---
        vec_scores: dict[int, float] = {}  # knowledge id → cosine similarity
        if self._built and self._documents:
            query_vec = self._embed_query(query)
            for i, doc_vec in enumerate(self._vectors):
                sim = self._cosine_similarity(query_vec, doc_vec)
                if sim > 0.0:
                    vec_scores[self._documents[i].id] = sim

        # --- Combine ---
        all_ids = set(bm25_scores.keys()) | set(vec_scores.keys())
        combined: list[tuple[int, float]] = []
        for doc_id in all_ids:
            bm25_s = bm25_scores.get(doc_id, 0.0)
            vec_s = vec_scores.get(doc_id, 0.0)
            final = bm25_weight * bm25_s + vec_weight * vec_s
            combined.append((doc_id, final))

        combined.sort(key=lambda x: x[1], reverse=True)

        # Build results — need to map id back to Knowledge
        id_to_doc = {doc.id: doc for doc in self._documents}
        # Also include FTS results not in _documents (if index not built for all)
        results: list[SearchResult] = []
        for doc_id, score in combined[:limit]:
            if doc_id in id_to_doc:
                results.append(SearchResult(knowledge=id_to_doc[doc_id], score=score))
            else:
                # Fetch from BM25 results
                try:
                    row = self._store._conn.execute(
                        "SELECT id, text, source, agent, created_at FROM knowledge_content WHERE id = ?",
                        (doc_id,),
                    ).fetchone()
                    if row:
                        k = Knowledge(id=row[0], text=row[1], source=row[2], agent=row[3], created_at=row[4])
                        results.append(SearchResult(knowledge=k, score=score))
                except Exception:
                    pass

        return results

    def _embed_query(self, query: str) -> list[float]:
        """Embed a query, applying HyDE if configured.

        HyDE: generates a hypothetical answer via hyde_model, then embeds that.
        Graceful fallback: if hyde_model raises, logs warning and embeds raw query.
        """
        text_to_embed = query

        if self._hyde_model is not None:
            try:
                hypothetical = self._hyde_model(query)
                if hypothetical:
                    text_to_embed = hypothetical
            except Exception:
                # ponytail: HyDE failure is never fatal — embed raw query
                logger.warning("HyDE generation failed for query %r, using raw query", query)

        if self._embed_fn is not None:
            return self._embed_fn(text_to_embed)
        return self._tfidf_vector(text_to_embed)

    # --- TF-IDF implementation ---

    def _build_tfidf(self) -> None:
        """Build TF-IDF vectors for all documents."""
        # Tokenize all documents
        doc_tokens = [self._tokenize(doc.text) for doc in self._documents]

        # Build vocabulary from all documents
        all_tokens: set[str] = set()
        for tokens in doc_tokens:
            all_tokens.update(tokens)
        self._vocab = sorted(all_tokens)
        vocab_idx = {word: i for i, word in enumerate(self._vocab)}

        # Compute IDF
        n_docs = len(doc_tokens)
        doc_freq: Counter[str] = Counter()
        for tokens in doc_tokens:
            unique_tokens = set(tokens)
            for token in unique_tokens:
                doc_freq[token] += 1

        self._idf = {word: math.log((n_docs + 1) / (df + 1)) + 1 for word, df in doc_freq.items()}

        # Compute TF-IDF vectors
        self._vectors = []
        for tokens in doc_tokens:
            vec = self._compute_tfidf_vec(tokens, vocab_idx)
            self._vectors.append(vec)

    def _tfidf_vector(self, text: str) -> list[float]:
        """Compute TF-IDF vector for a query text."""
        tokens = self._tokenize(text)
        vocab_idx = {word: i for i, word in enumerate(self._vocab)}
        return self._compute_tfidf_vec(tokens, vocab_idx)

    def _compute_tfidf_vec(self, tokens: list[str], vocab_idx: dict[str, int]) -> list[float]:
        """Compute a TF-IDF vector for a list of tokens."""
        vec = [0.0] * len(self._vocab)
        if not tokens:
            return vec

        tf = Counter(tokens)
        max_tf = max(tf.values()) if tf else 1

        for word, count in tf.items():
            if word in vocab_idx:
                # Augmented TF (prevents bias toward long documents)
                normalized_tf = 0.5 + 0.5 * (count / max_tf)
                idf = self._idf.get(word, 1.0)
                vec[vocab_idx[word]] = normalized_tf * idf

        return vec

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """Simple whitespace + punctuation tokenizer. Lowercases and removes stop words."""
        # Split on non-alphanumeric
        tokens = re.findall(r"[a-z0-9]+", text.lower())
        # Remove very short tokens and common stop words
        stop_words = {
            "the",
            "a",
            "an",
            "is",
            "are",
            "was",
            "were",
            "be",
            "been",
            "being",
            "have",
            "has",
            "had",
            "do",
            "does",
            "did",
            "will",
            "would",
            "could",
            "should",
            "may",
            "might",
            "shall",
            "can",
            "to",
            "of",
            "in",
            "for",
            "on",
            "with",
            "at",
            "by",
            "from",
            "it",
            "this",
            "that",
            "these",
            "those",
            "and",
            "or",
            "but",
            "not",
            "no",
            "if",
            "then",
            "than",
            "so",
            "as",
        }
        return [t for t in tokens if len(t) > 1 and t not in stop_words]

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        """Compute cosine similarity between two vectors."""
        if len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(x * x for x in b))
        if norm_a == 0.0 or norm_b == 0.0:
            return 0.0
        return dot / (norm_a * norm_b)
