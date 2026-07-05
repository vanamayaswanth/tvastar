"""pgvector-based RAG retrieval adapter.

ponytail: pgvector for RAG at current scale (2000 chunks, 5 QPS)
ceiling: ~50K vectors → add HNSW index. Still slow at 500K+ → swap adapter to Qdrant.
upgrade path: KnowledgePort interface stays same, only this file changes.
"""
from ports.knowledge import KnowledgePort


# ponytail: pgvector uses the same PostgreSQL connection — zero new infra
