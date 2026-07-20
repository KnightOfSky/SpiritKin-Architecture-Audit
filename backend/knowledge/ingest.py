from __future__ import annotations

from backend.knowledge.base import KnowledgeChunk, KnowledgeDocument
from backend.knowledge.chunking import chunk_text
from backend.knowledge.store import InMemoryKnowledgeStore


def ingest_text_document(
    store: InMemoryKnowledgeStore,
    document_id: str,
    title: str,
    content: str,
    metadata: dict | None = None,
    *,
    chunk_size: int = 400,
    overlap: int = 50,
) -> KnowledgeDocument:
    document = KnowledgeDocument(document_id=document_id, title=title, content=content, metadata=metadata or {})
    chunks = [
        KnowledgeChunk(chunk_id=f"{document_id}:{index}", document_id=document_id, text=text, metadata=document.metadata)
        for index, text in enumerate(chunk_text(content, chunk_size=chunk_size, overlap=overlap))
    ]
    store.upsert_document(document)
    store.upsert_chunks(chunks)
    return document