from __future__ import annotations

import os

from backend.knowledge.base import (
    BaseEmbeddingProvider,
    BaseKnowledgeRetriever,
    BaseKnowledgeStore,
    BaseVectorStore,
    RetrievalHit,
    VectorRecord,
)
from backend.knowledge.embedding import HashingEmbeddingProvider, get_embedding_service, hashing_embeddings_enabled
from backend.knowledge.reranker import BaseReranker, build_reranker
from backend.knowledge.vector_store import InMemoryVectorStore, JsonVectorStore


class EmbeddingKnowledgeRetriever(BaseKnowledgeRetriever):
    def __init__(
        self,
        embedding_provider: BaseEmbeddingProvider,
        vector_store: BaseVectorStore,
        *,
        reranker: BaseReranker | None = None,
        source_records: list[VectorRecord] | None = None,
    ):
        self._embedding_provider = embedding_provider
        self._vector_store = vector_store
        self._reranker = reranker
        self._source_records = list(source_records or [])
        self._index_dimensions = len(self._source_records[0].embedding) if self._source_records else 0

    def retrieve(self, query: str, top_k: int = 5) -> list[RetrievalHit]:
        query_embedding = self._embedding_provider.embed_query(query)
        if self._index_dimensions and len(query_embedding) != self._index_dimensions:
            self._reindex_after_provider_transition()
            query_embedding = self._embedding_provider.embed_query(query)
        hits = self._vector_store.search(query_embedding, top_k=max(10, top_k * 2))
        if self._reranker is not None:
            return self._reranker.rerank(query, hits, top_k=top_k)
        return hits[:top_k]

    def _reindex_after_provider_transition(self) -> None:
        if not self._source_records:
            return
        embeddings = self._embedding_provider.embed_documents([record.chunk.text for record in self._source_records])
        if len(embeddings) != len(self._source_records):
            raise RuntimeError("embedding provider transition returned an incomplete index")
        records = [
            VectorRecord(chunk=record.chunk, source_title=record.source_title, embedding=embedding)
            for record, embedding in zip(self._source_records, embeddings, strict=True)
        ]
        self._vector_store.upsert_records(records)
        self._source_records = records
        self._index_dimensions = len(records[0].embedding) if records else 0


def build_embedding_retriever_from_store(
    store: BaseKnowledgeStore,
    *,
    embedding_provider: BaseEmbeddingProvider | None = None,
    vector_store: BaseVectorStore | None = None,
    vector_store_path: str | os.PathLike[str] | None = None,
    reranker: BaseReranker | str | None = "auto",
) -> EmbeddingKnowledgeRetriever:
    provider = embedding_provider or get_embedding_service()
    target_store = vector_store or build_vector_store(vector_store_path)
    resolved_reranker = build_reranker(reranker) if isinstance(reranker, str) else reranker
    chunks = store.list_chunks()
    try:
        embeddings = provider.embed_documents([chunk.text for chunk in chunks])
    except Exception:
        if not hashing_embeddings_enabled():
            raise
        provider = HashingEmbeddingProvider()
        embeddings = provider.embed_documents([chunk.text for chunk in chunks])
    records = [
        VectorRecord(
            chunk=chunk,
            source_title=(store.get_document(chunk.document_id).title if store.get_document(chunk.document_id) else chunk.document_id),
            embedding=embedding,
        )
        for chunk, embedding in zip(chunks, embeddings, strict=False)
    ]
    target_store.upsert_records(records)
    return EmbeddingKnowledgeRetriever(provider, target_store, reranker=resolved_reranker, source_records=records)


def build_vector_store(path: str | os.PathLike[str] | None = None) -> BaseVectorStore:
    raw_path = str(path or os.getenv("SPIRITKIN_VECTOR_STORE_PATH") or "").strip()
    if raw_path:
        return JsonVectorStore(raw_path)
    return InMemoryVectorStore()
