from __future__ import annotations

import json
import os
import time
from math import sqrt
from pathlib import Path
from typing import Any

from backend.knowledge.base import BaseVectorStore, EmbeddingVector, KnowledgeChunk, RetrievalHit, VectorRecord

VECTOR_STORE_SCHEMA_VERSION = "spiritkin.vector_store.v1"


def _cosine_similarity(left: EmbeddingVector, right: EmbeddingVector) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = sqrt(sum(value * value for value in left))
    right_norm = sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return numerator / (left_norm * right_norm)


class InMemoryVectorStore(BaseVectorStore):
    def __init__(self):
        self._records: dict[str, VectorRecord] = {}

    def upsert_records(self, records: list[VectorRecord]) -> None:
        for record in records:
            self._records[record.chunk.chunk_id] = record

    def search(self, query_embedding: EmbeddingVector, top_k: int = 5) -> list[RetrievalHit]:
        hits: list[RetrievalHit] = []
        for record in self._records.values():
            score = _cosine_similarity(query_embedding, record.embedding)
            if score > 0:
                hits.append(RetrievalHit(chunk=record.chunk, score=score, source_title=record.source_title))
        hits.sort(key=lambda item: item.score, reverse=True)
        return hits[: max(1, top_k)]


class JsonVectorStore(BaseVectorStore):
    """Persistent vector store for small/local knowledge bases.

    This is deliberately simple JSON persistence for project/docs scale. It is
    not a replacement for a production vector database when KB volume grows.
    """

    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(path).resolve()
        self._records: dict[str, VectorRecord] = {}
        self._load()

    def upsert_records(self, records: list[VectorRecord]) -> None:
        for record in records:
            self._records[record.chunk.chunk_id] = record
        self._save()

    def search(self, query_embedding: EmbeddingVector, top_k: int = 5) -> list[RetrievalHit]:
        hits: list[RetrievalHit] = []
        for record in self._records.values():
            score = _cosine_similarity(query_embedding, record.embedding)
            if score > 0:
                hits.append(RetrievalHit(chunk=record.chunk, score=score, source_title=record.source_title))
        hits.sort(key=lambda item: item.score, reverse=True)
        return hits[: max(1, top_k)]

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": VECTOR_STORE_SCHEMA_VERSION,
            "path": str(self.path),
            "total": len(self._records),
            "chunk_ids": sorted(self._records),
        }

    def _load(self) -> None:
        if not self.path.exists():
            self._records = {}
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._records = {}
            return
        raw_records = payload.get("records") if isinstance(payload, dict) else []
        if not isinstance(raw_records, list):
            self._records = {}
            return
        records: dict[str, VectorRecord] = {}
        for item in raw_records:
            if not isinstance(item, dict):
                continue
            try:
                record = vector_record_from_snapshot(item)
            except Exception:
                continue
            records[record.chunk.chunk_id] = record
        self._records = records

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": VECTOR_STORE_SCHEMA_VERSION,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "records": [vector_record_snapshot(record) for record in self._records.values()],
        }
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def vector_record_snapshot(record: VectorRecord) -> dict[str, Any]:
    return {
        "chunk": {
            "chunk_id": record.chunk.chunk_id,
            "document_id": record.chunk.document_id,
            "text": record.chunk.text,
            "metadata": dict(record.chunk.metadata or {}),
            "citations": [list(item) for item in record.chunk.citations],
        },
        "source_title": record.source_title,
        "embedding": [float(item) for item in record.embedding],
    }


def vector_record_from_snapshot(payload: dict[str, Any]) -> VectorRecord:
    chunk_payload = payload.get("chunk") if isinstance(payload.get("chunk"), dict) else {}
    citations = []
    for item in chunk_payload.get("citations") or ():
        if isinstance(item, (list, tuple)) and len(item) == 2:
            try:
                citations.append((int(item[0]), int(item[1])))
            except (TypeError, ValueError):
                continue
    chunk = KnowledgeChunk(
        chunk_id=str(chunk_payload.get("chunk_id") or payload.get("chunk_id") or ""),
        document_id=str(chunk_payload.get("document_id") or payload.get("document_id") or ""),
        text=str(chunk_payload.get("text") or payload.get("text") or ""),
        metadata=dict(chunk_payload.get("metadata") or {}) if isinstance(chunk_payload.get("metadata"), dict) else {},
        citations=tuple(citations),
    )
    return VectorRecord(
        chunk=chunk,
        source_title=str(payload.get("source_title") or chunk.document_id),
        embedding=[float(item) for item in payload.get("embedding") or ()],
    )
