from __future__ import annotations

import re

from backend.knowledge.base import BaseKnowledgeStore, KnowledgeChunk, KnowledgeDocument, RetrievalHit

TOKEN_PATTERN = re.compile(r"[a-z0-9_]+|[\u4e00-\u9fff]+", re.IGNORECASE)


def _is_cjk_token(token: str) -> bool:
    return bool(token) and all("\u4e00" <= char <= "\u9fff" for char in token)


def tokenize_text(text: str) -> list[str]:
    tokens: list[str] = []
    for raw_token in TOKEN_PATTERN.findall((text or "").lower()):
        token = raw_token.strip()
        if not token:
            continue
        tokens.append(token)
        if _is_cjk_token(token) and len(token) > 1:
            tokens.extend(token[index : index + 2] for index in range(len(token) - 1))
    return list(dict.fromkeys(tokens))


class InMemoryKnowledgeStore(BaseKnowledgeStore):
    def __init__(self):
        self._documents: dict[str, KnowledgeDocument] = {}
        self._chunks: dict[str, KnowledgeChunk] = {}

    def upsert_document(self, document: KnowledgeDocument) -> None:
        self._documents[document.document_id] = document

    def upsert_chunks(self, chunks: list[KnowledgeChunk]) -> None:
        for chunk in chunks:
            self._chunks[chunk.chunk_id] = chunk

    def list_documents(self) -> list[KnowledgeDocument]:
        return list(self._documents.values())

    def list_chunks(self) -> list[KnowledgeChunk]:
        return list(self._chunks.values())

    def get_document(self, document_id: str) -> KnowledgeDocument | None:
        return self._documents.get(document_id)

    def search_chunks(self, query: str, top_k: int = 5) -> list[RetrievalHit]:
        terms = tokenize_text(query)
        if not terms:
            return []

        hits: list[RetrievalHit] = []
        for chunk in self._chunks.values():
            document = self._documents.get(chunk.document_id)
            title = document.title if document else chunk.document_id
            haystack = f"{title} {chunk.text}".lower()
            score = float(sum(haystack.count(term) for term in terms))
            if score > 0:
                hits.append(RetrievalHit(chunk=chunk, score=score, source_title=title))

        hits.sort(key=lambda item: item.score, reverse=True)
        return hits[: max(1, top_k)]

    def remove_document(self, document_id: str) -> bool:
        if document_id not in self._documents:
            return False
        chunk_ids = [cid for cid, chunk in self._chunks.items() if chunk.document_id == document_id]
        for cid in chunk_ids:
            del self._chunks[cid]
        del self._documents[document_id]
        return True

    def list_stale_documents(self, cutoff_timestamp: float) -> list[str]:
        stale: list[str] = []
        for doc in self._documents.values():
            if doc.expires_at is not None and doc.expires_at < cutoff_timestamp:
                stale.append(doc.document_id)
        return stale