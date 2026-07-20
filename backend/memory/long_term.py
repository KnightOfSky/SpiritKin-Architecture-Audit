from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.knowledge.base import BaseEmbeddingProvider
from backend.memory.activation import DEFAULT_ACTIVATION_POLICY, MemoryActivationPolicy
from backend.memory.audit import audit_memory_state, summarize_memory_audit
from backend.memory.conflicts import MemoryConflict, find_conflict_candidate


@dataclass(frozen=True)
class LongTermMemoryEntry:
    entry_id: str
    category: str
    content: str
    importance: float = 0.5
    timestamp: float = field(default_factory=time.time)
    access_count: int = 0
    last_recalled: float = 0.0
    activation: float = 50.0
    memory_state: str = "active"
    metadata: dict[str, Any] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "category": self.category,
            "content": self.content,
            "importance": self.importance,
            "timestamp": self.timestamp,
            "access_count": self.access_count,
            "last_recalled": self.last_recalled,
            "activation": self.activation,
            "memory_state": self.memory_state,
            "metadata": self.metadata,
        }

    @classmethod
    def from_snapshot(cls, data: dict[str, Any]) -> LongTermMemoryEntry:
        raw_activation = float(data.get("activation") if data.get("activation") is not None else data.get("importance") or 0.5)
        if 0.0 < raw_activation <= 1.0:
            raw_activation *= 100.0
        return cls(
            entry_id=str(data.get("entry_id") or ""),
            category=str(data.get("category") or "conversation"),
            content=str(data.get("content") or ""),
            importance=float(data.get("importance") or 0.5),
            timestamp=float(data.get("timestamp") or time.time()),
            access_count=int(data.get("access_count") or 0),
            last_recalled=float(data.get("last_recalled") or 0),
            activation=DEFAULT_ACTIVATION_POLICY.clamp(raw_activation),
            memory_state=DEFAULT_ACTIVATION_POLICY.state(raw_activation),
            metadata=dict(data.get("metadata") or {}),
        )


def _semantic_tokens(text: str) -> set[str]:
    normalized = str(text or "").lower()
    tokens = set(re.findall(r"[a-z0-9_]+", normalized))
    for sequence in re.findall(r"[\u3400-\u9fff]+", normalized):
        tokens.update(sequence)
        tokens.update(sequence[index : index + 2] for index in range(max(0, len(sequence) - 1)))
    return {token for token in tokens if token}


def _semantic_relevance(query: str, content: str) -> float:
    query_tokens = _semantic_tokens(query)
    if not query_tokens:
        return 0.5
    content_tokens = _semantic_tokens(content)
    overlap = len(query_tokens & content_tokens)
    if overlap == 0:
        return 0.0
    return min(1.0, overlap / math.sqrt(max(1, len(query_tokens) * len(content_tokens))))


def _decayed_activation(entry: LongTermMemoryEntry, now: float, policy: MemoryActivationPolicy) -> float:
    recalled_at = entry.last_recalled or entry.timestamp
    age_days = max(0.0, now - recalled_at) / 86400.0
    maintained_at = float(entry.metadata.get("last_maintained_at") or entry.timestamp)
    maintenance_days = max(0.0, now - maintained_at) / 86400.0
    return policy.decay(
        entry.activation,
        days_idle=age_days,
        days_since_maintenance=maintenance_days,
        intrinsic_value=entry.importance,
    )


def _ranking_score(entry: LongTermMemoryEntry, relevance: float, activation: float, now: float) -> float:
    recalled_at = entry.last_recalled or entry.timestamp
    age_days = max(0.0, now - recalled_at) / 86400.0
    recency = math.exp(-age_days / 30.0)
    frequency = min(1.0, math.log1p(entry.access_count) / math.log(11.0))
    return relevance * (1.0 + activation / 100.0) + entry.importance * 0.2 + frequency * 0.08 + recency * 0.05


class LongTermMemoryStore:
    CATEGORIES = ("conversation", "preference", "habit", "emotional_event", "knowledge_fact", "user_feedback")
    CONFLICT_CATEGORIES = frozenset({"preference", "habit", "knowledge_fact", "user_feedback"})

    def __init__(
        self,
        limit: int = 500,
        *,
        embedding_provider: BaseEmbeddingProvider | None = None,
        activation_policy: MemoryActivationPolicy = DEFAULT_ACTIVATION_POLICY,
    ):
        self._entries: dict[str, LongTermMemoryEntry] = {}
        self._conflicts: dict[str, MemoryConflict] = {}
        self._limit = max(50, limit)
        self._counter = 0
        self._conflict_counter = 0
        self._embedding_provider = embedding_provider
        self._activation_policy = activation_policy

    def _next_id(self) -> str:
        self._counter += 1
        return f"ltm-{self._counter:06d}"

    def add(self, category: str, content: str, *, importance: float = 0.5, metadata: dict[str, Any] | None = None) -> LongTermMemoryEntry:
        if category not in self.CATEGORIES:
            category = "conversation"
        entry_metadata = dict(metadata or {})
        if self._embedding_provider is not None and content.strip():
            try:
                vectors = self._embedding_provider.embed_documents([content])
                if vectors and vectors[0]:
                    entry_metadata["semantic_embedding"] = [float(value) for value in vectors[0]]
            except Exception as exc:
                entry_metadata["embedding_degraded"] = type(exc).__name__
        initial_activation = self._activation_policy.clamp(max(self._activation_policy.active_threshold, importance * 100.0))
        entry = LongTermMemoryEntry(
            entry_id=self._next_id(),
            category=category,
            content=content,
            importance=max(0.0, min(1.0, importance)),
            activation=initial_activation,
            memory_state=self._activation_policy.state(initial_activation),
            metadata=entry_metadata,
        )
        self._entries[entry.entry_id] = entry
        self._detect_conflicts(entry)
        if len(self._entries) > self._limit:
            oldest = min(self._entries.keys(), key=lambda k: self._entries[k].timestamp)
            del self._entries[oldest]
        return entry

    def recall(self, query: str = "", *, category: str | None = None, top_k: int = 10, min_importance: float = 0.0) -> list[LongTermMemoryEntry]:
        candidates: list[LongTermMemoryEntry] = []
        now = time.time()
        for entry in self._entries.values():
            if entry.memory_state == "archived" or entry.metadata.get("resolution_status") == "superseded":
                continue
            if category is not None and entry.category != category:
                continue
            if entry.importance < min_importance:
                continue
            candidates.append(entry)

        semantic_relevance = self._semantic_relevance_by_id(query, candidates)
        ranked: list[tuple[float, LongTermMemoryEntry, float, str]] = []
        for candidate in candidates:
            entry = self._entries.get(candidate.entry_id, candidate)
            relevance = semantic_relevance.get(entry.entry_id, _semantic_relevance(query, entry.content))
            if query and relevance <= 0.0:
                continue
            decayed = _decayed_activation(entry, now, self._activation_policy)
            activated = self._activation_policy.on_user_hit(decayed, entry.access_count) if query else decayed
            state = self._activation_policy.state(activated)
            if state != "active":
                continue
            ranked.append((_ranking_score(entry, relevance, activated, now), entry, activated, state))

        ranked.sort(key=lambda item: (item[0], item[1].importance, item[1].timestamp), reverse=True)
        results: list[LongTermMemoryEntry] = []
        for _, entry, activation, state in ranked[: max(0, top_k)]:
            updated = LongTermMemoryEntry(
                entry_id=entry.entry_id,
                category=entry.category,
                content=entry.content,
                importance=entry.importance,
                timestamp=entry.timestamp,
                access_count=entry.access_count + 1,
                last_recalled=now,
                activation=activation,
                memory_state=state,
                metadata=entry.metadata,
            )
            self._entries[entry.entry_id] = updated
            results.append(updated)
        return results

    def maintain(self, entry_id: str) -> LongTermMemoryEntry | None:
        entry = self._entries.get(entry_id)
        if entry is None:
            return None
        metadata = dict(entry.metadata)
        maintenance_count = int(metadata.get("maintenance_count") or 0)
        metadata["maintenance_count"] = maintenance_count + 1
        metadata["last_maintained_at"] = time.time()
        activation = self._activation_policy.on_maintenance(entry.activation, maintenance_count)
        updated = replace_entry(entry, activation=activation, memory_state=self._activation_policy.state(activation), metadata=metadata)
        self._entries[entry_id] = updated
        return updated

    def _semantic_relevance_by_id(self, query: str, entries: list[LongTermMemoryEntry]) -> dict[str, float]:
        if not query or self._embedding_provider is None or not entries:
            return {}
        try:
            query_vector = self._embedding_provider.embed_query(query)
            missing = [
                entry
                for entry in entries
                if len(_entry_embedding(entry)) != len(query_vector)
            ]
            if missing:
                vectors = self._embedding_provider.embed_documents([entry.content for entry in missing])
                for entry, vector in zip(missing, vectors, strict=False):
                    metadata = dict(entry.metadata)
                    metadata["semantic_embedding"] = [float(value) for value in vector]
                    updated = replace_entry(entry, metadata=metadata)
                    self._entries[entry.entry_id] = updated
            return {
                entry.entry_id: max(0.0, _cosine_similarity(query_vector, _entry_embedding(self._entries.get(entry.entry_id, entry))))
                for entry in entries
            }
        except Exception:
            return {}

    def consolidate(self, similarity_threshold: float = 0.7) -> int:
        merged = 0
        entries = list(self._entries.values())
        for i, e1 in enumerate(entries):
            for e2 in entries[i + 1:]:
                if e1.category != e2.category:
                    continue
                if e1.entry_id not in self._entries or e2.entry_id not in self._entries:
                    continue
                overlap = len(set(e1.content.lower().split()) & set(e2.content.lower().split()))
                max_len = max(len(e1.content.split()), 1)
                if overlap / max_len >= similarity_threshold:
                    merged_content = e1.content if len(e1.content) >= len(e2.content) else e2.content
                    merged_importance = max(e1.importance, e2.importance)
                    self.add(e1.category, merged_content, importance=merged_importance, metadata={"merged_from": [e1.entry_id, e2.entry_id]})
                    self._entries.pop(e1.entry_id, None)
                    self._entries.pop(e2.entry_id, None)
                    merged += 1
        return merged

    def decay_importance(self, days_threshold: float = 30.0) -> int:
        decayed = 0
        cutoff = time.time() - (days_threshold * 86400)
        for eid, entry in list(self._entries.items()):
            if entry.last_recalled < cutoff and entry.importance > 0.1:
                new_imp = max(0.05, entry.importance * 0.7)
                self._entries[eid] = LongTermMemoryEntry(
                    entry_id=entry.entry_id, category=entry.category, content=entry.content,
                    importance=new_imp, timestamp=entry.timestamp,
                    access_count=entry.access_count,
                    last_recalled=entry.last_recalled,
                    activation=self._activation_policy.decay(
                        entry.activation,
                        days_idle=max(0.0, (time.time() - (entry.last_recalled or entry.timestamp)) / 86400.0),
                        days_since_maintenance=max(0.0, (time.time() - float(entry.metadata.get("last_maintained_at") or entry.timestamp)) / 86400.0),
                        intrinsic_value=entry.importance,
                    ),
                    memory_state=self._activation_policy.state(
                        self._activation_policy.decay(
                            entry.activation,
                            days_idle=max(0.0, (time.time() - (entry.last_recalled or entry.timestamp)) / 86400.0),
                            days_since_maintenance=max(0.0, (time.time() - float(entry.metadata.get("last_maintained_at") or entry.timestamp)) / 86400.0),
                            intrinsic_value=entry.importance,
                        )
                    ),
                    metadata=entry.metadata,
                )
                decayed += 1
        return decayed

    def stats(self) -> dict[str, Any]:
        cats: dict[str, int] = {}
        for e in self._entries.values():
            cats[e.category] = cats.get(e.category, 0) + 1
        provider_snapshot = {}
        if self._embedding_provider is not None and hasattr(self._embedding_provider, "snapshot"):
            provider_snapshot = self._embedding_provider.snapshot()
        audit = self.audit()
        pending_conflicts = sum(1 for item in self._conflicts.values() if item.status in {"pending_review", "clarification_needed"})
        return {
            "total": len(self._entries),
            "by_category": cats,
            "semantic_enabled": self._embedding_provider is not None,
            "embedding": provider_snapshot,
            "conflict_count": len(self._conflicts),
            "pending_conflict_count": pending_conflicts,
            "audit": {key: value for key, value in audit.items() if key != "findings"},
        }

    def list_conflicts(self, *, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        conflicts = self._conflicts.values()
        if status:
            conflicts = [item for item in conflicts if item.status == status]
        ordered = sorted(conflicts, key=lambda item: (item.created_at, item.conflict_id), reverse=True)
        return [item.snapshot() for item in ordered[: max(0, limit)]]

    def resolve_conflict(self, conflict_id: str, resolution: str, *, reason: str = "") -> dict[str, Any]:
        conflict = self._conflicts.get(str(conflict_id or "").strip())
        if conflict is None:
            raise KeyError(f"unknown memory conflict: {conflict_id}")
        normalized = str(resolution or "").strip().lower()
        aliases = {
            "keep_new": "prefer_new",
            "keep_existing": "prefer_existing",
            "keep_old": "prefer_existing",
            "keep_both": "context_difference",
            "needs_clarification": "clarification_needed",
        }
        normalized = aliases.get(normalized, normalized)
        allowed = {"prefer_new", "prefer_existing", "context_difference", "dismiss", "clarification_needed"}
        if normalized not in allowed:
            raise ValueError(f"unsupported memory conflict resolution: {normalized}")
        resolution_reason = str(reason or "").strip()
        if normalized in {"prefer_new", "prefer_existing"} and not resolution_reason:
            raise ValueError("destructive memory conflict resolution requires a reason")
        if conflict.status in {"resolved", "dismissed"}:
            if conflict.resolution == normalized:
                return conflict.snapshot()
            raise ValueError(f"memory conflict is already closed: {conflict.conflict_id}")

        status = "clarification_needed" if normalized == "clarification_needed" else "dismissed" if normalized == "dismiss" else "resolved"
        if normalized == "prefer_new":
            self._mark_superseded(conflict.target_entry_id, conflict.source_entry_id, conflict.conflict_id)
        elif normalized == "prefer_existing":
            self._mark_superseded(conflict.source_entry_id, conflict.target_entry_id, conflict.conflict_id)

        updated = MemoryConflict(
            conflict_id=conflict.conflict_id,
            source_entry_id=conflict.source_entry_id,
            target_entry_id=conflict.target_entry_id,
            status=status,
            reason=conflict.reason,
            confidence=conflict.confidence,
            created_at=conflict.created_at,
            resolution=normalized,
            resolution_reason=resolution_reason,
            resolved_at=0.0 if status == "clarification_needed" else time.time(),
            metadata=conflict.metadata,
        )
        self._conflicts[updated.conflict_id] = updated
        self._on_conflict_changed(updated)
        return updated.snapshot()

    def audit(self) -> dict[str, Any]:
        return summarize_memory_audit(audit_memory_state(self._entries.values(), self._conflicts.values()))

    def management_snapshot(self, *, recent_limit: int = 20, conflict_limit: int = 100) -> dict[str, Any]:
        conflicts = sorted(self._conflicts.values(), key=lambda item: (item.created_at, item.conflict_id), reverse=True)
        return {
            "schema_version": "spiritkin.memory_management.v1",
            "stats": self.stats(),
            "recent_memories": self.recent(limit=recent_limit),
            "conflicts": [self._management_conflict_snapshot(item) for item in conflicts[: max(0, conflict_limit)]],
            "audit": self.audit(),
            "resolution_options": [
                "prefer_new",
                "prefer_existing",
                "context_difference",
                "dismiss",
                "clarification_needed",
            ],
        }

    def _management_conflict_snapshot(self, conflict: MemoryConflict) -> dict[str, Any]:
        payload = conflict.snapshot()
        source = self._entries.get(conflict.source_entry_id)
        target = self._entries.get(conflict.target_entry_id)
        payload["source_memory"] = source.snapshot() if source is not None else {}
        payload["target_memory"] = target.snapshot() if target is not None else {}
        return payload

    def _detect_conflicts(self, new_entry: LongTermMemoryEntry) -> None:
        if new_entry.category not in self.CONFLICT_CATEGORIES or new_entry.metadata.get("detect_conflicts") is False:
            return
        for existing in list(self._entries.values()):
            if existing.entry_id == new_entry.entry_id or existing.category != new_entry.category:
                continue
            if existing.memory_state == "archived" or existing.metadata.get("resolution_status") == "superseded":
                continue
            duplicate = any(
                conflict.status in {"pending_review", "clarification_needed"}
                and {conflict.source_entry_id, conflict.target_entry_id} == {new_entry.entry_id, existing.entry_id}
                for conflict in self._conflicts.values()
            )
            if duplicate:
                continue
            candidate = find_conflict_candidate(new_entry.content, existing.content, new_metadata=new_entry.metadata)
            if not candidate.is_candidate:
                continue
            self._conflict_counter += 1
            conflict = MemoryConflict(
                conflict_id=f"memconf-{self._conflict_counter:06d}",
                source_entry_id=new_entry.entry_id,
                target_entry_id=existing.entry_id,
                reason=candidate.reason,
                confidence=candidate.confidence,
                metadata={
                    "shared_topics": list(candidate.shared_topics),
                    "source_evidence_quotes": _evidence_quotes(new_entry.metadata),
                    "target_evidence_quotes": _evidence_quotes(existing.metadata),
                },
            )
            self._conflicts[conflict.conflict_id] = conflict
            self._on_conflict_changed(conflict)

    def _mark_superseded(self, entry_id: str, superseded_by: str, conflict_id: str) -> None:
        entry = self._entries.get(entry_id)
        if entry is None or superseded_by not in self._entries:
            raise ValueError("memory conflict references a missing entry")
        metadata = dict(entry.metadata)
        metadata.update(
            {
                "resolution_status": "superseded",
                "superseded_by": superseded_by,
                "resolution_conflict_id": conflict_id,
            }
        )
        self._entries[entry_id] = replace_entry(entry, activation=0.0, memory_state="archived", metadata=metadata)
        self._on_entry_changed(self._entries[entry_id])

    def _on_conflict_changed(self, conflict: MemoryConflict) -> None:
        del conflict

    def _on_entry_changed(self, entry: LongTermMemoryEntry) -> None:
        del entry

    def recent(self, limit: int = 10) -> list[dict[str, Any]]:
        sorted_entries = sorted(self._entries.values(), key=lambda e: e.timestamp, reverse=True)
        return [e.snapshot() for e in sorted_entries[:limit]]


class JsonlLongTermMemoryStore(LongTermMemoryStore):
    def __init__(
        self,
        path: str | Path,
        limit: int = 500,
        *,
        embedding_provider: BaseEmbeddingProvider | None = None,
        activation_policy: MemoryActivationPolicy = DEFAULT_ACTIVATION_POLICY,
    ):
        self._path = Path(path).resolve()
        self._conflict_path = self._path.with_name(f"{self._path.stem}.conflicts.jsonl")
        super().__init__(limit=limit, embedding_provider=embedding_provider, activation_policy=activation_policy)
        self._load_existing()
        self._load_conflicts()

    def _load_existing(self) -> None:
        if not self._path.exists():
            return
        try:
            for line in self._path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    entry = LongTermMemoryEntry.from_snapshot(data)
                    if entry.entry_id:
                        self._entries[entry.entry_id] = entry
                        match = re.fullmatch(r"ltm-(\d+)", entry.entry_id)
                        if match:
                            self._counter = max(self._counter, int(match.group(1)))
                except (json.JSONDecodeError, TypeError):
                    continue
        except (OSError, PermissionError):
            pass

    def _load_conflicts(self) -> None:
        if not self._conflict_path.exists():
            return
        try:
            for line in self._conflict_path.read_text(encoding="utf-8").splitlines():
                try:
                    conflict = MemoryConflict.from_snapshot(json.loads(line))
                except (json.JSONDecodeError, TypeError, ValueError):
                    continue
                if not conflict.conflict_id:
                    continue
                self._conflicts[conflict.conflict_id] = conflict
                match = re.fullmatch(r"memconf-(\d+)", conflict.conflict_id)
                if match:
                    self._conflict_counter = max(self._conflict_counter, int(match.group(1)))
        except (OSError, PermissionError):
            pass

    def add(self, category: str, content: str, *, importance: float = 0.5, metadata: dict[str, Any] | None = None) -> LongTermMemoryEntry:
        entry = super().add(category, content, importance=importance, metadata=metadata)
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry.snapshot(), ensure_ascii=False) + "\n")
        except OSError:
            pass
        return entry

    def recall(self, query: str = "", *, category: str | None = None, top_k: int = 10, min_importance: float = 0.0) -> list[LongTermMemoryEntry]:
        results = super().recall(query, category=category, top_k=top_k, min_importance=min_importance)
        if results:
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                with open(self._path, "a", encoding="utf-8") as fh:
                    for entry in results:
                        fh.write(json.dumps(entry.snapshot(), ensure_ascii=False) + "\n")
            except OSError:
                pass
        return results

    def maintain(self, entry_id: str) -> LongTermMemoryEntry | None:
        entry = super().maintain(entry_id)
        if entry is not None:
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                with open(self._path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(entry.snapshot(), ensure_ascii=False) + "\n")
            except OSError:
                pass
        return entry

    def _on_conflict_changed(self, conflict: MemoryConflict) -> None:
        self._append_jsonl(self._conflict_path, conflict.snapshot())

    def _on_entry_changed(self, entry: LongTermMemoryEntry) -> None:
        self._append_jsonl(self._path, entry.snapshot())

    @staticmethod
    def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except OSError:
            pass


def build_long_term_memory(path: str | Path | None = None) -> LongTermMemoryStore:
    if not path:
        return LongTermMemoryStore()
    embedding_provider = None
    try:
        from backend.knowledge.embedding import get_embedding_service

        embedding_provider = get_embedding_service()
    except Exception:
        pass
    return JsonlLongTermMemoryStore(path, embedding_provider=embedding_provider)


def _entry_embedding(entry: LongTermMemoryEntry) -> list[float]:
    raw = entry.metadata.get("semantic_embedding")
    if not isinstance(raw, list):
        return []
    try:
        return [float(value) for value in raw]
    except (TypeError, ValueError):
        return []


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=False))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm <= 0.0 or right_norm <= 0.0:
        return 0.0
    return dot / (left_norm * right_norm)


def replace_entry(entry: LongTermMemoryEntry, **changes: Any) -> LongTermMemoryEntry:
    payload = entry.snapshot()
    payload.update(changes)
    return LongTermMemoryEntry.from_snapshot(payload)


def _evidence_quotes(metadata: dict[str, Any]) -> list[str]:
    raw = metadata.get("evidence_quotes")
    if not isinstance(raw, list):
        return []
    return [str(item).strip()[:500] for item in raw if str(item or "").strip()]
