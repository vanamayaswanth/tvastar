"""Vector search extension for LTMStore — semantic similarity retrieval.

Uses a simple TF-IDF + cosine similarity approach with stdlib only.
No external embedding models required — works offline with zero deps.

For higher quality, users can pass a custom ``embed_fn`` that calls an
external embedding API (OpenAI, Cohere, local model, etc.).

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
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Callable, Optional

from .store import Knowledge, LTMStore


@dataclass
class SearchResult:
    """A single vector search result with similarity score."""

    knowledge: Knowledge
    score: float  # 0.0 to 1.0 similarity


# Type for custom embedding functions
EmbedFn = Callable[[str], list[float]]


class VectorIndex:
    """TF-IDF vector index over LTM knowledge entries.

    Provides semantic search using cosine similarity on TF-IDF vectors.
    Zero external dependencies — uses stdlib math and collections.

    For production quality, pass a custom embed_fn (e.g., OpenAI embeddings).

    Parameters
    ----------
    store: The LTMStore to index.
    embed_fn: Optional custom embedding function. If None, uses built-in TF-IDF.
    """

    def __init__(self, store: LTMStore, *, embed_fn: Optional[EmbedFn] = None) -> None:
        self._store = store
        self._embed_fn = embed_fn
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

        if self._embed_fn is not None:
            query_vec = self._embed_fn(query)
        else:
            query_vec = self._tfidf_vector(query)

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
