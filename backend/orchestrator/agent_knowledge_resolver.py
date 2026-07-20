"""Per-agent knowledge-base retrieval extracted from AgentCluster (cluster Q).

Resolves and caches directory-backed retrievers declared in an agent runtime
policy, then injects retrieved hits into the agent context. Owns its own
retriever cache; no dependency on AgentCluster internals. Logic preserved
verbatim.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from backend.agents.base import AgentContext


class AgentKnowledgeResolver:
    def __init__(self) -> None:
        self._retrievers: dict[str, object] = {}

    @staticmethod
    def serialize_hit(hit) -> dict[str, object]:
        chunk = getattr(hit, "chunk", None)
        return {
            "document_id": str(getattr(chunk, "document_id", "") or ""),
            "text": str(getattr(chunk, "text", "") or ""),
            "source_title": str(getattr(hit, "source_title", "") or ""),
            "score": float(getattr(hit, "score", 0.0) or 0.0),
            "metadata": dict(getattr(chunk, "metadata", {}) or {}),
        }

    def resolve_retriever(self, knowledge_base: dict[str, object]):
        if not bool(knowledge_base.get("enabled", True)):
            return None
        path_value = str(knowledge_base.get("path") or "").strip()
        if not path_value:
            return None
        path = Path(path_value)
        if not path.is_absolute():
            path = Path.cwd() / path
        path = path.resolve()
        if not path.exists() or not path.is_dir():
            return None
        cache_key = path.as_posix()
        if cache_key in self._retrievers:
            return self._retrievers[cache_key]
        try:
            from backend.knowledge import build_retriever_from_directory

            retriever = build_retriever_from_directory(path)
        except Exception:
            return None
        self._retrievers[cache_key] = retriever
        return retriever

    def with_hits(self, context: AgentContext, policy: dict[str, object]) -> AgentContext:
        knowledge_base = policy.get("knowledge_base")
        if not isinstance(knowledge_base, dict):
            return context
        retriever = self.resolve_retriever(knowledge_base)
        if retriever is None:
            return context
        try:
            hits = retriever.retrieve(context.user_input, top_k=3)
        except Exception:
            return context
        serialized = [self.serialize_hit(hit) for hit in hits]
        if not serialized:
            return context
        metadata = dict(context.metadata)
        existing_hits = list(metadata.get("knowledge_hits") or [])
        metadata["knowledge_hits"] = [*serialized, *existing_hits]
        metadata["agent_knowledge_hits"] = serialized
        return replace(context, metadata=metadata)
