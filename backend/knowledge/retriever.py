from __future__ import annotations

from backend.knowledge.base import BaseKnowledgeRetriever, RetrievalHit
from backend.knowledge.reranker import BaseReranker
from backend.knowledge.store import InMemoryKnowledgeStore


class SimpleKnowledgeRetriever(BaseKnowledgeRetriever):
    def __init__(self, store: InMemoryKnowledgeStore, *, reranker: BaseReranker | None = None):
        self._store = store
        self._reranker = reranker

    def retrieve(self, query: str, top_k: int = 5) -> list[RetrievalHit]:
        hits = self._store.search_chunks(query, top_k=max(10, top_k * 2))
        if self._reranker is not None:
            return self._reranker.rerank(query, hits, top_k=top_k)
        return hits[:top_k]