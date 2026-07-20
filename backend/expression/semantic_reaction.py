from __future__ import annotations

import json
import math
import os
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from backend.agents.base import AgentReply, _normalize_action, _normalize_emotion
from backend.knowledge.base import BaseEmbeddingProvider, EmbeddingVector
from backend.knowledge.embedding import get_embedding_service

DEFAULT_LIBRARY_PATH = Path(__file__).with_name("emotion_library.json")
DEFAULT_THRESHOLD = 0.42
_ASCII_WORD = re.compile(r"^[a-z0-9_'-]+$", re.IGNORECASE)


@dataclass(frozen=True)
class AvatarReaction:
    emotion: str
    action: str
    intensity: float
    confidence: float
    match_type: str
    profile_id: str
    provider: str
    degraded: bool
    reason: str
    source_hash: str

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": "spiritkin.avatar_reaction.v1",
            "emotion": self.emotion,
            "action": self.action,
            "intensity": round(self.intensity, 4),
            "confidence": round(self.confidence, 4),
            "match_type": self.match_type,
            "profile_id": self.profile_id,
            "provider": self.provider,
            "degraded": self.degraded,
            "reason": self.reason,
            "source_hash": self.source_hash,
        }


class SemanticReactionMatcher:
    """Select an avatar reaction through the shared embedding service.

    A failed or unavailable embedding provider never blocks the reply path. The
    matcher falls back to the same versioned library's keywords and records the
    degraded path in the event metadata.
    """

    def __init__(
        self,
        *,
        library_path: str | os.PathLike[str] | None = None,
        threshold: float | None = None,
        provider_factory: Callable[[], BaseEmbeddingProvider] | None = None,
    ) -> None:
        self.library_path = Path(library_path or DEFAULT_LIBRARY_PATH)
        self.threshold = float(threshold if threshold is not None else os.getenv("SPIRITKIN_AVATAR_SEMANTIC_THRESHOLD", DEFAULT_THRESHOLD))
        self._provider_factory = provider_factory or get_embedding_service
        self._profiles = self._load_profiles()
        self._provider: BaseEmbeddingProvider | None = None
        self._profile_embeddings: list[EmbeddingVector] | None = None
        self._provider_error = ""
        self._provider_retry_at = 0.0
        self._lock = threading.RLock()

    def match(self, text: str) -> AvatarReaction:
        source = str(text or "").strip()
        source_hash = sha256(source.encode("utf-8")).hexdigest()[:16] if source else ""
        if not source:
            return self._reaction(self._neutral_profile(), 0.0, "fallback", "none", True, "empty_text", source_hash)

        semantic = self._semantic_match(source, source_hash)
        if semantic is not None:
            return semantic

        keyword = self._keyword_match(source)
        if keyword is not None:
            reason = "embedding_unavailable" if self._provider_error else "semantic_below_threshold"
            return self._reaction(keyword, 0.78, "keyword_fallback", self._provider_name(), True, reason, source_hash)

        reason = self._provider_error or "no_reaction_above_threshold"
        return self._reaction(self._neutral_profile(), 0.0, "fallback", self._provider_name(), bool(self._provider_error), reason, source_hash)

    def explicit(self, text: str, emotion: str, action: str) -> AvatarReaction:
        source = str(text or "").strip()
        normalized_emotion = _normalize_emotion(emotion, "neutral")
        normalized_action = _normalize_action(action, "idle")
        profile = next(
            (
                item
                for item in self._profiles
                if item["emotion"] == normalized_emotion
                and (item["action"] == normalized_action or normalized_action in {"", "idle"})
            ),
            None,
        ) or next((item for item in self._profiles if item["emotion"] == normalized_emotion), self._neutral_profile())
        return AvatarReaction(
            emotion=normalized_emotion,
            action=normalized_action,
            intensity=float(profile.get("intensity") or 0.0),
            confidence=1.0,
            match_type="explicit",
            profile_id=str(profile.get("id") or normalized_emotion),
            provider="reply_contract",
            degraded=False,
            reason="reply_declared_reaction",
            source_hash=sha256(source.encode("utf-8")).hexdigest()[:16] if source else "",
        )

    def _load_profiles(self) -> list[dict[str, Any]]:
        payload = json.loads(self.library_path.read_text(encoding="utf-8"))
        raw_profiles = payload.get("profiles") if isinstance(payload, dict) else None
        if not isinstance(raw_profiles, list):
            raise ValueError(f"avatar reaction library has no profiles: {self.library_path}")
        profiles: list[dict[str, Any]] = []
        for raw in raw_profiles:
            if not isinstance(raw, dict) or not str(raw.get("id") or "").strip():
                continue
            profiles.append(
                {
                    **raw,
                    "id": str(raw.get("id") or "").strip(),
                    "emotion": _normalize_emotion(raw.get("emotion"), "neutral"),
                    "action": _normalize_action(raw.get("action"), "idle"),
                    "intensity": max(0.0, min(1.5, float(raw.get("intensity") or 0.0))),
                    "priority": int(raw.get("priority") or 0),
                    "keywords": [str(item).strip().lower() for item in raw.get("keywords") or [] if str(item).strip()],
                }
            )
        if not any(item["id"] == "neutral" for item in profiles):
            raise ValueError("avatar reaction library requires a neutral profile")
        return profiles

    def _profile_text(self, profile: dict[str, Any]) -> str:
        return " ".join(
            part
            for part in (
                str(profile.get("description") or ""),
                " ".join(profile.get("keywords") or []),
                str(profile.get("emotion") or ""),
                str(profile.get("action") or ""),
            )
            if part
        )

    def _ensure_provider(self) -> BaseEmbeddingProvider | None:
        with self._lock:
            if self._provider is not None:
                return self._provider
            if time.monotonic() < self._provider_retry_at:
                return None
            try:
                self._provider = self._provider_factory()
                self._profile_embeddings = self._provider.embed_documents([self._profile_text(item) for item in self._profiles])
                if len(self._profile_embeddings) != len(self._profiles):
                    raise RuntimeError("embedding provider returned an incomplete profile set")
                self._provider_error = ""
            except Exception as exc:
                self._provider = None
                self._profile_embeddings = None
                self._provider_error = f"{type(exc).__name__}: {exc}"[:240]
                self._provider_retry_at = time.monotonic() + 30.0
            return self._provider

    def _semantic_match(self, source: str, source_hash: str) -> AvatarReaction | None:
        provider = self._ensure_provider()
        if provider is None or not self._profile_embeddings:
            return None
        try:
            query = provider.embed_query(source[:1200])
            scores = [self._cosine(query, vector) for vector in self._profile_embeddings]
        except Exception as exc:
            with self._lock:
                self._provider = None
                self._profile_embeddings = None
                self._provider_error = f"{type(exc).__name__}: {exc}"[:240]
                self._provider_retry_at = time.monotonic() + 15.0
            return None
        if not scores:
            return None
        index = max(range(len(scores)), key=scores.__getitem__)
        score = float(scores[index])
        profile = self._profiles[index]
        if score < self.threshold or profile["id"] == "neutral":
            return None
        return self._reaction(profile, score, "semantic", self._provider_name(), False, "embedding_similarity", source_hash)

    def _keyword_match(self, source: str) -> dict[str, Any] | None:
        lowered = source.lower()
        compact = re.sub(r"\s+", "", lowered)
        candidates: list[tuple[int, int, dict[str, Any]]] = []
        for profile in self._profiles:
            for keyword in profile.get("keywords") or []:
                matched = bool(re.search(rf"\b{re.escape(keyword)}\b", lowered)) if _ASCII_WORD.fullmatch(keyword) else keyword.replace(" ", "") in compact
                if matched:
                    candidates.append((int(profile.get("priority") or 0), len(keyword), profile))
        if not candidates:
            return None
        candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return candidates[0][2]

    def _neutral_profile(self) -> dict[str, Any]:
        return next(item for item in self._profiles if item["id"] == "neutral")

    def _provider_name(self) -> str:
        if self._provider is not None:
            model = str(getattr(self._provider, "model", "") or "").strip()
            return f"{type(self._provider).__name__}:{model}" if model else type(self._provider).__name__
        return "none"

    @staticmethod
    def _cosine(left: EmbeddingVector, right: EmbeddingVector) -> float:
        if not left or not right or len(left) != len(right):
            return 0.0
        numerator = sum(float(a) * float(b) for a, b in zip(left, right, strict=False))
        left_norm = math.sqrt(sum(float(value) ** 2 for value in left))
        right_norm = math.sqrt(sum(float(value) ** 2 for value in right))
        if left_norm <= 0 or right_norm <= 0:
            return 0.0
        return numerator / (left_norm * right_norm)

    @staticmethod
    def _reaction(
        profile: dict[str, Any],
        confidence: float,
        match_type: str,
        provider: str,
        degraded: bool,
        reason: str,
        source_hash: str,
    ) -> AvatarReaction:
        return AvatarReaction(
            emotion=str(profile.get("emotion") or "neutral"),
            action=str(profile.get("action") or "idle"),
            intensity=float(profile.get("intensity") or 0.0),
            confidence=max(0.0, min(1.0, float(confidence))),
            match_type=match_type,
            profile_id=str(profile.get("id") or "neutral"),
            provider=provider,
            degraded=degraded,
            reason=reason,
            source_hash=source_hash,
        )


_MATCHER: SemanticReactionMatcher | None = None
_MATCHER_LOCK = threading.Lock()


def get_semantic_reaction_matcher() -> SemanticReactionMatcher:
    global _MATCHER
    if _MATCHER is None:
        with _MATCHER_LOCK:
            if _MATCHER is None:
                _MATCHER = SemanticReactionMatcher()
    return _MATCHER


def enrich_reply_avatar_reaction(reply: AgentReply, *, matcher: SemanticReactionMatcher | None = None) -> AgentReply:
    metadata = dict(reply.metadata or {})
    existing = metadata.get("avatar_reaction")
    if isinstance(existing, dict) and existing.get("schema_version") == "spiritkin.avatar_reaction.v1":
        return reply

    resolved_matcher = matcher or get_semantic_reaction_matcher()
    explicit = str(reply.emotion or "neutral") != "neutral" or str(reply.action or "idle") not in {"", "idle"}
    reaction = (
        resolved_matcher.explicit(reply.text, reply.emotion, reply.action)
        if explicit
        else resolved_matcher.match(reply.text)
    )
    reply.emotion = reaction.emotion
    reply.action = reaction.action
    metadata["avatar_reaction"] = reaction.snapshot()
    reply.metadata = metadata
    return reply
