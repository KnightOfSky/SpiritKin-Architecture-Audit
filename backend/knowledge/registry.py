from __future__ import annotations

from backend.knowledge.retriever import SimpleKnowledgeRetriever
from backend.knowledge.store import InMemoryKnowledgeStore


class KnowledgeRegistry:
    def __init__(self):
        self._stores: dict[str, InMemoryKnowledgeStore] = {}
        self._retrievers: dict[str, SimpleKnowledgeRetriever] = {}

    def register_store(self, name: str, store: InMemoryKnowledgeStore) -> None:
        self._stores[name] = store

    def register_retriever(self, name: str, retriever: SimpleKnowledgeRetriever) -> None:
        self._retrievers[name] = retriever

    def get_store(self, name: str) -> InMemoryKnowledgeStore | None:
        return self._stores.get(name)

    def get_retriever(self, name: str) -> SimpleKnowledgeRetriever | None:
        return self._retrievers.get(name)