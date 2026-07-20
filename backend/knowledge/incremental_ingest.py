from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from backend.knowledge.base import KnowledgeDocument
from backend.knowledge.indexer import SimpleKnowledgeIndexer
from backend.knowledge.ingest import ingest_text_document


class IncrementalKnowledgeIndexer(SimpleKnowledgeIndexer):
    def upsert_file(self, file_path: str | Path, *, document_id: str | None = None, title: str | None = None) -> KnowledgeDocument:
        path = Path(file_path)
        content = path.read_text(encoding="utf-8", errors="ignore")
        metadata = {"source_path": path.as_posix(), "indexed_mtime": path.stat().st_mtime}
        doc_id = document_id or path.as_posix()
        doc_title = title or path.stem
        return ingest_text_document(self._store, document_id=doc_id, title=doc_title, content=content, metadata=metadata)

    def delete_file(self, file_path: str | Path) -> bool:
        path = Path(file_path)
        doc_id = path.as_posix()
        if self._store.get_document(doc_id) is None:
            return False
        self._store.remove_document(doc_id)
        return True

    def apply_changes(self, changes: list, *, extensions: set[str] | None = None) -> dict[str, int]:
        allowed = {ext.lower() for ext in (extensions or {".md", ".txt", ".rst"})}
        result = {"added": 0, "updated": 0, "deleted": 0}
        for change in changes:
            path = Path(change.path)
            if path.suffix.lower() not in allowed:
                continue
            if change.event_type == "deleted":
                if self.delete_file(change.path):
                    result["deleted"] += 1
            elif change.event_type in ("created", "modified"):
                self.upsert_file(change.path)
                if change.event_type == "created":
                    result["added"] += 1
                else:
                    result["updated"] += 1
        return result


@dataclass
class DocumentTracker:
    _entries: dict[str, float] = field(default_factory=dict)

    def track(self, document_id: str, mtime: float | None = None) -> None:
        self._entries[document_id] = mtime if mtime is not None else time.time()

    def last_seen(self, document_id: str) -> float | None:
        return self._entries.get(document_id)

    def list_stale(self, *, ttl_days: float = 30.0, now: float | None = None) -> list[str]:
        current = now if now is not None else time.time()
        cutoff = current - (ttl_days * 86400)
        return [doc_id for doc_id, mtime in self._entries.items() if mtime < cutoff]

    def remove(self, document_id: str) -> None:
        self._entries.pop(document_id, None)

    def snapshot(self) -> dict[str, float]:
        return dict(self._entries)
