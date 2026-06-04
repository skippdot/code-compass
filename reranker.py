"""Reranking layer: a precision pass over the fused candidates.

Retrieval (dense + BM25 + RRF) optimizes recall — get the right chunk somewhere
in the top ~30. A cross-encoder reranker reads the query and each chunk
*together* and scores true relevance, which the bi-encoder embeddings (query and
chunk encoded separately) can't. This is what pushes the actual implementation
above same-words-different-meaning noise (migrations, tests, docs).

Behind an ABC so the Voyage API reranker can be swapped for a local
cross-encoder, mirroring the Embedder design.
"""

import math
from abc import ABC, abstractmethod


class Reranker(ABC):
    @abstractmethod
    def rerank(
        self, query: str, documents: list[str]
    ) -> list[tuple[int, float]]:
        """Return (index_into_documents, relevance_score) pairs, best-first.

        Scores MUST be in [0, 1]: the caller multiplies them by a code-vs-docs
        prior, which only behaves if scores are non-negative and comparable.
        (Cross-encoders that emit raw logits must squash with sigmoid first.)
        Scores are returned, not just an order, so that prior can be applied
        before the final cut."""


class VoyageReranker(Reranker):
    def __init__(self, model: str = "rerank-2.5-lite"):
        import voyageai

        self.client = voyageai.Client()
        self.model = model

    def rerank(
        self, query: str, documents: list[str]
    ) -> list[tuple[int, float]]:
        if not documents:
            return []
        result = self.client.rerank(
            query, documents, model=self.model, truncation=True
        )
        return [(r.index, r.relevance_score) for r in result.results]


class LocalReranker(Reranker):
    """Offline cross-encoder via sentence-transformers — no API.

    Default `ms-marco-MiniLM-L-6-v2` is tiny and fast; swap for
    `BAAI/bge-reranker-v2-m3` or `jinaai/jina-reranker-v2-base-multilingual`
    for stronger (heavier) code relevance. CrossEncoder emits raw logits, so we
    sigmoid them into [0, 1] to honor the Reranker contract.
    """

    def __init__(self, model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"):
        from sentence_transformers import CrossEncoder

        self.model = CrossEncoder(model)

    def rerank(
        self, query: str, documents: list[str]
    ) -> list[tuple[int, float]]:
        if not documents:
            return []
        logits = self.model.predict([(query, doc) for doc in documents])
        scored = [
            (i, 1.0 / (1.0 + math.exp(-float(s)))) for i, s in enumerate(logits)
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored
