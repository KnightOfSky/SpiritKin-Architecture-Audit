from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class KnowledgeDocument:
    document_id: str
    title: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    expires_at: float | None = None


@dataclass(frozen=True)
class KnowledgeChunk:
    chunk_id: str
    document_id: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    citations: tuple[tuple[int, int], ...] = ()


@dataclass(frozen=True)
class RetrievalHit:
    chunk: KnowledgeChunk
    score: float
    source_title: str


EmbeddingVector = list[float]


@dataclass(frozen=True)
class VectorRecord:
    chunk: KnowledgeChunk
    source_title: str
    embedding: EmbeddingVector


class BaseKnowledgeRetriever(ABC):
    @abstractmethod
    def retrieve(self, query: str, top_k: int = 5) -> list[RetrievalHit]:
        raise NotImplementedError


class BaseEmbeddingProvider(ABC):
    @abstractmethod
    def embed_documents(self, texts: list[str]) -> list[EmbeddingVector]:
        raise NotImplementedError

    @abstractmethod
    def embed_query(self, text: str) -> EmbeddingVector:
        raise NotImplementedError


class BaseVectorStore(ABC):
    @abstractmethod
    def upsert_records(self, records: list[VectorRecord]) -> None:
        raise NotImplementedError

    @abstractmethod
    def search(self, query_embedding: EmbeddingVector, top_k: int = 5) -> list[RetrievalHit]:
        raise NotImplementedError


class BaseKnowledgeStore(ABC):
    @abstractmethod
    def upsert_document(self, document: KnowledgeDocument) -> None:
        raise NotImplementedError

    @abstractmethod
    def upsert_chunks(self, chunks: list[KnowledgeChunk]) -> None:
        raise NotImplementedError

    @abstractmethod
    def list_documents(self) -> list[KnowledgeDocument]:
        raise NotImplementedError

    @abstractmethod
    def list_chunks(self) -> list[KnowledgeChunk]:
        raise NotImplementedError

    @abstractmethod
    def get_document(self, document_id: str) -> KnowledgeDocument | None:
        raise NotImplementedError

    @abstractmethod
    def search_chunks(self, query: str, top_k: int = 5) -> list[RetrievalHit]:
        raise NotImplementedError

    @abstractmethod
    def remove_document(self, document_id: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def list_stale_documents(self, cutoff_timestamp: float) -> list[str]:
        raise NotImplementedError