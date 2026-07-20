from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

DEFAULT_RELATIONSHIP_PATH = "state/relationship.json"
MAX_BOUNDARIES = 64

_BOUNDARY_PATTERNS = (
    re.compile(
        r"(?:以后|今后|从现在开始)?\s*(?:请)?\s*(?:不要再|别再|不要|别|不许|禁止)\s*(?P<subject>[^。！？!?\n]{1,120})",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:我不想再|我不想|我拒绝)\s*(?P<subject>(?:聊|谈|提|讨论|听|看到|收到|被叫)[^。！？!?\n]{0,110})",
        re.IGNORECASE,
    ),
)
_RELEASE_PATTERNS = (
    re.compile(r"(?:现在)?(?:可以|允许)(?:再)?\s*(?P<subject>[^。！？!?\n]{1,120})", re.IGNORECASE),
    re.compile(r"(?:我)?不介意\s*(?P<subject>[^。！？!?\n]{1,120})", re.IGNORECASE),
    re.compile(r"不用(?:再)?避开\s*(?P<subject>[^。！？!?\n]{1,120})", re.IGNORECASE),
)
_PRAISE_MARKERS = ("谢谢", "做得好", "很棒", "靠谱", "正是我想要的", "这样很好")
_CORRECTION_MARKERS = ("你又", "说过不要", "我已经说了", "别重复", "不尊重", "冒犯", "不是这个意思")
_DISTRESS_MARKERS = ("难过", "焦虑", "压力很大", "很累", "撑不住", "不开心", "失眠")


def _now() -> float:
    return time.time()


def _clean_subject(value: str) -> str:
    cleaned = re.sub(r"[\x00-\x1f\x7f]", " ", str(value or ""))
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ，,。；;：:")
    return cleaned[:120]


def _normalized_subject(value: str) -> str:
    return re.sub(r"[^a-z0-9\u3400-\u9fff]+", "", str(value or "").lower())


def _subject_tokens(value: str) -> set[str]:
    normalized = _normalized_subject(value)
    tokens = set(re.findall(r"[a-z0-9]+", normalized))
    chinese = "".join(re.findall(r"[\u3400-\u9fff]", normalized))
    tokens.update(chinese[index : index + 2] for index in range(max(0, len(chinese) - 1)))
    if chinese and len(chinese) < 2:
        tokens.add(chinese)
    return {item for item in tokens if item}


def _subjects_match(left: str, right: str) -> bool:
    left_normalized = _normalized_subject(left)
    right_normalized = _normalized_subject(right)
    if not left_normalized or not right_normalized:
        return False
    if left_normalized in right_normalized or right_normalized in left_normalized:
        return True
    left_tokens = _subject_tokens(left)
    right_tokens = _subject_tokens(right)
    if not left_tokens or not right_tokens:
        return False
    overlap = len(left_tokens & right_tokens)
    return overlap / max(1, min(len(left_tokens), len(right_tokens))) >= 0.6


def _boundary_kind(subject: str) -> str:
    normalized = subject.lower()
    if any(token in normalized for token in ("叫我", "称呼", "喊我", "昵称")):
        return "address"
    if any(token in normalized for token in ("主动", "提醒", "打扰", "通知", "弹窗", "推送", "消息")):
        return "proactive"
    if any(token in normalized for token in ("聊", "谈", "提", "讨论", "问", "话题")):
        return "topic"
    if any(token in normalized for token in ("语气", "回复", "玩笑", "表情", "emoji", "风格")):
        return "style"
    return "general"


@dataclass(frozen=True)
class RelationshipBoundary:
    boundary_id: str
    kind: str
    subject: str
    statement: str
    active: bool = True
    created_at: float = field(default_factory=_now)
    updated_at: float = field(default_factory=_now)
    repeated_count: int = 0
    source: str = "explicit_user"

    def snapshot(self) -> dict[str, Any]:
        return {
            "boundary_id": self.boundary_id,
            "kind": self.kind,
            "subject": self.subject,
            "statement": self.statement,
            "active": self.active,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "repeated_count": self.repeated_count,
            "source": self.source,
        }

    @classmethod
    def from_snapshot(cls, payload: dict[str, Any]) -> RelationshipBoundary:
        return cls(
            boundary_id=str(payload.get("boundary_id") or ""),
            kind=str(payload.get("kind") or "general"),
            subject=_clean_subject(str(payload.get("subject") or "")),
            statement=_clean_subject(str(payload.get("statement") or "")),
            active=bool(payload.get("active", True)),
            created_at=float(payload.get("created_at") or _now()),
            updated_at=float(payload.get("updated_at") or _now()),
            repeated_count=max(0, int(payload.get("repeated_count") or 0)),
            source=str(payload.get("source") or "explicit_user"),
        )


@dataclass
class RelationshipState:
    trust: float = 0.5
    familiarity: float = 0.0
    interaction_count: int = 0
    positive_signal_count: int = 0
    correction_count: int = 0
    last_signal: str = "neutral"
    last_signal_at: float = 0.0
    updated_at: float = field(default_factory=_now)
    boundaries: list[RelationshipBoundary] = field(default_factory=list)

    @property
    def stage(self) -> str:
        if self.interaction_count >= 50 and self.trust >= 0.75:
            return "trusted"
        if self.interaction_count >= 20 and self.trust >= 0.62:
            return "familiar"
        if self.interaction_count >= 5:
            return "acquainted"
        return "new"

    def active_boundaries(self) -> list[RelationshipBoundary]:
        return [item for item in self.boundaries if item.active]

    def care_strategy(self) -> dict[str, str]:
        active = self.active_boundaries()
        quiet_requested = any(
            item.kind == "proactive"
            and any(token in item.subject for token in ("主动", "打扰", "提醒", "通知", "消息", "推送"))
            for item in active
        )
        if quiet_requested:
            return {"mode": "quiet_presence", "proactive_level": "off", "tone": "brief_respectful"}
        if self.last_signal == "boundary":
            return {"mode": "boundary_acknowledgement", "proactive_level": "low", "tone": "brief_accountable"}
        if self.last_signal == "correction":
            return {"mode": "repair_and_listen", "proactive_level": "low", "tone": "accountable"}
        if self.last_signal == "distress":
            return {"mode": "gentle_support", "proactive_level": "low", "tone": "calm_non_intrusive"}
        return {
            "mode": "steady_companion" if self.stage in {"familiar", "trusted"} else "focused_support",
            "proactive_level": "normal" if self.stage == "trusted" else "low",
            "tone": "warm" if self.stage in {"familiar", "trusted"} else "respectful",
        }

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": "relationship.v1",
            "trust": round(max(0.0, min(1.0, self.trust)), 4),
            "familiarity": round(max(0.0, min(1.0, self.familiarity)), 4),
            "interaction_count": self.interaction_count,
            "positive_signal_count": self.positive_signal_count,
            "correction_count": self.correction_count,
            "last_signal": self.last_signal,
            "last_signal_at": self.last_signal_at,
            "updated_at": self.updated_at,
            "stage": self.stage,
            "care_strategy": self.care_strategy(),
            "active_boundary_count": len(self.active_boundaries()),
            "boundaries": [item.snapshot() for item in self.boundaries],
        }

    @classmethod
    def from_snapshot(cls, payload: dict[str, Any]) -> RelationshipState:
        boundaries = []
        for item in payload.get("boundaries") or []:
            if isinstance(item, dict):
                boundary = RelationshipBoundary.from_snapshot(item)
                if boundary.boundary_id and boundary.subject:
                    boundaries.append(boundary)
        return cls(
            trust=max(0.0, min(1.0, float(payload.get("trust") if payload.get("trust") is not None else 0.5))),
            familiarity=max(0.0, min(1.0, float(payload.get("familiarity") or 0.0))),
            interaction_count=max(0, int(payload.get("interaction_count") or 0)),
            positive_signal_count=max(0, int(payload.get("positive_signal_count") or 0)),
            correction_count=max(0, int(payload.get("correction_count") or 0)),
            last_signal=str(payload.get("last_signal") or "neutral"),
            last_signal_at=float(payload.get("last_signal_at") or 0.0),
            updated_at=float(payload.get("updated_at") or _now()),
            boundaries=boundaries[-MAX_BOUNDARIES:],
        )


class RelationshipStore:
    def __init__(self, path: str | Path | None = None):
        configured = path if path is not None else os.getenv("SPIRITKIN_RELATIONSHIP_PATH") or DEFAULT_RELATIONSHIP_PATH
        self._path = Path(configured).resolve() if str(configured) else None
        self._lock = threading.RLock()
        self._state = RelationshipState()
        self._load()

    @property
    def state(self) -> RelationshipState:
        return self._state

    def observe_user_input(self, text: str) -> dict[str, Any]:
        normalized = str(text or "").strip()
        if not normalized:
            return {"signal": "neutral", "changed": False}
        with self._lock:
            released = self._release_boundary(normalized)
            if released is not None:
                self._set_signal("boundary_released")
                self._save()
                return {"signal": "boundary_released", "changed": True, "boundary": released.snapshot()}

            boundary = self._extract_boundary(normalized)
            if boundary is not None:
                stored, created = self._store_boundary(boundary)
                self._set_signal("boundary")
                self._save()
                return {"signal": "boundary", "changed": True, "created": created, "boundary": stored.snapshot()}

            signal = self._detect_signal(normalized)
            if signal == "praise":
                self._state.positive_signal_count += 1
                self._state.trust = min(1.0, self._state.trust + 0.02)
            elif signal == "correction":
                self._state.correction_count += 1
                self._state.trust = max(0.0, self._state.trust - 0.04)
            if signal != "neutral":
                self._set_signal(signal)
                self._save()
            return {"signal": signal, "changed": signal != "neutral"}

    def record_interaction(self, *, success: bool = True) -> None:
        with self._lock:
            self._state.interaction_count += 1
            self._state.familiarity = min(1.0, self._state.interaction_count / 50.0)
            delta = 0.003 if success else -0.015
            self._state.trust = max(0.0, min(1.0, self._state.trust + delta))
            self._state.updated_at = _now()
            self._save()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._state.snapshot()

    def context_snapshot(self) -> dict[str, Any]:
        with self._lock:
            snapshot = self._state.snapshot()
            snapshot["boundaries"] = [item.snapshot() for item in self._state.active_boundaries()[-12:]]
            return snapshot

    def _extract_boundary(self, text: str) -> RelationshipBoundary | None:
        for pattern in _BOUNDARY_PATTERNS:
            match = pattern.search(text)
            if match is None:
                continue
            subject = _clean_subject(match.group("subject"))
            if not subject:
                continue
            key = _normalized_subject(subject)
            boundary_id = "boundary-" + hashlib.blake2s(key.encode("utf-8"), digest_size=8).hexdigest()
            return RelationshipBoundary(
                boundary_id=boundary_id,
                kind=_boundary_kind(subject),
                subject=subject,
                statement=_clean_subject(match.group(0)),
            )
        return None

    def _store_boundary(self, boundary: RelationshipBoundary) -> tuple[RelationshipBoundary, bool]:
        for index, existing in enumerate(self._state.boundaries):
            if existing.boundary_id == boundary.boundary_id or _subjects_match(existing.subject, boundary.subject):
                updated = replace(
                    existing,
                    active=True,
                    statement=boundary.statement,
                    updated_at=_now(),
                    repeated_count=existing.repeated_count + 1,
                )
                self._state.boundaries[index] = updated
                return updated, False
        self._state.boundaries.append(boundary)
        self._state.boundaries = self._state.boundaries[-MAX_BOUNDARIES:]
        return boundary, True

    def _release_boundary(self, text: str) -> RelationshipBoundary | None:
        for pattern in _RELEASE_PATTERNS:
            match = pattern.search(text)
            if match is None:
                continue
            subject = _clean_subject(match.group("subject"))
            for index, existing in enumerate(self._state.boundaries):
                if existing.active and _subjects_match(existing.subject, subject):
                    updated = replace(existing, active=False, updated_at=_now())
                    self._state.boundaries[index] = updated
                    return updated
        return None

    @staticmethod
    def _detect_signal(text: str) -> str:
        normalized = text.lower()
        if any(marker in normalized for marker in _CORRECTION_MARKERS):
            return "correction"
        if any(marker in normalized for marker in _DISTRESS_MARKERS):
            return "distress"
        if any(marker in normalized for marker in _PRAISE_MARKERS):
            return "praise"
        return "neutral"

    def _set_signal(self, signal: str) -> None:
        self._state.last_signal = signal
        self._state.last_signal_at = _now()
        self._state.updated_at = self._state.last_signal_at

    def _load(self) -> None:
        if self._path is None or not self._path.exists():
            return
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if isinstance(payload, dict):
            self._state = RelationshipState.from_snapshot(payload)

    def _save(self) -> None:
        if self._path is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(self._path.suffix + ".tmp")
        temporary.write_text(json.dumps(self._state.snapshot(), ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self._path)


def build_relationship_store(path: str | Path | None = None) -> RelationshipStore:
    return RelationshipStore(path)
