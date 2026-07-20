from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class UnifiedMemoryResult:
    short_term: list[dict[str, Any]] = field(default_factory=list)
    long_term: list[dict[str, Any]] = field(default_factory=list)
    workflow: list[dict[str, Any]] = field(default_factory=list)
    personality: dict[str, Any] = field(default_factory=dict)
    relationship: dict[str, Any] = field(default_factory=dict)
    recall_summary: str = ""


class MemoryOrchestrator:
    def __init__(
        self,
        *,
        short_term=None,
        long_term=None,
        workflow=None,
        personality_store=None,
        relationship_store=None,
        event_persistence=None,
    ):
        self._short_term = short_term
        self._long_term = long_term
        self._workflow = workflow
        self._personality_store = personality_store
        self._relationship_store = relationship_store
        self._event_persistence = event_persistence

    def recall(self, query: str = "", *, top_k: int = 5) -> UnifiedMemoryResult:
        result = UnifiedMemoryResult()

        if self._short_term is not None:
            result.short_term = self._short_term.recent(limit=top_k) if hasattr(self._short_term, "recent") else []

        if self._long_term is not None and hasattr(self._long_term, "recall"):
            entries = self._long_term.recall(query, top_k=top_k)
            result.long_term = [e.snapshot() for e in entries]

        if self._workflow is not None:
            result.workflow = self._workflow.recent(limit=top_k) if hasattr(self._workflow, "recent") else []

        if self._personality_store is not None and hasattr(self._personality_store, "state"):
            result.personality = self._personality_store.state.snapshot()

        if self._relationship_store is not None and hasattr(self._relationship_store, "snapshot"):
            result.relationship = self._relationship_store.snapshot()

        parts: list[str] = []
        if result.long_term:
            parts.append(f"长期记忆找到 {len(result.long_term)} 条")
        if result.workflow:
            parts.append(f"最近执行了 {len(result.workflow)} 个操作")
        if result.personality:
            parts.append(f"当前心情: {result.personality.get('mood', 'neutral')}")
        if result.relationship:
            parts.append(f"关系阶段: {result.relationship.get('stage', 'new')}")
        result.recall_summary = "；".join(parts) or "无相关记忆"

        return result

    def record_interaction(self, *, user_input: str, reply_text: str, success: bool = True, category: str = "conversation") -> None:
        if self._long_term is not None and hasattr(self._long_term, "add"):
            self._long_term.add(
                category,
                f"用户: {user_input[:200]} | 助手: {reply_text[:200]}",
                importance=0.3,
                metadata={
                    "source": "runtime_interaction",
                    "attribution": "mixed",
                    "evidence_quotes": [user_input[:500]],
                },
            )

        if self._personality_store is not None and hasattr(self._personality_store, "record_interaction"):
            self._personality_store.record_interaction(success=success)

        if self._relationship_store is not None and hasattr(self._relationship_store, "record_interaction"):
            self._relationship_store.record_interaction(success=success)

        if self._event_persistence is not None and hasattr(self._event_persistence, "record"):
            self._event_persistence.record("assistant.message", {"text": reply_text[:200], "success": success})

    def record_user_feedback(self, feedback: str) -> None:
        if self._long_term is not None and hasattr(self._long_term, "add"):
            self._long_term.add(
                "user_feedback",
                feedback,
                importance=0.7,
                metadata={
                    "source": "user_feedback",
                    "attribution": "user_explicit",
                    "evidence_quotes": [feedback[:500]],
                },
            )

    def memory_management_snapshot(self) -> dict[str, Any]:
        if self._long_term is None or not hasattr(self._long_term, "management_snapshot"):
            return {
                "schema_version": "spiritkin.memory_management.v1",
                "available": False,
                "stats": {},
                "recent_memories": [],
                "conflicts": [],
                "audit": {},
            }
        return dict(self._long_term.management_snapshot())

    def resolve_memory_conflict(self, conflict_id: str, resolution: str, *, reason: str = "") -> dict[str, Any]:
        if self._long_term is None or not hasattr(self._long_term, "resolve_conflict"):
            raise RuntimeError("long-term memory conflict management is unavailable")
        return dict(self._long_term.resolve_conflict(conflict_id, resolution, reason=reason))

    def consolidate_all(self) -> dict[str, int]:
        result = {}
        if self._long_term is not None and hasattr(self._long_term, "consolidate"):
            result["long_term_merged"] = self._long_term.consolidate()
        if self._long_term is not None and hasattr(self._long_term, "decay_importance"):
            result["long_term_decayed"] = self._long_term.decay_importance()
        return result

    def snapshot(self) -> dict[str, Any]:
        snap: dict[str, Any] = {}
        if self._short_term is not None and hasattr(self._short_term, "recent"):
            snap["short_term_count"] = len(self._short_term.recent(limit=100))
        if self._long_term is not None and hasattr(self._long_term, "stats"):
            snap["long_term"] = self._long_term.stats()
            if hasattr(self._long_term, "list_conflicts"):
                snap["long_term_conflicts"] = self._long_term.list_conflicts(limit=20)
            if hasattr(self._long_term, "audit"):
                audit = self._long_term.audit()
                snap["memory_audit"] = {key: value for key, value in audit.items() if key != "findings"}
        if self._workflow is not None and hasattr(self._workflow, "stats"):
            snap["workflow"] = self._workflow.stats()
        if self._personality_store is not None and hasattr(self._personality_store, "state"):
            snap["personality"] = self._personality_store.state.snapshot()
            snap["lpm_state"] = self._personality_store.state.lpm_state()
        if self._relationship_store is not None and hasattr(self._relationship_store, "snapshot"):
            snap["relationship"] = self._relationship_store.snapshot()
        if self._event_persistence is not None and hasattr(self._event_persistence, "stats"):
            snap["events"] = self._event_persistence.stats()
        return snap
