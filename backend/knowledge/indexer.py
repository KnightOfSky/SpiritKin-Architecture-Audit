from __future__ import annotations

from backend.knowledge.ingest import ingest_text_document
from backend.knowledge.store import InMemoryKnowledgeStore


class SimpleKnowledgeIndexer:
    def __init__(self, store: InMemoryKnowledgeStore):
        self._store = store

    def index_text(self, document_id: str, title: str, content: str, metadata: dict | None = None) -> None:
        ingest_text_document(self._store, document_id=document_id, title=title, content=content, metadata=metadata)